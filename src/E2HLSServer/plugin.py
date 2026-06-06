# -*- coding: utf-8 -*-
from __future__ import absolute_import

from Plugins.Plugin import PluginDescriptor

from .app import AppContext
from .platform.enigma2.config import PLUGIN_DESCRIPTION, PLUGIN_NAME, resolve_plugin_icon_path
from .platform.enigma2.ui import E2HlsServerMainScreen

_APP = None


def get_app():
    global _APP
    if _APP is None:
        _APP = AppContext()
        _APP.start()
    return _APP


def main(session, **kwargs):
    app = get_app()
    session.open(E2HlsServerMainScreen, app)


def autostart(reason, **kwargs):
    global _APP

    if reason == 0:
        get_app()
    elif reason == 1 and _APP is not None:
        _APP.stop()
        _APP = None


def Plugins(**kwargs):
    plugin_icon = resolve_plugin_icon_path()
    return [
        PluginDescriptor(
            name=PLUGIN_NAME,
            description=PLUGIN_DESCRIPTION,
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon=plugin_icon,
            fnc=main,
        ),
        PluginDescriptor(where=PluginDescriptor.WHERE_AUTOSTART, fnc=autostart),
    ]
