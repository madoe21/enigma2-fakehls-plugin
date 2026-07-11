# -*- coding: utf-8 -*-
"""Unit tests for PluginLogger: persistent handle, rotation, write recovery."""
import importlib.util
import os
import shutil
import tempfile
import unittest

# Load logger.py standalone: importing the E2HLSServer package would pull
# in enigma2-only modules that do not exist off the receiver.
_LOGGER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src", "E2HLSServer", "logger.py",
)
_spec = importlib.util.spec_from_file_location("e2logger", _LOGGER_PATH)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
PluginLogger = _module.PluginLogger


class PluginLoggerTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.log_dir = os.path.join(self.tmp_dir, "logs")
        self.logger = PluginLogger(name="Test", log_dir=self.log_dir)

    def tearDown(self):
        self.logger.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _log_contents(self):
        with open(self.logger.log_file, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_handle_stays_open_between_writes(self):
        self.logger.info("first")
        handle = self.logger._handle
        self.logger.info("second")
        self.assertIs(self.logger._handle, handle)
        content = self._log_contents()
        self.assertIn("first", content)
        self.assertIn("second", content)

    def test_rotation_when_size_cap_exceeded(self):
        self.logger.MAX_LOG_BYTES = 1024
        self.logger.info("x" * 2048)
        # The oversized write triggers rotation on the NEXT log call.
        self.logger.info("after rotation")
        self.assertTrue(os.path.exists(self.logger.log_file + ".1"))
        self.assertIn("x" * 2048, open(self.logger.log_file + ".1", encoding="utf-8").read())
        content = self._log_contents()
        self.assertIn("after rotation", content)
        self.assertNotIn("x" * 2048, content)

    def test_write_recovers_after_stale_handle(self):
        # Simulate a handle gone stale (e.g. rotated away underneath us).
        self.logger._handle.close()
        self.logger.info("recovered")
        self.assertIn("recovered", self._log_contents())

    def test_write_never_raises_when_log_unwritable(self):
        class BrokenHandle(object):
            def write(self, _data):
                raise IOError("disk full")

            def tell(self):
                return 0

            def close(self):
                pass

        self.logger._handle = BrokenHandle()
        # Force reopen to fail as well: point the logger at a bad path.
        self.logger.log_file = os.path.join(self.tmp_dir, "no_dir", "x.log")
        try:
            self.logger.info("must not raise")
        except Exception as exc:
            self.fail("logging raised: " + str(exc))

    def test_ffmpeg_exit_reads_bounded_tail_of_ffmpeg_log(self):
        ffmpeg_log = os.path.join(self.tmp_dir, "ffmpeg.log")
        with open(ffmpeg_log, "w", encoding="utf-8") as handle:
            for index in range(1000):
                handle.write("line %d\n" % index)
        self.logger.log_ffmpeg_exit("abcd1234", 1, ffmpeg_log)
        content = self._log_contents()
        # Only the last 5 lines of the ffmpeg log are kept.
        self.assertIn("line 999", content)
        self.assertIn("line 995", content)
        self.assertNotIn("line 994", content)

    def test_error_outside_except_block_has_no_traceback_noise(self):
        self.logger.error("plain failure")
        content = self._log_contents()
        self.assertIn("plain failure", content)
        self.assertNotIn("NoneType: None", content)

    def test_error_inside_except_block_includes_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError:
            self.logger.error("caught failure")
        content = self._log_contents()
        self.assertIn("caught failure", content)
        self.assertIn("ValueError: boom", content)

    def test_debug_suppressed_without_debug_mode(self):
        self.logger.debug("hidden")
        self.assertNotIn("hidden", self._log_contents())

    def test_debug_written_in_debug_mode(self):
        debug_logger = PluginLogger(name="Dbg", log_dir=self.log_dir, debug_mode=True)
        try:
            debug_logger.debug("visible")
            with open(debug_logger.log_file, "r", encoding="utf-8") as handle:
                self.assertIn("visible", handle.read())
        finally:
            debug_logger.close()


if __name__ == "__main__":
    unittest.main()
