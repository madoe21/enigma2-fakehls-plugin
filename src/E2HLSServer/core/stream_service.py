# -*- coding: utf-8 -*-
from __future__ import absolute_import

import hashlib
import os
import threading
import time

from .ffmpeg_service import build_stream_url, start_ffmpeg

QUALITY_PRESETS = {
    "hw_transcode": {"label": "Hardware-Transcode (Port 8002)", "seg_duration": 2},
    "low_latency":  {"label": "Niedrige Latenz (1s)",           "seg_duration": 1},
    "balanced":     {"label": "Ausgewogen (2s)",                 "seg_duration": 2},
    "stable":       {"label": "Stabil (4s)",                     "seg_duration": 4},
}


class Segmenter(threading.Thread):
    PIPE_READ_SIZE = 65536

    def __init__(self, stream_id, settings, logger, local_ip_provider, seg_duration=None):
        threading.Thread.__init__(self)
        self.stream_id = stream_id
        self.settings = settings
        self.logger = logger
        self.local_ip_provider = local_ip_provider
        self.daemon = True
        self._seg_duration = seg_duration if seg_duration is not None else settings.segment_duration()

        self._stop_event = threading.Event()
        self.segment_index = 0
        self.segments = []

        self.hls_dir = settings.hls_dir()
        self.pipe_path = os.path.join(self.hls_dir, stream_id + "_pipe")
        self.playlist = os.path.join(self.hls_dir, "live_" + stream_id + ".m3u8")

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

    def segment_path(self, index):
        slot = index % (self.settings.playlist_size() + 1)
        return os.path.join(self.hls_dir, self.stream_id + "_ring%02d.ts" % slot)

    def segment_url(self, index):
        ip_addr = self.local_ip_provider()
        port = self.settings.http_port()
        slot = index % (self.settings.playlist_size() + 1)
        return "http://" + ip_addr + ":" + str(port) + "/hls/" + self.stream_id + "_ring%02d.ts" % slot

    def create_pipe(self):
        if os.path.exists(self.pipe_path):
            os.unlink(self.pipe_path)
        os.mkfifo(self.pipe_path, 0o600)

    def remove_pipe(self):
        try:
            if os.path.exists(self.pipe_path):
                os.unlink(self.pipe_path)
        except Exception:
            pass

    def run(self):
        while not self.stopped():
            try:
                self._run_segmentation()
            except Exception as exc:
                self.logger.error("Segmenter loop error for " + self.stream_id + ": " + str(exc), exc_info=True)
                time.sleep(2)

    def _run_segmentation(self):
        try:
            pipe_fd = open(self.pipe_path, "rb")
        except Exception as exc:
            self.logger.error("Segmenter: failed to open pipe: " + str(exc))
            time.sleep(1)
            return

        buffer_data = bytearray()
        segment_start = time.time()

        try:
            while not self.stopped():
                chunk = pipe_fd.read(self.PIPE_READ_SIZE)
                if not chunk:
                    self.logger.warning("Segmenter: pipe closed (FFmpeg ended)")
                    break

                buffer_data.extend(chunk)
                now = time.time()
                if now - segment_start >= self._seg_duration:
                    if buffer_data:
                        self._write_segment(bytes(buffer_data))
                        buffer_data = bytearray()
                    segment_start = now

                self._clean_old_segments()
        except Exception as exc:
            self.logger.error("Segmenter: pipe read error: " + str(exc), exc_info=True)
        finally:
            try:
                pipe_fd.close()
            except Exception:
                pass

            if buffer_data and not self.stopped():
                self._write_segment(bytes(buffer_data))

    def _write_segment(self, data):
        if len(data) < 8 * 1024:
            return

        seg_path = self.segment_path(self.segment_index)
        created_at = time.time()

        try:
            with open(seg_path, "wb") as handle:
                handle.write(data)

            self.segments.append((self.segment_index, seg_path, created_at))
            self.segment_index += 1
            if self.segments:
                self._update_playlist()
        except Exception as exc:
            self.logger.error("Error writing segment " + str(self.segment_index) + ": " + str(exc))

    def _update_playlist(self):
        try:
            active = self.segments[-self.settings.playlist_size():]
            first_seq = active[0][0]

            content = "#EXTM3U\n"
            content += "#EXT-X-VERSION:3\n"
            content += "#EXT-X-TARGETDURATION:" + str(self._seg_duration + 1) + "\n"
            content += "#EXT-X-MEDIA-SEQUENCE:" + str(first_seq) + "\n"

            for idx, _seg_path, _created_at in active:
                content += "#EXTINF:" + str(float(self._seg_duration)) + ",\n"
                content += self.segment_url(idx) + "\n"

            tmp_path = self.playlist + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.rename(tmp_path, self.playlist)
        except Exception as exc:
            self.logger.error("Error updating playlist: " + str(exc))

    def _clean_old_segments(self):
        keep = self.settings.playlist_size() + 1
        if len(self.segments) > keep:
            self.segments = self.segments[-keep:]

    def cleanup_all(self):
        for slot in range(self.settings.playlist_size() + 1):
            seg_path = os.path.join(self.hls_dir, self.stream_id + "_ring%02d.ts" % slot)
            try:
                if os.path.exists(seg_path):
                    os.unlink(seg_path)
            except Exception:
                pass

        self.segments = []

        for path in [self.playlist, self.playlist + ".tmp"]:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except Exception:
                pass

        self.remove_pipe()


class StreamService(object):
    def __init__(self, settings, logger, local_ip_provider, ensure_hls_dir, reactor):
        self.streams = {}
        self.settings = settings
        self.logger = logger
        self.local_ip_provider = local_ip_provider
        self.ensure_hls_dir = ensure_hls_dir
        self.reactor = reactor
        self._cleanup_timer = None

        self._ensure_directories()
        self._start_cleanup_timer()

    def _ensure_directories(self):
        self.ensure_hls_dir(self.settings.hls_dir())

    def _start_cleanup_timer(self):
        self._cleanup_timer = self.reactor.callLater(self.settings.cleanup_interval(), self._cleanup)

    def _cleanup(self):
        try:
            self._cleanup_inactive_streams()
        except Exception as exc:
            self.logger.error("Error in cleanup: " + str(exc))
        finally:
            self._cleanup_timer = self.reactor.callLater(self.settings.cleanup_interval(), self._cleanup)

    def _cleanup_inactive_streams(self):
        now = time.time()
        to_delete = []

        for stream_id, info in list(self.streams.items()):
            process = info.get("process")
            if process and process.poll() is not None:
                self.logger.info("Stream " + stream_id + " FFmpeg exited (code " + str(process.returncode) + "), removing")
                self._stop_stream(stream_id, delete_files=True)
                to_delete.append(stream_id)
                continue

            inactive = now - info["last_accessed"]
            if inactive > self.settings.inactivity_timeout():
                self.logger.info("Stream " + stream_id + " inactive for " + str(int(inactive)) + "s, stopping")
                self._stop_stream(stream_id, delete_files=True)
                to_delete.append(stream_id)

        for stream_id in to_delete:
            self.streams.pop(stream_id, None)

    def _read_e2_credentials(self):
        try:
            from ..platform.enigma2.config import read_e2_credentials
            return read_e2_credentials()
        except Exception:
            return "root", ""

    def generate_stream_id(self, params):
        quality = params.get("quality", "balanced")
        base = params["ref"] + ":" + str(self.settings.stream_port()) + ":" + quality
        if params.get("user"):
            base += ":" + params["user"]
        return hashlib.md5(base.encode()).hexdigest()[:8]

    def get_or_create_stream(self, params):
        quality = params.get("quality", "balanced")
        preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["balanced"])
        hw = (quality == "hw_transcode")

        effective_params = dict(params)
        if hw:
            effective_params["hw"] = True
            e2_user, e2_pass = self._read_e2_credentials()
            effective_params["e2_user"] = e2_user
            effective_params["e2_pass"] = e2_pass

        stream_id = self.generate_stream_id(params)

        if stream_id in self.streams:
            info = self.streams[stream_id]
            info["last_accessed"] = time.time()
            info["access_count"] += 1
            self.logger.log_stream_active(stream_id, info)
            return stream_id, False

        self.logger.log_stream_start(stream_id, effective_params, self.settings.stream_port())
        self._cleanup_stream_files(stream_id)

        hls_dir = self.settings.hls_dir()
        log_dir = os.path.join(hls_dir, "logs")
        self.ensure_hls_dir(hls_dir)

        segmenter = Segmenter(stream_id, self.settings, self.logger, self.local_ip_provider,
                              seg_duration=preset["seg_duration"])
        segmenter.create_pipe()

        stream_url = build_stream_url(effective_params, self.settings)
        process = start_ffmpeg(
            stream_url,
            segmenter.pipe_path,
            stream_id,
            log_dir,
            self.settings,
            on_exit=self._on_ffmpeg_exit,
            e2_user=effective_params.get("e2_user"),
            e2_pass=effective_params.get("e2_pass"),
        )

        if process is None:
            self.logger.error("Failed to start FFmpeg for stream " + stream_id)
            segmenter.remove_pipe()
            return None, False

        segmenter.start()

        self.streams[stream_id] = {
            "id": stream_id,
            "params": params,
            "stream_url": stream_url,
            "process": process,
            "segmenter": segmenter,
            "started": time.time(),
            "last_accessed": time.time(),
            "access_count": 1,
            "crash_count": 0,
        }

        self.logger.info("Stream " + stream_id + " started (mode=copy)")
        return stream_id, True

    def _on_ffmpeg_exit(self, stream_id, retcode, ffmpeg_log):
        self.logger.log_ffmpeg_exit(stream_id, retcode, ffmpeg_log)
        if stream_id in self.streams and retcode != 0:
            self.streams[stream_id]["crash_count"] = self.streams[stream_id].get("crash_count", 0) + 1

    def _stop_stream(self, stream_id, delete_files=False):
        if stream_id not in self.streams:
            return

        info = self.streams[stream_id]

        if info.get("segmenter"):
            info["segmenter"].stop()
            if delete_files:
                info["segmenter"].cleanup_all()

        if info.get("process"):
            info["process"].terminate()
            try:
                info["process"].wait(timeout=2)
            except Exception:
                info["process"].kill()

        if delete_files:
            self._cleanup_stream_files(stream_id)

        self.streams.pop(stream_id, None)
        self.logger.log_stream_stop(stream_id)

    def stop_all(self):
        for stream_id in list(self.streams.keys()):
            self._stop_stream(stream_id, delete_files=True)

    def _cleanup_stream_files(self, stream_id):
        hls_dir = self.settings.hls_dir()
        try:
            if os.path.exists(hls_dir):
                for name in os.listdir(hls_dir):
                    if name.startswith(stream_id):
                        path = os.path.join(hls_dir, name)
                        if os.path.isfile(path):
                            os.unlink(path)
                playlist = os.path.join(hls_dir, "live_" + stream_id + ".m3u8")
                if os.path.exists(playlist):
                    os.unlink(playlist)
        except Exception as exc:
            self.logger.error("Error cleaning up files for " + stream_id + ": " + str(exc))

    def cleanup_old_session_files(self):
        hls_dir = self.settings.hls_dir()
        try:
            if os.path.exists(hls_dir):
                for name in os.listdir(hls_dir):
                    path = os.path.join(hls_dir, name)
                    if (name.endswith(".ts") or name.endswith(".m3u8") or name.endswith("_pipe")) and os.path.exists(path):
                        os.unlink(path)
                self.logger.info("Cleaned up old session files")
        except Exception as exc:
            self.logger.error("Error cleaning old files: " + str(exc))

    def update_access(self, stream_id):
        if stream_id in self.streams:
            self.streams[stream_id]["last_accessed"] = time.time()

    def get_status(self):
        hls_dir = self.settings.hls_dir()
        status = {}

        for stream_id, info in self.streams.items():
            files = []
            seg_count = 0
            if os.path.exists(hls_dir):
                files = [name for name in os.listdir(hls_dir) if name.startswith(stream_id)]
            if info.get("segmenter"):
                seg_count = len(info["segmenter"].segments)

            status[stream_id] = {
                "id": stream_id,
                "ref": info["params"]["ref"],
                "port": str(self.settings.stream_port()),
                "has_auth": bool(info["params"].get("user")),
                "uptime": int(time.time() - info["started"]),
                "files": files,
                "segments": seg_count,
                "access_count": info["access_count"],
                "crash_count": info.get("crash_count", 0),
                "hls_url": "/hls/live_" + stream_id + ".m3u8",
            }

        return status
