# -*- coding: utf-8 -*-
"""Unit tests for StreamService wiring (credentials provider, status)."""
import shutil
import tempfile
import time
import unittest

from e2core_loader import load

stream_service = load("stream_service")


class FakeSettings(object):
    def __init__(self, hls_dir):
        self._hls_dir = hls_dir

    def hls_dir(self):
        return self._hls_dir

    def stream_port(self):
        return 8001

    def cleanup_interval(self):
        return 10

    def playlist_size(self):
        return 3


class FakeLogger(object):
    def error(self, message, **_kwargs):
        pass

    def warning(self, message, **_kwargs):
        pass

    def info(self, message, **_kwargs):
        pass

    def debug(self, message, **_kwargs):
        pass


class FakeReactor(object):
    def callLater(self, _delay, _fn, *_args):
        return None


class FakeSegmenter(object):
    def __init__(self, segment_count):
        self.segments = [None] * segment_count


class StreamServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _service(self, provider=None):
        return stream_service.StreamService(
            settings=FakeSettings(self.tmp_dir),
            logger=FakeLogger(),
            ensure_hls_dir=lambda _d: True,
            reactor=FakeReactor(),
            credentials_provider=provider,
        )

    def test_credentials_come_from_injected_provider(self):
        service = self._service(provider=lambda: ("webif", "secret"))
        self.assertEqual(service._read_e2_credentials(), ("webif", "secret"))

    def test_credentials_default_without_provider(self):
        service = self._service(provider=None)
        self.assertEqual(service._read_e2_credentials(), ("root", ""))

    def test_credentials_default_when_provider_raises(self):
        def broken():
            raise IOError("settings file unreadable")

        service = self._service(provider=broken)
        self.assertEqual(service._read_e2_credentials(), ("root", ""))

    def test_get_status_reports_segment_count_without_listdir(self):
        service = self._service()
        service.streams["abcd1234"] = {
            "params": {"ref": "1:0:19:1:1:1:CCCC0000:0:0:0:"},
            "started": time.time(),
            "access_count": 2,
            "crash_count": 0,
            "segmenter": FakeSegmenter(segment_count=5),
        }
        status = service.get_status()
        self.assertEqual(status["abcd1234"]["segments"], 5)
        self.assertEqual(status["abcd1234"]["access_count"], 2)
        self.assertEqual(status["abcd1234"]["hls_url"], "/hls/live_abcd1234.m3u8")


if __name__ == "__main__":
    unittest.main()
