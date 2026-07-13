# -*- coding: utf-8 -*-
"""Unit tests for the SegmentCutter state machine (no pipe, no threads)."""
import unittest

from e2core_loader import load
from test_mpegts import audio_key_packet, make_packet, video_keyframe

stream_service = load("stream_service")
mpegts = load("mpegts")

PKT = mpegts.TS_PACKET_SIZE
SEG_DURATION = 2


class FakeClock(object):
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeLogger(object):
    def __init__(self):
        self.warnings = []

    def warning(self, message, **_kwargs):
        self.warnings.append(message)


def filler(count=1):
    return make_packet() * count


class SegmentCutterSyncTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cutter = stream_service.SegmentCutter(SEG_DURATION, clock=self.clock)

    def _first_segment_after(self, next_keyframe):
        self.clock.advance(SEG_DURATION + 0.5)
        return self.cutter.feed(next_keyframe + filler())

    def test_drops_data_before_first_video_keyframe(self):
        keyframe = video_keyframe(pcr=0)
        self.assertEqual(self.cutter.feed(filler(3) + keyframe + filler()), [])
        segments = self._first_segment_after(video_keyframe(pcr=225000))
        self.assertEqual(len(segments), 1)
        data, _duration, _is_discontinuity = segments[0]
        self.assertEqual(data[:PKT], keyframe)

    def test_audio_rai_packets_do_not_sync(self):
        # Every AAC frame carries RAI; syncing on one restores the mid-GOP
        # artifact this change removes.
        keyframe = video_keyframe(pcr=0)
        self.assertEqual(self.cutter.feed(audio_key_packet() * 5), [])
        self.assertEqual(self.cutter.feed(audio_key_packet() + keyframe), [])
        segments = self._first_segment_after(video_keyframe(pcr=225000))
        self.assertEqual(segments[0][0][:PKT], keyframe)

    def test_sync_gives_up_without_keyframes(self):
        logger = FakeLogger()
        cutter = stream_service.SegmentCutter(SEG_DURATION, clock=self.clock, logger=logger)
        self.assertEqual(cutter.feed(filler(2)), [])
        self.clock.advance(SEG_DURATION * cutter.KEYFRAME_WAIT_FACTOR + 0.1)
        self.assertEqual(cutter.feed(filler(2)), [])  # give-up happens here
        self.assertEqual(len(logger.warnings), 1)
        # After give-up: forced packet-boundary cuts keep the stream alive.
        self.clock.advance(SEG_DURATION * cutter.KEYFRAME_WAIT_FACTOR + 0.1)
        segments = cutter.feed(filler(2))
        self.assertEqual(len(segments), 1)

    def test_give_up_clock_starts_at_first_byte(self):
        # ffmpeg spends seconds on connect/probe before the first byte; that
        # wait must not consume the keyframe-search window.
        self.clock.advance(SEG_DURATION * 10)
        keyframe = video_keyframe(pcr=0)
        self.assertEqual(self.cutter.feed(filler() + keyframe), [])
        segments = self._first_segment_after(video_keyframe(pcr=225000))
        self.assertEqual(segments[0][0][:PKT], keyframe)


class SegmentCutterCutTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cutter = stream_service.SegmentCutter(SEG_DURATION, clock=self.clock)
        self.first_keyframe = video_keyframe(pcr=0)
        self.cutter.feed(filler() + self.first_keyframe + filler())

    def test_keyframe_before_target_duration_is_not_a_cut(self):
        # Cutting at the first keyframe in the buffer instead of the first
        # one after the target duration shrinks segments to one GOP and
        # grows the buffer without bound.
        early_keyframe = video_keyframe(pcr=90000)
        self.clock.advance(1.0)
        self.assertEqual(self.cutter.feed(early_keyframe + filler()), [])

        self.clock.advance(1.5)
        cut_keyframe = video_keyframe(pcr=225000)
        segments = self.cutter.feed(filler() + cut_keyframe + filler())
        self.assertEqual(len(segments), 1)
        data, duration, is_discontinuity = segments[0]
        self.assertEqual(data[:PKT], self.first_keyframe)
        self.assertIn(early_keyframe, data)  # retained, not cut at
        self.assertAlmostEqual(duration, 2.5)  # PCR media time, not wall time
        self.assertFalse(is_discontinuity)

    def test_next_segment_starts_at_cut_keyframe(self):
        self.clock.advance(2.1)
        cut_keyframe = video_keyframe(pcr=189000)
        self.cutter.feed(cut_keyframe + filler())
        self.clock.advance(2.1)
        segments = self.cutter.feed(video_keyframe(pcr=378000))
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0][0][:PKT], cut_keyframe)
        self.assertAlmostEqual(segments[0][1], 2.1)

    def test_duration_falls_back_to_wall_clock_without_pcr(self):
        self.clock.advance(2.5)
        segments = self.cutter.feed(video_keyframe())  # no PCR on this one
        self.assertEqual(len(segments), 1)
        self.assertAlmostEqual(segments[0][1], 2.5)

    def test_duration_falls_back_on_pcr_discontinuity(self):
        self.clock.advance(2.5)
        # 100 s PCR jump on a 2 s target -> wall clock wins, and the segment
        # starting right after the jump must be flagged: it splices two
        # unrelated timelines together (e.g. ffmpeg's -reconnect firing
        # mid-stream on a flaky source) with nothing else to say so.
        segments = self.cutter.feed(video_keyframe(pcr=9000000))
        self.assertAlmostEqual(segments[0][1], 2.5)
        self.assertTrue(segments[0][2])

    def test_forced_cut_after_wait_factor_without_keyframes(self):
        self.clock.advance(SEG_DURATION * self.cutter.KEYFRAME_WAIT_FACTOR + 0.1)
        segments = self.cutter.feed(filler(3))
        self.assertEqual(len(segments), 1)
        self.assertAlmostEqual(segments[0][1],
                               SEG_DURATION * self.cutter.KEYFRAME_WAIT_FACTOR + 0.1)

    def test_flush_returns_remainder_with_wall_duration(self):
        self.clock.advance(1.3)
        self.cutter.feed(filler())
        segments = self.cutter.flush()
        self.assertEqual(len(segments), 1)
        data, duration, is_discontinuity = segments[0]
        self.assertEqual(data[:PKT], self.first_keyframe)
        self.assertAlmostEqual(duration, 1.3)
        self.assertFalse(is_discontinuity)
        self.assertEqual(self.cutter.flush(), [])  # buffer is gone now

    def test_flush_on_empty_buffer(self):
        cutter = stream_service.SegmentCutter(SEG_DURATION, clock=self.clock)
        self.assertEqual(cutter.flush(), [])


if __name__ == "__main__":
    unittest.main()
