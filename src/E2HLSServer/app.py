# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os

from twisted.internet import reactor

from .core.stream_service import StreamService
from .logger import PluginLogger
from .platform.enigma2.config import (
    Enigma2Settings,
    ensure_hls_dir,
    get_local_ip,
    read_e2_credentials,
)
from .platform.enigma2.http_server import HlsHttpServer


class AppContext(object):
    def __init__(self):
        self.settings = Enigma2Settings()
        ensure_hls_dir(self.settings.hls_dir())

        self.logger = PluginLogger(
            name="E2HLSServer",
            log_dir=os.path.join(self.settings.hls_dir(), "logs"),
            debug_mode=self.settings.debug_enabled(),
        )

        self.stream_service = StreamService(
            settings=self.settings,
            logger=self.logger,
            ensure_hls_dir=ensure_hls_dir,
            reactor=reactor,
            credentials_provider=read_e2_credentials,
        )

        self.server = HlsHttpServer(
            stream_service=self.stream_service,
            logger=self.logger,
            settings=self.settings,
            local_ip_provider=get_local_ip,
        )

    def start(self):
        self.stream_service.cleanup_old_session_files()
        if self.settings.plugin_enabled() and self.settings.autostart_enabled():
            self.server.start()

    def stop(self):
        self.server.stop()
        self.stream_service.stop_all()
        self.logger.close()
