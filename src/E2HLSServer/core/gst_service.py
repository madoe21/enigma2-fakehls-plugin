# -*- coding: utf-8 -*-
"""GStreamer-backed replacement for the old ffmpeg-based remux engine.

Same job as the file this replaces: pull an MPEG-TS input, pass video
through untouched (no re-encode - ever), transcode audio to AAC (browser
MSE needs it; broadcast AC-3/E-AC-3/MP2 does not decode there), and write
the result as MPEG-TS into the same named FIFO Segmenter already reads
from (stream_service.py). Everything downstream of that FIFO - segment
cutting, playlist, hold-back, HTTP delivery - is untouched by this file.

Built from individual elements (Gst.ElementFactory.make + Gst.Pipeline),
not playbin and not Gst.parse_launch: playbin auto-negotiates its own
decode/render path, which fights the "video must never be re-encoded"
rule, and parse_launch is still the gst-launch mini-language rather than
the element API. No subprocess/shell involved anywhere in this module.
"""
from __future__ import absolute_import

import base64
import errno
import itertools
import os
import re
import threading
import time
import urllib.parse
import urllib.request

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst
except (ImportError, ValueError):
    # Off-device (Windows dev machine, CI): GStreamer/PyGObject aren't
    # installed there. The URL-building helpers below don't need them;
    # only pipeline construction/spawn does, and that code path is simply
    # never exercised in that environment (see tests/test_gst_service.py,
    # which mocks Gst/GLib in place for the parts it does cover).
    gi = None
    Gst = None
    GLib = None

_gst_init_lock = threading.Lock()
_gst_initialized = False


def _ensure_gst_initialized():
    """Lazy, one-time Gst.init() - not done at import time so importing
    this module (e.g. for its URL helpers) never has a side effect."""
    global _gst_initialized
    if _gst_initialized:
        return
    with _gst_init_lock:
        if not _gst_initialized:
            # Gst.init(None) raises TypeError on this platform's PyGObject
            # (verified on-device) - it wants an actual list, not None.
            Gst.init([])
            _gst_initialized = True


# Must approximate stream_service.py's filler runway (2 filler segments at
# its _FILLER_DURATION each) - see _apply_filler_runway_offset below.
# Not imported from there directly: gst_service <- stream_service already,
# importing back would be circular. Keep the two in sync by hand.
_FILLER_RUNWAY_OFFSET_SECONDS = 4.0

# Per-life FIFO write retry: opening a FIFO for writing blocks until a
# reader has it open (see async_start_gst's docstring) - bounded so a
# stream whose segmenter never starts (should not happen, but never
# block a thread forever on a bug elsewhere) doesn't leak a thread.
_FIFO_OPEN_TIMEOUT_SECONDS = 15

# HTTP source hardening, mirrors the old ffmpeg flags:
# -timeout 30000000 (30s) and generous probe/queue slack.
_HTTP_SOURCE_TIMEOUT_SECONDS = 30
_QUEUE_MAX_SIZE_TIME_NS = 4 * Gst.SECOND if Gst is not None else 0
_AUDIO_BITRATE_BPS = 192000

# souphttpsrc has no built-in equivalent of ffmpeg's -reconnect/
# -reconnect_streamed. Confirmed necessary on real hardware (not just a
# defensive guess): this receiver's internal streaming server
# (port 8001) can answer the very first connection to a given service
# with a bogus ~28-byte "HTTP/1.0 400 Bad Request" body while the tuner
# is still locking, then close - reproduced with plain ffmpeg (no
# -reconnect flags) and with curl, both got the exact same truncated
# response; only ffmpeg's -reconnect_streamed made it retry past it.
# _PipelineRunner.run() replicates that: reconnect with backoff up to
# this cap, matching -reconnect_delay_max 5, until real data has flowed
# at least once - after that a failure is treated as genuine (tuner
# lost, network gone) and reported instead of retried forever silently.
_RECONNECT_INITIAL_DELAY_SECONDS = 0.5
_RECONNECT_MAX_DELAY_SECONDS = 5.0

# Confirmed on-device that the bogus-response failure above does not
# always reach the bus as an ERROR or EOS message: tsdemux can be handed
# a source that already hit EOS after ~28 garbage bytes and simply never
# post anything - no ERROR, no EOS, forever. Bus-driven reconnect alone
# can't detect that, so each attempt also gets a plain wall-clock
# watchdog: if no real data has shown up in this many seconds, the
# attempt is torn down and retried exactly like a bus-reported failure.
_ATTEMPT_NO_DATA_TIMEOUT_SECONDS = 8


def mask_credentials(url):
    """Log-safe form of a stream URL — embedded credentials stripped."""
    return re.sub(r"//[^/@]+@", "//***@", url)


def _streamrelay_url(ref, settings):
    """Relay URL when the receiver routes this service through the softcam
    stream relay; None otherwise. Pulling a whitelisted (ICAM) service from
    the plain stream port yields a scrambled TS, so the relay wins over
    both the stream port and the HW transcode port."""
    whitelist_fn = getattr(settings, "streamrelay_whitelist", None)
    port_fn = getattr(settings, "streamrelay_port", None)
    if whitelist_fn is None or port_fn is None:  # platform without relay support
        return None
    if not whitelist_fn().contains(ref):
        return None
    return "http://127.0.0.1:" + str(port_fn()) + "/" + ref


def uses_stream_relay(ref, settings):
    """True if this ref is routed through the softcam stream relay (see
    _streamrelay_url) - the relay takes priority over hardware transcode,
    same as it does over the plain stream port."""
    return _streamrelay_url(ref, settings) is not None


def resolve_hw_stream_url(ref, settings, e2_user=None, e2_pass=None, timeout=3):
    """Ask OpenWebif for a session-scoped hardware-transcode stream URL.

    The hw port (settings.stream_hw_port(), conventionally 8002) does not
    accept static bitrate/width/height query params directly - that is a
    legacy scheme from older enigma2/OpenWebif versions. The current
    mechanism issues a short-lived session token embedded as pseudo Basic
    Auth credentials (``http://-sid:<token>@host:port/<ref>``) that must be
    requested per-stream first via OpenWebif's own streamm3u endpoint;
    box-wide bitrate/resolution/aspect ratio come from the box's own
    Transcoding Setup config, not from us. Raises on failure (OpenWebif not
    installed/reachable, or no hardware transcoder present on this box).

    Blocking (real HTTP call) - must only run off the reactor thread.
    """
    request_url = ("http://127.0.0.1/web/streamm3u?device=phone&ref="
                    + urllib.parse.quote(ref, safe=""))
    request = urllib.request.Request(request_url)
    if e2_user and e2_pass:
        creds = base64.b64encode((e2_user + ":" + e2_pass).encode()).decode()
        request.add_header("Authorization", "Basic " + creds)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="ignore")
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            return line
    raise RuntimeError(
        "OpenWebif did not return a transcode stream URL for " + ref
        + " (OpenWebif missing, or no hardware transcoder on this box?)")


def build_stream_url(params, settings):
    ref = params.get("ref", "")
    hw = params.get("hw", False)

    relay_url = _streamrelay_url(ref, settings)
    if relay_url:
        return relay_url

    if hw:
        # Placeholder only - the real session-scoped URL is resolved from
        # OpenWebif asynchronously in async_start_gst's spawn thread (see
        # resolve_hw_stream_url). Used here just for the "Stream X input:
        # ..." log line before that resolution has happened.
        port = str(settings.stream_hw_port())
        return "http://127.0.0.1:" + port + "/" + ref

    port = str(settings.stream_port())
    user = params.get("user")
    password = params.get("password")

    if user and password:
        encoded_user = urllib.parse.quote(user, safe="")
        encoded_password = urllib.parse.quote(password, safe="&$!'()*+,;=-._~")
        return "http://" + encoded_user + ":" + encoded_password + "@127.0.0.1:" + port + "/" + ref

    return "http://127.0.0.1:" + port + "/" + ref


# Per-life pid counter: GstPipelineHandle has no real OS pid (it's an
# in-process pipeline, not a subprocess) - this is only for correlating
# log lines, same role ffmpeg's real PID played.
_PIPELINE_ID_SEQUENCE = itertools.count(1)


class GstPipelineHandle(object):
    """subprocess.Popen-compatible facade around a _PipelineRunner.

    stream_service.py treats whatever async_start_gst hands it exactly
    like the old subprocess.Popen from ffmpeg: .poll() (cleanup timer),
    .terminate()/.wait(timeout)/.kill() (_stop_stream), .pid (log line
    only). Keeping that contract means stream_service.py needs zero
    changes beyond the one import line - see the migration plan.

    Wraps the runner rather than a single Gst.Pipeline directly because
    the runner replaces its pipeline object on every reconnect attempt
    (see _PipelineRunner.run) - a handle created once at stream start
    must still reach whichever pipeline is current.
    """

    def __init__(self, runner):
        self._runner = runner
        self.pid = next(_PIPELINE_ID_SEQUENCE)
        self.returncode = None
        self._finished = threading.Event()

    @property
    def pipeline(self):
        return self._runner.pipeline

    def _mark_finished(self, returncode):
        # Only ever called once, from _PipelineRunner.run()'s single exit
        # path (see request_stop() below - it asks run()'s loop to stop
        # rather than marking finished itself, so there's exactly one
        # writer).
        if self.returncode is None:
            self.returncode = returncode
        self._finished.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._finished.wait(timeout):
            raise TimeoutError("GStreamer pipeline did not exit in time")
        return self.returncode

    def terminate(self):
        # Straight to NULL, no EOS drain, and stop reconnecting: a channel
        # switch needs the old pipeline gone quickly (mirrors ffmpeg's
        # SIGTERM, which is also a fast stop, not a graceful flush).
        self._runner.request_stop()

    def kill(self):
        self.terminate()


def _branch_kind_for_structure_name(name):
    """Which mux branch a newly-appeared tsdemux pad belongs to, from its
    caps structure name alone - kept separate from the real pad-added
    callback so it's testable without a live GStreamer pipeline.

    Returns "video" or "audio" (never distinguishes video codec here -
    the caller still needs the caps to pick h264parse vs h265parse) or
    None for anything this pipeline doesn't carry (data/subtitle PIDs).
    """
    if name is None:
        return None
    if name.startswith("video/"):
        return "video"
    if name.startswith("audio/"):
        return "audio"
    return None


class _PipelineRunner(object):
    """Owns one stream's Gst.Pipeline: builds it, drives it on a private
    GLib.MainLoop thread, and reports readiness/exit through the same
    on_ready/on_exit callbacks async_start_gst was given.
    """

    def __init__(self, stream_url, output_pipe, stream_id, log_path,
                 e2_user, e2_pass, logger, on_exit):
        self._stream_url = stream_url
        self._output_pipe = output_pipe
        self._stream_id = stream_id
        self._log_path = log_path
        self._e2_user = e2_user
        self._e2_pass = e2_pass
        self._logger = logger
        self._on_exit = on_exit
        self._pipeline = None
        self._loop = None
        self._log_handle = None
        self._pipe_fd = None
        # ffmpeg's old command mapped only the first video/audio stream
        # (-map 0:v:0? -map 0:a:0?) - match that here, or a multi-language
        # HD service would get every audio track muxed in instead of one.
        # Reset at the top of every reconnect attempt (see run()).
        self._video_linked = False
        self._audio_linked = False
        # Set once real demuxed data has actually flowed (see
        # _on_demux_pad_added) - distinguishes a genuine failure from a
        # startup-race failure that should just be retried. See run().
        self._data_seen = False
        self._attempt_returncode = 0
        self._stop_requested = threading.Event()

    @property
    def pipeline(self):
        return self._pipeline

    def request_stop(self):
        """Ask run()'s reconnect loop to stop and tear down now, instead
        of retrying - called from GstPipelineHandle.terminate()/kill(),
        on whatever thread the caller is on (stream_service.py's reactor
        thread). GLib.MainLoop.quit() and Gst element state changes are
        both safe to call cross-thread."""
        self._stop_requested.set()
        if self._loop is not None and self._loop.is_running():
            self._loop.quit()
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)

    def _log(self, line):
        if self._log_handle is None:
            try:
                self._log_handle = open(self._log_path, "w", encoding="utf-8")
            except Exception:
                return
        try:
            self._log_handle.write(line + "\n")
            self._log_handle.flush()
        except Exception:
            pass

    def _open_output_fd(self):
        # A FIFO's write end blocks until a reader has it open - expected
        # here, since Segmenter opens its (non-blocking) read end
        # independently and very shortly after this call starts (see
        # stream_service.py's get_or_create_stream). Bounded with a
        # poll/retry (O_NONBLOCK open raises ENXIO instead of blocking
        # while no reader exists yet) so a bug elsewhere that never starts
        # the segmenter can't leak this thread forever.
        deadline = time.monotonic() + _FIFO_OPEN_TIMEOUT_SECONDS
        while True:
            try:
                fd = os.open(self._output_pipe, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as exc:
                if exc.errno != errno.ENXIO or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        # Back to blocking mode once a reader is attached: fdsink expects
        # a normal blocking fd for its own write() calls, not EAGAIN.
        import fcntl
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        self._pipe_fd = fd

    _REQUIRED_ELEMENTS = ("souphttpsrc", "queue", "tsdemux", "mpegtsmux",
                          "fdsink", "h264parse", "h265parse", "decodebin",
                          "audioconvert", "audioresample", "avenc_aac",
                          "aacparse", "identity")

    def prepare(self):
        """One-time setup that must succeed before on_ready fires: every
        element this pipeline could need actually exists on this system.
        Failing fast here (once) beats discovering a missing element only
        after the reconnect loop in run() has already started retrying a
        connection that could never succeed anyway."""
        _ensure_gst_initialized()
        missing = [name for name in self._REQUIRED_ELEMENTS
                   if Gst.ElementFactory.find(name) is None]
        if missing:
            raise RuntimeError("required GStreamer element(s) missing: " + ", ".join(missing))

    def _build_pipeline(self):
        pipeline = Gst.Pipeline.new("stream-" + self._stream_id)
        source = Gst.ElementFactory.make("souphttpsrc", "source")
        source.set_property("location", self._stream_url)
        source.set_property("timeout", _HTTP_SOURCE_TIMEOUT_SECONDS)
        source.set_property("is-live", True)
        if self._e2_user and self._e2_pass:
            creds = base64.b64encode(
                (self._e2_user + ":" + self._e2_pass).encode()).decode()
            headers = Gst.Structure.new_empty("extra-headers")
            headers.set_value("Authorization", "Basic " + creds)
            source.set_property("extra-headers", headers)

        pre_queue = self._make_queue("pre-demux-queue")
        demux = Gst.ElementFactory.make("tsdemux", "demux")

        mux = Gst.ElementFactory.make("mpegtsmux", "mux")
        sink_queue = self._make_queue("sink-queue")
        sink = Gst.ElementFactory.make("fdsink", "sink")
        sink.set_property("fd", self._pipe_fd)
        sink.set_property("sync", False)

        for element in (source, pre_queue, demux, mux, sink_queue, sink):
            if element is None:
                raise RuntimeError("required GStreamer element unavailable on this system")
            pipeline.add(element)

        if not source.link(pre_queue) or not pre_queue.link(demux):
            raise RuntimeError("failed to link source -> demux")
        if not mux.link(sink_queue) or not sink_queue.link(sink):
            raise RuntimeError("failed to link mux -> sink")

        demux.connect("pad-added", self._on_demux_pad_added, mux)

        self._pipeline = pipeline
        return pipeline

    def _make_queue(self, name):
        queue = Gst.ElementFactory.make("queue", name)
        # Generous time-based slack, same intent as ffmpeg's
        # -max_muxing_queue_size 4096: absorb a brief encoder/mux stall
        # instead of dropping data.
        queue.set_property("max-size-time", _QUEUE_MAX_SIZE_TIME_NS)
        queue.set_property("max-size-buffers", 0)
        queue.set_property("max-size-bytes", 0)
        return queue

    def _on_demux_pad_added(self, _demux, pad, mux):
        caps = pad.get_current_caps()
        if caps is None or caps.get_size() == 0:
            return
        structure = caps.get_structure(0)
        name = structure.get_name()
        kind = _branch_kind_for_structure_name(name)
        if kind is None:
            return  # data/subtitle PID - not carried through
        if kind == "video" and self._video_linked:
            return  # only the first video track, matching -map 0:v:0?
        if kind == "audio" and self._audio_linked:
            return  # only the first audio track, matching -map 0:a:0?

        try:
            if kind == "video":
                self._link_video_branch(pad, name, mux)
                self._video_linked = True
            else:
                self._link_audio_branch(pad, mux)
                self._audio_linked = True
            # tsdemux only reaches pad-added once it has actually found
            # PAT/PMT and demuxed a real elementary stream - a solid
            # signal that this attempt is past the startup-race failure
            # class (see run()'s reconnect-vs-report decision).
            self._data_seen = True
        except Exception as exc:
            self._log("branch link failed for pad " + pad.get_name()
                       + " (" + name + "): " + str(exc))

    @staticmethod
    def _link_pads(src_pad, sink_pad, description):
        result = src_pad.link(sink_pad)
        if result != Gst.PadLinkReturn.OK:
            raise RuntimeError(
                "pad link failed (" + description + "): " + str(result))

    def _link_video_branch(self, pad, caps_name, mux):
        # Video is never re-encoded - parse only, straight through to the
        # muxer. Codec-specific parser: h264parse/h265parse each only
        # understand their own bitstream.
        parser_name = "h265parse" if "x-h265" in caps_name else "h264parse"
        queue = self._make_queue("video-queue")
        parser = Gst.ElementFactory.make(parser_name, "video-parse")
        offset = self._make_ts_offset("video-ts-offset")
        for element in (queue, parser, offset):
            self._pipeline.add(element)
            element.sync_state_with_parent()
        self._link_pads(pad, queue.get_static_pad("sink"), "demux video -> queue")
        queue.link(parser)
        parser.link(offset)
        self._link_pads(offset.get_static_pad("src"), mux.request_pad_simple("sink_%d"),
                         "video offset -> mux")

    def _link_audio_branch(self, pad, mux):
        # decodebin covers whatever broadcast audio shows up (AC-3,
        # E-AC-3, MPEG audio, already-AAC) without hand-picking a decoder
        # per format; only the encode side is fixed (AAC, for browser MSE
        # compatibility - see build_stream_url's docstring history).
        queue = self._make_queue("audio-in-queue")
        decode = Gst.ElementFactory.make("decodebin", "audio-decode")
        convert = Gst.ElementFactory.make("audioconvert", "audio-convert")
        resample = Gst.ElementFactory.make("audioresample", "audio-resample")
        encode = Gst.ElementFactory.make("avenc_aac", "audio-encode")
        encode.set_property("bitrate", _AUDIO_BITRATE_BPS)
        parse = Gst.ElementFactory.make("aacparse", "audio-parse")
        offset = self._make_ts_offset("audio-ts-offset")

        chain = (queue, decode, convert, resample, encode, parse, offset)
        for element in chain:
            self._pipeline.add(element)
            element.sync_state_with_parent()
        self._link_pads(pad, queue.get_static_pad("sink"), "demux audio -> queue")
        queue.link(decode)
        # decodebin's own output pad only exists once it has typefound the
        # elementary stream - link the fixed tail once that pad appears.
        decode.connect("pad-added", self._on_decodebin_pad_added, convert)
        convert.link(resample)
        resample.link(encode)
        encode.link(parse)
        parse.link(offset)
        self._link_pads(offset.get_static_pad("src"), mux.request_pad_simple("sink_%d"),
                         "audio offset -> mux")

    def _on_decodebin_pad_added(self, _decodebin, pad, convert):
        # Fires asynchronously (once decodebin's internal typefind
        # resolves), outside _on_demux_pad_added's try/except - a link
        # failure here must not escape into GLib's callback dispatch.
        try:
            self._link_pads(pad, convert.get_static_pad("sink"), "decodebin -> audioconvert")
        except Exception as exc:
            self._log("decodebin pad link failed: " + str(exc))

    def _make_ts_offset(self, name):
        # Mirrors ffmpeg's -output_ts_offset: shifts real content's
        # timeline forward past the filler runway stream_service.py's
        # Segmenter already plays while ffmpeg/gst starts up, so the
        # filler->real cut is a small forward gap instead of a backwards
        # PTS jump. #EXT-X-DISCONTINUITY is still the authoritative signal
        # for players that need an exact reset either way.
        offset = Gst.ElementFactory.make("identity", name)
        offset.set_property("ts-offset", int(_FILLER_RUNWAY_OFFSET_SECONDS * Gst.SECOND))
        return offset

    def run(self):
        """Blocking: drives the pipeline, reconnecting with backoff on
        early failures (see _RECONNECT_* constants) until told to stop, or
        until a failure happens after real data had already flowed at
        least once (a genuine failure, not a startup race - reported
        instead of retried forever). Call on a dedicated thread - see
        async_start_gst."""
        delay = _RECONNECT_INITIAL_DELAY_SECONDS
        final_returncode = 0
        while not self._stop_requested.is_set():
            self._data_seen = False
            self._video_linked = False
            self._audio_linked = False
            self._attempt_returncode = 0
            try:
                self._run_one_attempt()
            except Exception as exc:
                self._log("pipeline attempt failed to start: " + str(exc))
                self._attempt_returncode = 1

            if self._stop_requested.is_set():
                final_returncode = 0
                break
            if self._data_seen:
                final_returncode = self._attempt_returncode
                break
            self._log("no data before failure - reconnecting in %.1fs" % delay)
            # Event.wait(), not time.sleep(): a stop request landing mid-
            # backoff (e.g. a fast channel switch while a previous attempt
            # is still waiting to retry) must interrupt the wait immediately
            # instead of leaving the old pipeline's thread - and the tuner
            # it's holding - unreachable for up to _RECONNECT_MAX_DELAY_SECONDS.
            self._stop_requested.wait(delay)
            delay = min(delay * 2, _RECONNECT_MAX_DELAY_SECONDS)

        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
        if self._on_exit:
            self._on_exit(self._stream_id, final_returncode, self._log_path)

    def _run_one_attempt(self):
        """Build, run and tear down a single connection attempt."""
        self._open_output_fd()
        try:
            self._pipeline = self._build_pipeline()
            bus = self._pipeline.get_bus()
            self._loop = GLib.MainLoop()
            bus.add_signal_watch()
            handler_id = bus.connect("message", self._on_bus_message)
            if self._stop_requested.is_set():
                # request_stop() landed in the narrow window between
                # prepare() and here - do not start a doomed attempt.
                self._pipeline.set_state(Gst.State.NULL)
                return
            watchdog_id = GLib.timeout_add_seconds(
                _ATTEMPT_NO_DATA_TIMEOUT_SECONDS, self._on_no_data_watchdog)
            self._pipeline.set_state(Gst.State.PLAYING)
            try:
                self._loop.run()
            finally:
                try:
                    GLib.source_remove(watchdog_id)
                except Exception:
                    pass  # already fired (one-shot) or otherwise gone
                bus.remove_signal_watch()
                bus.disconnect(handler_id)
                self._pipeline.set_state(Gst.State.NULL)
        finally:
            try:
                os.close(self._pipe_fd)
            except Exception:
                pass

    def _on_no_data_watchdog(self):
        if not self._data_seen:
            self._log("no data within %ds - treating this attempt as failed"
                       % _ATTEMPT_NO_DATA_TIMEOUT_SECONDS)
            self._attempt_returncode = 1
            self._quit_loop()
        return False  # GLib.SOURCE_REMOVE - one-shot, not repeating

    def _on_bus_message(self, _bus, message):
        mtype = message.type
        if mtype == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            self._log("ERROR from " + message.src.get_name() + ": " + str(error)
                       + (" (" + debug + ")" if debug else ""))
            self._attempt_returncode = 1
            self._quit_loop()
        elif mtype == Gst.MessageType.EOS:
            self._log("EOS")
            self._attempt_returncode = 0
            self._quit_loop()
        elif mtype == Gst.MessageType.WARNING:
            warning, debug = message.parse_warning()
            self._log("WARNING from " + message.src.get_name() + ": " + str(warning)
                       + (" (" + debug + ")" if debug else ""))

    def _quit_loop(self):
        if self._loop is not None and self._loop.is_running():
            self._loop.quit()


def async_start_gst(stream_url, output_pipe, stream_id, log_dir, settings,
                     on_ready, on_exit=None, e2_user=None, e2_pass=None,
                     logger=None, hw_ref=None):
    """Start a GStreamer pipeline in a background thread; same calling
    contract as the old async_start_ffmpeg (see the migration plan for
    why): invokes on_ready(stream_id, handle, log_path) once the pipeline
    is up (or failed to start, with handle=None), and on_exit(stream_id,
    returncode, log_path) once it stops. handle behaves like
    subprocess.Popen - see GstPipelineHandle.

    hw_ref resolution (OpenWebif session URL) is unchanged from the
    ffmpeg version: still a blocking HTTP call made in this background
    thread, never on the caller's (reactor) thread.

    The log file keeps the "*_ffmpeg.log" name stream_service.py's
    cleanup (_remove_ffmpeg_log) already looks for, even though nothing
    here spawns ffmpeg anymore - avoids a second stream_service.py touch
    point for a cosmetic filename.
    """
    log_path = os.path.join(log_dir, stream_id + "_ffmpeg.log")

    def _spawn():
        nonlocal stream_url, e2_user, e2_pass
        if hw_ref is not None:
            try:
                stream_url = resolve_hw_stream_url(
                    hw_ref, settings, e2_user=e2_user, e2_pass=e2_pass)
            except Exception as exc:
                if logger is not None:
                    logger.error(
                        "Stream " + stream_id
                        + ": could not resolve hardware-transcode URL: " + str(exc))
                on_ready(stream_id, None, log_path)
                return
            # The resolved URL already embeds its own one-time session
            # credentials; an explicit Authorization header here would
            # override that and get rejected instead of the valid token.
            e2_user, e2_pass = None, None

        # on_exit is wired in below (after the handle exists, so the
        # wrapper can mark it finished) rather than passed in here.
        runner = _PipelineRunner(
            stream_url, output_pipe, stream_id, log_path, e2_user, e2_pass,
            logger, on_exit=None)
        try:
            runner.prepare()
        except Exception as exc:
            if logger is not None:
                logger.error("Error starting GStreamer pipeline for stream "
                             + stream_id + ": " + str(exc))
            on_ready(stream_id, None, log_path)
            return

        handle = GstPipelineHandle(runner)
        if logger is not None:
            logger.info("GStreamer pipeline started for stream " + stream_id
                        + " (id " + str(handle.pid)
                        + ", mode=" + ("hw" if hw_ref is not None else "copy") + ")")
        on_ready(stream_id, handle, log_path)

        def _report_exit(sid, returncode, log):
            handle._mark_finished(returncode)
            if on_exit:
                on_exit(sid, returncode, log)
        runner._on_exit = _report_exit

        runner.run()  # blocks this thread; reconnects internally, see run()'s docstring

    threading.Thread(target=_spawn, daemon=True, name="gst-" + stream_id).start()
