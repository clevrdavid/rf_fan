"""Config and options flows for rf_fan.

Setup is split into two screens:
  Screen 1 – device name, primary entity type, RF transmitter & frequency.
  Screen 2 – entity-specific options (speeds for fan, stop button for cover, etc.).
Then the wizard walks through learning each button.

The options flow (Configure) lets you re-learn existing buttons or add new
sub-entities (fan / cover / light / button) to an existing device.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .command import async_send_stored
from .const import (
    CLEAN_REPEAT,
    CMD_CLOSE,
    CMD_LIGHT,
    CMD_OFF,
    CMD_ON,
    CMD_OPEN,
    CMD_STOP,
    CONF_COMMANDS,
    CONF_CUSTOM_BUTTONS,
    CONF_CUSTOMS,
    CONF_DIRECT,
    CONF_ENTITY_TYPE,
    CONF_FREQUENCY,
    CONF_HAS_ON,
    CONF_HAS_STOP,
    CONF_LIGHT_MODE,
    CONF_NAME,
    CONF_SPEED_COUNT,
    CONF_SUB_ENTITIES,
    CONF_TRANSMITTER,
    DEFAULT_FREQUENCY,
    DOMAIN,
    ENTITY_TYPE_BUTTON,
    ENTITY_TYPE_COVER,
    ENTITY_TYPE_FAN,
    ENTITY_TYPE_LIGHT,
    LIGHT_MODE_ON_OFF,
    LIGHT_MODE_TOGGLE,
)
from .rf import async_capture_packet, async_sweep_frequency, find_rf_devices

_LOGGER = logging.getLogger(__name__)

FREQ_OPTIONS = [
    selector.SelectOptionDict(value=str(DEFAULT_FREQUENCY), label="433.92 MHz"),
    selector.SelectOptionDict(value="315000000", label="315 MHz"),
    selector.SelectOptionDict(value="868000000", label="868 MHz"),
    selector.SelectOptionDict(value="915000000", label="915 MHz"),
]

# Plain option values - the labels come from translations/<lang>.json under
# "selector" -> <translation_key> -> "options".
ENTITY_TYPE_OPTIONS = [
    ENTITY_TYPE_FAN,
    ENTITY_TYPE_COVER,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_BUTTON,
]

LIGHT_MODE_OPTIONS = [
    LIGHT_MODE_TOGGLE,
    LIGHT_MODE_ON_OFF,
]

# Learn-screen button labels. These land in description_placeholders, which Home
# Assistant does not translate, so pick the table from hass.config.language.
_LABELS_EN: dict[str, str] = {
    CMD_OFF: "Off",
    CMD_ON: "On",
    CMD_LIGHT: "Light toggle",
    CMD_OPEN: "Open",
    CMD_CLOSE: "Close",
    CMD_STOP: "Stop",
    "speed": "Speed {number} of {total}",
    "speed_lowest": " (lowest)",
    "speed_highest": " (highest)",
}

_LABELS_ES: dict[str, str] = {
    CMD_OFF: "Apagar",
    CMD_ON: "Encender",
    CMD_LIGHT: "Luz",
    CMD_OPEN: "Abrir",
    CMD_CLOSE: "Cerrar",
    CMD_STOP: "Stop",
    "speed": "Velocidad {number} de {total}",
    "speed_lowest": " (mínima)",
    "speed_highest": " (máxima)",
}


def _labels_for(hass: Any) -> dict[str, str]:
    """Return the label table matching the Home Assistant UI language."""
    language = getattr(getattr(hass, "config", None), "language", "") or ""
    return _LABELS_ES if language.lower().startswith("es") else _LABELS_EN


async def _delayed_capture(device: Any) -> dict[str, Any]:
    """Brief settle, then capture the packet (sweep flow, phase 2)."""
    await asyncio.sleep(1)
    return await async_capture_packet(device)


def _unique_custom_key(name: str, taken: set[str]) -> str:
    """Build a unique custom_<slug> key for a button name."""
    base = f"custom_{slugify(name)}" if slugify(name) else "custom"
    key = base
    suffix = 2
    while key in taken:
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def _parse_custom_buttons(raw: Any, taken: set[str]) -> list[dict[str, str]]:
    """Parse a comma/newline separated list of button names into custom entries.

    Names are stripped, empties dropped and duplicates removed case-insensitively
    while preserving the order they were typed in.
    """
    names: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"[,\r\n]+", str(raw or "")):
        name = chunk.strip()
        if not name:
            continue
        lowered = name.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        names.append(name)

    used = set(taken)
    customs: list[dict[str, str]] = []
    for name in names:
        key = _unique_custom_key(name, used)
        used.add(key)
        customs.append({"key": key, "name": name})
    return customs


def _sub_key_prefix(name: str, taken: set[str]) -> str:
    """Build a unique sub-entity key prefix from the entity name."""
    base = slugify(name) or "entity"
    key = base
    suffix = 2
    while key in taken:
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def _build_queue(
    entity_type: str,
    *,
    speed_count: int = 0,
    has_on: bool = False,
    has_stop: bool = False,
    light_mode: str = LIGHT_MODE_TOGGLE,
    customs: list[dict] = [],
    key_prefix: str = "",
) -> list[str]:
    """Return the ordered list of command keys to learn for an entity."""

    def pk(cmd: str) -> str:
        return f"{key_prefix}_{cmd}" if key_prefix else cmd

    queue: list[str] = []
    if entity_type == ENTITY_TYPE_FAN:
        queue.append(pk(CMD_OFF))
        queue += [pk(f"speed_{i}") for i in range(1, speed_count + 1)]
        if has_on:
            queue.append(pk(CMD_ON))
    elif entity_type == ENTITY_TYPE_COVER:
        queue.append(pk(CMD_OPEN))
        queue.append(pk(CMD_CLOSE))
        if has_stop:
            queue.append(pk(CMD_STOP))
    elif entity_type == ENTITY_TYPE_LIGHT:
        if light_mode == LIGHT_MODE_TOGGLE:
            queue.append(pk(CMD_LIGHT))
        else:
            queue.append(pk(CMD_ON))
            queue.append(pk(CMD_OFF))
    elif entity_type == ENTITY_TYPE_BUTTON and key_prefix:
        # A button sub-entity is a single press stored under its bare prefix -
        # that is the key button.py looks it up by.
        queue.append(key_prefix)
    # A primary ENTITY_TYPE_BUTTON entity has no key of its own; it is made up
    # entirely of the custom buttons appended below.
    queue += [c["key"] for c in customs]
    return queue


class _LearnFlowMixin:
    """Shared two-phase learning steps for both the config and options flows."""

    _device: Any
    _current_key: str | None
    _task: Any
    _pending: dict[str, Any] | None
    _learn_error: str | None
    _commands: dict[str, Any]
    _transmitter: str
    _frequency: int
    _custom_names: dict[str, str]
    _speed_count: int
    _direct: bool
    _entity_type: str

    def _label(self, key: str | None) -> str:
        """Human-friendly label for a command key."""
        if key is None:
            return ""
        labels = _labels_for(getattr(self, "hass", None))
        # A button sub-entity is learned under its bare key_prefix - show its name.
        for sub_entity in getattr(self, "_sub_entities", []):
            if sub_entity.get("key_prefix") == key:
                return sub_entity.get("name") or key
        # Strip key_prefix if present
        bare = key.split("_", 1)[1] if "_" in key and not key.startswith("speed_") and not key.startswith("custom_") else key
        if bare == CMD_OFF or key == CMD_OFF:
            return labels[CMD_OFF]
        if bare == CMD_ON or key == CMD_ON:
            return labels[CMD_ON]
        if bare == CMD_LIGHT or key == CMD_LIGHT:
            return labels[CMD_LIGHT]
        if bare == CMD_OPEN or key == CMD_OPEN:
            return labels[CMD_OPEN]
        if bare == CMD_CLOSE or key == CMD_CLOSE:
            return labels[CMD_CLOSE]
        if bare == CMD_STOP or key == CMD_STOP:
            return labels[CMD_STOP]
        if key.startswith("speed_") or bare.startswith("speed_"):
            raw = key if key.startswith("speed_") else bare
            number = raw.removeprefix("speed_")
            total = self._speed_count
            hint = ""
            if number == "1":
                hint = labels["speed_lowest"]
            elif number == str(total):
                hint = labels["speed_highest"]
            return labels["speed"].format(number=number, total=total) + hint
        return self._custom_names.get(key, key)

    def _reset_learn_state(self) -> None:
        """Reset per-button learning state."""
        self._task = None
        self._pending = None
        self._learn_error = None

    async def _async_after_confirm(self) -> ConfigFlowResult:
        """Called after a code is kept. Implemented by the concrete flow."""
        raise NotImplementedError

    async def async_step_learn(self, user_input=None) -> ConfigFlowResult:
        """Learn a button - either direct capture or the two-phase sweep."""
        label = self._label(self._current_key)

        if self._direct:
            # Direct capture: listen at the chosen frequency, single press.
            if self._task is None:
                self._task = self.hass.async_create_task(
                    async_capture_packet(self._device, self._frequency / 1_000_000)
                )
                return self.async_show_progress(
                    step_id="learn",
                    progress_action="press",
                    description_placeholders={"button": label},
                    progress_task=self._task,
                )
            try:
                self._pending = await self._task
                self._learn_error = None
            except Exception as err:  # noqa: BLE001 - surface any capture failure
                self._learn_error = str(err)
                self._task = None
                return self.async_show_progress_done(next_step_id="learn_error")
            self._task = None
            return self.async_show_progress_done(next_step_id="learn_result")

        # Sweep flow, phase 1: hold the button while the carrier is found.
        if self._task is None:
            self._task = self.hass.async_create_task(
                async_sweep_frequency(self._device)
            )
            return self.async_show_progress(
                step_id="learn",
                progress_action="sweep",
                description_placeholders={"button": label},
                progress_task=self._task,
            )
        try:
            await self._task
        except Exception as err:  # noqa: BLE001 - surface any capture failure
            self._learn_error = str(err)
            self._task = None
            return self.async_show_progress_done(next_step_id="learn_error")
        self._task = None
        return self.async_show_progress_done(next_step_id="learn_press")

    async def async_step_learn_press(self, user_input=None) -> ConfigFlowResult:
        """Sweep flow, between phases: release and press again."""
        label = self._label(self._current_key)
        if user_input is not None:
            self._task = self.hass.async_create_task(_delayed_capture(self._device))
            return self.async_show_progress(
                step_id="learn_capture",
                progress_action="capture",
                description_placeholders={"button": label},
                progress_task=self._task,
            )
        return self.async_show_form(
            step_id="learn_press",
            data_schema=vol.Schema({}),
            description_placeholders={"button": label},
        )

    async def async_step_learn_capture(self, user_input=None) -> ConfigFlowResult:
        """Sweep flow, phase 2 done: store the captured code (or report failure)."""
        try:
            self._pending = await self._task
            self._learn_error = None
        except Exception as err:  # noqa: BLE001 - surface any capture failure
            self._learn_error = str(err)
            self._task = None
            return self.async_show_progress_done(next_step_id="learn_error")
        self._task = None
        return self.async_show_progress_done(next_step_id="learn_result")

    async def async_step_learn_result(self, user_input=None) -> ConfigFlowResult:
        """Offer to test, keep, or retry the just-learned code."""
        return self.async_show_menu(
            step_id="learn_result",
            menu_options=["confirm", "test", "retry"],
            description_placeholders={
                "button": self._label(self._current_key),
                "pulses": str(self._pending["length"] if self._pending else 0),
            },
        )

    async def async_step_confirm(self, user_input=None) -> ConfigFlowResult:
        """Store the learned code and continue."""
        assert self._pending is not None
        self._commands[self._current_key] = {
            "timings": self._pending["timings"],
            "repeat": self._pending["repeat"],
            "b64": self._pending["b64"],
        }
        return await self._async_after_confirm()

    async def async_step_test(self, user_input=None) -> ConfigFlowResult:
        """Transmit the just-learned code so the user can verify it."""
        if self._pending is not None:
            try:
                await async_send_stored(
                    self.hass,
                    self._transmitter,
                    self._pending,
                    self._frequency,
                    clean=self._direct,
                    repeat=CLEAN_REPEAT if self._direct else 0,
                )
            except HomeAssistantError as err:
                _LOGGER.error("rf_fan: test send failed: %s", err)
        return await self.async_step_learn_result()

    async def async_step_retry(self, user_input=None) -> ConfigFlowResult:
        """Re-learn the current button the same way."""
        self._reset_learn_state()
        return await self.async_step_learn()

    async def async_step_retry_direct(self, user_input=None) -> ConfigFlowResult:
        """Switch to direct capture (no sweep) and re-learn the current button."""
        self._direct = True
        self._reset_learn_state()
        return await self.async_step_learn()

    async def async_step_learn_error(self, user_input=None) -> ConfigFlowResult:
        """Offer to retry - and, if sweeping, to capture without the sweep."""
        menu_options = ["retry"]
        if not self._direct:
            menu_options.append("retry_direct")
        return self.async_show_menu(
            step_id="learn_error",
            menu_options=menu_options,
            description_placeholders={
                "button": self._label(self._current_key),
                "error": self._learn_error or "",
            },
        )


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------
class RfFanConfigFlow(_LearnFlowMixin, ConfigFlow, domain=DOMAIN):
    """Two-screen setup wizard + button-learning for an RF device."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialise wizard state."""
        self._data: dict[str, Any] = {}
        self._commands: dict[str, Any] = {}
        self._customs: list[dict[str, str]] = []
        self._custom_names: dict[str, str] = {}
        self._queue: list[str] = []
        self._current_key: str | None = None
        self._device: Any = None
        self._task: Any = None
        self._pending: dict[str, Any] | None = None
        self._learn_error: str | None = None
        self._speed_count: int = 0
        self._transmitter: str = ""
        self._frequency: int = DEFAULT_FREQUENCY
        self._direct: bool = False
        self._entity_type: str = ENTITY_TYPE_FAN
        self._has_on: bool = False
        self._has_stop: bool = False
        self._light_mode: str = LIGHT_MODE_TOGGLE

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow (re-learn / add buttons)."""
        return RfFanOptionsFlow()

    # ------------------------------------------------------------------
    # Screen 1: name + entity type + transmitter + frequency
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Collect the device's name, transmitter and type."""
        devices = find_rf_devices(self.hass)
        if not devices:
            return self.async_abort(reason="no_rf_device")

        if user_input is not None:
            self._device = next(iter(devices.values()))
            self._entity_type = user_input[CONF_ENTITY_TYPE]
            self._transmitter = user_input[CONF_TRANSMITTER]
            self._frequency = int(user_input[CONF_FREQUENCY])
            self._data = {
                CONF_NAME: user_input[CONF_NAME],
                CONF_ENTITY_TYPE: self._entity_type,
                CONF_TRANSMITTER: self._transmitter,
                CONF_FREQUENCY: self._frequency,
            }
            return await self.async_step_entity_options()

        schema = vol.Schema({
            vol.Required(CONF_NAME): selector.TextSelector(),
            vol.Required(CONF_ENTITY_TYPE, default=ENTITY_TYPE_FAN): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=ENTITY_TYPE_OPTIONS, translation_key="entity_type"
                )
            ),
            vol.Required(CONF_TRANSMITTER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="radio_frequency")
            ),
            vol.Required(CONF_FREQUENCY, default=str(DEFAULT_FREQUENCY)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=FREQ_OPTIONS)
            ),
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    # ------------------------------------------------------------------
    # Screen 2: entity-type-specific options
    # ------------------------------------------------------------------
    async def async_step_entity_options(self, user_input=None) -> ConfigFlowResult:
        entity_type = self._entity_type

        if user_input is not None:
            self._direct = user_input.get(CONF_DIRECT, False)

            if entity_type == ENTITY_TYPE_FAN:
                self._speed_count = int(user_input[CONF_SPEED_COUNT])
                self._has_on = user_input.get(CONF_HAS_ON, False)
                self._data.update({
                    CONF_SPEED_COUNT: self._speed_count,
                    CONF_HAS_ON: self._has_on,
                })
            elif entity_type == ENTITY_TYPE_COVER:
                self._has_stop = user_input.get(CONF_HAS_STOP, False)
                self._data[CONF_HAS_STOP] = self._has_stop
            elif entity_type == ENTITY_TYPE_LIGHT:
                self._light_mode = user_input.get(CONF_LIGHT_MODE, LIGHT_MODE_TOGGLE)
                self._data[CONF_LIGHT_MODE] = self._light_mode
            # BUTTON: no extra options beyond custom button names

            # Parse custom button names
            taken = set(self._commands) | set(self._custom_names)
            self._customs = _parse_custom_buttons(
                user_input.get(CONF_CUSTOM_BUTTONS, ""), taken
            )
            self._custom_names = {}
            for custom in self._customs:
                self._custom_names[custom["key"]] = custom["name"]

            self._data[CONF_DIRECT] = self._direct

            self._queue = _build_queue(
                entity_type,
                speed_count=self._speed_count,
                has_on=self._has_on,
                has_stop=self._has_stop,
                light_mode=self._light_mode,
                customs=self._customs,
            )
            return await self.async_step_next()

        # Build the schema depending on entity type
        fields: dict = {}
        if entity_type == ENTITY_TYPE_FAN:
            fields[vol.Required(CONF_SPEED_COUNT, default=3)] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.BOX)
            )
            fields[vol.Required(CONF_HAS_ON, default=False)] = selector.BooleanSelector()
        elif entity_type == ENTITY_TYPE_COVER:
            fields[vol.Required(CONF_HAS_STOP, default=True)] = selector.BooleanSelector()
        elif entity_type == ENTITY_TYPE_LIGHT:
            fields[vol.Required(CONF_LIGHT_MODE, default=LIGHT_MODE_TOGGLE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=LIGHT_MODE_OPTIONS, translation_key="light_mode"
                )
            )
        # Shared. A standalone Button entity is nothing but its custom buttons,
        # so there the list is required rather than optional.
        marker = vol.Required if entity_type == ENTITY_TYPE_BUTTON else vol.Optional
        fields[marker(CONF_CUSTOM_BUTTONS, default="")] = selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        )
        fields[vol.Required(CONF_DIRECT, default=False)] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="entity_options",
            data_schema=vol.Schema(fields),
            description_placeholders={"entity_type": entity_type},
        )

    async def async_step_next(self, user_input=None) -> ConfigFlowResult:
        """Advance to the next button to learn, or finish."""
        if not self._queue:
            return self.async_create_entry(
                title=self._data[CONF_NAME],
                data={
                    **self._data,
                    CONF_COMMANDS: self._commands,
                    CONF_CUSTOMS: self._customs,
                    CONF_SUB_ENTITIES: [],
                },
            )
        self._current_key = self._queue.pop(0)
        self._reset_learn_state()
        return await self.async_step_learn()

    async def _async_after_confirm(self) -> ConfigFlowResult:
        """Continue learning the queued buttons."""
        return await self.async_step_next()


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------
class RfFanOptionsFlow(_LearnFlowMixin, OptionsFlow):
    """Re-learn existing buttons or add new sub-entities after setup."""

    def __init__(self) -> None:
        """Initialise options state (config entry is read lazily)."""
        self._commands: dict[str, Any] | None = None
        self._customs: list[dict[str, str]] = []
        self._custom_names: dict[str, str] = {}
        self._sub_entities: list[dict[str, Any]] = []
        self._transmitter: str = ""
        self._frequency: int = DEFAULT_FREQUENCY
        self._speed_count: int = 0
        self._direct: bool = False
        self._device: Any = None
        self._current_key: str | None = None
        self._task: Any = None
        self._pending: dict[str, Any] | None = None
        self._learn_error: str | None = None
        self._entity_type: str = ENTITY_TYPE_FAN
        # State for add-entity wizard
        self._adding_entity_type: str = ENTITY_TYPE_BUTTON
        self._adding_name: str = ""
        self._adding_key_prefix: str = ""
        self._adding_queue: list[str] = []

    def _load_entry(self) -> None:
        entry = self.config_entry
        self._commands = dict(entry.data[CONF_COMMANDS])
        self._customs = [dict(c) for c in entry.data.get(CONF_CUSTOMS, [])]
        self._custom_names = {c["key"]: c["name"] for c in self._customs}
        self._sub_entities = [dict(s) for s in entry.data.get(CONF_SUB_ENTITIES, [])]
        self._transmitter = entry.data[CONF_TRANSMITTER]
        self._frequency = entry.data[CONF_FREQUENCY]
        self._speed_count = entry.data.get(CONF_SPEED_COUNT, 0)
        self._direct = entry.data.get(CONF_DIRECT, False)
        self._entity_type = entry.data.get(CONF_ENTITY_TYPE, ENTITY_TYPE_FAN)

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Show the main configure menu."""
        if self._commands is None:
            self._load_entry()

        devices = find_rf_devices(self.hass)
        if not devices:
            return self.async_abort(reason="no_rf_device")
        self._device = next(iter(devices.values()))

        return self.async_show_menu(
            step_id="init", menu_options=["relearn", "add_entity", "finish"]
        )

    # ------------------------------------------------------------------
    # Re-learn an existing button
    # ------------------------------------------------------------------
    async def async_step_relearn(self, user_input=None) -> ConfigFlowResult:
        """Pick an existing button to re-learn."""
        if user_input is not None:
            self._current_key = user_input["button"]
            self._reset_learn_state()
            return await self.async_step_learn()
        options = [
            selector.SelectOptionDict(value=key, label=self._label(key))
            for key in self._commands
        ]
        return self.async_show_form(
            step_id="relearn",
            data_schema=vol.Schema(
                {
                    vol.Required("button"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    # ------------------------------------------------------------------
    # Add a new sub-entity (screen A: type + name)
    # ------------------------------------------------------------------
    async def async_step_add_entity(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._adding_entity_type = user_input[CONF_ENTITY_TYPE]
            self._adding_name = (user_input.get(CONF_NAME) or "").strip()
            if not self._adding_name:
                return await self.async_step_init()
            return await self.async_step_add_entity_options()

        schema = vol.Schema({
            vol.Required(CONF_ENTITY_TYPE, default=ENTITY_TYPE_BUTTON): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=ENTITY_TYPE_OPTIONS, translation_key="entity_type"
                )
            ),
            vol.Required(CONF_NAME): selector.TextSelector(),
        })
        return self.async_show_form(step_id="add_entity", data_schema=schema)

    # ------------------------------------------------------------------
    # Add a new sub-entity (screen B: type-specific options)
    # ------------------------------------------------------------------
    async def async_step_add_entity_options(self, user_input=None) -> ConfigFlowResult:
        entity_type = self._adding_entity_type

        if user_input is not None:
            # Build a unique key_prefix
            taken_prefixes = {s["key_prefix"] for s in self._sub_entities}
            self._adding_key_prefix = _sub_key_prefix(self._adding_name, taken_prefixes)

            speed_count = 0
            has_on = False
            has_stop = False
            light_mode = LIGHT_MODE_TOGGLE
            taken = set(self._commands or {}) | {c["key"] for c in self._customs}
            customs: list[dict] = _parse_custom_buttons(
                user_input.get(CONF_CUSTOM_BUTTONS, ""), taken
            )
            custom_names: dict[str, str] = {c["key"]: c["name"] for c in customs}

            if entity_type == ENTITY_TYPE_FAN:
                speed_count = int(user_input.get(CONF_SPEED_COUNT, 3))
                has_on = user_input.get(CONF_HAS_ON, False)
            elif entity_type == ENTITY_TYPE_COVER:
                has_stop = user_input.get(CONF_HAS_STOP, True)
            elif entity_type == ENTITY_TYPE_LIGHT:
                light_mode = user_input.get(CONF_LIGHT_MODE, LIGHT_MODE_TOGGLE)

            # Register the sub-entity
            self._sub_entities.append({
                "entity_type": entity_type,
                "name": self._adding_name,
                "key_prefix": self._adding_key_prefix,
                "speed_count": speed_count,
                "has_on": has_on,
                "has_stop": has_stop,
                "light_mode": light_mode,
            })
            # Merge new customs
            self._customs.extend(customs)
            self._custom_names.update(custom_names)

            # Build and enqueue the learning queue for this sub-entity
            self._adding_queue = _build_queue(
                entity_type,
                speed_count=speed_count,
                has_on=has_on,
                has_stop=has_stop,
                light_mode=light_mode,
                customs=customs,
                key_prefix=self._adding_key_prefix,
            )
            # Update speed_count context for label formatting if it's a fan
            if entity_type == ENTITY_TYPE_FAN:
                self._speed_count = speed_count
            return await self.async_step_add_entity_learn()

        # Build schema
        fields: dict = {}
        if entity_type == ENTITY_TYPE_FAN:
            fields[vol.Required(CONF_SPEED_COUNT, default=3)] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.BOX)
            )
            fields[vol.Required(CONF_HAS_ON, default=False)] = selector.BooleanSelector()
        elif entity_type == ENTITY_TYPE_COVER:
            fields[vol.Required(CONF_HAS_STOP, default=True)] = selector.BooleanSelector()
        elif entity_type == ENTITY_TYPE_LIGHT:
            fields[vol.Required(CONF_LIGHT_MODE, default=LIGHT_MODE_TOGGLE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=LIGHT_MODE_OPTIONS, translation_key="light_mode"
                )
            )
        fields[vol.Optional(CONF_CUSTOM_BUTTONS, default="")] = selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        )
        return self.async_show_form(
            step_id="add_entity_options",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "entity_type": entity_type,
                "name": self._adding_name,
            },
        )

    async def async_step_add_entity_learn(self, user_input=None) -> ConfigFlowResult:
        """Dequeue the next button for the new sub-entity."""
        if not self._adding_queue:
            return await self.async_step_init()
        self._current_key = self._adding_queue.pop(0)
        self._reset_learn_state()
        return await self.async_step_learn()

    async def _async_after_confirm(self) -> ConfigFlowResult:
        """After each code is kept: continue the adding queue, or back to menu."""
        if self._adding_queue:
            return await self.async_step_add_entity_learn()
        return await self.async_step_init()

    # ------------------------------------------------------------------
    # Finish: persist changes
    # ------------------------------------------------------------------
    async def async_step_finish(self, user_input=None) -> ConfigFlowResult:
        """Persist the updated commands and reload the entry."""
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                CONF_COMMANDS: self._commands,
                CONF_CUSTOMS: self._customs,
                CONF_SUB_ENTITIES: self._sub_entities,
                CONF_DIRECT: self._direct,
            },
        )
        return self.async_create_entry(title="", data={})
