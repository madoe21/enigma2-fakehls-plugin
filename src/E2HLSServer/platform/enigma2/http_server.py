# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import urllib.parse

from twisted.web.resource import Resource
from twisted.web.server import Site

from .config import HTML_TEMPLATE_FILE, get_favicon_path


def load_html_template():
    try:
        if os.path.exists(HTML_TEMPLATE_FILE):
            with open(HTML_TEMPLATE_FILE, "r", encoding="utf-8") as handle:
                return handle.read()
        print("[E2HLSServer] ERROR: HTML template not found: " + HTML_TEMPLATE_FILE)
        return None
    except Exception as exc:
        print("[E2HLSServer] Error loading HTML template: " + str(exc))
        return None


class HlsRoot(Resource):
    def __init__(self, stream_service, logger, settings, local_ip_provider, html_template):
        Resource.__init__(self)
        self.stream_service = stream_service
        self.logger = logger
        self.settings = settings
        self.local_ip_provider = local_ip_provider
        self.html_template = html_template

    def getChild(self, name, request):
        return self

    def render_GET(self, request):
        path = request.path.decode()

        if path == "/player":
            return self.render_player(request)
        if path == "/favicon.ico" or path == "/res/favicon.png":
            return self.render_favicon(request)
        if path == "/stream":
            return self.render_stream(request)
        if path.startswith("/hls/"):
            return self.render_hls(request)
        if path == "/status":
            return self.render_status(request)
        if path == "/logs":
            return self.render_logs(request)

        request.setResponseCode(404)
        return b"Not Found"

    def _parse_basic_auth(self, request):
        header = request.getHeader("authorization")
        if not header or not header.startswith("Basic "):
            return None, None

        try:
            token = header[6:].strip()
            decoded = base64.b64decode(token).decode("utf-8")
            if ":" not in decoded:
                return None, None
            return decoded.split(":", 1)
        except Exception:
            self.logger.warning("Invalid Authorization header received")
            return None, None

    def _parse_params(self, request):
        args = request.args
        ref = args.get(b"ref", [None])[0]
        user = args.get(b"user", [None])[0]
        password = args.get(b"pass", [None])[0]

        if ref is None:
            return None

        query_user = urllib.parse.unquote(user.decode()) if user else None
        query_pass = urllib.parse.unquote(password.decode()) if password else None
        header_user, header_pass = self._parse_basic_auth(request)

        return {
            "ref": urllib.parse.unquote(ref.decode()),
            "user": query_user if query_user is not None else header_user,
            "password": query_pass if query_pass is not None else header_pass,
        }

    def _build_external_url(self, params, path):
        ip_addr = self.local_ip_provider()
        port = self.settings.http_port()
        parts = ["ref=" + urllib.parse.quote(params["ref"])]
        if params.get("user"):
            parts.append("user=" + urllib.parse.quote(params["user"]))
        if params.get("password"):
            parts.append("pass=" + urllib.parse.quote(params["password"]))
        return "http://" + ip_addr + ":" + str(port) + path + "?" + "&".join(parts)

    def render_player(self, request):
        if self.html_template is None:
            return b"ERROR: HTML template not loaded"

        params = self._parse_params(request)
        if params is None:
            return b"Missing ref parameter"

        stream_id, _is_new = self.stream_service.get_or_create_stream(params)
        if stream_id is None:
            return b"Failed to start stream"

        ip_addr = self.local_ip_provider()
        port = self.settings.http_port()
        hls_url = "http://" + ip_addr + ":" + str(port) + "/hls/live_" + stream_id + ".m3u8"
        ext_url = self._build_external_url(params, "/stream")

        try:
            html = self.html_template
            html = html.replace("{stream_id}", stream_id)
            html = html.replace("{service_ref}", params["ref"])
            html = html.replace("{external_url}", ext_url)
            html = html.replace("{hls_url}", hls_url)
            self.logger.log_request("GET", "/player", request.getClientIP(), 200, len(html))
            return html.encode()
        except Exception as exc:
            self.logger.error("HTML template error: " + str(exc))
            return ("Error generating player page: " + str(exc)).encode()

    def render_favicon(self, request):
        favicon_path = get_favicon_path()
        if not os.path.exists(favicon_path):
            request.setResponseCode(404)
            return b""

        try:
            with open(favicon_path, "rb") as handle:
                data = handle.read()
            request.setHeader(b"Content-Type", b"image/png")
            request.setHeader(b"Cache-Control", b"public, max-age=3600")
            self.logger.log_request("GET", "/res/favicon.png", request.getClientIP(), 200, len(data))
            return data
        except Exception as exc:
            self.logger.error("Error reading favicon: " + str(exc))
            request.setResponseCode(500)
            return b""

    def render_stream(self, request):
        params = self._parse_params(request)
        if params is None:
            request.setResponseCode(400)
            return b"Missing ref parameter"

        stream_id, _is_new = self.stream_service.get_or_create_stream(params)
        if stream_id is None:
            request.setResponseCode(500)
            return b"Failed to start stream"

        import time

        playlist = os.path.join(self.settings.hls_dir(), "live_" + stream_id + ".m3u8")
        for _ in range(20):
            if os.path.exists(playlist):
                break
            time.sleep(0.5)

        request.redirect(("/hls/live_" + stream_id + ".m3u8").encode())
        return b""

    def render_hls(self, request):
        path = request.path.decode()
        filename = path.split("/")[-1]
        filepath = os.path.join(self.settings.hls_dir(), filename)

        if filename.startswith("live_") and filename.endswith(".m3u8"):
            stream_id = filename[5:-5]
            self.stream_service.update_access(stream_id)

        if not os.path.exists(filepath):
            request.setResponseCode(404)
            return b""

        try:
            with open(filepath, "rb") as handle:
                data = handle.read()
        except Exception as exc:
            self.logger.error("Error reading HLS file " + filename + ": " + str(exc))
            request.setResponseCode(500)
            return b""

        if filename.endswith(".m3u8"):
            request.setHeader(b"Content-Type", b"application/vnd.apple.mpegurl")
            request.setHeader(b"Cache-Control", b"no-cache, no-store")
        elif filename.endswith(".ts"):
            request.setHeader(b"Content-Type", b"video/MP2T")

        self.logger.log_request("GET", "/hls/" + filename, request.getClientIP(), 200, len(data))
        return data

    def render_status(self, request):
        ip_addr = self.local_ip_provider()
        port = self.settings.http_port()
        status = self.stream_service.get_status()

        response = "HLS Plugin Status\n"
        response += "=" * 40 + "\n"
        response += "Server:   " + ip_addr + ":" + str(port) + "\n"
        response += "HLS dir:  " + self.settings.hls_dir() + "\n"
        response += "Streams:  " + str(len(status)) + "\n\n"

        for stream_id, info in status.items():
            response += "Stream " + stream_id + ":\n"
            response += "  Ref:      " + info["ref"] + "\n"
            response += "  Mode:     copy\n"
            response += "  Uptime:   " + str(info["uptime"]) + "s\n"
            response += "  Segments: " + str(info["segments"]) + "\n"
            response += "  Accesses: " + str(info["access_count"]) + "\n"
            response += "  Crashes:  " + str(info["crash_count"]) + "\n"
            response += "  HLS URL:  http://" + ip_addr + ":" + str(port) + info["hls_url"] + "\n\n"

        self.logger.log_request("GET", "/status", request.getClientIP(), 200)
        return response.encode()

    def render_logs(self, request):
        log_file = os.path.join(self.settings.hls_dir(), "logs", "plugin.log")
        try:
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8", errors="ignore") as handle:
                    lines = handle.readlines()[-100:]
                content = "".join(lines)
                self.logger.log_request("GET", "/logs", request.getClientIP(), 200)
                return content.encode()
            return b"No logs available"
        except Exception as exc:
            return ("Error reading logs: " + str(exc)).encode()


class HlsHttpServer(object):
    def __init__(self, stream_service, logger, settings, local_ip_provider):
        self.stream_service = stream_service
        self.logger = logger
        self.settings = settings
        self.local_ip_provider = local_ip_provider
        self._listener = None
        self._html_template = load_html_template()

    def is_running(self):
        return self._listener is not None

    def start(self):
        if self._listener:
            self.logger.info("HTTP server already running")
            return True

        try:
            from twisted.internet import reactor

            root = HlsRoot(
                stream_service=self.stream_service,
                logger=self.logger,
                settings=self.settings,
                local_ip_provider=self.local_ip_provider,
                html_template=self._html_template,
            )
            site = Site(root)
            port = self.settings.http_port()
            self.logger.info("Starting HTTP server on port " + str(port))
            self._listener = reactor.listenTCP(port, site)
            self.logger.info("HTTP server started on port " + str(port))
            return True
        except Exception as exc:
            self.logger.error("Failed to start HTTP server: " + str(exc))
            self._listener = None
            return False

    def stop(self):
        if not self._listener:
            return

        try:
            self._listener.stopListening()
            self._listener = None
            self.logger.info("HTTP server stopped")
        except Exception as exc:
            self.logger.error("Error stopping HTTP server: " + str(exc))
            self._listener = None

    def restart(self):
        self.stream_service.stop_all()
        self.stop()

        import time

        time.sleep(1)
        self.start()
        self.logger.info("Server restarted")
