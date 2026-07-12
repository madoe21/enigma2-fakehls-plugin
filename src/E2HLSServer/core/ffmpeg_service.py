# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import re
import subprocess
import threading
import urllib.parse


# Must approximate stream_service.py's filler runway (2 filler segments at
# its _FILLER_DURATION each) - see build_ffmpeg_cmd's -output_ts_offset.
# Not imported from there directly: ffmpeg_service <- stream_service already,
# importing back would be circular. Keep the two in sync by hand.
_FILLER_RUNWAY_OFFSET_SECONDS = 4.0


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


def build_stream_url(params, settings):
    ref = params.get("ref", "")
    hw = params.get("hw", False)

    relay_url = _streamrelay_url(ref, settings)
    if relay_url:
        return relay_url

    if hw:
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
    import base64
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
        # Copy mode only needs PAT/PMT + codec ids; the 5 MB default probe
        # costs several seconds of startup on an HD transport stream.
        "-probesize",
        "1000000",
        "-analyzeduration",
        "1000000",
        "-fflags",
        "nobuffer",
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
        "-f",
        "mpegts",
        "-y",
        output_pipe,
    ]
    return cmd


def async_start_ffmpeg(stream_url, output_pipe, stream_id, log_dir, settings,
                       on_ready, on_exit=None, e2_user=None, e2_pass=None,
                       logger=None):
    """Start ffmpeg in a background thread so the caller doesn't block.

    Invokes ``on_ready(process, ffmpeg_log)`` when ffmpeg is ready (or
    fails), with ``process`` being *None* on failure.  Optionally calls
    ``on_exit(stream_id, retcode, ffmpeg_log)`` when the process terminates.
    Lifecycle messages go to ``logger`` (info/error) when provided.
    """
    ffmpeg_log = os.path.join(log_dir, stream_id + "_ffmpeg.log")
    cmd = build_ffmpeg_cmd(stream_url, output_pipe, settings, e2_user=e2_user, e2_pass=e2_pass)

    def _spawn():
        try:
            with open(ffmpeg_log, "w", encoding="utf-8") as log_handle:
                process = subprocess.Popen(
                    cmd,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
            if logger is not None:
                logger.info("FFmpeg started for stream " + stream_id
                            + " (PID " + str(process.pid) + ", mode=copy)")
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
