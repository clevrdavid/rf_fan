"""Core RF helpers for the rf_fan integration.

Decodes Broadlink RF pulse packets into raw OOK timings - the inverse of
``homeassistant.components.broadlink.radio_frequency.encode_rf_packet`` - and
drives the Broadlink two-step RF learn flow. Together these let a code captured
by a Broadlink be replayed through the generic ``radio_frequency`` platform.
"""

from __future__ import annotations

import asyncio
from base64 import b64encode
import logging
import time
from typing import Any

from broadlink.exceptions import ReadError, StorageError

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import BROADLINK_DOMAIN

_LOGGER = logging.getLogger(__name__)

# Broadlink RF front-end timing resolution. Must match encode_rf_packet().
_TICK_US = 32.84

# Matches the Broadlink remote's own learning behaviour.
LEARNING_TIMEOUT = 30.0  # seconds


def decode_broadlink_packet(packet: bytes) -> tuple[list[int], int]:
    """Decode a Broadlink pulse packet into signed alternating microseconds.

    Packet layout (see encode_rf_packet):
        byte 0       type byte (0xB2/0xD7 433 MHz, 0xB4 315 MHz) - ignored here
        byte 1       repeat count
        bytes 2..3   payload length, little-endian, counted from byte 4
        bytes 4..    pulses: one byte per pulse, or 0x00 followed by a two-byte
                     big-endian tick count for pulses of 256 ticks or more

    Returns ``(timings, repeat_count)``. Even indices are marks (positive us),
    odd indices are spaces (negative us) - the format OOKCommand expects. The
    leading type byte does not affect pulse decoding, so this is robust to the
    small differences between learned and re-encoded packets.
    """
    repeat = packet[1]
    length = packet[2] | (packet[3] << 8)
    pulses = packet[4 : 4 + length]

    timings: list[int] = []
    i = 0
    while i < len(pulses):
        if pulses[i] == 0x00:
            ticks = (pulses[i + 1] << 8) | pulses[i + 2]
            i += 3
        else:
            ticks = pulses[i]
            i += 1
        microseconds = round(ticks * _TICK_US)
        timings.append(microseconds if len(timings) % 2 == 0 else -microseconds)
    return timings, repeat


def find_rf_devices(hass: HomeAssistant) -> dict[str, Any]:
    """Return ``{mac_address: BroadlinkDevice}`` for RF-capable Broadlink units.

    Only devices whose API exposes ``sweep_frequency`` (e.g. RM Pro / RM4 Pro)
    can learn RF.
    """
    data = hass.data.get(BROADLINK_DOMAIN)
    devices = getattr(data, "devices", None)
    if not devices:
        return {}
    return {
        device.mac_address: device
        for device in devices.values()
        if hasattr(getattr(device, "api", None), "sweep_frequency")
    }


def find_rf_device_for_transmitter(hass: HomeAssistant, entity_id: str) -> Any | None:
    """Return the BroadlinkDevice that owns a radio_frequency entity.

    Learning must sweep the same unit that the chosen transmitter entity sends
    through. Returns None when the entity cannot be resolved to an RF-capable
    Broadlink device.
    """
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or entity.config_entry_id is None:
        return None
    config_entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    if config_entry is None:
        return None
    mac = str(config_entry.data.get("mac", "")).replace(":", "").lower()
    if not mac:
        return None
    for device_mac, device in find_rf_devices(hass).items():
        if str(device_mac).replace(":", "").lower() == mac:
            return device
    return None


async def async_sweep_frequency(device: Any) -> float:
    """Phase 1 of RF learning: sweep for the carrier while the user holds.

    Returns the detected frequency (MHz). Raises ``TimeoutError`` if nothing is
    found within the learning window.
    """
    api = device.api
    await device.async_request(api.sweep_frequency)
    _LOGGER.debug("rf_fan: sweeping - PRESS AND HOLD the remote button now")
    deadline = time.monotonic() + LEARNING_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(1)
        is_found, frequency = await device.async_request(api.check_frequency)
        if is_found:
            _LOGGER.debug("rf_fan: detected RF at ~%s MHz", frequency)
            return frequency
    await device.async_request(api.cancel_sweep_frequency)
    raise TimeoutError("No RF frequency detected - hold the button during the sweep")


async def async_capture_packet(
    device: Any, frequency: float | None = None
) -> dict[str, Any]:
    """Capture an RF packet - phase 2 of the sweep flow, or a direct capture.

    If ``frequency`` (in MHz) is given, the device listens at that frequency
    directly with no sweep - just one button press. This handles remotes the
    frequency sweep can't lock onto (short-burst remotes like the Mercator
    FRM97). Returns ``{"b64", "timings", "repeat", "length"}``. Raises
    ``TimeoutError``.
    """
    api = device.api
    await device.async_request(api.find_rf_packet, frequency)
    if frequency:
        _LOGGER.debug(
            "rf_fan: listening at %.3f MHz - PRESS the button once", frequency
        )
    else:
        _LOGGER.debug("rf_fan: locked on - RELEASE, then PRESS the same button again")
    deadline = time.monotonic() + LEARNING_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(1)
        try:
            code = await device.async_request(api.check_data)
        except (ReadError, StorageError):
            continue  # nothing captured yet, keep polling
        timings, repeat = decode_broadlink_packet(code)
        _LOGGER.debug(
            "rf_fan: captured %d pulses (raw repeat byte=%d, ignored on resend)",
            len(timings),
            repeat,
        )
        return {
            "b64": b64encode(code).decode("utf8"),
            "timings": timings,
            "repeat": repeat,
            "length": len(timings),
        }
    raise TimeoutError("No RF code received - press the button after the sweep")


async def async_capture_rf(device: Any) -> dict[str, Any]:
    """Full single-shot capture (sweep then packet) - used by the debug service."""
    await async_sweep_frequency(device)
    await asyncio.sleep(1)
    return await async_capture_packet(device)
