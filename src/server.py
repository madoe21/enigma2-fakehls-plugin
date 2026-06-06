# -*- coding: utf-8 -*-
from __future__ import absolute_import

from .E2HLSServer.plugin import get_app


def startServer(manager, logger):
    if hasattr(manager, "app"):
        return manager.app.server.start()
    return get_app().server.start()


def stopServer(logger):
    return get_app().server.stop()


def isRunning():
    return get_app().server.is_running()


def stopAllStreams(manager, logger):
    if hasattr(manager, "stop_all"):
        manager.stop_all()
    elif hasattr(manager, "stopAll"):
        manager.stopAll()


def restartServer(manager, logger):
    if hasattr(manager, "app"):
        manager.app.server.restart()
    else:
        get_app().server.restart()
