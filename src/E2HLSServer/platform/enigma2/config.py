# -*- coding: utf-8 -*-
from __future__ import absolute_import

import gettext
import os
import socket
import subprocess

from Components.Language import language
from Components.config import (
	ConfigInteger,
	ConfigSelection,
	ConfigSubsection,
	ConfigText,
	config,
)
from Tools.Directories import SCOPE_PLUGINS, resolveFilename


def localeInit():
	try:
		lang = language.getLanguage()[:2]
		os.environ["LANGUAGE"] = lang
		gettext.bindtextdomain("E2HLSServer", resolveFilename(SCOPE_PLUGINS, "Extensions/E2HLSServer/locale"))
	except Exception as exc:
		print("[E2HLSServer] ERROR in localeInit: " + str(exc))


def _(txt):
	try:
		return gettext.dgettext("E2HLSServer", txt)
	except Exception:
		return txt


try:
	localeInit()
	language.addCallback(localeInit)
except Exception as exc:
	print("[E2HLSServer] ERROR setting up translations: " + str(exc))


PLUGIN_NAME = "E2HLS Server"
PLUGIN_VERSION = "1.0"
PLUGIN_DESCRIPTION = _("E2HLS Server - Stream Enigma2 channels as HLS to any device")
PLUGIN_RES_PATH = "Extensions/E2HLSServer/E2HLSServer/res"
PLUGIN_ICON_FALLBACK = "plugin.png"
FAVICON_FILENAME = "favicon.png"
PLUGIN_PATH = "/usr/lib/enigma2/python/Plugins/Extensions/E2HLSServer"
HTML_TEMPLATE_FILE = os.path.join(PLUGIN_PATH, "player_template.html")


def icon_file_for_aspect_ratio():
	try:
		from enigma import getDesktop

		size = getDesktop(0).size()
		width = int(size.width())
		height = int(size.height())
		if height > 0:
			ratio = float(width) / float(height)
			if ratio < 1.2:
				return "plugin_1x1.png"
			if ratio < 1.5:
				return "plugin_4x3.png"
			if ratio < 1.7:
				return "plugin_16x10.png"
			return "plugin_16x9.png"
	except Exception:
		pass

	return "plugin_16x9.png"


def resolve_plugin_icon_path():
	icon_name = icon_file_for_aspect_ratio()
	icon_path = resolveFilename(SCOPE_PLUGINS, "%s/%s" % (PLUGIN_RES_PATH, icon_name))
	if os.path.exists(icon_path):
		return icon_path
	return resolveFilename(SCOPE_PLUGINS, "%s/%s" % (PLUGIN_RES_PATH, PLUGIN_ICON_FALLBACK))


def get_favicon_path():
	return os.path.join(PLUGIN_PATH, "res", FAVICON_FILENAME)


# Fallbacks when Enigma2 config is unavailable.
DEFAULT_SEGMENT_DURATION = 2
DEFAULT_PLAYLIST_SIZE = 3
DEFAULT_SEGMENT_MAX_AGE = 30
DEFAULT_CLEANUP_INTERVAL = 10
DEFAULT_INACTIVITY_TIMEOUT = 45


config.plugins.e2hlsserver = ConfigSubsection()
config.plugins.e2hlsserver.enabled = ConfigSelection(default="false", choices=[("true", _("Yes")), ("false", _("No"))])
config.plugins.e2hlsserver.port = ConfigInteger(default=8003, limits=(1024, 65535))
config.plugins.e2hlsserver.stream_port = ConfigInteger(default=8001, limits=(1000, 9999))
config.plugins.e2hlsserver.stream_hw_port = ConfigInteger(default=8002, limits=(1000, 9999))
config.plugins.e2hlsserver.autostart = ConfigSelection(default="false", choices=[("true", _("Yes")), ("false", _("No"))])
config.plugins.e2hlsserver.debug = ConfigSelection(default="false", choices=[("true", _("Yes")), ("false", _("No"))])
config.plugins.e2hlsserver.bind_ip = ConfigText(default="auto")
config.plugins.e2hlsserver.hls_dir = ConfigText(default="/tmp/fakehls")
config.plugins.e2hlsserver.segment_duration = ConfigInteger(default=2, limits=(2, 30))
config.plugins.e2hlsserver.cleanup_interval = ConfigInteger(default=10, limits=(5, 300))
config.plugins.e2hlsserver.inactivity_timeout = ConfigInteger(default=45, limits=(10, 3600))


def read_e2_credentials():
	try:
		with open("/etc/enigma2/settings", "r") as handle:
			user, pw = "root", ""
			for line in handle:
				if line.startswith("config.OpenWebif.auth_user="):
					user = line.split("=", 1)[1].strip()
				elif line.startswith("config.ipboxclient.password="):
					pw = line.split("=", 1)[1].strip()
			return user, pw
	except Exception:
		return "root", ""


def ensure_hls_dir(hls_dir=None):
	if hls_dir is None:
		hls_dir = config.plugins.e2hlsserver.hls_dir.value
	log_dir = os.path.join(hls_dir, "logs")
	for directory in [hls_dir, log_dir]:
		if not os.path.exists(directory):
			try:
				os.makedirs(directory, mode=0o755)
				print("[E2HLSServer] Created directory: " + directory)
			except Exception as exc:
				print("[E2HLSServer] ERROR creating directory " + directory + ": " + str(exc))
				return False
		elif not os.access(directory, os.W_OK):
			print("[E2HLSServer] ERROR: no write access to " + directory)
			return False
	return True


def get_local_ip():
	try:
		configured = config.plugins.e2hlsserver.bind_ip.value
		if configured and configured != "auto":
			return configured
	except Exception:
		pass

	try:
		result = subprocess.check_output(
			["ip", "-4", "addr", "show"],
			encoding="utf-8",
			stderr=subprocess.DEVNULL,
		)
		best_ip = None
		for line in result.splitlines():
			line = line.strip()
			if line.startswith("inet "):
				ip_addr = line.split()[1].split("/")[0]
				if ip_addr.startswith("127."):
					continue
				if ip_addr.startswith("10."):
					if best_ip is None:
						best_ip = ip_addr
					continue
				return ip_addr
		if best_ip:
			return best_ip
	except Exception:
		pass

	try:
		sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		sock.connect(("8.8.8.8", 80))
		ip_addr = sock.getsockname()[0]
		sock.close()
		return ip_addr
	except Exception:
		return "127.0.0.1"


class Enigma2Settings(object):
	def plugin_enabled(self):
		return config.plugins.e2hlsserver.enabled.value == "true"

	def autostart_enabled(self):
		return config.plugins.e2hlsserver.autostart.value == "true"

	def debug_enabled(self):
		return config.plugins.e2hlsserver.debug.value == "true"

	def http_port(self):
		return config.plugins.e2hlsserver.port.value

	def stream_port(self):
		return config.plugins.e2hlsserver.stream_port.value

	def stream_hw_port(self):
		try:
			return config.plugins.e2hlsserver.stream_hw_port.value
		except Exception:
			return 8002

	def hls_dir(self):
		return config.plugins.e2hlsserver.hls_dir.value

	def segment_duration(self):
		try:
			return config.plugins.e2hlsserver.segment_duration.value
		except Exception:
			return DEFAULT_SEGMENT_DURATION

	def cleanup_interval(self):
		try:
			return config.plugins.e2hlsserver.cleanup_interval.value
		except Exception:
			return DEFAULT_CLEANUP_INTERVAL

	def inactivity_timeout(self):
		try:
			return config.plugins.e2hlsserver.inactivity_timeout.value
		except Exception:
			return DEFAULT_INACTIVITY_TIMEOUT

	def playlist_size(self):
		return DEFAULT_PLAYLIST_SIZE

	def ffmpeg_bin(self):
		return "/usr/bin/ffmpeg"
