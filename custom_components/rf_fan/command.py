"""Build and send learned RF codes through the radio_frequency platform."""

from __future__ import annotations

from base64 import b64decode
import logging
from typing import Any

from broadlink.exceptions import BroadlinkException
from broadlink.remote import rm4pro
from rf_protocols import ModulationType, RadioFrequencyCommand

from homeassistant.components.radio_frequency import async_send_command
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import BROADLINK_DOMAIN
from .mercator import clean_frame
from .packet import RM4_RF_TYPE, decode_broadlink_packet, encode_rm4_packet

_LOGGER = logging.getLogger(__name__)


class CapturedCommand(RadioFrequencyCommand):
    """A learned RF code, replayed as raw OOK timings.

    We subclass the stable, top-level ``rf_protocols.RadioFrequencyCommand`` (the
    same import Home Assistant core uses) and set the attributes the transmitter
    reads directly, rather than importing the library's internal command modules
    - their layout differs between rf_protocols releases (``commands`` is a
    module in the shipped version, a package on ``main``). The Broadlink
    transmitter only consumes ``frequency``, ``repeat_count`` and
    ``get_raw_timings()``; ``modulation`` gates the transmitter-support check.
    We deliberately skip ``super().__init__`` to stay immune to constructor
    changes across releases.
    """

    def __init__(
        self, *, frequency: int, timings: list[int], repeat_count: int = 0
    ) -> None:
        """Initialise from decoded raw timings."""
        self.frequency = frequency
        self.modulation = ModulationType.OOK
        self.repeat_count = repeat_count
        self.symbol_rate = None
        self.output_power = None
        self._timings = timings

    def get_raw_timings(self) -> list[int]:
        """Return the signed alternating microsecond timings."""
        return self._timings


# Drop leading/trailing gaps longer than this (microseconds). Direct captures can
# include tens of milliseconds (up to seconds) of idle before/after the real code;
# transmitting that desyncs some receivers - notably the Mercator FRM97 - and it
# isn't part of the signal. Real inter-frame gaps are well under this.
_IDLE_TRIM_US = 20000


def _trim_idle(timings: list[int]) -> list[int]:
    """Drop huge leading/trailing idle gaps from a captured pulse train."""
    ts = [int(t) for t in timings]
    while ts and abs(ts[0]) > _IDLE_TRIM_US:
        ts.pop(0)
    while ts and abs(ts[-1]) > _IDLE_TRIM_US:
        ts.pop()
    return ts


def _stored_timings(data: dict[str, Any]) -> list[int]:
    """Return a stored command's pulse train in microseconds at today's tick.

    The raw Broadlink packet (``b64``) is the capture's ground truth: it holds
    tick counts, and the transmitter re-quantises our microseconds back to
    ticks. ``timings`` were decoded with whatever tick was in force when the
    button was learned, and that tick changed in Home Assistant 2026.10 (32.84
    -> 30.45 us), so a capture learned before the upgrade would otherwise be
    replayed about 8% too slow. Re-decoding the packet keeps replays exact
    without re-learning. ``timings`` remain the fallback for entries without a
    packet.
    """
    if b64 := data.get("b64"):
        try:
            timings, _ = decode_broadlink_packet(b64decode(b64))
        except (ValueError, IndexError):
            _LOGGER.debug("rf_fan: stored packet undecodable, using stored timings")
        else:
            if timings:
                return timings
    return [int(t) for t in data["timings"]]


async def async_send_stored(
    hass: HomeAssistant,
    transmitter: str,
    data: dict[str, Any],
    frequency: int,
    *,
    clean: bool = False,
    repeat: int = 0,
) -> None:
    """Send learned RF timings through the selected transmitter.

    Direct captures use one consensus frame with its measured gap, repeated
    ``repeat`` additional times. Native RM4 Pro captures retain their carrier
    and flags and use HA's managed Broadlink connection: the core RF encoder
    only builds legacy packets, which discard that native carrier field.
    Other transmitters continue through the radio_frequency platform.
    """
    raw = _stored_timings(data)
    if clean:
        timings = clean_frame(raw) or _trim_idle(raw)
    else:
        timings = _trim_idle(raw)
    _LOGGER.debug(
        "rf_fan: sending %d pulses via %s (clean=%s, repeat=%d, trailing gap=%d us)",
        len(timings),
        transmitter,
        clean,
        repeat,
        timings[-1] if timings else 0,
    )
    try:
        packet = b64decode(data.get("b64", ""))
    except ValueError:
        packet = b""
    if packet and packet[0] == RM4_RF_TYPE:
        registry = er.async_get(hass)
        transmitter = er.async_validate_entity_id(registry, transmitter)
        entry = registry.async_get(transmitter)
        if entry is not None and entry.platform == BROADLINK_DOMAIN:
            devices = getattr(hass.data.get(BROADLINK_DOMAIN), "devices", {})
            device = devices.get(entry.config_entry_id)
            state = hass.states.get(transmitter)
            if (
                device is None
                or not device.available
                or state is None
                or state.state == STATE_UNAVAILABLE
            ):
                raise HomeAssistantError(f"RF transmitter '{transmitter}' is unavailable")
            if isinstance(device.api, rm4pro):
                native_packet = encode_rm4_packet(packet, timings, repeat_count=repeat)
                _LOGGER.debug(
                    "rf_fan: sending native RM4 RF packet (%d bytes, carrier=%d kHz, flags=0x%02x)",
                    len(native_packet),
                    int.from_bytes(native_packet[4:8], "little"),
                    native_packet[1],
                )
                try:
                    await device.async_request(device.api.send_data, native_packet)
                except (BroadlinkException, OSError) as err:
                    raise HomeAssistantError(
                        f"Native RM4 RF transmission failed: {err}"
                    ) from err
                return

    command = CapturedCommand(
        frequency=frequency, timings=timings, repeat_count=repeat
    )
    await async_send_command(hass, transmitter, command)
