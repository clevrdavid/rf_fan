"""Constants for the rf_fan integration."""

DOMAIN = "rf_fan"

# The Broadlink integration stores its devices under this domain in hass.data.
BROADLINK_DOMAIN = "broadlink"

# 433.92 MHz - the carrier almost every cheap RF fan/remote kit uses.
DEFAULT_FREQUENCY = 433_920_000

# Config entry / form keys.
CONF_NAME = "name"
CONF_TRANSMITTER = "transmitter"
CONF_FREQUENCY = "frequency"

# Primary entity type (first screen)
CONF_ENTITY_TYPE = "entity_type"

# Fan-specific
CONF_SPEED_COUNT = "speed_count"
CONF_HAS_ON = "has_on_button"

# Cover-specific
CONF_HAS_STOP = "has_stop_button"

# Light-specific
CONF_LIGHT_MODE = "light_mode"         # "toggle" | "on_off"

CONF_CUSTOMS = "customs"               # stored list of {"key", "name"}
CONF_CUSTOM_BUTTONS = "custom_buttons"  # form field: comma/newline separated names
CONF_COMMANDS = "commands"
CONF_DIRECT = "direct_capture"  # skip the frequency sweep, listen at the set freq

# Stored list of sub-entities added via Configure (each has entity_type + name + key prefix)
CONF_SUB_ENTITIES = "sub_entities"

# Learned-command keys.
CMD_OFF = "off"
CMD_ON = "on"
CMD_LIGHT = "light"
CMD_STOP = "stop"
CMD_OPEN = "open"
CMD_CLOSE = "close"

# Entity types
ENTITY_TYPE_FAN = "fan"
ENTITY_TYPE_COVER = "cover"
ENTITY_TYPE_LIGHT = "light"
ENTITY_TYPE_BUTTON = "button"

# Light modes
LIGHT_MODE_TOGGLE = "toggle"
LIGHT_MODE_ON_OFF = "on_off"

# Times to repeat a de-noised consensus frame for direct-capture remotes.
CLEAN_REPEAT = 10
