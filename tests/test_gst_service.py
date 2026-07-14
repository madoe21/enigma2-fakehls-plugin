# -*- coding: utf-8 -*-
"""Unit tests for gst_service: URL building (same contract as the old
ffmpeg_service - see test_ffmpeg_service.py's history), the GstPipelineHandle
process-facade, branch routing, and the async_start_gst spawn contract.

Real pipeline construction/execution needs a live GStreamer runtime that
does not exist on this dev machine - gst_service.py already accounts for
that (Gst/GLib import failure leaves them None, see its top-of-file
comment), so these tests stay at the same boundary
test_ffmpeg_service.py used for subprocess.Popen: mock _PipelineRunner
itself for the spawn-contract tests, and mock the module-level Gst name
for GstPipelineHandle's calls into it. Real pipeline behaviour is
verified on-device instead (see the migration plan).
"""
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from e2core_loader import load

gst_service = load("gst_service")

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
    # Same assertions as ffmpeg_service's BuildStreamUrlTest -
    # build_stream_url is a verbatim, engine-agnostic copy.
    def test_plain_ref_uses_stream_port(self):
        url = gst_service.build_stream_url({"ref": PLAIN_REF}, FakeSettings())
        self.assertEqual(url, "http://127.0.0.1:8001/" + PLAIN_REF)

    def test_hw_ref_uses_hw_port(self):
        url = gst_service.build_stream_url(
            {"ref": PLAIN_REF, "hw": True}, FakeSettings())
        self.assertEqual(url, "http://127.0.0.1:8002/" + PLAIN_REF)

    def test_whitelisted_ref_uses_relay_port(self):
        settings = FakeSettings(relay_refs=[WHITELISTED_REF])
        url = gst_service.build_stream_url({"ref": WHITELISTED_REF}, settings)
        self.assertEqual(url, "http://127.0.0.1:17999/" + WHITELISTED_REF)

    def test_relay_wins_over_hw_transcode(self):
        settings = FakeSettings(relay_refs=[WHITELISTED_REF])
        url = gst_service.build_stream_url(
            {"ref": WHITELISTED_REF, "hw": True}, settings)
        self.assertEqual(url, "http://127.0.0.1:17999/" + WHITELISTED_REF)

    def test_relay_match_is_normalized(self):
        settings = FakeSettings(relay_refs=[WHITELISTED_REF])
        lower_ref = WHITELISTED_REF.lower().rstrip(":")
        url = gst_service.build_stream_url({"ref": lower_ref}, settings)
        self.assertEqual(url, "http://127.0.0.1:17999/" + lower_ref)

    def test_non_whitelisted_ref_unaffected_by_relay(self):
        settings = FakeSettings(relay_refs=[WHITELISTED_REF])
        url = gst_service.build_stream_url({"ref": PLAIN_REF}, settings)
        self.assertEqual(url, "http://127.0.0.1:8001/" + PLAIN_REF)

    def test_settings_without_relay_support(self):
        url = gst_service.build_stream_url({"ref": PLAIN_REF}, MinimalSettings())
        self.assertEqual(url, "http://127.0.0.1:8001/" + PLAIN_REF)

    def test_credentials_are_url_encoded(self):
        url = gst_service.build_stream_url(
            {"ref": PLAIN_REF, "user": "root", "password": "p@ss:w"},
            FakeSettings())
        self.assertTrue(url.startswith("http://root:"))
        self.assertIn("@127.0.0.1:8001/", url)
        self.assertNotIn("p@ss:w@127", url)  # raw '@'/':' must be quoted


class MaskCredentialsTest(unittest.TestCase):
    def test_strips_userinfo(self):
        masked = gst_service.mask_credentials("http://root:secret@127.0.0.1:8001/ref")
        self.assertEqual(masked, "http://***@127.0.0.1:8001/ref")

    def test_leaves_plain_url(self):
        url = "http://127.0.0.1:17999/" + PLAIN_REF
        self.assertEqual(gst_service.mask_credentials(url), url)


class UsesStreamRelayTest(unittest.TestCase):
    def test_true_for_whitelisted_ref(self):
        settings = FakeSettings(relay_refs=[WHITELISTED_REF])
        self.assertTrue(gst_service.uses_stream_relay(WHITELISTED_REF, settings))

    def test_false_for_plain_ref(self):
        settings = FakeSettings(relay_refs=[WHITELISTED_REF])
        self.assertFalse(gst_service.uses_stream_relay(PLAIN_REF, settings))

    def test_false_without_relay_support(self):
        self.assertFalse(gst_service.uses_stream_relay(PLAIN_REF, MinimalSettings()))


class ResolveHwStreamUrlTest(unittest.TestCase):
    """resolve_hw_stream_url() - OpenWebif session-token resolution."""

    def _urlopen_returning(self, body):
        response = mock.MagicMock()
        response.read.return_value = body.encode("utf-8")
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return mock.Mock(return_value=response)

    def test_extracts_url_line_from_m3u_body(self):
        body = ("#EXTM3U \n#EXTVLCOPT:http-reconnect=true \n"
                 "http://-sid:abc123@127.0.0.1:8002/" + PLAIN_REF + "\n")
        with mock.patch.object(gst_service.urllib.request, "urlopen",
                                self._urlopen_returning(body)):
            url = gst_service.resolve_hw_stream_url(PLAIN_REF, FakeSettings())
        self.assertEqual(url, "http://-sid:abc123@127.0.0.1:8002/" + PLAIN_REF)

    def test_raises_when_no_url_in_response(self):
        with mock.patch.object(gst_service.urllib.request, "urlopen",
                                self._urlopen_returning("Missing file parameter")):
            with self.assertRaises(RuntimeError):
                gst_service.resolve_hw_stream_url(PLAIN_REF, FakeSettings())

    def test_propagates_connection_failure(self):
        with mock.patch.object(gst_service.urllib.request, "urlopen",
                                mock.Mock(side_effect=OSError("connection refused"))):
            with self.assertRaises(OSError):
                gst_service.resolve_hw_stream_url(PLAIN_REF, FakeSettings())


class BranchKindForStructureNameTest(unittest.TestCase):
    def test_video_h264(self):
        self.assertEqual(gst_service._branch_kind_for_structure_name("video/x-h264"), "video")

    def test_video_h265(self):
        self.assertEqual(gst_service._branch_kind_for_structure_name("video/x-h265"), "video")

    def test_audio_ac3(self):
        self.assertEqual(gst_service._branch_kind_for_structure_name("audio/x-ac3"), "audio")

    def test_audio_eac3(self):
        self.assertEqual(gst_service._branch_kind_for_structure_name("audio/x-eac3"), "audio")

    def test_audio_mpeg(self):
        self.assertEqual(gst_service._branch_kind_for_structure_name("audio/mpeg"), "audio")

    def test_subtitle_or_data_pid_ignored(self):
        self.assertIsNone(gst_service._branch_kind_for_structure_name("subpicture/x-dvb"))
        self.assertIsNone(gst_service._branch_kind_for_structure_name("private/x-teletext"))

    def test_none_caps_name(self):
        self.assertIsNone(gst_service._branch_kind_for_structure_name(None))


class GstPipelineHandleTest(unittest.TestCase):
    """GstPipelineHandle's subprocess.Popen-facade behaviour.

    Wraps a _PipelineRunner (not a raw Gst.Pipeline - the runner swaps in
    a new pipeline on every reconnect attempt, see the runner's own
    docstring), so terminate()/kill() just delegate to the runner's
    request_stop() and finishing is driven by the runner's on_exit
    callback - covered end-to-end in AsyncStartGstTest, not here.
    """

    def test_poll_returns_none_while_running(self):
        handle = gst_service.GstPipelineHandle(mock.MagicMock())
        self.assertIsNone(handle.poll())

    def test_pipeline_property_delegates_to_runner(self):
        runner = mock.MagicMock()
        runner.pipeline = mock.sentinel.current_pipeline
        handle = gst_service.GstPipelineHandle(runner)
        self.assertIs(handle.pipeline, mock.sentinel.current_pipeline)

    def test_terminate_delegates_to_runner_request_stop(self):
        runner = mock.MagicMock()
        handle = gst_service.GstPipelineHandle(runner)
        handle.terminate()
        runner.request_stop.assert_called_once()
        # terminate() itself does not decide the outcome - that's the
        # runner's on_exit callback's job (see AsyncStartGstTest).
        self.assertIsNone(handle.poll())

    def test_kill_behaves_like_terminate(self):
        runner = mock.MagicMock()
        handle = gst_service.GstPipelineHandle(runner)
        handle.kill()
        runner.request_stop.assert_called_once()

    def test_wait_blocks_until_mark_finished(self):
        handle = gst_service.GstPipelineHandle(mock.MagicMock())
        results = []

        def waiter():
            results.append(handle.wait(timeout=3))

        thread = threading.Thread(target=waiter)
        thread.start()
        handle._mark_finished(7)
        thread.join(timeout=3)
        self.assertEqual(results, [7])

    def test_wait_raises_when_not_finished_in_time(self):
        handle = gst_service.GstPipelineHandle(mock.MagicMock())
        with self.assertRaises(TimeoutError):
            handle.wait(timeout=0.05)

    def test_mark_finished_first_writer_wins(self):
        # An explicit terminate() (returncode=0) racing a bus ERROR
        # (returncode=1) that landed first must not clobber the real
        # error code with "we asked it to stop".
        handle = gst_service.GstPipelineHandle(mock.MagicMock())
        handle._mark_finished(1)
        handle._mark_finished(0)
        self.assertEqual(handle.poll(), 1)

    def test_pid_is_unique_per_handle(self):
        first = gst_service.GstPipelineHandle(mock.MagicMock())
        second = gst_service.GstPipelineHandle(mock.MagicMock())
        self.assertNotEqual(first.pid, second.pid)


class RecordingLogger(object):
    def __init__(self):
        self.infos = []
        self.errors = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def error(self, message, **_kwargs):
        self.errors.append(message)


class AsyncStartGstTest(unittest.TestCase):
    """Spawn-contract tests with _PipelineRunner stubbed out entirely -
    same boundary test_ffmpeg_service.py used for subprocess.Popen. Real
    pipeline construction/execution is covered by on-device testing
    instead (see the migration plan)."""

    def setUp(self):
        self.log_dir = tempfile.mkdtemp()
        self.ready = threading.Event()
        self.exited = threading.Event()
        self.got = {}

    def tearDown(self):
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def _on_ready(self, stream_id, handle, log_path):
        self.got["stream_id"] = stream_id
        self.got["handle"] = handle
        self.ready.set()

    def _on_exit(self, stream_id, returncode, log_path):
        self.got["returncode"] = returncode
        self.exited.set()

    def test_success_calls_on_ready_with_handle(self):
        fake_runner = mock.MagicMock()
        fake_runner.pipeline = mock.MagicMock()
        fake_runner.run.side_effect = lambda: None  # returns immediately, no EOS/ERROR

        with mock.patch.object(gst_service, "_PipelineRunner", return_value=fake_runner):
            gst_service.async_start_gst(
                "http://127.0.0.1:8001/ref", "/tmp/pipe", "sid1", self.log_dir,
                FakeSettings(), on_ready=self._on_ready, on_exit=self._on_exit)
            self.assertTrue(self.ready.wait(timeout=3), "on_ready not called")

        self.assertEqual(self.got["stream_id"], "sid1")
        self.assertIsInstance(self.got["handle"], gst_service.GstPipelineHandle)
        self.assertIs(self.got["handle"].pipeline, fake_runner.pipeline)

    def test_build_failure_reports_none_handle_and_logs_error(self):
        fake_runner = mock.MagicMock()
        fake_runner.prepare.side_effect = RuntimeError("no such element")
        logger = RecordingLogger()

        with mock.patch.object(gst_service, "_PipelineRunner", return_value=fake_runner):
            gst_service.async_start_gst(
                "http://127.0.0.1:8001/ref", "/tmp/pipe", "sid1", self.log_dir,
                FakeSettings(), on_ready=self._on_ready, on_exit=self._on_exit, logger=logger)
            self.assertTrue(self.ready.wait(timeout=3), "on_ready not called")

        self.assertIsNone(self.got["handle"])
        self.assertTrue(any("Error starting GStreamer pipeline" in msg for msg in logger.errors))

    def test_on_exit_marks_handle_finished(self):
        fake_runner = mock.MagicMock()
        fake_runner.pipeline = mock.MagicMock()

        def fake_run():
            # Simulate the bus firing EOS: _PipelineRunner invokes
            # whatever on_exit callback async_start_gst wired onto it.
            fake_runner._on_exit("sid1", 0, "log")

        fake_runner.run.side_effect = fake_run

        with mock.patch.object(gst_service, "_PipelineRunner", return_value=fake_runner):
            gst_service.async_start_gst(
                "http://127.0.0.1:8001/ref", "/tmp/pipe", "sid1", self.log_dir,
                FakeSettings(), on_ready=self._on_ready, on_exit=self._on_exit)
            self.assertTrue(self.ready.wait(timeout=3), "on_ready not called")
            self.assertTrue(self.exited.wait(timeout=3), "on_exit not called")

        self.assertEqual(self.got["returncode"], 0)
        self.assertEqual(self.got["handle"].poll(), 0)

    def test_hw_ref_resolves_url_before_building(self):
        fake_runner = mock.MagicMock()
        fake_runner.pipeline = mock.MagicMock()
        resolved = "http://-sid:tok123@127.0.0.1:8002/ref"
        captured = {}

        def capture_init(stream_url, *args, **kwargs):
            captured["stream_url"] = stream_url
            return fake_runner

        with mock.patch.object(gst_service, "resolve_hw_stream_url",
                                return_value=resolved) as resolve:
            with mock.patch.object(gst_service, "_PipelineRunner", side_effect=capture_init):
                gst_service.async_start_gst(
                    "http://127.0.0.1:8002/ref", "/tmp/pipe", "sid1", self.log_dir,
                    FakeSettings(), on_ready=self._on_ready, on_exit=self._on_exit,
                    hw_ref="ref", e2_user="root", e2_pass="secret")
                self.assertTrue(self.ready.wait(timeout=3), "on_ready not called")

        resolve.assert_called_once_with("ref", mock.ANY, e2_user="root", e2_pass="secret")
        self.assertEqual(captured["stream_url"], resolved)

    def test_hw_ref_resolution_failure_reports_none_handle_and_never_builds(self):
        logger = RecordingLogger()
        with mock.patch.object(gst_service, "resolve_hw_stream_url",
                                side_effect=RuntimeError("no transcoder")):
            with mock.patch.object(gst_service, "_PipelineRunner") as runner_cls:
                gst_service.async_start_gst(
                    "http://127.0.0.1:8002/ref", "/tmp/pipe", "sid1", self.log_dir,
                    FakeSettings(), on_ready=self._on_ready, on_exit=self._on_exit,
                    logger=logger, hw_ref="ref")
                self.assertTrue(self.ready.wait(timeout=3), "on_ready not called")

        self.assertIsNone(self.got["handle"])
        runner_cls.assert_not_called()
        self.assertTrue(any("could not resolve hardware-transcode URL" in msg
                             for msg in logger.errors))


class PipelineRunnerReconnectTest(unittest.TestCase):
    """_PipelineRunner.run()'s reconnect-with-backoff state machine - the
    behaviour this migration added after confirming on-device that the
    receiver's internal stream server needs it (see the module's
    _RECONNECT_* constants and their comment). _run_one_attempt is
    stubbed here - real Gst pipeline execution needs a live runtime and
    is exercised on-device instead (see the migration plan)."""

    def setUp(self):
        self.log_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def _runner(self):
        import os
        return gst_service._PipelineRunner(
            "http://127.0.0.1:8001/ref", "/tmp/pipe", "sid1",
            os.path.join(self.log_dir, "sid1.log"), None, None, None, on_exit=None)

    def test_gives_up_immediately_if_stop_requested_before_first_attempt(self):
        runner = self._runner()
        runner._stop_requested.set()
        results = []
        runner._on_exit = lambda sid, rc, log: results.append(rc)

        with mock.patch.object(runner, "_run_one_attempt") as run_once:
            runner.run()

        run_once.assert_not_called()
        self.assertEqual(results, [0])

    def test_retries_with_doubling_backoff_on_no_data_failures(self):
        runner = self._runner()
        attempts = []

        def fake_attempt():
            attempts.append(1)
            runner._attempt_returncode = 1  # e.g. the on-device 400-then-close
            if len(attempts) >= 3:
                runner._stop_requested.set()

        results = []
        runner._on_exit = lambda sid, rc, log: results.append(rc)

        with mock.patch.object(runner, "_run_one_attempt", side_effect=fake_attempt):
            with mock.patch.object(runner._stop_requested, "wait") as wait_mock:
                runner.run()

        self.assertEqual(len(attempts), 3)
        # Event.wait(), not time.sleep(): a stop request landing mid-backoff
        # must interrupt the wait immediately (see run()'s comment).
        delays = [call.args[0] for call in wait_mock.call_args_list]
        self.assertEqual(delays, [
            gst_service._RECONNECT_INITIAL_DELAY_SECONDS,
            gst_service._RECONNECT_INITIAL_DELAY_SECONDS * 2,
        ])
        # Loop ended because request_stop() landed, not because the
        # connection kept failing - report a clean stop, not an error.
        self.assertEqual(results, [0])

    def test_reports_failure_once_real_data_had_flowed(self):
        runner = self._runner()

        def fake_attempt():
            runner._data_seen = True  # tsdemux found a real elementary stream
            runner._attempt_returncode = 1  # then a bus ERROR mid-stream

        results = []
        runner._on_exit = lambda sid, rc, log: results.append(rc)

        with mock.patch.object(runner, "_run_one_attempt", side_effect=fake_attempt):
            with mock.patch.object(runner._stop_requested, "wait") as wait_mock:
                runner.run()

        wait_mock.assert_not_called()  # no retry once real data had flowed
        self.assertEqual(results, [1])

    def test_exception_during_attempt_is_treated_as_a_failed_attempt(self):
        runner = self._runner()
        call_count = {"n": 0}

        def fake_attempt():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("element link failed")
            runner._stop_requested.set()

        results = []
        runner._on_exit = lambda sid, rc, log: results.append(rc)

        with mock.patch.object(runner, "_run_one_attempt", side_effect=fake_attempt):
            with mock.patch.object(runner._stop_requested, "wait"):
                runner.run()

        self.assertEqual(call_count["n"], 2)
        self.assertEqual(results, [0])

    def test_stop_during_backoff_wait_is_not_delayed_by_a_full_sleep(self):
        # The bug this test guards against: request_stop() must interrupt
        # an in-progress backoff wait immediately, not leave the pipeline
        # thread (and the tuner it's holding) unreachable for up to
        # _RECONNECT_MAX_DELAY_SECONDS - this is what made a fast channel
        # switch feel like it "didn't kill the old stream properly".
        runner = self._runner()

        def fake_attempt():
            runner._attempt_returncode = 1  # no data - triggers a backoff wait

        results = []
        runner._on_exit = lambda sid, rc, log: results.append(rc)

        with mock.patch.object(runner, "_run_one_attempt", side_effect=fake_attempt):
            # Real Event.wait() here (not mocked) - request_stop() sets the
            # event from this test's thread while run() is genuinely
            # blocked inside it, on the runner's own thread.
            thread = threading.Thread(target=runner.run)
            thread.start()
            # Give run() a moment to reach the backoff wait.
            time.sleep(0.1)
            start = time.monotonic()
            runner.request_stop()
            thread.join(timeout=2)
            elapsed = time.monotonic() - start

        self.assertFalse(thread.is_alive(), "run() did not exit promptly")
        self.assertLess(elapsed, 1.0,
                         "stop took as long as a full backoff delay - Event.wait() regressed to time.sleep()")
        self.assertEqual(results, [0])

    def test_request_stop_before_first_attempt_race_is_not_started(self):
        # request_stop() landing between prepare() and the pipeline
        # actually being built must not start a doomed attempt - see
        # _run_one_attempt's own guard. Exercised directly here since it
        # needs a real (if minimal) Gst/GLib stand-in.
        runner = self._runner()
        fake_pipeline = mock.MagicMock()
        fake_bus = mock.MagicMock()
        fake_pipeline.get_bus.return_value = fake_bus

        def build_and_stop():
            runner._stop_requested.set()
            return fake_pipeline

        with mock.patch.object(gst_service, "Gst", mock.MagicMock()) as mock_gst:
            with mock.patch.object(gst_service, "GLib", mock.MagicMock()):
                with mock.patch.object(runner, "_build_pipeline", side_effect=build_and_stop):
                    with mock.patch.object(runner, "_open_output_fd"):
                        runner._run_one_attempt()

        fake_pipeline.set_state.assert_called_once_with(mock_gst.State.NULL)


if __name__ == "__main__":
    unittest.main()
