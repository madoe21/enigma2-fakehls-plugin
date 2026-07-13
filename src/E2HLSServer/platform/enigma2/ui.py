# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os

from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.ScrollLabel import ScrollLabel
from Components.Sources.StaticText import StaticText
from Components.config import config, getConfigListEntry
from Screens.Screen import Screen
from Tools.Directories import SCOPE_PLUGINS, resolveFilename
from twisted.internet import reactor

from .config import _, ensure_hls_dir, get_local_ip


class E2HlsServerConfigScreen(ConfigListScreen, Screen):
    skin = """
        <screen name="E2HLSServerConfig" position="center,center" size="700,580" title="E2 HLS Server - Settings">
            <widget name="config" position="10,10" size="680,446" scrollbarMode="showOnDemand" />
              <ePixmap pixmap="/usr/lib/enigma2/python/Plugins/Extensions/E2HLSServer/res/qr_buymeacoffee.png"
                 position="10,462" size="80,80" alphatest="on" />
            <widget name="support" position="100,462" size="590,80"
                    font="Regular;16" foregroundColor="#666666" valign="center" />
            <ePixmap pixmap="skin_default/buttons/red.png" position="20,550" size="140,40" alphatest="on" />
            <ePixmap pixmap="skin_default/buttons/green.png" position="180,550" size="140,40" alphatest="on" />
            <widget name="key_red" position="20,550" size="140,40" valign="center" halign="center" font="Regular;20" transparent="1" />
            <widget name="key_green" position="180,550" size="140,40" valign="center" halign="center" font="Regular;20" transparent="1" />
        </screen>
    """

    def __init__(self, session, app):
        Screen.__init__(self, session)
        self.app = app

        self.list = []
        self._build_config_list()
        ConfigListScreen.__init__(self, self.list, session=session, on_change=self._on_changed)

        self["key_red"] = Label(_("Cancel"))
        self["key_green"] = Label(_("Save"))
        self["support"] = Label(_("Support this plugin: https://buymeacoffee.com/madoe21"))

        self["setupActions"] = ActionMap(["SetupActions", "ColorActions"], {
            "red": self.cancel,
            "green": self.save,
            "save": self.save,
            "cancel": self.cancel,
            "ok": self.save,
        }, -2)

    def _build_config_list(self):
        self.list.append(getConfigListEntry(_("Enable Plugin"), config.plugins.e2hlsserver.enabled))
        self.list.append(getConfigListEntry(_("HTTP Port"), config.plugins.e2hlsserver.port))
        self.list.append(getConfigListEntry(_("Enigma2 Stream Port (4 digits)"), config.plugins.e2hlsserver.stream_port))
        self.list.append(getConfigListEntry(_("Enigma2 HW Transcode Port"), config.plugins.e2hlsserver.stream_hw_port))
        self.list.append(getConfigListEntry(_("Autostart on boot"), config.plugins.e2hlsserver.autostart))
        self.list.append(getConfigListEntry(_("HLS Temp Directory"), config.plugins.e2hlsserver.hls_dir))
        self.list.append(getConfigListEntry(_("Segment Duration (seconds)"), config.plugins.e2hlsserver.segment_duration))
        self.list.append(getConfigListEntry(_("Cleanup Interval (seconds)"), config.plugins.e2hlsserver.cleanup_interval))
        self.list.append(getConfigListEntry(_("Inactivity Timeout (seconds)"), config.plugins.e2hlsserver.inactivity_timeout))
        self.list.append(getConfigListEntry(_("Debug Mode"), config.plugins.e2hlsserver.debug))

    def _on_changed(self):
        return

    def save(self):
        for item in self.list:
            item[1].save()

        ensure_hls_dir(config.plugins.e2hlsserver.hls_dir.value)

        if config.plugins.e2hlsserver.enabled.value == "true":
            self.app.server.start()
        else:
            self.app.server.stop()

        self.close()

    def cancel(self):
        for item in self.list:
            item[1].cancel()
        self.close()


class E2HlsServerMainScreen(Screen):
    skin = """
        <screen name="E2HLSServerMain" position="center,center" size="700,540" title="E2 HLS Server v1.0">
            <widget name="status" position="10,10" size="680,370" font="Regular;19" />
            <widget name="support" position="10,385" size="680,24"
                    font="Regular;16" foregroundColor="#666666" />
            <ePixmap pixmap="skin_default/buttons/red.png" position="10,420" size="165,40" alphatest="on" />
            <ePixmap pixmap="skin_default/buttons/green.png" position="180,420" size="165,40" alphatest="on" />
            <ePixmap pixmap="skin_default/buttons/yellow.png" position="350,420" size="165,40" alphatest="on" />
            <ePixmap pixmap="skin_default/buttons/blue.png" position="520,420" size="170,40" alphatest="on" />
            <widget name="key_red" position="10,420" size="165,40" valign="center" halign="center" font="Regular;20" transparent="1" />
            <widget name="key_green" position="180,420" size="165,40" valign="center" halign="center" font="Regular;20" transparent="1" />
            <widget name="key_yellow" position="350,420" size="165,40" valign="center" halign="center" font="Regular;20" transparent="1" />
            <widget name="key_blue" position="520,420" size="170,40" valign="center" halign="center" font="Regular;20" transparent="1" />
        </screen>
    """

    def __init__(self, session, app):
        Screen.__init__(self, session)
        self.app = app

        self["status"] = Label("")
        self["support"] = Label("Buy me a coffee: https://buymeacoffee.com/madoe21")
        self["key_red"] = Label(_("Close"))
        self["key_green"] = Label(_("Settings"))
        self["key_yellow"] = Label(_("Restart"))
        self["key_blue"] = Label(_("Information"))

        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "red": self.close,
            "green": self.open_settings,
            "yellow": self.restart_server,
            "blue": self.open_info,
            "cancel": self.close,
        }, -2)

        self.update_status()

    def update_status(self):
        ip_addr = get_local_ip()
        port = self.app.settings.http_port()
        hls_dir = self.app.settings.hls_dir()
        running = self.app.server.is_running()
        streams = self.app.stream_service.get_status()

        status = "E2 HLS Server v1.0\n\n"
        status += _("HTTP Server") + ":   " + (_("Running") if running else _("Stopped")) + "\n"
        status += _("Address") + ":       http://" + ip_addr + ":" + str(port) + "\n"
        status += _("HLS Directory") + ": " + hls_dir + "\n"
        status += _("Active Streams") + ":  " + str(len(streams)) + "\n\n"

        if streams:
            status += _("Active Streams") + ":\n"
            for stream_id, info in streams.items():
                status += "  " + stream_id + "  " + str(info["uptime"]) + "s"
                status += "  " + str(info["segments"]) + " " + _("segments") + "\n"
            status += "\n"

        status += "URLs:\n"
        status += "  Web:    http://" + ip_addr + ":" + str(port) + "/web\n"
        status += "  Stream: http://" + ip_addr + ":" + str(port) + "/<ref>\n"
        status += "  Status: http://" + ip_addr + ":" + str(port) + "/status\n"
        status += "  Logs:   http://" + ip_addr + ":" + str(port) + "/logs"

        self["status"].setText(status)

    def kill_streams(self):
        self.app.stream_service.stop_all()
        self.update_status()

    def restart_server(self):
        self.app.stream_service.stop_all()
        self.app.server.stop()
        reactor.callLater(1, self._do_restart)

    def _do_restart(self):
        self.app.server.start()
        self.update_status()

    def open_info(self):
        self.session.open(E2HlsInfoScreen)

    def open_settings(self):
        self.session.openWithCallback(self.update_status, E2HlsServerConfigScreen, self.app)


class E2HlsInfoScreen(Screen):
    skin = """
        <screen name="E2HlsInfoScreen" position="center,90" size="1000,620" title="E2 HLS Server Info">
            <widget source="title" render="Label" position="20,10" size="960,35" font="Regular;30" />
            <widget name="body" position="20,55" size="690,500" font="Regular;24" scrollbarMode="showOnDemand" />
            <widget name="qr" position="740,100" size="240,240" alphatest="blend" />
            <widget source="support" render="Label" position="20,560" size="960,24" font="Regular;20" foregroundColor="#666666" />
            <ePixmap pixmap="skin_default/buttons/red.png" position="20,585" size="220,30" alphatest="on" />
            <widget source="key_red" render="Label" position="20,585" size="220,30" font="Regular;22" halign="center" valign="center" transparent="1" />
        </screen>
    """

    def __init__(self, session):
        Screen.__init__(self, session)
        self["title"] = StaticText(_("Information"))
        self["key_red"] = StaticText(_("Close"))
        self["support"] = StaticText("Buy me a coffee: https://buymeacoffee.com/madoe21")
        self["body"] = ScrollLabel(self._info_text())
        self["qr"] = Pixmap()
        self.onLayoutFinish.append(self._load_qr)

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "cancel": self.close,
                "ok": self.close,
                "red": self.close,
                "up": self["body"].pageUp,
                "down": self["body"].pageDown,
                "left": self["body"].pageUp,
                "right": self["body"].pageDown,
            },
            -1,
        )

    def _info_text(self):
        lines = [
            "E2 HLS Server Plugin",
            "",
            _("Description") + ":",
            "  Converts Enigma2 streams to HLS format",
            "  for playback in web browsers and media players.",
            "",
            _("Controls") + ":",
            u"  Gr\u00fcn   \u2192 " + _("Settings"),
            u"  Gelb   \u2192 " + _("Restart"),
            u"  Blau   \u2192 " + _("Information"),
            u"  Rot    \u2192 " + _("Close"),
            "",
            "Buy me a coffee: https://buymeacoffee.com/madoe21",
            "GitHub: https://github.com/madoe21/enigma2-fakehls-plugin",
        ]
        return "\n".join(lines)

    def _load_qr(self):
        for path in [
            resolveFilename(SCOPE_PLUGINS, "Extensions/E2HLSServer/res/qr_buymeacoffee.png"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "res", "qr_buymeacoffee.png"),
        ]:
            if os.path.exists(path):
                try:
                    self["qr"].instance.setPixmapFromFile(path)
                    return
                except Exception:
                    pass
