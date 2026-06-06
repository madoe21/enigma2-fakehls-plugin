# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os

from .E2HLSServer.core.ffmpeg_service import (
    build_ffmpeg_cmd,
    build_stream_url,
    start_ffmpeg,
)
from .E2HLSServer.platform.enigma2.config import Enigma2Settings


def buildFFmpegCmd(stream_url, output_pipe, sid, log_dir):
    settings = Enigma2Settings()
    cmd = build_ffmpeg_cmd(stream_url, output_pipe, settings)
    ffmpeg_log = os.path.join(log_dir, sid + "_ffmpeg.log")
    return cmd, ffmpeg_log


def buildStreamUrl(params):
    return build_stream_url(params, Enigma2Settings())


def startFFmpeg(stream_url, output_pipe, sid, log_dir, on_exit=None):
    return start_ffmpeg(stream_url, output_pipe, sid, log_dir, Enigma2Settings(), on_exit=on_exit)
