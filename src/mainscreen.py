# -*- coding: utf-8 -*-
from __future__ import absolute_import

from .E2HLSServer.plugin import get_app
from .E2HLSServer.platform.enigma2.ui import E2HlsServerMainScreen


class E2HLSServerMain(E2HlsServerMainScreen):
	def __init__(self, session, manager=None, logger=None, app=None):
		if app is None:
			app = get_app()
		E2HlsServerMainScreen.__init__(self, session, app)


__all__ = ["E2HLSServerMain"]
