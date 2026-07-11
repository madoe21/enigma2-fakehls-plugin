# -*- coding: utf-8 -*-
"""Unit tests for stream URL building including stream-relay routing."""
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from e2core_loader import load

ffmpeg_service = load("ffmpeg_service")

WHITELISTED_REF = "1:0:19:115:2:85:C00000:0:0:0:"
PLAIN_REF = "1:0:19:283D:3FB:1:C00000:0:0:0:"


class FakeWhitelist(object):
    def __init__(self, refs):
        self._refs = {r.rstrip(":").upper() for r in refs}

    def contains(self, ref):
        return ref.strip().upper().rstrip(":") in self._refs


class FakeSettings(object):
    """Settings with stream-relay support (enigma2 platform shape)."""

    def __init__(self, relay_refs=()):
        self._whitelist = FakeWhitelist(relay_refs)

    def stream_port(self):
        return 8001

    def stream_hw_port(self):
        return 8002

    def streamrelay_port(self):
        return 17999

    def streamrelay_whitelist(self):
        return self._whitelist


class MinimalSettings(object):
    """Settings without relay support (e.g. another platform)."""

    def stream_port(self):
        return 8001

    def stream_hw_port(self):
        return 8002


class BuildStreamUrlTest(unittest.TestCase):
    def test_plain_ref_uses_stream_port(self):
        url = ffmpeg_service.build_stream_url({"ref": PLAIN_REF}, FakeSettings())
        self.assertEqual(url, "http://127.0.0.1:8001/" + PLAIN_REF)

    def test_hw_ref_uses_hw_port(self):
        url = ffmpeg_service.build_stream_url(
            {"ref": PLAIN_REF, "hw": True}, FakeSettings())
        self.assertEqual(url, "http://127.0.0.1:8002/" + PLAIN_REF)

    def test_whitelisted_ref_uses_relay_port(self):
        settings = FakeSettings(relay_refs=[WHITELISTED_REF])
        url = ffmpeg_service.build_stream_url({"ref": WHITELISTED_REF}, settings)
        self.assertEqual(url, "http://127.0.0.1:17999/" + WHITELISTED_REF)

    def test_relay_wins_over_hw_transcode(self):
        # An ICAM service is scrambled on the HW transcode port too.
        settings = FakeSettings(relay_refs=[WHITELISTED_REF])
        url = ffmpeg_service.build_stream_url(
            {"ref": WHITELISTED_REF, "hw": True}, settings)
        self.assertEqual(url, "http://127.0.0.1:17999/" + WHITELISTED_REF)

    def test_relay_match_is_normalized(self):
        settings = FakeSettings(relay_refs=[WHITELISTED_REF])
        lower_ref = WHITELISTED_REF.lower().rstrip(":")
        url = ffmpeg_service.build_stream_url({"ref": lower_ref}, settings)
        self.assertEqual(url, "http://127.0.0.1:17999/" + lower_ref)

    def test_non_whitelisted_ref_unaffected_by_relay(self):
        settings = FakeSettings(relay_refs=[WHITELISTED_REF])
        url = ffmpeg_service.build_stream_url({"ref": PLAIN_REF}, settings)
        self.assertEqual(url, "http://127.0.0.1:8001/" + PLAIN_REF)

    def test_settings_without_relay_support(self):
        url = ffmpeg_service.build_stream_url({"ref": PLAIN_REF}, MinimalSettings())
        self.assertEqual(url, "http://127.0.0.1:8001/" + PLAIN_REF)

    def test_mask_credentials_strips_userinfo(self):
        masked = ffmpeg_service.mask_credentials("http://root:secret@127.0.0.1:8001/ref")
        self.assertEqual(masked, "http://***@127.0.0.1:8001/ref")

    def test_mask_credentials_leaves_plain_url(self):
        url = "http://127.0.0.1:17999/" + PLAIN_REF
        self.assertEqual(ffmpeg_service.mask_credentials(url), url)

    def test_credentials_are_url_encoded(self):
        url = ffmpeg_service.build_stream_url(
            {"ref": PLAIN_REF, "user": "root", "password": "p@ss:w"},
            FakeSettings())
        self.assertTrue(url.startswith("http://root:"))
        self.assertIn("@127.0.0.1:8001/", url)
        self.assertNotIn("p@ss:w@127", url)  # raw '@'/':' must be quoted


class SpawnSettings(object):
    """Minimal settings for the spawn path (build_ffmpeg_cmd)."""

    def ffmpeg_bin(self):
        return "/usr/bin/ffmpeg"


class FakeProcess(object):
    pid = 4711

    def __init__(self, retcode=0):
        self._retcode = retcode

    def wait(self):
        return self._retcode


class RecordingLogger(object):
    def __init__(self):
        self.infos = []
        self.errors = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def error(self, message, **_kwargs):
        self.errors.append(message)


class AsyncStartFfmpegTest(unittest.TestCase):
    """Spawn-path tests with a stubbed subprocess.Popen (no real ffmpeg)."""

    def setUp(self):
        self.log_dir = tempfile.mkdtemp()
        self.ready = threading.Event()
        self.exited = threading.Event()
        self.got = {}

    def tearDown(self):
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def _on_ready(self, stream_id, process, ffmpeg_log):
        self.got["stream_id"] = stream_id
        self.got["process"] = process
        self.ready.set()

    def _on_exit(self, stream_id, retcode, ffmpeg_log):
        self.got["retcode"] = retcode
        self.exited.set()

    def _start(self, logger, popen):
        with mock.patch.object(ffmpeg_service.subprocess, "Popen", popen):
            ffmpeg_service.async_start_ffmpeg(
                "http://127.0.0.1:8001/ref", "/tmp/pipe", "sid1",
                self.log_dir, SpawnSettings(),
                on_ready=self._on_ready, on_exit=self._on_exit, logger=logger)
            self.assertTrue(self.ready.wait(timeout=3), "on_ready not called")

    def test_success_calls_on_ready_and_on_exit_and_logs_info(self):
        logger = RecordingLogger()
        process = FakeProcess(retcode=0)
        self._start(logger, mock.Mock(return_value=process))
        self.assertTrue(self.exited.wait(timeout=3), "on_exit not called")
        self.assertIs(self.got["process"], process)
        self.assertEqual(self.got["retcode"], 0)
        self.assertTrue(any("FFmpeg started" in msg for msg in logger.infos))
        self.assertEqual(logger.errors, [])

    def test_spawn_failure_reports_none_process_and_logs_error(self):
        logger = RecordingLogger()
        self._start(logger, mock.Mock(side_effect=OSError("no ffmpeg")))
        self.assertIsNone(self.got["process"])
        self.assertTrue(any("Error starting FFmpeg" in msg for msg in logger.errors))

    def test_logger_none_stays_silent_and_functional(self):
        self._start(None, mock.Mock(return_value=FakeProcess()))
        self.assertIsNotNone(self.got["process"])  # callbacks unaffected


if __name__ == "__main__":
    unittest.main()
