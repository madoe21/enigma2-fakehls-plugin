# -*- coding: utf-8 -*-
from __future__ import absolute_import

import hashlib
import itertools
import os
import select
import stat
import threading
import time

from .ffmpeg_service import async_start_ffmpeg, build_stream_url, mask_credentials, uses_stream_relay
from .mpegts import (
    TS_PACKET_SIZE,
    find_keyframe_cut,
    pcr_delta_seconds,
    read_pcr_base,
)

QUALITY_PRESETS = {
    "hw_transcode": {"label": "Hardware-Transcode (Port 8002)", "seg_duration": 2},
    "low_latency":  {"label": "Niedrige Latenz (1s)",           "seg_duration": 1},
    "balanced":     {"label": "Ausgewogen (2s)",                 "seg_duration": 2},
    "stable":       {"label": "Stabil (4s)",                     "seg_duration": 4},
}


class SegmentCutter(object):
    """Pure segmentation state machine: TS bytes in, finished segments out.

    Splits the stream at video keyframes so every segment starts decodable
    (joins and recoveries show no artifacts), and derives durations from the
    PCR media clock so EXTINF matches what players actually schedule on.
    Wall-clock and packet-boundary fallbacks keep exotic streams alive.
    Worst-case buffered data is KEYFRAME_WAIT_FACTOR target durations
    (~12 MB at 8 Mbit/s on the 4 s preset).
    """

    # If no video keyframe shows up within this many target durations
    # (stream without RAI marks), cut on a bare packet boundary like before.
    KEYFRAME_WAIT_FACTOR = 3

    def __init__(self, seg_duration, clock=time.time, logger=None):
        self._seg_duration = seg_duration
        self._clock = clock
        self._logger = logger
        self._buffer = bytearray()
        self._scan_pos = 0
        self._synced = False
        self._segment_start = None  # set on first byte, not at construction
        self._previous_cut_pcr = None
        self._warned_no_keyframes = False

    def feed(self, chunk):
        """Consume stream bytes; return a list of finished (data, duration)."""
        self._buffer.extend(chunk)
        now = self._clock()
        if self._segment_start is None:
            # The give-up clock must start at the first byte: ffmpeg needs
            # seconds for connect/probe before any output exists, and that
            # wait must not eat the keyframe-search window.
            self._segment_start = now
        if not self._synced:
            self._try_sync(now)
            return []
        return self._try_cut(now)

    def flush(self):
        """Return whatever is still buffered as the final segment."""
        if not self._buffer:
            return []
        if self._segment_start is not None:
            duration = max(0.0, self._clock() - self._segment_start)
        else:
            duration = self._seg_duration
        data = bytes(self._buffer)
        self._buffer = bytearray()
        return [(data, duration, False)]

    def _aligned_end(self):
        return len(self._buffer) - (len(self._buffer) % TS_PACKET_SIZE)

    def _extract_segment(self, cut):
        """Copy buffer[:cut] once and drop it from the buffer.

        A plain bytes(self._buffer[:cut]) copies the multi-MB segment twice
        (bytearray slice, then bytes); slicing a memoryview is zero-copy, so
        bytes() materialises exactly once. The view must be released before
        del resizes the bytearray, or that raises BufferError.
        """
        view = memoryview(self._buffer)
        segment = bytes(view[:cut])
        view.release()
        del self._buffer[:cut]
        return segment

    def _warn(self, message):
        if self._logger is not None and not self._warned_no_keyframes:
            self._warned_no_keyframes = True
            self._logger.warning(message)

    def _try_sync(self, now):
        # Drop everything before the first video keyframe: a segment 0 that
        # starts mid-GOP decodes as garbage on every player.
        cut, self._scan_pos = find_keyframe_cut(self._buffer, self._scan_pos)
        if cut is not None:
            del self._buffer[:cut]
            self._scan_pos = TS_PACKET_SIZE
            self._synced = True
            self._segment_start = now
            self._previous_cut_pcr = read_pcr_base(self._buffer[:TS_PACKET_SIZE])
        elif now - self._segment_start >= self._seg_duration * self.KEYFRAME_WAIT_FACTOR:
            self._warn("SegmentCutter: no video keyframe marks in stream, using packet-boundary cuts")
            self._synced = True
            self._segment_start = now

    def _try_cut(self, now):
        elapsed = now - self._segment_start
        if elapsed < self._seg_duration or len(self._buffer) < TS_PACKET_SIZE:
            # Skip keyframes arriving before the duration gate: the cut must
            # be the first keyframe AFTER the target duration, or segments
            # shrink to one GOP and the buffer grows without bound.
            self._scan_pos = self._aligned_end()
            return []

        cut, self._scan_pos = find_keyframe_cut(self._buffer, self._scan_pos)
        if cut == 0:
            # Buffer already starts on a keyframe (fresh after a forced
            # cut): look for the next one instead of cutting nothing.
            self._scan_pos = TS_PACKET_SIZE
            if self._previous_cut_pcr is None:
                self._previous_cut_pcr = read_pcr_base(self._buffer[:TS_PACKET_SIZE])
            return []
        if cut is not None:
            cut_pcr = read_pcr_base(self._buffer[cut:cut + TS_PACKET_SIZE])
            duration, is_discontinuity = self._segment_duration_from_pcr(
                self._previous_cut_pcr, cut_pcr, elapsed)
            segment = self._extract_segment(cut)
            self._scan_pos = TS_PACKET_SIZE
            self._previous_cut_pcr = cut_pcr
            self._segment_start = now
            return [(segment, duration, is_discontinuity)]

        if elapsed >= self._seg_duration * self.KEYFRAME_WAIT_FACTOR:
            # Stream carries no random-access marks (or a huge GOP): fall
            # back to a bare TS-packet-boundary cut to keep the stream going.
            self._warn("SegmentCutter: no video keyframe found, forcing packet-boundary cut")
            forced_cut = self._aligned_end()
            if forced_cut > 0:
                segment = self._extract_segment(forced_cut)
                self._scan_pos = 0
                self._previous_cut_pcr = None
                self._segment_start = now
                return [(segment, elapsed, False)]
        return []

    def _segment_duration_from_pcr(self, previous_pcr, cut_pcr, wall_elapsed):
        """Media-time duration between two cuts, and whether a PCR
        discontinuity was detected; wall clock when PCR is unusable."""
        if previous_pcr is None or cut_pcr is None:
            return wall_elapsed, False
        duration = pcr_delta_seconds(previous_pcr, cut_pcr)
        # A PCR discontinuity (channel switch upstream, encoder restart, or
        # on this receiver ffmpeg's -reconnect firing mid-stream on a flaky
        # source) can yield an absurd span; the wall clock is the safer
        # estimate then. The segment starting right after it spliced two
        # unrelated timelines together with nothing to say so - flag it so
        # the playlist can warn players (native decoders resync silently;
        # MSE players reject the append outright without the warning).
        if not 0.2 <= duration <= self._seg_duration * 4:
            return wall_elapsed, True
        return duration, False


# Per-life FIFO suffix: stream_id is a deterministic hash, so a reaped and
# re-created stream would otherwise reuse the same pipe path — an orphan
# ffmpeg from the previous life could attach to the new segmenter's FIFO
# and interleave TS packets until its terminate lands.
_PIPE_SEQUENCE = itertools.count()


_FILLER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "filler.ts")
# Must match the actual encoded duration of assets/filler.ts (currently a
# 2 s black+silence clip - see assets/README.md for the ffmpeg command that
# generated it). Used both as the EXTINF for each filler entry and as the
# cadence for re-emitting it while still waiting for ffmpeg.
_FILLER_DURATION = 2.0
_filler_bytes_cache = None
_filler_load_attempted = False


def _load_filler_bytes(logger=None):
    """Bundled placeholder segment, read once and cached for the process
    lifetime (it's ~36 KB and never changes at runtime)."""
    global _filler_bytes_cache, _filler_load_attempted
    if _filler_load_attempted:
        return _filler_bytes_cache
    _filler_load_attempted = True
    try:
        with open(_FILLER_PATH, "rb") as handle:
            _filler_bytes_cache = handle.read()
    except Exception as exc:
        if logger is not None:
            logger.warning("Segmenter: filler asset unavailable (" + str(exc)
                            + ") - first request will wait on ffmpeg like before")
        _filler_bytes_cache = None
    return _filler_bytes_cache


class Segmenter(threading.Thread):
    PIPE_READ_SIZE = 65536
    # Default FIFO capacity (64 KB) is ~65 ms of an 8 Mbit/s TS: any stall in
    # this thread back-pressures ffmpeg and, via TCP, the tuner stream itself,
    # which shows up as picture artifacts. 1 MB buys ~1 s of slack.
    PIPE_BUFFER_BYTES = 1 << 20

    def __init__(self, stream_id, settings, logger, seg_duration=None):
        threading.Thread.__init__(self)
        self.stream_id = stream_id
        self.settings = settings
        self.logger = logger
        self.daemon = True
        self._seg_duration = seg_duration if seg_duration is not None else settings.segment_duration()

        self._stop_event = threading.Event()
        self._writer_exited = threading.Event()
        self.segment_index = 0
        self.segments = []
        # RFC 8216 §6.2.1: EXT-X-TARGETDURATION must not change between
        # reloads — keep a never-decreasing value instead of the window max.
        self._target_duration = int(self._seg_duration) + 1
        # A live-video filler must not be shorter than the segments that
        # follow it, or TARGETDURATION would have to shrink after the fact.
        self._target_duration = max(self._target_duration, int(_FILLER_DURATION) + 1)
        self._filler_bytes = _load_filler_bytes(logger)
        self._last_filler_at = None

        self.hls_dir = settings.hls_dir()
        self.pipe_path = os.path.join(
            self.hls_dir, stream_id + "_pipe" + str(next(_PIPE_SEQUENCE)))
        self.playlist = os.path.join(self.hls_dir, "live_" + stream_id + ".m3u8")

    def write_initial_segment(self):
        """Write the filler as segment 0 synchronously, before this thread
        even starts. Call this from the request-handling thread right after
        construction so the playlist file - and therefore a redirect target
        the HTTP handler can use *immediately, with no polling* - exists
        before get_or_create_stream() returns.

        This existed as an async, polled step before (the HTTP handler
        waited on reactor.callLater ticks for the playlist to appear) but
        that polling turned out to be unreliable here: enigma2's reactor
        integration services callLater ticks on the order of ~10s, not the
        0.25s they're scheduled for, so a client could still time out
        waiting on ticks that were individually fine but arrived far too
        slowly in wall time. Making this synchronous removes the dependency
        on the reactor's timer granularity for the part that actually needs
        to be fast.
        """
        if self._filler_bytes is None:
            return False
        self._write_segment(self._filler_bytes, _FILLER_DURATION, is_filler=True)
        self._last_filler_at = time.time()
        return True

    def stop(self):
        self._stop_event.set()

    def notify_writer_exited(self):
        """FFmpeg is gone: after draining the FIFO, run() exits instead of
        re-opening the pipe and polling EOF until the cleanup timer fires."""
        self._writer_exited.set()

    def join(self, timeout=None):
        """Wait for the segmentation loop to exit (pipe closed or stop signal)."""
        threading.Thread.join(self, timeout)

    def stopped(self):
        return self._stop_event.is_set()

    def segment_path(self, index):
        # Unique, monotonically increasing names. Reusing a fixed set of ring
        # slots violates the HLS spec (same URI, new content) — VLC matches
        # live-playlist reloads by URI and stalls on recycled names.
        return os.path.join(self.hls_dir, self.stream_id + "_seg%05d.ts" % index)

    def segment_uri(self, index):
        # Relative URI — playlist and segments live in the same /hls/ path.
        # Absolute URLs cost an `ip addr` subprocess per playlist line (12
        # fork/execs every cut on a weak STB CPU) and broke access through
        # any interface other than the guessed one.
        return self.stream_id + "_seg%05d.ts" % index

    def create_pipe(self):
        if os.path.exists(self.pipe_path):
            os.unlink(self.pipe_path)
        os.mkfifo(self.pipe_path, 0o600)

    def remove_pipe(self):
        try:
            if os.path.exists(self.pipe_path):
                os.unlink(self.pipe_path)
        except Exception:
            pass

    def run(self):
        while not self.stopped() and not self._writer_exited.is_set():
            try:
                self._run_segmentation()
            except Exception as exc:
                self.logger.error("Segmenter loop error for " + self.stream_id + ": " + str(exc), exc_info=True)
                time.sleep(2)

    def _grow_pipe_buffer(self, pipe_fd):
        try:
            import fcntl
            setpipe_sz = getattr(fcntl, "F_SETPIPE_SZ", 1031)
            fcntl.fcntl(pipe_fd, setpipe_sz, self.PIPE_BUFFER_BYTES)
        except Exception as exc:
            # Without the bigger FIFO any write stall back-pressures ffmpeg
            # again — worth surfacing, the stream still works.
            self.logger.warning("Segmenter: could not grow pipe buffer: " + str(exc))

    def _run_segmentation(self):
        # O_NONBLOCK: a blocking open() would park this thread until ffmpeg
        # opens the writer end — forever if the spawn failed (bad URL, auth
        # error) — and stop() could never wake it. A non-blocking read-end
        # open always succeeds; the EOF handling below covers the window
        # before ffmpeg attaches.
        try:
            pipe_fd = os.open(self.pipe_path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            self.logger.error("Segmenter: failed to open pipe: " + str(exc))
            time.sleep(1)
            return

        self._grow_pipe_buffer(pipe_fd)
        cutter = SegmentCutter(self._seg_duration, logger=self.logger)
        got_data = False

        # write_initial_segment() (called by the request handler before this
        # thread even started) already wrote segment 0 as filler, so the
        # playlist exists and self._last_filler_at is set. Keep re-emitting
        # on the same cadence while still waiting for real ffmpeg data - a
        # cold ffmpeg start (spawn + probe + sync to the first keyframe) can
        # legitimately run into the tens of seconds on the receiver's CPU.
        if self._last_filler_at is None:
            self._maybe_emit_filler(got_data, force=True)

        try:
            while not self.stopped():
                try:
                    chunk = os.read(pipe_fd, self.PIPE_READ_SIZE)
                except BlockingIOError:
                    # Writer attached but no data right now — wait, bounded
                    # so stop() is honoured within a second.
                    self._maybe_emit_filler(got_data)
                    select.select([pipe_fd], [], [], 1.0)
                    continue
                except OSError as exc:
                    self.logger.error("Segmenter: pipe read error: " + str(exc), exc_info=True)
                    break
                if not chunk:
                    # EOF on a FIFO means "no writer": before the first byte
                    # ffmpeg simply has not opened its end yet; afterwards
                    # it exited. A known-dead ffmpeg will never attach, so
                    # stop waiting immediately in that case too.
                    if got_data:
                        self.logger.warning("Segmenter: pipe closed (FFmpeg ended)")
                        break
                    if self._writer_exited.is_set():
                        break
                    self._maybe_emit_filler(got_data)
                    time.sleep(0.1)
                    continue
                got_data = True
                for data, duration, is_discontinuity in cutter.feed(chunk):
                    self._write_segment(data, duration, is_discontinuity=is_discontinuity)
        finally:
            try:
                os.close(pipe_fd)
            except Exception:
                pass

            if not self.stopped():
                for data, duration, is_discontinuity in cutter.flush():
                    self._write_segment(data, duration, is_discontinuity=is_discontinuity)

    def _maybe_emit_filler(self, got_data, force=False):
        """Write another filler segment if real data hasn't started yet and
        the previous one is due to run out (self._last_filler_at). No-op
        once got_data is True or no filler asset was bundled/loadable."""
        if got_data or self._filler_bytes is None or self.stopped():
            return
        now = time.time()
        if not force and self._last_filler_at is not None and now - self._last_filler_at < _FILLER_DURATION:
            return
        self._write_segment(self._filler_bytes, _FILLER_DURATION, is_filler=True)
        self._last_filler_at = now

    def _write_segment(self, data, duration, is_filler=False, is_discontinuity=False):
        if len(data) < 8 * 1024:
            return

        seg_path = self.segment_path(self.segment_index)
        created_at = time.time()

        try:
            with open(seg_path, "wb") as handle:
                handle.write(data)

            self.segments.append(
                (self.segment_index, seg_path, created_at, duration, is_filler, is_discontinuity))
            self.segment_index += 1
            self._update_playlist()
            # Once per segment is enough; running this per 64 KB chunk was
            # pure overhead in the hot pipe-read loop.
            self._clean_old_segments()
        except Exception as exc:
            self.logger.error("Error writing segment " + str(self.segment_index) + ": " + str(exc))

    def _update_playlist(self):
        try:
            # The filler clip is an independently-encoded standalone file
            # (different resolution/profile - see assets/README.md) with its
            # own SPS/PPS and its own PTS/DTS timeline; real segments use
            # -copyts -start_at_zero, a completely different timeline.
            # #EXT-X-DISCONTINUITY correctly signals the jump, but MSE still
            # has to actually renegotiate the SourceBuffer across a
            # resolution/profile change to append across it, which browsers
            # handle poorly to begin with - and Shaka's live-edge start
            # position for a *freshly ready* stream (barely any real content
            # yet) can easily land exactly there. Once any real segment
            # exists, stop advertising filler ones at all so a client never
            # has a reason to cross that boundary; VLC never depended on
            # them being listed either. Filler files still get cleaned up
            # normally via segment retention/cleanup_all - only the
            # playlist's view of them changes here.
            real_segments = [seg for seg in self.segments if not seg[4]]
            source = real_segments if real_segments else self.segments

            active = source[-self.settings.playlist_size():]
            first_seq = active[0][0]
            # Players schedule fetches from EXTINF; report measured durations,
            # not the nominal target, or the live edge drifts and stutters.
            max_duration = max(seg[3] for seg in active)
            self._target_duration = max(self._target_duration, int(max_duration) + 1)

            content = "#EXTM3U\n"
            content += "#EXT-X-VERSION:3\n"
            content += "#EXT-X-TARGETDURATION:" + str(self._target_duration) + "\n"
            content += "#EXT-X-MEDIA-SEQUENCE:" + str(first_seq) + "\n"

            # A PCR discontinuity (e.g. ffmpeg's -reconnect firing mid-stream
            # on a flaky source) splices two unrelated decode timelines
            # together inside what otherwise looks like ordinary real
            # content - unlike the filler boundary this can happen anywhere,
            # repeatedly, throughout playback. Native decoders resync past
            # it silently; MSE players (Shaka et al.) reject the append
            # without the tag. Not meaningful on the very first listed
            # segment - nothing precedes it for a joining client.
            for i, (idx, _seg_path, _created_at, duration, _is_filler, is_discontinuity) in enumerate(active):
                if is_discontinuity and i > 0:
                    content += "#EXT-X-DISCONTINUITY\n"
                content += "#EXTINF:%.3f,\n" % duration
                content += self.segment_uri(idx) + "\n"

            tmp_path = self.playlist + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp_path, self.playlist)
        except Exception as exc:
            self.logger.error("Error updating playlist: " + str(exc))

    def _clean_old_segments(self):
        # RFC 8216 §6.2.2: a segment must stay available for at least
        # playlist_duration + segment_duration after it leaves the
        # playlist.  With 1–2 s segments a slow client that fetched the
        # previous playlist can 404 on its oldest entry.  Keep twice the
        # playlist size to cover the window safely (cheap on tmpfs).
        keep = 2 * self.settings.playlist_size()
        if len(self.segments) > keep:
            for _idx, seg_path, _created_at, _duration, _is_filler, _is_discontinuity in self.segments[:-keep]:
                try:
                    if os.path.exists(seg_path):
                        os.unlink(seg_path)
                except Exception:
                    pass
            self.segments = self.segments[-keep:]

    def cleanup_all(self):
        for _idx, seg_path, _created_at, _duration, _is_filler, _is_discontinuity in self.segments:
            try:
                if os.path.exists(seg_path):
                    os.unlink(seg_path)
            except Exception:
                pass

        self.segments = []

        for path in [self.playlist, self.playlist + ".tmp"]:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except Exception:
                pass

        self.remove_pipe()


class StreamService(object):
    def __init__(self, settings, logger, ensure_hls_dir, reactor, credentials_provider=None):
        self.streams = {}
        self.settings = settings
        self.logger = logger
        self.ensure_hls_dir = ensure_hls_dir
        self.reactor = reactor
        self._credentials_provider = credentials_provider  # None = default "root", ""
        self._cleanup_timer = None

        self._ensure_directories()
        self._start_cleanup_timer()

    def _ensure_directories(self):
        self.ensure_hls_dir(self.settings.hls_dir())

    def _start_cleanup_timer(self):
        self._cleanup_timer = self.reactor.callLater(self.settings.cleanup_interval(), self._cleanup)

    def _cleanup(self):
        try:
            self._cleanup_inactive_streams()
        except Exception as exc:
            self.logger.error("Error in cleanup: " + str(exc))
        finally:
            self._cleanup_timer = self.reactor.callLater(self.settings.cleanup_interval(), self._cleanup)

    def _cleanup_inactive_streams(self):
        now = time.time()
        to_delete = []

        for stream_id, info in list(self.streams.items()):
            process = info.get("process")
            if process and process.poll() is not None:
                self.logger.info("Stream " + stream_id + " FFmpeg exited (code " + str(process.returncode) + "), removing")
                self._stop_stream(stream_id, delete_files=True)
                to_delete.append(stream_id)
                continue

            inactive = now - info["last_accessed"]
            if inactive > self.settings.inactivity_timeout():
                self.logger.info("Stream " + stream_id + " inactive for " + str(int(inactive)) + "s, stopping")
                self._stop_stream(stream_id, delete_files=True)
                to_delete.append(stream_id)

        for stream_id in to_delete:
            self.streams.pop(stream_id, None)

    def _read_e2_credentials(self):
        """Enigma2 basic-auth credentials from the injected provider."""
        if self._credentials_provider is None:
            return "root", ""
        try:
            return self._credentials_provider()
        except Exception:
            return "root", ""

    def generate_stream_id(self, params):
        quality = params.get("quality", "balanced")
        base = params["ref"] + ":" + str(self.settings.stream_port()) + ":" + quality
        if params.get("user"):
            base += ":" + params["user"]
        return hashlib.md5(base.encode()).hexdigest()[:8]

    def get_or_create_stream(self, params):
        quality = params.get("quality", "balanced")
        preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["balanced"])
        hw = (quality == "hw_transcode")

        effective_params = dict(params)
        if hw:
            effective_params["hw"] = True
            e2_user, e2_pass = self._read_e2_credentials()
            effective_params["e2_user"] = e2_user
            effective_params["e2_pass"] = e2_pass

        stream_id = self.generate_stream_id(params)

        if stream_id in self.streams:
            info = self.streams[stream_id]
            info["last_accessed"] = time.time()
            info["access_count"] += 1
            self.logger.log_stream_active(stream_id, info)
            return stream_id, False

        self.logger.log_stream_start(stream_id, effective_params, self.settings.stream_port())
        self._cleanup_stream_files(stream_id)

        hls_dir = self.settings.hls_dir()
        log_dir = os.path.join(hls_dir, "logs")
        self.ensure_hls_dir(hls_dir)

        segmenter = Segmenter(stream_id, self.settings, self.logger,
                              seg_duration=preset["seg_duration"])
        segmenter.create_pipe()
        # Synchronous and cheap (one 36 KB file write): guarantees the
        # playlist exists, and therefore that the caller can redirect
        # immediately with no polling, before this call returns. See
        # write_initial_segment()'s docstring for why polling isn't reliable
        # enough here to do this step asynchronously instead.
        segmenter.write_initial_segment()

        stream_url = build_stream_url(effective_params, self.settings)
        # The relay wins over hardware transcode too (see build_stream_url) -
        # only resolve a real transcode session when the relay didn't
        # already claim this ref.
        hw_ref = params["ref"] if (hw and not uses_stream_relay(params["ref"], self.settings)) else None
        if hw_ref is not None:
            self.logger.info("Stream " + stream_id + " input: hardware-transcode (resolving via OpenWebif)")
        else:
            self.logger.info("Stream " + stream_id + " input: " + mask_credentials(stream_url))

        info = {
            "id": stream_id,
            "params": params,
            "stream_url": stream_url,
            "segmenter": segmenter,
            "started": time.time(),
            "last_accessed": time.time(),
            "access_count": 1,
            "crash_count": 0,
        }
        self.streams[stream_id] = info

        # The lambdas bind this registration's segmenter so a late callback
        # from a previous life of the same stream_id (deterministic MD5) can
        # be recognised as stale and ignored.
        async_start_ffmpeg(
            stream_url,
            segmenter.pipe_path,
            stream_id,
            log_dir,
            self.settings,
            on_exit=self._marshal(
                lambda sid, rc, log: self._on_ffmpeg_exit(sid, rc, log, segmenter)),
            on_ready=self._marshal(
                lambda sid, proc, log: self._on_ffmpeg_spawned(sid, proc, log, segmenter)),
            e2_user=effective_params.get("e2_user"),
            e2_pass=effective_params.get("e2_pass"),
            logger=self.logger,
            hw_ref=hw_ref,
        )

        # The segmenter runs in its own thread and consumes the pipe until
        # ffmpeg exits, continuing the filler cadence (if still waiting) or
        # cutting real segments once data arrives.
        segmenter.start()

        return stream_id, True

    def _marshal(self, fn):
        """Wrap a callback so it runs on the reactor thread.

        The ffmpeg spawn/exit callbacks fire on watcher threads; mutating
        self.streams there races the reactor (cleanup timer, get_status
        iteration). callFromThread serialises them onto the reactor.
        """
        def _on_reactor(*args):
            self.reactor.callFromThread(fn, *args)
        return _on_reactor

    def _stream_info_for(self, stream_id, expected_segmenter):
        """Current stream entry, or None when the callback is stale.

        stream_id is a deterministic hash of the request params, so a
        reaped-and-recreated stream reuses the id; the segmenter identity
        captured at callback registration tells the two lives apart.
        """
        info = self.streams.get(stream_id)
        if info is None:
            return None
        if expected_segmenter is not None and info.get("segmenter") is not expected_segmenter:
            return None
        return info

    def _on_ffmpeg_exit(self, stream_id, retcode, ffmpeg_log, expected_segmenter=None):
        """Runs on the reactor thread (marshalled via _marshal)."""
        self.logger.log_ffmpeg_exit(stream_id, retcode, ffmpeg_log)
        info = self._stream_info_for(stream_id, expected_segmenter)
        if info is None:
            # Stale exit: after the old segmenter's remove_pipe(), the
            # orphan ffmpeg may have re-created its pipe path as a regular
            # file (O_CREAT) and dumped TS into it — remove that residue
            # now instead of leaving it until the next plugin start.
            if expected_segmenter is not None:
                self._remove_residue_pipe(expected_segmenter)
            return
        if info.get("segmenter"):
            # No writer will attach to the FIFO anymore — let the
            # segmenter thread finish instead of polling until cleanup.
            info["segmenter"].notify_writer_exited()
        if retcode != 0 and info.get("process") is not None:
            info["crash_count"] = info.get("crash_count", 0) + 1

    def _remove_residue_pipe(self, segmenter):
        """Delete a stale life's pipe path; per-life names make it garbage."""
        path = segmenter.pipe_path
        try:
            if not path or not os.path.exists(path):
                return
            # The residue is by definition a regular file (ffmpeg's O_CREAT
            # after remove_pipe). An actual FIFO here is never ours to
            # delete — it would mean a live segmenter owns the path.
            if stat.S_ISFIFO(os.stat(path).st_mode):
                return
            os.unlink(path)
            self.logger.debug("Removed residue pipe file " + path)
        except Exception as exc:
            self.logger.warning(
                "Could not remove residue pipe file: " + str(exc))

    def _on_ffmpeg_spawned(self, stream_id, process, ffmpeg_log, expected_segmenter=None):
        """Runs on the reactor thread (marshalled via _marshal)."""
        info = self._stream_info_for(stream_id, expected_segmenter)
        if info is None:
            # Stale registration: the stream this ffmpeg was spawned for is
            # gone, so nobody else will ever terminate the process.
            if process is not None:
                try:
                    process.terminate()
                except Exception:
                    pass
            return
        if info.get("process") is None:
            info["process"] = process
            if process is not None:
                self.logger.info("Stream " + stream_id + " started (mode=copy)")
            else:
                self.logger.error("Failed to start FFmpeg for stream " + stream_id)
                # Clean up — the segmenter is still running but ffmpeg failed.
                self._stop_stream(stream_id, delete_files=True)

    def _stop_stream(self, stream_id, delete_files=False):
        if stream_id not in self.streams:
            return

        info = self.streams[stream_id]

        if info.get("segmenter"):
            info["segmenter"].stop()
            if delete_files:
                # The segmenter may have been started (normal case) or not
                # (early cleanup before it had a chance to start).
                seg = info["segmenter"]
                if seg.is_alive():
                    seg.join(timeout=3)
                seg.cleanup_all()

        if info.get("process"):
            try:
                info["process"].terminate()
            except Exception:
                pass
            try:
                info["process"].wait(timeout=2)
            except Exception:
                try:
                    info["process"].kill()
                except Exception:
                    pass

        if delete_files:
            self._cleanup_stream_files(stream_id)

        self.streams.pop(stream_id, None)
        self.logger.log_stream_stop(stream_id)

    def stop_all(self):
        for stream_id in list(self.streams.keys()):
            self._stop_stream(stream_id, delete_files=True)

    def _cleanup_stream_files(self, stream_id):
        # Use the segmenter's own bookkeeping instead of scanning the
        # directory — avoids a blocking os.listdir on the reactor thread.
        info = self.streams.get(stream_id)
        if info and info.get("segmenter"):
            info["segmenter"].cleanup_all()
        self._remove_ffmpeg_log(stream_id)

    def _remove_ffmpeg_log(self, stream_id):
        # async_start_ffmpeg() names it deterministically from stream_id -
        # not tracked in self.streams, so recompute rather than store it.
        # Without this, every distinct channel ever watched leaves a
        # permanent orphan file in tmpfs (only reused/truncated if the same
        # channel is opened again).
        log_path = os.path.join(self.settings.hls_dir(), "logs", stream_id + "_ffmpeg.log")
        try:
            if os.path.exists(log_path):
                os.unlink(log_path)
        except Exception:
            pass

    def cleanup_old_session_files(self):
        hls_dir = self.settings.hls_dir()
        try:
            if os.path.exists(hls_dir):
                for name in os.listdir(hls_dir):
                    path = os.path.join(hls_dir, name)
                    # .m3u8.tmp: a crash between the playlist tmp-write and
                    # its rename leaves the tmp file behind.
                    if (name.endswith((".ts", ".m3u8", ".m3u8.tmp"))
                            or "_pipe" in name) and os.path.exists(path):
                        os.unlink(path)
                self._cleanup_old_ffmpeg_logs(hls_dir)
                self.logger.info("Cleaned up old session files")
        except Exception as exc:
            self.logger.error("Error cleaning old files: " + str(exc))

    def _cleanup_old_ffmpeg_logs(self, hls_dir):
        # Per-stream ffmpeg logs (<id>_ffmpeg.log) from a prior enigma2/
        # plugin life are otherwise never revisited unless that exact
        # channel gets watched again - leaves orphans in tmpfs indefinitely.
        # plugin.log/plugin.log.1 live in the same directory and must stay.
        log_dir = os.path.join(hls_dir, "logs")
        if not os.path.exists(log_dir):
            return
        for name in os.listdir(log_dir):
            if name.endswith("_ffmpeg.log"):
                try:
                    os.unlink(os.path.join(log_dir, name))
                except Exception:
                    pass

    def update_access(self, stream_id):
        if stream_id in self.streams:
            self.streams[stream_id]["last_accessed"] = time.time()

    def has_real_data(self, stream_id):
        """True once at least one non-filler segment has been cut.

        Lets a caller wait for real content instead of the filler before
        handing a URL to a player that would otherwise have to sit through
        the filler->real #EXT-X-DISCONTINUITY transition itself (native
        players like VLC handle that fine and don't need this; browser
        MSE-based players are visibly slow re-initialising around it, so the
        web player polls this instead of loading the filler at all).
        """
        info = self.streams.get(stream_id)
        if info is None or info.get("segmenter") is None:
            return False
        return any(not seg[4] for seg in info["segmenter"].segments)

    def get_status(self):
        status = {}

        for stream_id, info in self.streams.items():
            # Use the segmenter's internal list instead of os.listdir —
            # avoids a blocking directory scan on the reactor thread.
            seg_count = 0
            if info.get("segmenter"):
                seg_count = len(info["segmenter"].segments)

            status[stream_id] = {
                "id": stream_id,
                "ref": info["params"]["ref"],
                "port": str(self.settings.stream_port()),
                "has_auth": bool(info["params"].get("user")),
                "uptime": int(time.time() - info["started"]),
                "segments": seg_count,
                "access_count": info["access_count"],
                "crash_count": info.get("crash_count", 0),
                "hls_url": "/hls/live_" + stream_id + ".m3u8",
            }

        return status
