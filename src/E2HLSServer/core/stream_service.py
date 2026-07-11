# -*- coding: utf-8 -*-
from __future__ import absolute_import

import hashlib
import os
import select
import threading
import time

from .ffmpeg_service import async_start_ffmpeg, build_stream_url, mask_credentials
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
        return [(data, duration)]

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
            duration = self._segment_duration_from_pcr(self._previous_cut_pcr, cut_pcr, elapsed)
            segment = self._extract_segment(cut)
            self._scan_pos = TS_PACKET_SIZE
            self._previous_cut_pcr = cut_pcr
            self._segment_start = now
            return [(segment, duration)]

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
                return [(segment, elapsed)]
        return []

    def _segment_duration_from_pcr(self, previous_pcr, cut_pcr, wall_elapsed):
        """Media-time duration between two cuts; wall clock when PCR is unusable."""
        if previous_pcr is None or cut_pcr is None:
            return wall_elapsed
        duration = pcr_delta_seconds(previous_pcr, cut_pcr)
        # A PCR discontinuity (channel switch upstream, encoder restart) can
        # yield an absurd span; the wall clock is the safer estimate then.
        if not 0.2 <= duration <= self._seg_duration * 4:
            return wall_elapsed
        return duration


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
        self.segment_index = 0
        self.segments = []
        # RFC 8216 §6.2.1: EXT-X-TARGETDURATION must not change between
        # reloads — keep a never-decreasing value instead of the window max.
        self._target_duration = int(self._seg_duration) + 1

        self.hls_dir = settings.hls_dir()
        self.pipe_path = os.path.join(self.hls_dir, stream_id + "_pipe")
        self.playlist = os.path.join(self.hls_dir, "live_" + stream_id + ".m3u8")

    def stop(self):
        self._stop_event.set()

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
        while not self.stopped():
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

        try:
            while not self.stopped():
                try:
                    chunk = os.read(pipe_fd, self.PIPE_READ_SIZE)
                except BlockingIOError:
                    # Writer attached but no data right now — wait, bounded
                    # so stop() is honoured within a second.
                    select.select([pipe_fd], [], [], 1.0)
                    continue
                except OSError as exc:
                    self.logger.error("Segmenter: pipe read error: " + str(exc), exc_info=True)
                    break
                if not chunk:
                    # EOF on a FIFO means "no writer": before the first byte
                    # ffmpeg simply has not opened its end yet; afterwards
                    # it exited.
                    if got_data:
                        self.logger.warning("Segmenter: pipe closed (FFmpeg ended)")
                        break
                    time.sleep(0.1)
                    continue
                got_data = True
                for data, duration in cutter.feed(chunk):
                    self._write_segment(data, duration)
        finally:
            try:
                os.close(pipe_fd)
            except Exception:
                pass

            if not self.stopped():
                for data, duration in cutter.flush():
                    self._write_segment(data, duration)

    def _write_segment(self, data, duration):
        if len(data) < 8 * 1024:
            return

        seg_path = self.segment_path(self.segment_index)
        created_at = time.time()

        try:
            with open(seg_path, "wb") as handle:
                handle.write(data)

            self.segments.append((self.segment_index, seg_path, created_at, duration))
            self.segment_index += 1
            self._update_playlist()
            # Once per segment is enough; running this per 64 KB chunk was
            # pure overhead in the hot pipe-read loop.
            self._clean_old_segments()
        except Exception as exc:
            self.logger.error("Error writing segment " + str(self.segment_index) + ": " + str(exc))

    def _update_playlist(self):
        try:
            active = self.segments[-self.settings.playlist_size():]
            first_seq = active[0][0]
            # Players schedule fetches from EXTINF; report measured durations,
            # not the nominal target, or the live edge drifts and stutters.
            max_duration = max(seg[3] for seg in active)
            self._target_duration = max(self._target_duration, int(max_duration) + 1)

            content = "#EXTM3U\n"
            content += "#EXT-X-VERSION:3\n"
            content += "#EXT-X-TARGETDURATION:" + str(self._target_duration) + "\n"
            content += "#EXT-X-MEDIA-SEQUENCE:" + str(first_seq) + "\n"

            for idx, _seg_path, _created_at, duration in active:
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
            for _idx, seg_path, _created_at, _duration in self.segments[:-keep]:
                try:
                    if os.path.exists(seg_path):
                        os.unlink(seg_path)
                except Exception:
                    pass
            self.segments = self.segments[-keep:]

    def cleanup_all(self):
        for _idx, seg_path, _created_at, _duration in self.segments:
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

        stream_url = build_stream_url(effective_params, self.settings)
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

        async_start_ffmpeg(
            stream_url,
            segmenter.pipe_path,
            stream_id,
            log_dir,
            self.settings,
            on_exit=self._on_ffmpeg_exit,
            on_ready=self._on_ffmpeg_spawned,
            e2_user=effective_params.get("e2_user"),
            e2_pass=effective_params.get("e2_pass"),
        )

        # The segmenter runs in its own thread and consumes the pipe until
        # ffmpeg exits.  It must be started *before* returning so the
        # playlist file exists when the first client polls for it.
        segmenter.start()

        return stream_id, True

    def _on_ffmpeg_exit(self, stream_id, retcode, ffmpeg_log):
        self.logger.log_ffmpeg_exit(stream_id, retcode, ffmpeg_log)
        info = self.streams.get(stream_id)
        if info is not None and retcode != 0 and info.get("process") is not None:
            info["crash_count"] = info.get("crash_count", 0) + 1

    def _on_ffmpeg_spawned(self, stream_id, process, ffmpeg_log):
        """Called from the ffmpeg background thread when the process is ready."""
        info = self.streams.get(stream_id)
        if info is not None and info.get("process") is None:
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

    def cleanup_old_session_files(self):
        hls_dir = self.settings.hls_dir()
        try:
            if os.path.exists(hls_dir):
                for name in os.listdir(hls_dir):
                    path = os.path.join(hls_dir, name)
                    if (name.endswith(".ts") or name.endswith(".m3u8") or name.endswith("_pipe")) and os.path.exists(path):
                        os.unlink(path)
                self.logger.info("Cleaned up old session files")
        except Exception as exc:
            self.logger.error("Error cleaning old files: " + str(exc))

    def update_access(self, stream_id):
        if stream_id in self.streams:
            self.streams[stream_id]["last_accessed"] = time.time()

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
