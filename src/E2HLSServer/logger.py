# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import sys
import threading
import traceback
from datetime import datetime


class PluginLogger(object):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    # The log lives in tmpfs (RAM) — an unbounded file eventually starves
    # the box. Rotate once past this size, keep one previous generation.
    MAX_LOG_BYTES = 512 * 1024

    def __init__(self, name="HLSPlugin", log_dir="/tmp/fakehls/logs", debug_mode=False):
        self.name = name
        self.debug_mode = debug_mode
        self.log_dir = log_dir
        self.log_file = os.path.join(log_dir, "plugin.log")
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # One persistent line-buffered handle instead of open/close per
        # line; writers include the reactor and the segmenter thread.
        self._lock = threading.Lock()
        self._handle = None

        self._ensure_directories()
        self._open_log()
        self._write_raw_log("=" * 80)
        self._write_raw_log("PLUGIN SESSION STARTED - " + self.session_id)
        self._write_raw_log("   Log Level: " + ("DEBUG" if debug_mode else "INFO"))
        self._write_raw_log("   Python: " + sys.version.replace("\n", " "))
        self._write_raw_log("=" * 80)

    def _ensure_directories(self):
        try:
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir, mode=0o755)
        except Exception as exc:
            print("EMERGENCY: Failed to create log directory: " + str(exc))

    def _open_log(self):
        try:
            self._handle = open(self.log_file, "a", encoding="utf-8", buffering=1)
        except Exception as exc:
            self._handle = None
            print("CRITICAL: Cannot open log file: " + str(exc))

    def _rotate_locked(self):
        """Swap the log for a fresh one; caller holds the lock."""
        try:
            self._handle.close()
        except Exception:
            pass
        self._handle = None
        try:
            os.replace(self.log_file, self.log_file + ".1")
        except Exception:
            pass
        self._open_log()

    def _write_raw_log(self, message):
        with self._lock:
            if self._handle is None:
                self._open_log()
            if self._handle is None:
                print("[" + self.name + "] " + message)
                return
            try:
                self._handle.write(message + "\n")
                if self._handle.tell() > self.MAX_LOG_BYTES:
                    self._rotate_locked()
            except Exception:
                print("[" + self.name + "] " + message)

    def _format_message(self, level, message, details=None):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_line = "[" + timestamp + "] [" + self.name + "] [" + level + "] " + message

        if details:
            if isinstance(details, dict):
                for key, value in details.items():
                    log_line += "\n  - " + str(key) + ": " + str(value)
            elif isinstance(details, list):
                for item in details:
                    log_line += "\n  - " + str(item)
            else:
                log_line += "\n  - " + str(details)

        return log_line

    def _log(self, level, level_name, message, details=None, exc_info=False):
        if level < self.INFO and not self.debug_mode:
            return

        log_line = self._format_message(level_name, message, details)
        if exc_info and level >= self.ERROR:
            log_line += "\n" + traceback.format_exc()

        self._write_raw_log(log_line)
        print("[" + self.name + "] [" + level_name + "] " + message)

    def debug(self, message, details=None):
        self._log(self.DEBUG, "DEBUG", message, details)

    def info(self, message, details=None):
        self._log(self.INFO, "INFO", message, details)

    def warning(self, message, details=None):
        self._log(self.WARNING, "WARNING", message, details)

    def error(self, message, details=None, exc_info=True):
        self._log(self.ERROR, "ERROR", message, details, exc_info)

    def critical(self, message, details=None, exc_info=True):
        self._log(self.CRITICAL, "CRITICAL", message, details, exc_info)

    def log_stream_start(self, stream_id, params, stream_port):
        self.info("Starting new stream " + stream_id, {
            "Stream ID": stream_id,
            "Service Ref": params.get("ref", "unknown"),
            "Stream Port": str(stream_port),
            "Auth": "Yes" if params.get("user") else "No",
            "User": params.get("user", "none"),
            "Mode": "copy",
        })

    def log_stream_stop(self, stream_id, reason="normal"):
        self.info("Stream " + stream_id + " stopped", {"Reason": reason})

    def log_stream_active(self, stream_id, info):
        import time

        self.debug("Stream " + stream_id + " already active", {
            "Uptime": str(int(time.time() - info["started"])) + "s",
            "Accesses": info.get("access_count", 0),
        })

    def log_ffmpeg_exit(self, stream_id, retcode, log_file=None):
        exit_meanings = {
            0: "Normal exit",
            1: "General error",
            127: "Command not found",
            146: "Connection timed out / SIGTERM",
        }
        meaning = exit_meanings.get(retcode, "Unknown error code " + str(retcode))
        details = {"Exit Code": retcode, "Meaning": meaning}

        if log_file and os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as handle:
                    lines = handle.readlines()[-5:]
                    details["Last FFmpeg output"] = "".join(lines)
            except Exception:
                pass

        if retcode == 0:
            self.info("FFmpeg for stream " + stream_id + " finished", details)
        else:
            self.error("FFmpeg for stream " + stream_id + " crashed", details)

    def log_request(self, method, path, client_ip, status, size=None):
        if self.debug_mode:
            details = {"Method": method, "Path": path, "Client": client_ip, "Status": status}
            if size is not None:
                details["Size"] = str(size) + " bytes"
            self.debug("HTTP " + method + " " + path, details)

    def close(self):
        self.info("Plugin session ended")
        self._write_raw_log("=" * 80)
        self._write_raw_log("PLUGIN SESSION ENDED - " + self.session_id)
        self._write_raw_log("=" * 80)
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.close()
                except Exception:
                    pass
                self._handle = None
