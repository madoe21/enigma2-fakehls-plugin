# -*- coding: utf-8 -*-
"""Unit tests for StreamService wiring (credentials provider, status)."""
import os
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
    def __init__(self):
        self.warnings = []

    def error(self, message, **_kwargs):
        pass

    def warning(self, message, **_kwargs):
        self.warnings.append(message)

    def info(self, message, **_kwargs):
        pass

    def debug(self, message, **_kwargs):
        pass

    def log_ffmpeg_exit(self, _stream_id, _retcode, _log_file=None):
        pass


class FakeReactor(object):
    def __init__(self):
        self.marshalled = []

    def callLater(self, _delay, _fn, *_args):
        return None

    def callFromThread(self, fn, *args):
        self.marshalled.append((fn, args))
        fn(*args)


class FakeSegmenter(object):
    def __init__(self, segment_count, pipe_path=None):
        self.segments = [None] * segment_count
        self.writer_exit_notified = False
        self.pipe_path = pipe_path

    def notify_writer_exited(self):
        self.writer_exit_notified = True


class StreamServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _service(self, provider=None):
        self.reactor = FakeReactor()
        return stream_service.StreamService(
            settings=FakeSettings(self.tmp_dir),
            logger=FakeLogger(),
            ensure_hls_dir=lambda _d: True,
            reactor=self.reactor,
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

    def test_marshal_routes_callback_through_reactor(self):
        service = self._service()
        calls = []
        wrapped = service._marshal(lambda *args: calls.append(args))
        wrapped("sid", 1)
        self.assertEqual(calls, [("sid", 1)])
        self.assertEqual(len(self.reactor.marshalled), 1)

    def test_ffmpeg_exit_notifies_segmenter_and_counts_crash(self):
        service = self._service()
        segmenter = FakeSegmenter(segment_count=0)
        service.streams["abcd1234"] = {
            "params": {"ref": "1:0:1"},
            "started": time.time(),
            "access_count": 1,
            "crash_count": 0,
            "process": object(),
            "segmenter": segmenter,
        }
        service._on_ffmpeg_exit("abcd1234", 1, None)
        self.assertTrue(segmenter.writer_exit_notified)
        self.assertEqual(service.streams["abcd1234"]["crash_count"], 1)

    def test_ffmpeg_exit_for_unknown_stream_is_ignored(self):
        service = self._service()
        service._on_ffmpeg_exit("gone", 1, None)  # must not raise

    def _stream_entry(self, segmenter, process=None):
        return {
            "params": {"ref": "1:0:1"},
            "started": time.time(),
            "access_count": 1,
            "crash_count": 0,
            "process": process,
            "segmenter": segmenter,
        }

    def test_stale_ffmpeg_exit_is_ignored_after_stream_id_reuse(self):
        # stream_id is a deterministic hash: a reaped stream can be
        # re-created before an old marshalled callback dequeues.
        service = self._service()
        old_segmenter = FakeSegmenter(segment_count=0)
        new_segmenter = FakeSegmenter(segment_count=0)
        service.streams["abcd1234"] = self._stream_entry(new_segmenter, process=object())
        service._on_ffmpeg_exit("abcd1234", 1, None, expected_segmenter=old_segmenter)
        self.assertFalse(new_segmenter.writer_exit_notified)
        self.assertEqual(service.streams["abcd1234"]["crash_count"], 0)

    def test_stale_ffmpeg_exit_removes_residue_pipe_file(self):
        # After remove_pipe(), the orphan ffmpeg's O_CREAT may have
        # re-created the old pipe path as a regular file full of TS.
        service = self._service()
        residue = os.path.join(self.tmp_dir, "abcd1234_pipe3")
        with open(residue, "wb") as handle:
            handle.write(b"ts-garbage")
        old_segmenter = FakeSegmenter(0, pipe_path=residue)
        service.streams["abcd1234"] = self._stream_entry(FakeSegmenter(0))
        service._on_ffmpeg_exit("abcd1234", 1, None,
                                expected_segmenter=old_segmenter)
        self.assertFalse(os.path.exists(residue))

    def test_residue_pipe_unlink_failure_logs_warning(self):
        service = self._service()
        residue = os.path.join(self.tmp_dir, "abcd1234_pipe5")
        with open(residue, "wb") as handle:
            handle.write(b"x")
        old_segmenter = FakeSegmenter(0, pipe_path=residue)
        original_unlink = stream_service.os.unlink

        def failing_unlink(_path):
            raise OSError("permission denied")

        stream_service.os.unlink = failing_unlink
        try:
            service._on_ffmpeg_exit("gone", 1, None,
                                    expected_segmenter=old_segmenter)
        finally:
            stream_service.os.unlink = original_unlink
        self.assertTrue(
            any("residue" in msg for msg in service.logger.warnings))

    def test_stale_ffmpeg_exit_without_residue_is_harmless(self):
        service = self._service()
        missing = os.path.join(self.tmp_dir, "abcd1234_pipe9")
        old_segmenter = FakeSegmenter(0, pipe_path=missing)
        # Stream gone entirely — must not raise, nothing to unlink.
        service._on_ffmpeg_exit("gone", 1, None,
                                expected_segmenter=old_segmenter)
        self.assertFalse(os.path.exists(missing))

    def test_stale_ffmpeg_spawn_terminates_orphan_process(self):
        class FakeProcess(object):
            def __init__(self):
                self.terminated = False

            def terminate(self):
                self.terminated = True

        service = self._service()
        orphan = FakeProcess()
        # Stream gone entirely — nobody else would ever reap this process.
        service._on_ffmpeg_spawned("gone", orphan, None,
                                   expected_segmenter=FakeSegmenter(0))
        self.assertTrue(orphan.terminated)

    def test_cleanup_old_session_files_removes_suffixed_pipes(self):
        service = self._service()
        leftover_pipe = os.path.join(self.tmp_dir, "abcd1234_pipe7")
        # Pre-upgrade runs left unsuffixed pipes behind; both forms must go.
        leftover_old_pipe = os.path.join(self.tmp_dir, "abcd1234_pipe")
        leftover_seg = os.path.join(self.tmp_dir, "abcd1234_seg00001.ts")
        # Crash between playlist tmp-write and rename leaves a .m3u8.tmp.
        leftover_tmp = os.path.join(self.tmp_dir, "live_abcd1234.m3u8.tmp")
        keeper = os.path.join(self.tmp_dir, "unrelated.txt")
        for path in (leftover_pipe, leftover_old_pipe, leftover_seg,
                     leftover_tmp, keeper):
            with open(path, "wb") as handle:
                handle.write(b"x")
        service.cleanup_old_session_files()
        self.assertFalse(os.path.exists(leftover_pipe))
        self.assertFalse(os.path.exists(leftover_old_pipe))
        self.assertFalse(os.path.exists(leftover_seg))
        self.assertFalse(os.path.exists(leftover_tmp))
        self.assertTrue(os.path.exists(keeper))

    def test_clean_ffmpeg_exit_notifies_without_crash_count(self):
        service = self._service()
        segmenter = FakeSegmenter(segment_count=0)
        service.streams["abcd1234"] = {
            "params": {"ref": "1:0:1"},
            "started": time.time(),
            "access_count": 1,
            "crash_count": 0,
            "process": object(),
            "segmenter": segmenter,
        }
        service._on_ffmpeg_exit("abcd1234", 0, None)
        self.assertTrue(segmenter.writer_exit_notified)
        self.assertEqual(service.streams["abcd1234"]["crash_count"], 0)


if __name__ == "__main__":
    unittest.main()
