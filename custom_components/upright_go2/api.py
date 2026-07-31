"""BLE client for the Upright GO 2."""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass, field

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
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
RECONNECT_MIN_DELAY = 5
RECONNECT_MAX_DELAY = 120


class UprightGo2Error(Exception):
    """Raised when the device cannot be read or written."""


@dataclass(slots=True)
class UprightGo2Data:
    """The current view of the device."""

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


def translate_battery_level(raw: int) -> int | None:
    """Convert the raw battery level byte to a percentage.

    The byte is a small level index, not a percentage — the app maps it with
    translateBatteryLevelToPercent. A full battery reports 12, which is 100 %.
    """
    if raw == 0:
        return None
    if raw == 1:
        return 0
    if raw == 2:
        return 5
    return min((raw - 2) * 10, 100)


def decode_angle(payload: bytes) -> float | None:
    """Decode SMOOTH_ANGLE: signed 16-bit little-endian, tenths of a degree."""
    if len(payload) < 2:
        return None
    return struct.unpack_from("<h", payload, 0)[0] / 10


def _decode_bitmask(value: int, names: tuple[str, ...]) -> list[str]:
    """Return the names whose bit is set, LSB first."""
    return [name for index, name in enumerate(names) if value & (1 << index)]


def decode_errors(
    payload: bytes,
) -> tuple[list[str], bool | None, str | None, list[str]]:
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
    """Holds a connection to the device and streams posture notifications.

    The official app subscribes to the posture characteristics rather than
    polling them, and the device only reports posture and angle while a
    subscriber is attached — so a live connection is kept open and the
    notifications drive the state. Battery, settings and errors have no
    notification, so they are re-read on the coordinator's slower tick over
    that same connection.
    """

    def __init__(
        self,
        ble_device: BLEDevice,
        notify_callback: Callable[[UprightGo2Data], None] | None = None,
    ) -> None:
        """Initialise the client for a device."""
        self._ble_device = ble_device
        self._notify_callback = notify_callback
        self._lock = asyncio.Lock()
        self._client: BleakClient | None = None
        self._static_info: dict[str, str | None] = {}
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_delay = RECONNECT_MIN_DELAY
        self._closing = False
        self.data = UprightGo2Data()

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Update the underlying device, e.g. when it moves between proxies."""
        self._ble_device = ble_device

    @property
    def connected(self) -> bool:
        """Return whether the link is currently up."""
        return self._client is not None and self._client.is_connected

    # --- connection handling ------------------------------------------------

    def _on_disconnect(self, _client: BleakClient) -> None:
        """Handle an unsolicited disconnect by scheduling a reconnect."""
        if self._closing:
            return
        _LOGGER.debug("%s disconnected", self._ble_device.address)
        self._client = None
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Reconnect with backoff until the link is restored."""
        while not self._closing and not self.connected:
            await asyncio.sleep(self._reconnect_delay)
            if self._closing:
                return
            try:
                await self.async_connect()
            except UprightGo2Error as err:
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, RECONNECT_MAX_DELAY
                )
                _LOGGER.debug(
                    "Reconnect failed (%s); retrying in %ss",
                    err,
                    self._reconnect_delay,
                )
            else:
                self._reconnect_delay = RECONNECT_MIN_DELAY

    async def async_connect(self) -> None:
        """Establish the link and subscribe to posture notifications."""
        async with self._lock:
            if self.connected:
                return
            self._closing = False
            try:
                self._client = await establish_connection(
                    BleakClient,
                    self._ble_device,
                    self._ble_device.address,
                    disconnected_callback=self._on_disconnect,
                    timeout=CONNECT_TIMEOUT,
                )
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(
                    f"Could not connect to {self._ble_device.address}: {err}"
                ) from err

            await self._async_subscribe()

    async def _async_subscribe(self) -> None:
        """Attach to the characteristics that push updates."""
        assert self._client is not None
        for uuid, handler in (
            (CHAR_POSTURE_STATUS, self._handle_posture),
            (CHAR_SMOOTH_ANGLE, self._handle_angle),
            (CHAR_VIBRATION_STATUS, self._handle_vibration),
        ):
            try:
                await self._client.start_notify(uuid, handler)
            except (BleakError, TimeoutError) as err:
                # Not every firmware exposes all three as notifiable; the
                # slow poll still picks these values up.
                _LOGGER.debug("Could not subscribe to %s: %s", uuid, err)

    async def async_disconnect(self) -> None:
        """Tear the link down and stop reconnecting."""
        self._closing = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        async with self._lock:
            if self._client is None:
                return
            try:
                await self._client.disconnect()
            except (BleakError, EOFError, TimeoutError) as err:
                _LOGGER.debug("Error while disconnecting: %s", err)
            finally:
                self._client = None

    # --- notification handlers ----------------------------------------------

    def _publish(self) -> None:
        if self._notify_callback is not None:
            self._notify_callback(self.data)

    def _handle_posture(
        self, _char: BleakGATTCharacteristic, payload: bytearray
    ) -> None:
        if not payload:
            return
        try:
            self.data.posture = PostureState(payload[0])
        except ValueError:
            _LOGGER.debug("Unknown posture state %s", payload[0])
            return
        self._publish()

    def _handle_angle(
        self, _char: BleakGATTCharacteristic, payload: bytearray
    ) -> None:
        if (angle := decode_angle(bytes(payload))) is None:
            return
        self.data.angle = angle
        self._publish()

    def _handle_vibration(
        self, _char: BleakGATTCharacteristic, payload: bytearray
    ) -> None:
        if not payload:
            return
        self.data.vibration_on = payload[0] == VibrationMode.ON
        self._publish()

    # --- reads --------------------------------------------------------------

    async def _read(self, uuid: str) -> bytes | None:
        """Read a characteristic, returning None when it is absent."""
        if self._client is None:
            return None
        try:
            return bytes(await self._client.read_gatt_char(uuid))
        except (BleakError, EOFError, TimeoutError) as err:
            _LOGGER.debug("Could not read %s: %s", uuid, err)
            return None

    @staticmethod
    def _decode_text(payload: bytes | None) -> str | None:
        if not payload:
            return None
        return payload.decode("utf-8", errors="replace").strip("\x00").strip() or None

    async def async_poll(self) -> UprightGo2Data:
        """Refresh the values that have no notification."""
        if not self.connected:
            await self.async_connect()

        async with self._lock:
            data = self.data

            if (power_first := await self._read(CHAR_POWER_DATA_FIRST)) and len(
                power_first
            ) > OFFSET_BATTERY_LEVEL:
                # A freshly reconnected device reports level 0 ("unknown") for
                # the first read or two. Keep the last real reading rather than
                # blanking the sensor after every reconnect.
                level = translate_battery_level(power_first[OFFSET_BATTERY_LEVEL])
                if level is not None:
                    data.battery_level = level

            if (power_second := await self._read(CHAR_POWER_DATA_SECOND)) and len(
                power_second
            ) > OFFSET_CHARGING_STATE:
                raw = power_second[OFFSET_CHARGING_STATE]
                try:
                    data.charging_state = ChargingState(raw)
                except ValueError:
                    _LOGGER.debug("Unknown charging state %s", raw)

            # Seed the notification-backed values on the first pass, so the
            # entities are populated before anything moves.
            if data.posture is None and (posture := await self._read(
                CHAR_POSTURE_STATUS
            )):
                try:
                    data.posture = PostureState(posture[0])
                except ValueError:
                    _LOGGER.debug("Unknown posture state %s", posture[0])

            if data.angle is None and (angle := await self._read(CHAR_SMOOTH_ANGLE)):
                data.angle = decode_angle(angle)

            if vibration := await self._read(CHAR_VIBRATION_STATUS):
                data.vibration_on = vibration[0] == VibrationMode.ON

            if errors := await self._read(CHAR_ERRORS):
                (
                    data.errors,
                    data.malfunction,
                    data.shutdown_reason,
                    data.reset_reasons,
                ) = decode_errors(errors)

            if (freestyle := await self._read(CHAR_FREESTYLE_SETTING)) and len(
                freestyle
            ) >= FREESTYLE_LENGTH:
                data.sensitivity_range = freestyle[OFFSET_RANGE]
                delay_raw = freestyle[OFFSET_DELAY_LOW] | (
                    freestyle[OFFSET_DELAY_HIGH] << 8
                )
                data.delay_seconds = delay_raw // DELAY_MULTIPLIER
                try:
                    data.vibration_pattern = VibrationPattern(
                        freestyle[OFFSET_VIB_PATTERN]
                    )
                except ValueError:
                    _LOGGER.debug("Unknown pattern %s", freestyle[OFFSET_VIB_PATTERN])
                data.vibration_strength = freestyle[OFFSET_VIB_STRENGTH]
                data.backwards_slouch_range = freestyle[OFFSET_BACKWARDS_SLOUCH_RANGE]

            if not self._static_info:
                self._static_info = {
                    "serial_number": self._decode_text(
                        await self._read(CHAR_SERIAL_NUMBER)
                    ),
                    "firmware_version": self._decode_text(
                        await self._read(CHAR_FW_VERSION)
                    ),
                    "hardware_version": self._decode_text(
                        await self._read(CHAR_HW_VERSION)
                    ),
                }
            data.serial_number = self._static_info.get("serial_number")
            data.firmware_version = self._static_info.get("firmware_version")
            data.hardware_version = self._static_info.get("hardware_version")

            return data

    # --- writes -------------------------------------------------------------

    async def _async_write(self, uuid: str, payload: bytes) -> None:
        """Write a single characteristic over the live connection."""
        if not self.connected:
            await self.async_connect()
        async with self._lock:
            if self._client is None:
                raise UprightGo2Error("Not connected")
            try:
                await self._client.write_gatt_char(uuid, payload, response=True)
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(f"Could not write {uuid}: {err}") from err

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
        """Turn the slouch buzz on or off.

        The app writes the mode twice — `new Uint8Array([v, v])`. A single byte
        is silently ignored by the device, so the length matters.
        """
        mode = VibrationMode.ON if enabled else VibrationMode.OFF
        await self._async_write(CHAR_VIBRATION_STATUS, bytes([mode, mode]))
        self.data.vibration_on = enabled

    async def async_deep_sleep(self) -> None:
        """Put the device into deep sleep."""
        await self._async_write(CHAR_HAL_CONTROL, bytes([HalControlCommand.DEEP_SLEEP]))

    async def async_update_freestyle(self, **changes: int) -> None:
        """Read the freestyle block, apply changes, and write it back.

        The block is written whole, so it has to be read first: writing a
        partial buffer would clear the settings we are not touching.
        """
        if not self.connected:
            await self.async_connect()

        async with self._lock:
            if self._client is None:
                raise UprightGo2Error("Not connected")

            current = await self._read(CHAR_FREESTYLE_SETTING)
            if not current or len(current) < FREESTYLE_LENGTH:
                raise UprightGo2Error("Device did not return freestyle settings")

            payload = bytearray(current)
            if (value := changes.get("sensitivity_range")) is not None:
                payload[OFFSET_RANGE] = max(GO_RANGE_MIN, min(GO_RANGE_MAX, int(value)))
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

            try:
                await self._client.write_gatt_char(
                    CHAR_FREESTYLE_SETTING, bytes(payload), response=True
                )
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(f"Could not write settings: {err}") from err
