# -*- coding: utf-8 -*-
"""Unit tests for the stream-relay whitelist handling."""
import os
import shutil
import tempfile
import unittest

from e2core_loader import load

streamrelay = load("streamrelay")

REF_A = "1:0:19:115:2:85:C00000:0:0:0:"
REF_B = "1:0:19:6E:D:85:C00000:0:0:0:"


class NormalizeServiceRefTest(unittest.TestCase):
    def test_strips_trailing_colon(self):
        self.assertEqual(
            streamrelay.normalize_service_ref(REF_A),
            streamrelay.normalize_service_ref(REF_A.rstrip(":")))

    def test_case_insensitive(self):
        self.assertEqual(
            streamrelay.normalize_service_ref("1:0:19:6e:d:85:c00000:0:0:0:"),
            streamrelay.normalize_service_ref(REF_B))

    def test_strips_whitespace(self):
        self.assertEqual(
            streamrelay.normalize_service_ref("  " + REF_A + "\r\n"),
            streamrelay.normalize_service_ref(REF_A))


class StreamRelayWhitelistTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp_dir, "whitelist_streamrelay")
        self.whitelist = streamrelay.StreamRelayWhitelist(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, content):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def test_missing_file_means_empty_whitelist(self):
        self.assertEqual(self.whitelist.refs(), frozenset())
        self.assertFalse(self.whitelist.contains(REF_A))

    def test_parses_refs_and_skips_comments_and_blanks(self):
        self._write(REF_A + "\n\n# comment\n" + REF_B + "\n")
        self.assertTrue(self.whitelist.contains(REF_A))
        self.assertTrue(self.whitelist.contains(REF_B))
        self.assertEqual(len(self.whitelist.refs()), 2)

    def test_contains_matches_normalized_variants(self):
        self._write(REF_A + "\n")
        self.assertTrue(self.whitelist.contains(REF_A.rstrip(":").lower()))

    def test_reloads_when_file_changes(self):
        self._write(REF_A + "\n")
        self.assertTrue(self.whitelist.contains(REF_A))
        self._write(REF_B + "\n")
        # force a different mtime — some filesystems have coarse resolution
        stat = os.stat(self.path)
        os.utime(self.path, (stat.st_atime, stat.st_mtime + 5))
        self.assertFalse(self.whitelist.contains(REF_A))
        self.assertTrue(self.whitelist.contains(REF_B))

    def test_cached_between_reads_without_change(self):
        self._write(REF_A + "\n")
        first = self.whitelist.refs()
        self.assertIs(self.whitelist.refs(), first)

    def test_deleted_file_empties_whitelist(self):
        self._write(REF_A + "\n")
        self.assertTrue(self.whitelist.contains(REF_A))
        os.unlink(self.path)
        self.assertFalse(self.whitelist.contains(REF_A))

    def test_reload_after_recreation(self):
        self._write(REF_A + "\n")
        self.whitelist.refs()
        os.unlink(self.path)
        self.whitelist.refs()
        self._write(REF_B + "\n")
        self.assertTrue(self.whitelist.contains(REF_B))

    def test_unreadable_file_keeps_previous_refs_and_retries(self):
        self._write(REF_A + "\n")
        self.assertTrue(self.whitelist.contains(REF_A))
        # replace the file with a directory: os.stat succeeds, open() fails
        os.unlink(self.path)
        os.mkdir(self.path)
        self.assertTrue(self.whitelist.contains(REF_A))  # old refs kept
        # once readable again, the next call picks it up (no cached miss)
        os.rmdir(self.path)
        self._write(REF_B + "\n")
        self.assertTrue(self.whitelist.contains(REF_B))
        self.assertFalse(self.whitelist.contains(REF_A))


if __name__ == "__main__":
    unittest.main()
