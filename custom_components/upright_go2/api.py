"""BLE client for the Upright GO 2."""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass, field

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from .const import (
    CHAR_ERRORS,
    CHAR_FREESTYLE_SETTING,
    CHAR_FW_VERSION,
    CHAR_HAL_CONTROL,
    CHAR_HW_VERSION,
    CHAR_POSTURE_STATUS,
    CHAR_POWER_DATA_FIRST,
    CHAR_POWER_DATA_SECOND,
    CHAR_SERIAL_NUMBER,
    CHAR_SMOOTH_ANGLE,
    CHAR_START_CALIBRATION,
    CHAR_VIBRATION_STATUS,
    DELAY_MULTIPLIER,
    DEVICE_ERRORS,
    FREESTYLE_LENGTH,
    GO_RANGE_MAX,
    GO_RANGE_MIN,
    OFFSET_BACKWARDS_SLOUCH_RANGE,
    OFFSET_BATTERY_LEVEL,
    OFFSET_CHARGING_STATE,
    OFFSET_DELAY_HIGH,
    OFFSET_DELAY_LOW,
    OFFSET_ERROR_CODE,
    OFFSET_MALFUNCTION,
    OFFSET_RANGE,
    OFFSET_RESET_REASON_HIGH,
    OFFSET_RESET_REASON_LOW,
    OFFSET_SHUTDOWN_REASON,
    OFFSET_STOP_PERIODS,
    OFFSET_VIB_PATTERN,
    OFFSET_VIB_STRENGTH,
    RESET_REASONS,
    SHUTDOWN_REASONS,
    CalibrationCommand,
    ChargingState,
    HalControlCommand,
    PostureState,
    VibrationMode,
    VibrationPattern,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 20.0


class UprightGo2Error(Exception):
    """Raised when the device cannot be read or written."""


@dataclass(slots=True)
class UprightGo2Data:
    """A snapshot of everything read in one polling pass."""

    battery_level: int | None = None
    charging_state: ChargingState | None = None
    posture: PostureState | None = None
    angle: float | None = None
    vibration_on: bool | None = None
    errors: list[str] = field(default_factory=list)
    malfunction: bool | None = None
    shutdown_reason: str | None = None
    reset_reasons: list[str] = field(default_factory=list)

    # Freestyle settings
    sensitivity_range: int | None = None
    delay_seconds: int | None = None
    vibration_pattern: VibrationPattern | None = None
    vibration_strength: int | None = None
    backwards_slouch_range: int | None = None

    # Device information (read once)
    serial_number: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None


def decode_angle(payload: bytes) -> float | None:
    """Decode SMOOTH_ANGLE: signed 16-bit little-endian, tenths of a degree."""
    if len(payload) < 2:
        return None
    return struct.unpack_from("<h", payload, 0)[0] / 10


def _decode_bitmask(value: int, names: tuple[str, ...]) -> list[str]:
    """Return the names whose bit is set, LSB first."""
    return [name for index, name in enumerate(names) if value & (1 << index)]


def decode_errors(payload: bytes) -> tuple[list[str], bool | None, str | None, list[str]]:
    """Decode the ERRORS characteristic.

    Returns the active errors, the malfunction flag, the shutdown reason and
    the active reset reasons. Each field sits at its own offset — the error
    bitmask is only the first two bytes, not the whole payload.
    """
    errors: list[str] = []
    malfunction: bool | None = None
    shutdown: str | None = None
    resets: list[str] = []

    if len(payload) > OFFSET_ERROR_CODE + 1:
        code = int.from_bytes(
            payload[OFFSET_ERROR_CODE : OFFSET_ERROR_CODE + 2], "little"
        )
        errors = _decode_bitmask(code, DEVICE_ERRORS)

    if len(payload) > OFFSET_MALFUNCTION:
        malfunction = bool(payload[OFFSET_MALFUNCTION])

    if len(payload) > OFFSET_SHUTDOWN_REASON:
        index = payload[OFFSET_SHUTDOWN_REASON]
        if index < len(SHUTDOWN_REASONS):
            shutdown = SHUTDOWN_REASONS[index]

    if len(payload) > OFFSET_RESET_REASON_HIGH:
        raw = payload[OFFSET_RESET_REASON_LOW] | (
            payload[OFFSET_RESET_REASON_HIGH] << 8
        )
        resets = _decode_bitmask(raw, RESET_REASONS)

    return errors, malfunction, shutdown, resets


class UprightGo2Client:
    """Connect to the device, read a snapshot, and disconnect.

    The GO 2 only accepts one connection at a time, so the client holds the
    link for as short a time as possible: the phone app stays usable between
    polls.
    """

    def __init__(self, ble_device: BLEDevice) -> None:
        """Initialise the client for a device."""
        self._ble_device = ble_device
        self._lock = asyncio.Lock()
        self._static_info: dict[str, str | None] = {}

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Update the underlying device, e.g. when it moves between proxies."""
        self._ble_device = ble_device

    async def _connect(self) -> BleakClient:
        return await establish_connection(
            BleakClient,
            self._ble_device,
            self._ble_device.address,
            timeout=CONNECT_TIMEOUT,
        )

    @staticmethod
    async def _read(client: BleakClient, uuid: str) -> bytes | None:
        """Read a characteristic, returning None when it is absent."""
        try:
            return bytes(await client.read_gatt_char(uuid))
        except (BleakError, EOFError, TimeoutError) as err:
            _LOGGER.debug("Could not read %s: %s", uuid, err)
            return None

    @staticmethod
    def _decode_text(payload: bytes | None) -> str | None:
        if not payload:
            return None
        return payload.decode("utf-8", errors="replace").strip("\x00").strip() or None

    async def async_poll(self) -> UprightGo2Data:
        """Connect, read every exposed value, and disconnect."""
        async with self._lock:
            try:
                client = await self._connect()
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(
                    f"Could not connect to {self._ble_device.address}: {err}"
                ) from err

            try:
                return await self._async_read_all(client)
            finally:
                try:
                    await client.disconnect()
                except (BleakError, EOFError, TimeoutError) as err:
                    _LOGGER.debug("Error while disconnecting: %s", err)

    async def _async_read_all(self, client: BleakClient) -> UprightGo2Data:
        data = UprightGo2Data()

        if (power_first := await self._read(client, CHAR_POWER_DATA_FIRST)) and len(
            power_first
        ) > OFFSET_BATTERY_LEVEL:
            data.battery_level = power_first[OFFSET_BATTERY_LEVEL]

        if (power_second := await self._read(client, CHAR_POWER_DATA_SECOND)) and len(
            power_second
        ) > OFFSET_CHARGING_STATE:
            raw = power_second[OFFSET_CHARGING_STATE]
            try:
                data.charging_state = ChargingState(raw)
            except ValueError:
                _LOGGER.debug("Unknown charging state %s", raw)

        if posture := await self._read(client, CHAR_POSTURE_STATUS):
            try:
                data.posture = PostureState(posture[0])
            except ValueError:
                _LOGGER.debug("Unknown posture state %s", posture[0])

        if angle := await self._read(client, CHAR_SMOOTH_ANGLE):
            data.angle = decode_angle(angle)

        if vibration := await self._read(client, CHAR_VIBRATION_STATUS):
            data.vibration_on = vibration[0] == VibrationMode.ON

        if errors := await self._read(client, CHAR_ERRORS):
            (
                data.errors,
                data.malfunction,
                data.shutdown_reason,
                data.reset_reasons,
            ) = decode_errors(errors)

        if (freestyle := await self._read(client, CHAR_FREESTYLE_SETTING)) and len(
            freestyle
        ) >= FREESTYLE_LENGTH:
            data.sensitivity_range = freestyle[OFFSET_RANGE]
            delay_raw = freestyle[OFFSET_DELAY_LOW] | (
                freestyle[OFFSET_DELAY_HIGH] << 8
            )
            data.delay_seconds = delay_raw // DELAY_MULTIPLIER
            try:
                data.vibration_pattern = VibrationPattern(freestyle[OFFSET_VIB_PATTERN])
            except ValueError:
                _LOGGER.debug("Unknown pattern %s", freestyle[OFFSET_VIB_PATTERN])
            data.vibration_strength = freestyle[OFFSET_VIB_STRENGTH]
            data.backwards_slouch_range = freestyle[OFFSET_BACKWARDS_SLOUCH_RANGE]

        # Device information never changes; read it once and cache it.
        if not self._static_info:
            self._static_info = {
                "serial_number": self._decode_text(
                    await self._read(client, CHAR_SERIAL_NUMBER)
                ),
                "firmware_version": self._decode_text(
                    await self._read(client, CHAR_FW_VERSION)
                ),
                "hardware_version": self._decode_text(
                    await self._read(client, CHAR_HW_VERSION)
                ),
            }
        data.serial_number = self._static_info.get("serial_number")
        data.firmware_version = self._static_info.get("firmware_version")
        data.hardware_version = self._static_info.get("hardware_version")

        return data

    async def _async_write(self, uuid: str, payload: bytes) -> None:
        """Connect and write a single characteristic."""
        async with self._lock:
            try:
                client = await self._connect()
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(
                    f"Could not connect to {self._ble_device.address}: {err}"
                ) from err
            try:
                await client.write_gatt_char(uuid, payload, response=True)
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(f"Could not write {uuid}: {err}") from err
            finally:
                try:
                    await client.disconnect()
                except (BleakError, EOFError, TimeoutError) as err:
                    _LOGGER.debug("Error while disconnecting: %s", err)

    async def async_calibrate(self) -> None:
        """Set the current pose as the upright reference."""
        await self._async_write(
            CHAR_START_CALIBRATION, bytes([CalibrationCommand.START_CALIB])
        )

    async def async_clear_calibration(self) -> None:
        """Drop the stored calibration."""
        await self._async_write(
            CHAR_START_CALIBRATION, bytes([CalibrationCommand.CLEAR_CALIB])
        )

    async def async_set_vibration(self, enabled: bool) -> None:
        """Turn the slouch buzz on or off."""
        mode = VibrationMode.ON if enabled else VibrationMode.OFF
        await self._async_write(CHAR_VIBRATION_STATUS, bytes([mode]))

    async def async_deep_sleep(self) -> None:
        """Put the device into deep sleep."""
        await self._async_write(
            CHAR_HAL_CONTROL, bytes([HalControlCommand.DEEP_SLEEP])
        )

    async def async_update_freestyle(self, **changes: int) -> None:
        """Read the freestyle block, apply changes, and write it back.

        The block is written whole, so it has to be read first: writing a
        partial buffer would clear the settings we are not touching.
        """
        async with self._lock:
            try:
                client = await self._connect()
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(
                    f"Could not connect to {self._ble_device.address}: {err}"
                ) from err
            try:
                current = await self._read(client, CHAR_FREESTYLE_SETTING)
                if not current or len(current) < FREESTYLE_LENGTH:
                    raise UprightGo2Error("Device did not return freestyle settings")

                payload = bytearray(current)
                if (value := changes.get("sensitivity_range")) is not None:
                    payload[OFFSET_RANGE] = max(
                        GO_RANGE_MIN, min(GO_RANGE_MAX, int(value))
                    )
                if (value := changes.get("delay_seconds")) is not None:
                    raw = int(value) * DELAY_MULTIPLIER
                    payload[OFFSET_DELAY_LOW] = raw % 256
                    payload[OFFSET_DELAY_HIGH] = raw // 256
                if (value := changes.get("vibration_pattern")) is not None:
                    payload[OFFSET_VIB_PATTERN] = int(value)
                if (value := changes.get("vibration_strength")) is not None:
                    payload[OFFSET_VIB_STRENGTH] = int(value)
                if (value := changes.get("stop_periods")) is not None:
                    payload[OFFSET_STOP_PERIODS] = int(value)
                if (value := changes.get("backwards_slouch_range")) is not None:
                    payload[OFFSET_BACKWARDS_SLOUCH_RANGE] = int(value)

                await client.write_gatt_char(
                    CHAR_FREESTYLE_SETTING, bytes(payload), response=True
                )
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(f"Could not write settings: {err}") from err
            finally:
                try:
                    await client.disconnect()
                except (BleakError, EOFError, TimeoutError) as err:
                    _LOGGER.debug("Error while disconnecting: %s", err)
