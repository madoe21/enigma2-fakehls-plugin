# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import subprocess
import threading
import urllib.parse


def build_stream_url(params, settings):
    ref = params.get("ref", "")
    hw = params.get("hw", False)

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
        "-c",
        "copy",
        "-copyts",
        "-start_at_zero",
        "-f",
        "mpegts",
        "-y",
        output_pipe,
    ]
    return cmd


def start_ffmpeg(stream_url, output_pipe, stream_id, log_dir, settings, on_exit=None, e2_user=None, e2_pass=None):
    ffmpeg_log = os.path.join(log_dir, stream_id + "_ffmpeg.log")
    cmd = build_ffmpeg_cmd(stream_url, output_pipe, settings, e2_user=e2_user, e2_pass=e2_pass)

    try:
        with open(ffmpeg_log, "w", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                cmd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )

        print("[E2HLSServer] FFmpeg started for stream " + stream_id + " PID=" + str(process.pid) + " mode=copy")

        if on_exit:
            def monitor():
                ret = process.wait()
                on_exit(stream_id, ret, ffmpeg_log)

            threading.Thread(target=monitor, daemon=True).start()

        return process
    except Exception as exc:
        print("[E2HLSServer] ERROR starting FFmpeg: " + str(exc))
        return None
