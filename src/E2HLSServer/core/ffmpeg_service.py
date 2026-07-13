# -*- coding: utf-8 -*-
from __future__ import absolute_import

import base64
import os
import re
import subprocess
import threading
import urllib.parse
import urllib.request


# Must approximate stream_service.py's filler runway (2 filler segments at
# its _FILLER_DURATION each) - see build_ffmpeg_cmd's -output_ts_offset.
# Not imported from there directly: ffmpeg_service <- stream_service already,
# importing back would be circular. Keep the two in sync by hand.
_FILLER_RUNWAY_OFFSET_SECONDS = 4.0


def mask_credentials(url):
    """Log-safe form of a stream URL — embedded credentials stripped."""
    return re.sub(r"//[^/@]+@", "//***@", url)


# Tried PR_SET_PDEATHSIG via preexec_fn here to auto-kill orphaned ffmpeg
# children if enigma2 dies (crash/kill -9/restart mid-stream) - reverted.
# preexec_fn runs in whichever thread called Popen() (our short-lived
# "ffmpeg-<id>" spawn thread), and on this receiver's kernel the deathsig
# fires when THAT THREAD exits, not when the whole process dies. Since the
# spawn thread returns almost immediately after Popen(), every ffmpeg got
# SIGKILLed within milliseconds of starting (measured: Exit Code -9, ~5ms
# after spawn). Needs a different mechanism (e.g. a dedicated long-lived
# thread holding the fork, or process-group + explicit kill on shutdown)
# before revisiting - do not re-add preexec_fn=this without re-verifying
# against a live process, not just re-reading kernel docs.


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
        # OpenWebif asynchronously in async_start_ffmpeg's spawn thread
        # (see resolve_hw_stream_url). Used here just for the "Stream X
        # input: ..." log line before that resolution has happened.
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


def build_ffmpeg_cmd(stream_url, output_pipe, settings, e2_user=None, e2_pass=None):
    cmd = [
        settings.ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
        "-timeout",
        "30000000",
        # 1MB/1s was too small to sync the AC3 audio PID on some channels
        # (multi-track HD services carrying 5.1 + a second-language stereo
        # track) - ffmpeg would then either drop the optional audio map
        # entirely (silent output) or pick it up mid-stream (audio/video
        # start offset that compounds into growing desync). Measured on the
        # receiver: reliably resolves both AC3 tracks' channel count/sample
        # rate from 4s of probe data, never at 3s. The filler-segment system
        # (stream_service.py) already hides ffmpeg startup latency from
        # every client, so this extra probe time costs nothing user-visible.
        "-probesize",
        "5000000",
        "-analyzeduration",
        "5000000",
        "-fflags",
        "nobuffer",
        # Softcam CW-rotation glitches (brief ECM/control-word switch
        # windows) show up here as a short burst of "non-existing PPS
        # referenced" / "no frame!" parser warnings - native decoders
        # (VLC, the TV's own tuner) resync past them without a hiccup,
        # but ffmpeg's stricter default error handling can otherwise let
        # that confusion affect packet framing on the way through, even in
        # copy mode. Ignoring detected errors here makes the demuxer/parser
        # more lenient about exactly this class of transient glitch.
        "-err_detect",
        "ignore_err",
    ]
    if e2_user and e2_pass:
        creds = base64.b64encode((e2_user + ":" + e2_pass).encode()).decode()
        cmd += ["-headers", "Authorization: Basic " + creds + "\r\n"]
    cmd += [
        "-i",
        stream_url,
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        # Video stays copy (no re-encode). Audio must be transcoded: broadcast
        # TV carries AC-3/MP2, which no browser can decode via MSE — hls.js
        # then stalls on the dead audio track (buffered but not playing).
        # AAC stereo plays everywhere, including VLC.
        "-c:v",
        "copy",
        # Re-encoding audio from a decoder that silently drops/duplicates
        # samples on a corrupt AC3 frame (common on this signal - see the
        # PPS warnings in the video log) means the AAC encoder's sample
        # count and the copied video PTS slowly stop agreeing. aresample's
        # async mode inserts/drops samples to keep audio output timestamps
        # tracking the input pts instead of just counting encoded samples,
        # which is what actually stops the drift instead of just reducing
        # its starting offset.
        "-af",
        "aresample=async=1:min_hard_comp=0.100000:first_pts=0",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-b:a",
        "192k",
        "-copyts",
        "-start_at_zero",
        # Real content's clock would otherwise restart at ~0 while the
        # bundled filler (see stream_service.py's Segmenter) already ran the
        # playlist's clock up to ~2x its own duration - a *backwards* PTS
        # jump at the filler->real boundary, on top of the discontinuity
        # itself. Offsetting past the common case (2 filler segments) turns
        # that into a small forward gap instead; #EXT-X-DISCONTINUITY is
        # still the authoritative signal for players that need an exact
        # reset, this just makes the common case less jarring for those that
        # are more lenient about it.
        "-output_ts_offset",
        str(_FILLER_RUNWAY_OFFSET_SECONDS),
        # Live satellite feeds occasionally drop/corrupt AC3 frames; without
        # a buffer big enough to absorb the resulting encoder/muxer stalls,
        # packets get silently dropped instead of just delayed - defends
        # against another source of audio gaps beyond the probe-size fix.
        "-max_muxing_queue_size",
        "4096",
        "-f",
        "mpegts",
        "-y",
        output_pipe,
    ]
    return cmd


def async_start_ffmpeg(stream_url, output_pipe, stream_id, log_dir, settings,
                       on_ready, on_exit=None, e2_user=None, e2_pass=None,
                       logger=None, hw_ref=None):
    """Start ffmpeg in a background thread so the caller doesn't block.

    Invokes ``on_ready(process, ffmpeg_log)`` when ffmpeg is ready (or
    fails), with ``process`` being *None* on failure.  Optionally calls
    ``on_exit(stream_id, retcode, ffmpeg_log)`` when the process terminates.
    Lifecycle messages go to ``logger`` (info/error) when provided.

    If *hw_ref* is given, *stream_url* is only a placeholder for the "input:"
    log line - the real, session-scoped hardware-transcode URL is resolved
    from OpenWebif inside this background thread instead (resolve_hw_stream_
    url makes a real blocking HTTP call, which must never happen on the
    caller's thread - callers run on the reactor thread, which also drives
    enigma2's GUI and the live TV output).
    """
    ffmpeg_log = os.path.join(log_dir, stream_id + "_ffmpeg.log")

    def _spawn():
        nonlocal stream_url, e2_user, e2_pass
        try:
            if hw_ref is not None:
                try:
                    stream_url = resolve_hw_stream_url(
                        hw_ref, settings, e2_user=e2_user, e2_pass=e2_pass)
                except Exception as exc:
                    if logger is not None:
                        logger.error(
                            "Stream " + stream_id
                            + ": could not resolve hardware-transcode URL: " + str(exc))
                    on_ready(stream_id, None, ffmpeg_log)
                    return
                # The resolved URL already embeds its own one-time session
                # credentials (http://-sid:<token>@host:port/<ref>); an
                # explicit Authorization header here would override that
                # and get rejected instead of the valid session token.
                e2_user, e2_pass = None, None

            cmd = build_ffmpeg_cmd(stream_url, output_pipe, settings, e2_user=e2_user, e2_pass=e2_pass)
            with open(ffmpeg_log, "w", encoding="utf-8") as log_handle:
                process = subprocess.Popen(
                    cmd,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
            if logger is not None:
                logger.info("FFmpeg started for stream " + stream_id
                            + " (PID " + str(process.pid)
                            + ", mode=" + ("hw" if hw_ref is not None else "copy") + ")")
            on_ready(stream_id, process, ffmpeg_log)

            if on_exit:
                def monitor():
                    ret = process.wait()
                    on_exit(stream_id, ret, ffmpeg_log)

                threading.Thread(target=monitor, daemon=True, name="ffmpeg-exit-" + stream_id).start()
        except Exception as exc:
            if logger is not None:
                logger.error("Error starting FFmpeg for stream " + stream_id + ": " + str(exc))
            on_ready(stream_id, None, ffmpeg_log)

    threading.Thread(target=_spawn, daemon=True, name="ffmpeg-" + stream_id).start()
