# -*- coding: utf-8 -*-
from __future__ import absolute_import

from twisted.internet import reactor

from .E2HLSServer.core.stream_service import StreamService
from .E2HLSServer.platform.enigma2.config import Enigma2Settings, ensure_hls_dir


class StreamManager(StreamService):
    def __init__(self, logger):
        StreamService.__init__(
            self,
            settings=Enigma2Settings(),
            logger=logger,
            ensure_hls_dir=ensure_hls_dir,
            reactor=reactor,
        )

    def getOrCreateStream(self, params):
        return self.get_or_create_stream(params)

    def cleanupOldSessionFiles(self):
        return self.cleanup_old_session_files()

    def updateAccess(self, sid):
        return self.update_access(sid)

    def getStatus(self):
        return self.get_status()

    def stopAll(self):
        return self.stop_all()