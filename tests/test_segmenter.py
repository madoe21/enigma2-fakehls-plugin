# -*- coding: utf-8 -*-
"""Unit tests for the Segmenter's file/playlist logic (no pipe, no thread run)."""
import os
import shutil
import tempfile
import unittest

from e2core_loader import load

stream_service = load("stream_service")


class FakeSettings(object):
    def __init__(self, hls_dir):
        self._hls_dir = hls_dir

    def hls_dir(self):
        return self._hls_dir

    def segment_duration(self):
        return 2

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


class SegmenterTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.logger = FakeLogger()
        self.segmenter = stream_service.Segmenter(
            "abcd1234", FakeSettings(self.tmp_dir), self.logger, seg_duration=2)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_segments(self, count, size=16 * 1024):
        for i in range(count):
            self.segmenter._write_segment(bytes(size), 2.0 + i * 0.1)

    def _playlist_content(self):
        with open(self.segmenter.playlist, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_segment_uri_is_relative(self):
        self.assertEqual(self.segmenter.segment_uri(7), "abcd1234_seg00007.ts")

    def test_pipe_path_is_unique_per_segmenter_life(self):
        # stream_id is a deterministic hash: a re-created stream must not
        # reuse the previous life's FIFO, or an orphan ffmpeg writes into it.
        other = stream_service.Segmenter(
            "abcd1234", FakeSettings(self.tmp_dir), self.logger, seg_duration=2)
        self.assertNotEqual(self.segmenter.pipe_path, other.pipe_path)
        self.assertIn("abcd1234_pipe", os.path.basename(self.segmenter.pipe_path))

    def test_write_segment_creates_file_and_playlist(self):
        self._write_segments(1)
        self.assertTrue(os.path.exists(self.segmenter.segment_path(0)))
        content = self._playlist_content()
        self.assertIn("#EXTM3U", content)
        self.assertIn("#EXTINF:2.000,", content)
        self.assertIn("abcd1234_seg00000.ts", content)
        self.assertNotIn("http://", content)

    def test_tiny_segment_is_skipped(self):
        self.segmenter._write_segment(bytes(1024), 2.0)
        self.assertEqual(self.segmenter.segments, [])
        self.assertFalse(os.path.exists(self.segmenter.playlist))

    def test_playlist_window_and_media_sequence(self):
        self._write_segments(5)
        content = self._playlist_content()
        # playlist_size = 3 -> newest three segments, sequence starts at 2
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:2", content)
        self.assertNotIn("abcd1234_seg00001.ts", content)
        self.assertIn("abcd1234_seg00002.ts", content)
        self.assertIn("abcd1234_seg00004.ts", content)

    def test_old_segment_files_are_deleted(self):
        self._write_segments(6)
        # keep = 2 * playlist_size = 6 newest files (RFC 8216 §6.2.2)
        # All 6 segments are kept since total == keep, so none are deleted.
        self.assertEqual(len(self.segmenter.segments), 6)
        for index in range(6):
            self.assertTrue(os.path.exists(self.segmenter.segment_path(index)))

    def test_segment_retention_exceeds_playlist_size(self):
        """RFC 8216 §6.2.2: keep segments beyond the playlist window."""
        self._write_segments(8)
        # keep = 2 * 3 = 6; segments 0,1 are cleaned, 2-7 remain
        self.assertFalse(os.path.exists(self.segmenter.segment_path(0)))
        self.assertFalse(os.path.exists(self.segmenter.segment_path(1)))
        for index in range(2, 8):
            self.assertTrue(os.path.exists(self.segmenter.segment_path(index)))
        self.assertEqual(len(self.segmenter.segments), 6)

    def test_run_exits_promptly_after_writer_exit_notification(self):
        # A known-dead ffmpeg means no writer will ever attach: run() must
        # return without re-opening the pipe or waiting for the cleanup timer.
        # Watchdog via thread join: a hang fails the assertion instead of
        # stalling the whole test run.
        self.segmenter.notify_writer_exited()
        self.segmenter.start()
        self.segmenter.join(timeout=2)
        self.assertFalse(self.segmenter.is_alive())

    def test_playlist_updates_are_not_logged_as_errors(self):
        self._write_segments(4)
        self.assertEqual(self.logger.errors, [])

    def test_target_duration_covers_longest_segment(self):
        self.segmenter._write_segment(bytes(16 * 1024), 2.0)
        self.segmenter._write_segment(bytes(16 * 1024), 3.4)
        self.assertIn("#EXT-X-TARGETDURATION:4", self._playlist_content())

    def test_target_duration_never_decreases(self):
        # RFC 8216 §6.2.1: TARGETDURATION must not change between reloads.
        self.segmenter._write_segment(bytes(16 * 1024), 3.4)
        for _ in range(5):
            self.segmenter._write_segment(bytes(16 * 1024), 2.0)
        self.assertIn("#EXT-X-TARGETDURATION:4", self._playlist_content())

    def test_cleanup_all_removes_everything(self):
        self._write_segments(3)
        self.segmenter.cleanup_all()
        self.assertEqual(self.segmenter.segments, [])
        self.assertFalse(os.path.exists(self.segmenter.playlist))
        leftovers = [n for n in os.listdir(self.tmp_dir) if n.endswith(".ts")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
