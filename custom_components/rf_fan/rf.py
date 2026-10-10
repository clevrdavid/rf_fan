"""Broadlink RF learning helpers for the rf_fan integration.

Capture packets using the Broadlink two-step RF learn flow or direct capture,
then decode their pulse timings for storage and replay.
"""

from __future__ import annotations

import asyncio
from base64 import b64encode
import logging
import time
from typing import Any

from broadlink.exceptions import ReadError, StorageError

from homeassistant.core import HomeAssistant

from .const import BROADLINK_DOMAIN
from .packet import decode_broadlink_packet

_LOGGER = logging.getLogger(__name__)

# Matches the Broadlink remote's own learning behaviour.
LEARNING_TIMEOUT = 30.0  # seconds


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
            "rf_fan: captured %d pulses (packet byte 1=%d, ignored on resend)",
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
