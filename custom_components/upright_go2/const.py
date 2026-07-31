"""Constants for the Upright GO 2 integration.

Every UUID, offset and enum value here was recovered from the official Android
app (com.uprightpose.upright_go2 v3.0.19). See docs/PROTOCOL.md for the
derivation.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final

DOMAIN: Final = "upright_go2"

CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 300
MIN_SCAN_INTERVAL: Final = 60
MAX_SCAN_INTERVAL: Final = 3600

CONF_HISTORY_INTERVAL: Final = "history_interval"
DEFAULT_HISTORY_INTERVAL: Final = 3600
MIN_HISTORY_INTERVAL: Final = 600
MAX_HISTORY_INTERVAL: Final = 86400

MANUFACTURER: Final = "Upright Technologies"
MODEL: Final = "GO 2"

# Advertised local names (GO_NAMES in the app bundle).
DEVICE_NAME_GO2: Final = "UprightGO2"
DEVICE_NAME_GOS: Final = "UprightGOS"
DEVICE_NAME_DFU: Final = "UprightGo2DFU"
KNOWN_LOCAL_NAMES: Final = (DEVICE_NAME_GO2, DEVICE_NAME_GOS)


def short_uuid(short: str) -> str:
    """Expand a 16-bit short UUID using the Bluetooth base UUID."""
    return f"0000{short}-0000-1000-8000-00805f9b34fb"


# --- Services ---------------------------------------------------------------

SERVICE_TRAIN: Final = short_uuid("aac0")
SERVICE_DATA: Final = short_uuid("baa0")
SERVICE_SETTING: Final = short_uuid("bab0")
SERVICE_CALIBRATION: Final = short_uuid("bac0")
SERVICE_POWER: Final = short_uuid("bad0")
SERVICE_TEST: Final = short_uuid("bae0")
SERVICE_BOOTLOADER: Final = short_uuid("baf0")
SERVICE_INFO: Final = short_uuid("180a")

# --- Characteristics --------------------------------------------------------

CHAR_FREESTYLE_SETTING: Final = short_uuid("bab2")
CHAR_GENERAL_SETTING: Final = short_uuid("bab3")
CHAR_VIBRATION_STATUS: Final = short_uuid("bab5")

CHAR_START_CALIBRATION: Final = short_uuid("bac1")
CHAR_CALIBRATION_ACK: Final = short_uuid("bac2")
CHAR_POSTURE_STATUS: Final = short_uuid("bac3")
CHAR_SMOOTH_ANGLE: Final = short_uuid("bac4")

CHAR_POWER_DATA_FIRST: Final = short_uuid("bad1")
CHAR_POWER_DATA_SECOND: Final = short_uuid("bad2")
CHAR_ERRORS: Final = short_uuid("bad3")
CHAR_HAL_CONTROL: Final = short_uuid("bad4")

CHAR_DATA_AMOUNT: Final = short_uuid("baa1")
CHAR_DATA_COMMAND: Final = short_uuid("baa2")
CHAR_PACKET_NUMBER: Final = short_uuid("baa3")
CHAR_OFFLINE_DATA: Final = short_uuid("baa4")
CHAR_ONLINE_DATA: Final = short_uuid("baa5")
CHAR_CURRENT_TIMESTAMP: Final = short_uuid("baa6")

CHAR_SERIAL_NUMBER: Final = short_uuid("2a25")
CHAR_FW_VERSION: Final = short_uuid("2a26")
CHAR_HW_VERSION: Final = short_uuid("2a27")

# --- Payload offsets --------------------------------------------------------

# POWER_DATA_FIRST
OFFSET_BATTERY_LEVEL: Final = 0
OFFSET_TILE_FLAGS: Final = 4

# POWER_DATA_SECOND
OFFSET_CHARGING_STATE: Final = 0

# FREESTYLE_SETTING
OFFSET_RANGE: Final = 0
OFFSET_DELAY_LOW: Final = 1
OFFSET_DELAY_HIGH: Final = 2
OFFSET_VIB_PATTERN: Final = 3
OFFSET_VIB_STRENGTH: Final = 4
OFFSET_STOP_PERIODS: Final = 5
OFFSET_BACKWARDS_SLOUCH_RANGE: Final = 10
FREESTYLE_LENGTH: Final = 11

DELAY_MULTIPLIER: Final = 10
LOW_BATTERY_VALUE: Final = 10


class ChargingState(IntEnum):
    """Value of POWER_DATA_SECOND byte 0."""

    DISCONNECTED = 0
    CHARGING = 1
    CHARGED = 2


class DataTransferCommand(IntEnum):
    """Single byte written to DATA_COMMAND to drive a history download."""

    START_TRANSFER_WITH_APPROVAL = 0
    START_TRANSFER_NO_APPROVAL = 1
    DELETE_DATA = 2
    SEND_NEXT = 3
    RESEND_CURRENT = 4
    CLEAN_TIMESTAMP = 5
    DIRTY_TIMESTAMP = 6


# GENERAL_SETTING byte 0 (IntervalFrequency) -> seconds covered by one record.
INTERVAL_SECONDS: Final = {1: 1, 5: 5, 7: 10, 10: 30, 11: 60}
DEFAULT_INTERVAL_FREQUENCY: Final = 7

SESSION_HEADER_LENGTH: Final = 10
END_OF_DATA: Final = 0xFF
# Low nibble of a session header's first byte.
HEADER_CLEAN_NIBBLE: Final = 0x7
HEADER_DIRTY_NIBBLE: Final = 0xF


class PostureState(IntEnum):
    """Value of POSTURE_STATUS."""

    STRAIGHT = 0
    SLOUCH = 1


class VibrationMode(IntEnum):
    """Value of VIBRATION_STATUS."""

    ON = 0
    OFF = 1


class CalibrationCommand(IntEnum):
    """Single byte written to START_CALIBRATION."""

    START_CALIB = 0
    CLEAR_CALIB = 1
    SILENT_CALIB = 2


class HalControlCommand(IntEnum):
    """Single byte written to HAL_CONTROL."""

    DO_NOTHING = 0
    FW_UPDATE = 1
    FW_UPDATE_WITH_BULK_ERASE = 2
    DEEP_SLEEP = 8
    AIRPLANE_MODE = 9


class VibrationPattern(IntEnum):
    """FREESTYLE_SETTING byte 3."""

    LONG = 0
    MEDIUM = 1
    SHORT = 2
    RUMPUP = 3
    KNOCK = 4
    HEARTBEAT = 5
    TUK_TUK = 6
    ECSTATIC = 7
    MUZZLE = 8


# Lower value buzzes harder — this is the app's own ordering, not a mistake.
VIBRATION_STRENGTHS: Final = {"strong": 1, "medium": 35, "gentle": 70}

GO_RANGE_MIN: Final = 1
GO_RANGE_MAX: Final = 6
GO_RANGE_DEFAULT: Final = 5

# ERRORS characteristic layout.
OFFSET_ERROR_CODE: Final = 0  # two bytes, little-endian bitmask
OFFSET_MALFUNCTION: Final = 4
OFFSET_SHUTDOWN_REASON: Final = 5
OFFSET_RESET_REASON_LOW: Final = 7
OFFSET_RESET_REASON_HIGH: Final = 9

# Bit positions within the two error-code bytes (GoDeviceErrorStatus).
DEVICE_ERRORS: Final = (
    "no_magnometer",
    "memory_failure",
    "memory_full",
    "read_sensors_failure",
    "empty_battery",
    "wrong_battery",
    "shutdown_in_one_minute",
    "shutdown_due_to_button_press",
    "memory_mutex_lock_failure",
    "sensor_mutex_lock_failure",
    "flash_reload_values_failure",
    "soft_reset_due_to_button_press",
    "no_sensor_is_detected",
)

# Index into SHUTDOWN_REASONS (a plain value, not a bitmask).
SHUTDOWN_REASONS: Final = (
    "regular_off",
    "is_active",
    "softreset",
    "empty_battery",
    "bootloader",
    "deepsleep",
    "fw_update_with_bulk_erase",
    "fw_update_without_bulk_erase",
    "fw_unattached",
    "fw_uncalibrated",
)

# Bit positions across the two reset-reason bytes. These mirror the nRF
# RESETREAS register, whose upper causes live in bits 16-19 — which is why the
# app reads byte 7 and byte 9 rather than two adjacent bytes.
RESET_REASONS: Final = (
    "pin_reset_detected",
    "watchdog_detected",
    "soft_reset_detected",
    "cpu_lock_up_detected",
    "detect_signal_from_gpio",
    "anadetect_signal_from_lpcomp",
    "entering_into_debug_interface_mode",
    "nfc_field_detect",
)
