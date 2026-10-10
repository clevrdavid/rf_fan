"""Broadlink RF pulse-packet decoding and native RM4 Pro encoding."""

from __future__ import annotations

# Match the tick used by Home Assistant's Broadlink RF encoder. Older supported
# HA releases use broadlink 0.19, which has no TICK and encodes at 32.84 us.
try:
    from broadlink.remote import TICK as _TICK_US
except ImportError:
    _TICK_US = 32.84

RM4_RF_TYPE = 0xB1


def decode_broadlink_packet(packet: bytes) -> tuple[list[int], int]:
    """Return signed pulse timings and packet byte 1.

    The payload length at bytes 2..3 counts from byte 4. Legacy packets start
    their timings there; RM4 Pro 0xB1 packets have a four-byte carrier in kHz
    first, so their timings start at byte 8. Byte 1 is a repeat count only in
    legacy packets: an RM4 capture carries flags (0xC0), not 192 repeats.
    """
    offset = 8 if packet[0] == RM4_RF_TYPE else 4
    length = int.from_bytes(packet[2:4], "little")
    end = 4 + length
    if len(packet) < offset or end < offset or end > len(packet):
        raise ValueError("Incomplete Broadlink RF packet")
    pulses = packet[offset:end]
    timings: list[int] = []
    i = 0
    while i < len(pulses):
        if pulses[i] == 0x00:
            if i + 2 >= len(pulses):
                raise ValueError("Incomplete Broadlink pulse tick count")
            ticks = (pulses[i + 1] << 8) | pulses[i + 2]
            i += 3
        else:
            ticks = pulses[i]
            i += 1
        microseconds = round(ticks * _TICK_US)
        timings.append(microseconds if len(timings) % 2 == 0 else -microseconds)
    return timings, packet[1]


def encode_rm4_packet(
    captured_packet: bytes, timings: list[int], *, repeat_count: int = 0
) -> bytes:
    """Rebuild native RF timings, retaining the RM4's flags and learned carrier.

    Unlike a legacy 0xB2 packet, byte 1 must remain the capture's 0xC0 flags.
    Repeat the pulse train in the payload instead of overwriting those flags.
    ``repeat_count`` counts additional transmissions after the first.
    """
    if len(captured_packet) < 8 or captured_packet[0] != RM4_RF_TYPE:
        raise ValueError("A native RM4 RF capture is required")
    payload = bytearray()
    for duration in timings:
        ticks = round(abs(duration) / _TICK_US)
        if ticks >= 256:
            payload.append(0)
            payload.extend(ticks.to_bytes(2, "big"))
        else:
            payload.append(ticks)
    payload *= repeat_count + 1
    packet = bytearray(captured_packet[:8])
    packet[2:4] = (4 + len(payload)).to_bytes(2, "little")
    packet.extend(payload)
    return bytes(packet)
