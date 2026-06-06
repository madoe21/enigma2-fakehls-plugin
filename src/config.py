# -*- coding: utf-8 -*-
from __future__ import absolute_import

from .E2HLSServer.platform.enigma2.config import (
    DEFAULT_PLAYLIST_SIZE as PLAYLIST_SIZE,
    DEFAULT_SEGMENT_DURATION as SEGMENT_DURATION,
    DEFAULT_SEGMENT_MAX_AGE as SEGMENT_MAX_AGE,
    HTML_TEMPLATE_FILE,
    PLUGIN_DESCRIPTION,
    PLUGIN_ICON,
    PLUGIN_NAME,
    PLUGIN_PATH,
    PLUGIN_VERSION,
    _,
    config,
    ensure_hls_dir,
    get_local_ip,
    localeInit,
)

FFMPEG_BIN = "/usr/bin/ffmpeg"


def ensureHlsDir(hls_dir=None):
    return ensure_hls_dir(hls_dir)


def getLocalIP():
    return get_local_ip()
