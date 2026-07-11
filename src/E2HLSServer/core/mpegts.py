# -*- coding: utf-8 -*-
"""Minimal MPEG transport stream inspection for HLS segmentation.

Only what the segmenter needs: detecting keyframe packets (via the
adaptation-field random_access_indicator, which ffmpeg's mpegts muxer sets
on every video keyframe) and reading the PCR base clock so segment
durations can come from media time instead of wall time.
"""
from __future__ import absolute_import

TS_PACKET_SIZE = 188
TS_SYNC_BYTE = 0x47

# PCR base is a 33-bit counter in 90 kHz ticks; it wraps every ~26.5 h.
PCR_CLOCK_HZ = 90000
PCR_BASE_WRAP = 1 << 33

_HAS_ADAPTATION_FIELD = 0x2
_RANDOM_ACCESS_FLAG = 0x40
_PCR_FLAG = 0x10
_MIN_PCR_AF_LENGTH = 7
_PUSI_FLAG = 0x40
_PES_START_PREFIX = b"\x00\x00\x01"
# PES stream_id range assigned to video elementary streams (ISO 13818-1).
_VIDEO_STREAM_ID_MIN = 0xE0
_VIDEO_STREAM_ID_MAX = 0xEF


def is_random_access(packet):
    """Return True if the packet's adaptation field marks a random access point."""
    if len(packet) < 6 or packet[0] != TS_SYNC_BYTE:
        return False
    adaptation_ctrl = (packet[3] >> 4) & 0x3
    if not adaptation_ctrl & _HAS_ADAPTATION_FIELD:
        return False
    if packet[4] < 1:  # adaptation field present but empty
        return False
    return bool(packet[5] & _RANDOM_ACCESS_FLAG)


def read_pcr_base(packet):
    """Return the packet's 33-bit PCR base (90 kHz ticks), or None if absent."""
    if len(packet) < 12 or packet[0] != TS_SYNC_BYTE:
        return None
    adaptation_ctrl = (packet[3] >> 4) & 0x3
    if not adaptation_ctrl & _HAS_ADAPTATION_FIELD:
        return None
    if packet[4] < _MIN_PCR_AF_LENGTH or not packet[5] & _PCR_FLAG:
        return None
    return (
        (packet[6] << 25)
        | (packet[7] << 17)
        | (packet[8] << 9)
        | (packet[9] << 1)
        | (packet[10] >> 7)
    )


def pcr_delta_seconds(start_pcr, end_pcr):
    """Media-time span between two PCR base values, handling the 33-bit wrap."""
    delta = end_pcr - start_pcr
    if delta < 0:
        delta += PCR_BASE_WRAP
    return delta / float(PCR_CLOCK_HZ)


def is_video_keyframe(packet):
    """True if the packet is an RAI-marked start of a *video* PES packet.

    RAI alone is not enough: ffmpeg's mpegts muxer sets it on every packet
    flagged as a key packet, and every audio frame is one — with the audio
    track transcoded to AAC that is an RAI every ~21 ms on the audio PID.
    Requiring a video PES start code keeps cuts on real video keyframes.
    """
    if not is_random_access(packet):
        return False
    if not packet[1] & _PUSI_FLAG:
        return False
    payload_start = 5 + packet[4]  # header (4) + AF length byte + AF body
    if payload_start + 4 > len(packet):
        return False
    if bytes(packet[payload_start:payload_start + 3]) != _PES_START_PREFIX:
        return False
    stream_id = packet[payload_start + 3]
    return _VIDEO_STREAM_ID_MIN <= stream_id <= _VIDEO_STREAM_ID_MAX


def find_keyframe_cut(buffer_data, start):
    """Find the first video keyframe packet at/after packet-aligned ``start``.

    Returns ``(cut_offset, next_scan_pos)``. ``cut_offset`` is None when no
    video keyframe is present in the complete packets scanned;
    ``next_scan_pos`` is where a later call should resume so no packet is
    scanned twice.
    """
    end = len(buffer_data) - (len(buffer_data) % TS_PACKET_SIZE)
    pos = start
    # memoryview: per-packet slices without copying 188 bytes each.
    with memoryview(buffer_data) as view:
        while pos < end:
            if is_video_keyframe(view[pos:pos + TS_PACKET_SIZE]):
                return pos, pos
            pos += TS_PACKET_SIZE
    return None, max(start, end)
