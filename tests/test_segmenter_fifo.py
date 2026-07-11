# -*- coding: utf-8 -*-
"""Integration tests for the Segmenter against a real POSIX FIFO.

These exercise the O_NONBLOCK open, EOF-before/after-data handling and the
stop()/notify_writer_exited() wakeups that unit tests cannot reach. They
run in the Linux CI job and skip on Windows (no os.mkfifo).
"""
import os
import shutil
import tempfile
import threading
import time
import unittest

from e2core_loader import load
from test_mpegts import make_packet, video_keyframe

stream_service = load("stream_service")


class FakeSettings(object):
    def __init__(self, hls_dir):
        self._hls_dir = hls_dir

    def hls_dir(self):
        return self._hls_dir

    def segment_duration(self):
        return 1

    def playlist_size(self):
        return 3


class FakeLogger(object):
    def __init__(self):
        self.errors = []

    def error(self, message, **_kwargs):
        self.errors.append(message)

    def warning(self, message, **_kwargs):
        pass

    def info(self, message, **_kwargs):
        pass

    def debug(self, message, **_kwargs):
        pass


@unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFOs (Linux CI)")
class SegmenterFifoTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.logger = FakeLogger()
        self.segmenter = stream_service.Segmenter(
            "fifo1234", FakeSettings(self.tmp_dir), self.logger, seg_duration=1)

    def tearDown(self):
        self.segmenter.stop()
        if self.segmenter.is_alive():
            self.segmenter.join(timeout=3)
        self.segmenter.cleanup_all()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_stop_wakes_thread_when_no_writer_ever_attaches(self):
        # The bad-URL/auth-failure case: ffmpeg never opens the writer end.
        self.segmenter.create_pipe()
        self.segmenter.start()
        self.segmenter.stop()
        self.segmenter.join(timeout=3)
        self.assertFalse(self.segmenter.is_alive())

    def test_writer_exit_notification_wakes_waiting_thread(self):
        # ffmpeg died before ever attaching: notify must end the wait too.
        self.segmenter.create_pipe()
        self.segmenter.start()
        self.segmenter.notify_writer_exited()
        self.segmenter.join(timeout=3)
        self.assertFalse(self.segmenter.is_alive())

    def test_stream_through_fifo_produces_segment_and_exits_on_eof(self):
        self.segmenter.create_pipe()
        self.segmenter.start()

        def write_stream():
            # Keyframe first so the cutter syncs, then >8 KB of filler so
            # the EOF flush clears the minimum segment size.
            with open(self.segmenter.pipe_path, "wb") as writer:
                writer.write(video_keyframe(pcr=0))
                writer.write(make_packet() * 60)

        # Daemon: if the segmenter never opened the read end, a blocking
        # open("wb") must not hang the interpreter at exit.
        writer_thread = threading.Thread(target=write_stream, daemon=True)
        writer_thread.start()
        writer_thread.join(timeout=5)
        self.assertFalse(writer_thread.is_alive())

        # EOF after data: the segmenter flushes the remainder as a segment.
        # It then re-opens the pipe awaiting a writer, so signal exit.
        for _ in range(100):  # up to ~5 s
            if self.segmenter.segments:
                break
            time.sleep(0.05)
        self.assertTrue(self.segmenter.segments, "no segment written from FIFO data")
        self.assertTrue(os.path.exists(self.segmenter.playlist))
        self.assertEqual(self.logger.errors, [])

        self.segmenter.notify_writer_exited()
        self.segmenter.join(timeout=3)
        self.assertFalse(self.segmenter.is_alive())


if __name__ == "__main__":
    unittest.main()
