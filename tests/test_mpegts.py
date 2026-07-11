# -*- coding: utf-8 -*-
"""Unit tests for the MPEG-TS inspection helpers."""
import unittest

from e2core_loader import load

mpegts = load("mpegts")

VIDEO_STREAM_ID = 0xE0
AUDIO_STREAM_ID = 0xC0


def make_packet(rai=False, pcr=None, sync=0x47, adaptation=True,
                pusi=False, stream_id=None):
    """Craft one 188-byte TS packet with the requested header fields."""
    packet = bytearray(mpegts.TS_PACKET_SIZE)
    packet[0] = sync
    packet[1] = 0x40 if pusi else 0x00
    packet[2] = 0x64
    packet[3] = 0x30 if adaptation else 0x10  # AF+payload vs payload only
    if adaptation:
        flags = (0x40 if rai else 0) | (0x10 if pcr is not None else 0)
        if pcr is not None:
            packet[4] = 7
            packet[5] = flags
            packet[6] = (pcr >> 25) & 0xFF
            packet[7] = (pcr >> 17) & 0xFF
            packet[8] = (pcr >> 9) & 0xFF
            packet[9] = (pcr >> 1) & 0xFF
            packet[10] = ((pcr & 1) << 7) | 0x7E
            packet[11] = 0
            payload_start = 12
        else:
            packet[4] = 1
            packet[5] = flags
            payload_start = 6
    else:
        payload_start = 4
    if stream_id is not None:
        packet[payload_start:payload_start + 4] = b"\x00\x00\x01" + bytes([stream_id])
    return bytes(packet)


def video_keyframe(pcr=None):
    """A video keyframe packet as ffmpeg's mpegts muxer emits it."""
    return make_packet(rai=True, pusi=True, stream_id=VIDEO_STREAM_ID, pcr=pcr)


def audio_key_packet():
    """An audio PES start with RAI set — every AAC frame looks like this."""
    return make_packet(rai=True, pusi=True, stream_id=AUDIO_STREAM_ID)


class IsRandomAccessTest(unittest.TestCase):
    def test_detects_rai_flag(self):
        self.assertTrue(mpegts.is_random_access(make_packet(rai=True)))

    def test_rejects_packet_without_rai(self):
        self.assertFalse(mpegts.is_random_access(make_packet(rai=False)))

    def test_rejects_bad_sync_byte(self):
        self.assertFalse(mpegts.is_random_access(make_packet(rai=True, sync=0x48)))

    def test_rejects_packet_without_adaptation_field(self):
        self.assertFalse(mpegts.is_random_access(make_packet(adaptation=False)))

    def test_rejects_empty_adaptation_field(self):
        packet = bytearray(make_packet(rai=True))
        packet[4] = 0  # AF present but zero-length
        self.assertFalse(mpegts.is_random_access(bytes(packet)))

    def test_rejects_short_data(self):
        self.assertFalse(mpegts.is_random_access(b"\x47\x00\x64"))


class IsVideoKeyframeTest(unittest.TestCase):
    def test_detects_video_keyframe(self):
        self.assertTrue(mpegts.is_video_keyframe(video_keyframe()))

    def test_detects_video_keyframe_with_pcr(self):
        self.assertTrue(mpegts.is_video_keyframe(video_keyframe(pcr=90000)))

    def test_rejects_audio_pes_start_with_rai(self):
        self.assertFalse(mpegts.is_video_keyframe(audio_key_packet()))

    def test_rejects_video_pes_start_without_rai(self):
        packet = make_packet(rai=False, pusi=True, stream_id=VIDEO_STREAM_ID)
        self.assertFalse(mpegts.is_video_keyframe(packet))

    def test_rejects_rai_without_pes_start(self):
        self.assertFalse(mpegts.is_video_keyframe(make_packet(rai=True)))

    def test_rejects_rai_without_pusi(self):
        packet = make_packet(rai=True, pusi=False, stream_id=VIDEO_STREAM_ID)
        self.assertFalse(mpegts.is_video_keyframe(packet))

    def test_accepts_full_video_stream_id_range(self):
        for stream_id in (0xE0, 0xE7, 0xEF):
            packet = make_packet(rai=True, pusi=True, stream_id=stream_id)
            self.assertTrue(mpegts.is_video_keyframe(packet))

    def test_rejects_non_video_stream_ids(self):
        for stream_id in (0xBD, 0xC0, 0xDF, 0xF0):
            packet = make_packet(rai=True, pusi=True, stream_id=stream_id)
            self.assertFalse(mpegts.is_video_keyframe(packet))


class ReadPcrBaseTest(unittest.TestCase):
    def test_round_trips_pcr_base(self):
        for value in (0, 1, 90000, (1 << 33) - 1):
            self.assertEqual(mpegts.read_pcr_base(make_packet(pcr=value)), value)

    def test_returns_none_without_pcr_flag(self):
        self.assertIsNone(mpegts.read_pcr_base(make_packet(rai=True)))

    def test_returns_none_without_adaptation_field(self):
        self.assertIsNone(mpegts.read_pcr_base(make_packet(adaptation=False)))

    def test_returns_none_for_bad_sync(self):
        self.assertIsNone(mpegts.read_pcr_base(make_packet(pcr=100, sync=0x00)))


class PcrDeltaSecondsTest(unittest.TestCase):
    def test_plain_delta(self):
        self.assertAlmostEqual(mpegts.pcr_delta_seconds(0, 180000), 2.0)

    def test_wraparound(self):
        start = (1 << 33) - 90000  # one second before the wrap
        self.assertAlmostEqual(mpegts.pcr_delta_seconds(start, 90000), 2.0)


class FindKeyframeCutTest(unittest.TestCase):
    def test_finds_first_video_keyframe_offset(self):
        data = make_packet() + make_packet() + video_keyframe() + make_packet()
        cut, scan = mpegts.find_keyframe_cut(bytearray(data), 0)
        self.assertEqual(cut, 2 * mpegts.TS_PACKET_SIZE)
        self.assertEqual(scan, cut)

    def test_skips_audio_rai_packets(self):
        data = audio_key_packet() + audio_key_packet() + video_keyframe()
        cut, _ = mpegts.find_keyframe_cut(bytearray(data), 0)
        self.assertEqual(cut, 2 * mpegts.TS_PACKET_SIZE)

    def test_respects_start_offset(self):
        data = video_keyframe() + make_packet() + video_keyframe()
        cut, _ = mpegts.find_keyframe_cut(bytearray(data), mpegts.TS_PACKET_SIZE)
        self.assertEqual(cut, 2 * mpegts.TS_PACKET_SIZE)

    def test_no_keyframe_advances_scan_position(self):
        data = make_packet() + audio_key_packet()
        cut, scan = mpegts.find_keyframe_cut(bytearray(data), 0)
        self.assertIsNone(cut)
        self.assertEqual(scan, 2 * mpegts.TS_PACKET_SIZE)

    def test_ignores_trailing_partial_packet(self):
        data = make_packet() + video_keyframe()[:100]
        cut, scan = mpegts.find_keyframe_cut(bytearray(data), 0)
        self.assertIsNone(cut)
        self.assertEqual(scan, mpegts.TS_PACKET_SIZE)

    def test_start_beyond_data_never_rewinds(self):
        data = video_keyframe()
        cut, scan = mpegts.find_keyframe_cut(bytearray(data[:100]), mpegts.TS_PACKET_SIZE)
        self.assertIsNone(cut)
        self.assertEqual(scan, mpegts.TS_PACKET_SIZE)


if __name__ == "__main__":
    unittest.main()
