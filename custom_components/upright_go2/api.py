"""BLE client for the Upright GO 2."""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from .const import (
    CHAR_CURRENT_TIMESTAMP,
    CHAR_DATA_AMOUNT,
    CHAR_DATA_COMMAND,
    CHAR_ERRORS,
    CHAR_FREESTYLE_SETTING,
    CHAR_FW_VERSION,
    CHAR_GENERAL_SETTING,
    CHAR_HAL_CONTROL,
    CHAR_HW_VERSION,
    CHAR_OFFLINE_DATA,
    CHAR_ONLINE_DATA,
    CHAR_POSTURE_STATUS,
    CHAR_POWER_DATA_FIRST,
    CHAR_POWER_DATA_SECOND,
    CHAR_SERIAL_NUMBER,
    CHAR_SMOOTH_ANGLE,
    CHAR_START_CALIBRATION,
    CHAR_VIBRATION_STATUS,
    DEFAULT_POSTURE_DEBOUNCE,
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
    COMPATIBILITY_MODE_MSK,
    COMPATIBILITY_MODE_POSTURE,
    OFFSET_COMPATIBILITY,
    CalibrationCommand,
    ChargingState,
    ConnectionMode,
    DataTransferCommand,
    MovementStatus,
    HalControlCommand,
    PostureState,
    VibrationMode,
    VibrationPattern,
)
from .history import (
    Interval,
    StreamFramer,
    decode_data_amount,
    decode_session_timestamp,
    detect_frequency,
    expected_record_count,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 20.0
# The device notifies many times a second. Silence for this long while the link
# still claims to be up means it is dead and bleak has not noticed.
NOTIFY_TIMEOUT = 60.0
# Give up on a dump once the device has been silent this long.
IDLE_TIMEOUT = 8.0
# A single GATT operation must not be able to hang forever. Bleak applies no
# deadline of its own, and a read on a half-open link — the proxy still holds
# the connection, the device is long gone — simply never returns. Because reads
# hold the client lock and the coordinator only schedules its next poll once the
# current one finishes, one such read silently stopped every future poll,
# including the stall watchdog that exists to catch exactly this.
GATT_TIMEOUT = 10.0
# Without these the device is not usable: they carry the posture bit and the
# angle, and both only ever arrive as notifications. Service discovery can come
# back without them -- see _async_open -- and that has to be treated as a failed
# connection rather than a successful one that happens to be silent.
REQUIRED_CHARS = (CHAR_POSTURE_STATUS, CHAR_SMOOTH_ANGLE)
# How many times to re-attach the subscriptions before concluding that the link
# itself, and not just the subscription, is the problem.
MAX_RESUBSCRIBES = 2

# 0xFF is the app's IGNORE_VALUE; C3 BF is that byte after a UTF-8 round trip.
_FILLER_BYTES = frozenset({0x00, 0xFF, 0xC3, 0xBF})
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
    movement: MovementStatus | None = None
    mode: ConnectionMode | None = None
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

    # Cumulative posture time: live plus whatever the device recorded while
    # nothing was connected. The recorder derives per-day figures from these.
    slouching_seconds: int | None = None
    upright_seconds: int | None = None
    history_synced: datetime | None = None

    @property
    def on_charger(self) -> bool:
        """Return True while the unit is sitting on its charger.

        CHARGED means full but still plugged in, so it counts as on the
        charger just as much as CHARGING does.
        """
        return self.charging_state in (
            ChargingState.CHARGING,
            ChargingState.CHARGED,
        )

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
        # A posture change is only believed once it has held for this long.
        self.posture_debounce = DEFAULT_POSTURE_DEBOUNCE
        self._pending_posture: PostureState | None = None
        self._pending_since = 0.0
        self._last_notification = 0.0
        self._reads_ok = 0
        self._resubscribes = 0

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
        self._ensure_reconnecting()

    def _ensure_reconnecting(self) -> None:
        """Start the reconnect loop unless one is already running.

        Every route back to a live link goes through here, so a caller that
        drops the connection itself does not have to wait for the next poll to
        notice.
        """
        if self._closing:
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
            self._reconnect_task.add_done_callback(self._reconnect_finished)

    @staticmethod
    def _reconnect_finished(task: asyncio.Task[None]) -> None:
        """Surface a reconnect loop that died instead of losing it."""
        if task.cancelled():
            return
        if (err := task.exception()) is not None:
            _LOGGER.error("Reconnect loop stopped unexpectedly: %s", err)

    async def _reconnect_loop(self) -> None:
        """Reconnect with backoff until the link is restored."""
        while not self._closing and not self.connected:
            await asyncio.sleep(self._reconnect_delay)
            if self._closing:
                return
            try:
                await self.async_connect()
            except Exception as err:  # noqa: BLE001
                # Narrowing this to UprightGo2Error let anything else kill the
                # loop for good: the task ends, and only a fresh disconnect
                # callback would ever start another one.
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, RECONNECT_MAX_DELAY
                )
                _LOGGER.debug(
                    "Reconnect failed (%s); retrying in %ss",
                    err,
                    self._reconnect_delay,
                )

    async def async_connect(self) -> None:
        """Establish the link and subscribe to posture notifications."""
        async with self._lock:
            if self.connected:
                return
            self._closing = False
            await self._async_open(use_cache=True)

            if self._missing_chars():
                # Discovery came back without the characteristics the device
                # certainly has. establish_connection reuses a cached service
                # table by default, so every reconnect would be handed the same
                # broken one -- the link comes up, nothing ever notifies, and
                # there is no error anywhere to say why. Throw the cache away
                # and rediscover before treating this as a real failure.
                _LOGGER.debug("Incomplete service table; rediscovering")
                await self._async_drop(clear_cache=True)
                await self._async_open(use_cache=False)
                if missing := self._missing_chars():
                    await self._async_drop(clear_cache=True)
                    raise UprightGo2Error(
                        "Connected without the posture characteristics: "
                        + ", ".join(missing)
                    )

            self._last_notification = time.monotonic()
            self._reconnect_delay = RECONNECT_MIN_DELAY
            self._resubscribes = 0
            await self._async_subscribe()

    async def _async_open(self, *, use_cache: bool) -> None:
        """Bring the link up, optionally trusting the cached service table."""
        try:
            self._client = await establish_connection(
                BleakClient,
                self._ble_device,
                self._ble_device.address,
                disconnected_callback=self._on_disconnect,
                timeout=CONNECT_TIMEOUT,
                use_services_cache=use_cache,
            )
        except (BleakError, TimeoutError) as err:
            raise UprightGo2Error(
                f"Could not connect to {self._ble_device.address}: {err}"
            ) from err

    def _missing_chars(self) -> list[str]:
        """Return the required characteristics discovery did not find."""
        client = self._client
        if client is None:
            return list(REQUIRED_CHARS)
        try:
            services = client.services
        except BleakError:
            return list(REQUIRED_CHARS)
        return [
            uuid for uuid in REQUIRED_CHARS
            if services.get_characteristic(uuid) is None
        ]

    async def _async_drop(self, *, clear_cache: bool) -> None:
        """Close the link, optionally binning its cached service table."""
        client = self._client
        self._client = None
        if client is None:
            return
        if clear_cache:
            try:
                async with asyncio.timeout(GATT_TIMEOUT):
                    await client.clear_cache()
            except (BleakError, EOFError, TimeoutError, AttributeError) as err:
                _LOGGER.debug("Could not clear the service cache: %s", err)
        try:
            # The disconnect can hang for the same reason the link did.
            async with asyncio.timeout(GATT_TIMEOUT):
                await client.disconnect()
        except (BleakError, EOFError, TimeoutError) as err:
            _LOGGER.debug("Error dropping the link: %s", err)

    async def _async_subscribe(self) -> None:
        """Attach to the characteristics that push updates."""
        assert self._client is not None
        for uuid, handler in (
            (CHAR_POSTURE_STATUS, self._handle_posture),
            (CHAR_SMOOTH_ANGLE, self._handle_angle),
            (CHAR_VIBRATION_STATUS, self._handle_vibration),
            (CHAR_ONLINE_DATA, self._handle_online),
        ):
            try:
                await self._client.start_notify(uuid, handler)
            except (BleakError, TimeoutError) as err:
                # Not every firmware exposes all four as notifiable; the slow
                # poll still picks these values up. The ones that matter are
                # checked before we get here.
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
        self._last_notification = time.monotonic()
        self._resubscribes = 0
        if self._notify_callback is not None:
            self._notify_callback(self.data)

    @property
    def stalled(self) -> bool:
        """Return True when the link is up but nothing is coming through."""
        if not self.connected or not self._last_notification:
            return False
        return time.monotonic() - self._last_notification > NOTIFY_TIMEOUT

    async def async_force_reconnect(self) -> None:
        """Drop a dead link and start rebuilding it straight away."""
        self._last_notification = 0.0
        # A link worth killing is one whose cached service table is suspect.
        await self._async_drop(clear_cache=True)
        # Do not rely on the disconnect callback firing: on a half-open link it
        # is exactly the thing that failed to arrive.
        self._ensure_reconnecting()

    def _handle_posture(
        self, _char: BleakGATTCharacteristic, payload: bytearray
    ) -> None:
        if not payload:
            return
        try:
            raw = PostureState(payload[0])
        except ValueError:
            _LOGGER.debug("Unknown posture state %s", payload[0])
            return

        # The device flips this several times a second while you hover around
        # the threshold. Taking each flip at face value made the binary sensor
        # unusable for automations and polluted the time accounting, so a new
        # value has to hold before it counts.
        if raw is not self._pending_posture:
            self._pending_posture = raw
            self._pending_since = time.monotonic()
        if self.settle_posture():
            self._publish()

    def settle_posture(self) -> bool:
        """Commit a pending posture once it has held long enough.

        Returns True when the committed value changed. Called from the angle
        notifications and the poll as well, so a posture that stops chattering
        still lands even if no further posture notification arrives.
        """
        pending = self._pending_posture
        if pending is None or pending is self.data.posture:
            return False
        if time.monotonic() - self._pending_since < self.posture_debounce:
            return False
        self.data.posture = pending
        return True

    def _handle_angle(
        self, _char: BleakGATTCharacteristic, payload: bytearray
    ) -> None:
        if (angle := decode_angle(bytes(payload))) is None:
            return
        self.data.angle = angle
        self.settle_posture()
        self._publish()

    def _handle_online(
        self, _char: BleakGATTCharacteristic, payload: bytearray
    ) -> None:
        """Decode a live interval record.

        ONLINE_DATA carries the same one-byte records as the stored history, so
        bits 5-4 give the movement state the app shows as sitting vs moving.
        """
        if not payload:
            return
        try:
            movement = MovementStatus((payload[0] >> 4) & 0x03)
        except ValueError:
            return
        if movement is self.data.movement:
            return
        self.data.movement = movement
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
            async with asyncio.timeout(GATT_TIMEOUT):
                value = bytes(await self._client.read_gatt_char(uuid))
        except (BleakError, EOFError, TimeoutError) as err:
            _LOGGER.debug("Could not read %s: %s", uuid, err)
            return None
        self._reads_ok += 1
        return value

    @staticmethod
    def _decode_text(payload: bytes | None) -> str | None:
        """Decode a Device Information value.

        These are not always text: the serial number comes back as raw bytes
        padded with 0xFF (the app's IGNORE_VALUE), which decoded as UTF-8 turns
        into a row of replacement characters. Render printable payloads as
        text and anything else as hex, which is what the app's
        decodeByteArrayHex helper does.
        """
        if not payload:
            return None
        trimmed = payload.strip(b"\xff\x00")
        if not trimmed:
            return None

        # Some units ship with the serial never programmed. The field is 0xFF,
        # partly UTF-8 encoded so it arrives as runs of C3 BF — and not always
        # aligned, so it will not decode cleanly. If nothing but those filler
        # bytes is left, the value is absent rather than an identifier.
        if not (set(trimmed) - _FILLER_BYTES):
            return None

        if all(0x20 <= byte < 0x7F for byte in trimmed):
            return trimmed.decode("ascii").strip() or None
        return trimmed.hex().upper()

    async def async_poll(self) -> UprightGo2Data:
        """Refresh the values that have no notification."""
        if self.stalled:
            # bleak still reports the link as connected, but nothing has
            # arrived. Usually only the subscriptions died, and re-attaching
            # costs one round trip; tearing down a link that still reads fine
            # just makes every entity unavailable for no reason. Give that a
            # couple of tries before concluding the link itself is the problem.
            if self._resubscribes < MAX_RESUBSCRIBES and not self._missing_chars():
                self._resubscribes += 1
                _LOGGER.debug(
                    "No notifications for %.0fs — resubscribing (%s/%s)",
                    NOTIFY_TIMEOUT,
                    self._resubscribes,
                    MAX_RESUBSCRIBES,
                )
                async with self._lock:
                    await self._async_subscribe()
                self._last_notification = time.monotonic()
            else:
                _LOGGER.warning(
                    "No notifications for %.0fs — dropping the link and reconnecting",
                    NOTIFY_TIMEOUT,
                )
                await self.async_force_reconnect()
                raise UprightGo2Error("Notification stream stalled")

        if not self.connected:
            await self.async_connect()

        async with self._lock:
            data = self.data
            self._reads_ok = 0

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

            self.settle_posture()

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

            if (general := await self._read(CHAR_GENERAL_SETTING)) and len(
                general
            ) > OFFSET_COMPATIBILITY:
                data.mode = (
                    ConnectionMode.MSK
                    if general[OFFSET_COMPATIBILITY] == COMPATIBILITY_MODE_MSK
                    else ConnectionMode.POSTURE
                )

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
            if not self._reads_ok:
                # Every read came back empty. Reporting success here would leave
                # the entities on stale values and let the posture clock keep
                # crediting time against a device that is not answering.
                # The link is proven dead, so rebuild it now instead of waiting
                # out the stall watchdog for another minute of silence.
                await self.async_force_reconnect()
                raise UprightGo2Error("Device did not answer any read")

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
                async with asyncio.timeout(GATT_TIMEOUT):
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

    async def async_download_history(self, timeout: float = 90.0) -> list[Interval]:
        """Download the stored posture history and parse it.

        The device streams the whole dump on OFFLINE_DATA by itself once the
        transfer starts; DATA_AMOUNT says how many bytes to expect. Note this
        only covers stretches when nothing was connected — the device records
        to flash while offline, so with a live connection held open there is
        very little here. Nothing is deleted from the device.
        """
        if not self.connected:
            await self.async_connect()

        async with self._lock:
            if self._client is None:
                raise UprightGo2Error("Not connected")

            # Anchor the device clock against wall time so the timestamps in
            # the dump land on real dates whatever epoch the firmware uses.
            clock_offset = 0.0
            if (now := await self._read(CHAR_CURRENT_TIMESTAMP)) and len(now) >= 5:
                device_now = decode_session_timestamp(now)
                if device_now:
                    clock_offset = time.time() - device_now

            pending: int | None = None
            sessions: int | None = None
            if amount := await self._read(CHAR_DATA_AMOUNT):
                pending, sessions = decode_data_amount(amount)

            # Drive the download by how many bytes the device says it holds
            # rather than by an end marker. Framing is ambiguous until the
            # whole dump is in hand, so a marker spotted mid-stream may not be
            # one — that is what cut earlier downloads short.
            wanted = pending

            buffer = bytearray()
            finished = asyncio.Event()
            packets = 0

            packet_seen = asyncio.Event()

            def on_packet(_char: BleakGATTCharacteristic, payload: bytearray) -> None:
                nonlocal packets
                packets += 1
                buffer.extend(payload)
                packet_seen.set()

                # The device streams the whole dump by itself once started —
                # the app never writes SEND_NEXT, it only counts bytes until it
                # has as many as DATA_AMOUNT reported. Acknowledging each packet
                # is not part of the protocol and appeared to stall the stream.
                if not payload or (wanted is not None and len(buffer) >= wanted):
                    finished.set()

            try:
                await self._client.start_notify(CHAR_OFFLINE_DATA, on_packet)
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(
                    f"Could not subscribe to the history stream: {err}"
                ) from err

            try:
                async with asyncio.timeout(GATT_TIMEOUT):
                    await self._client.write_gatt_char(
                        CHAR_DATA_COMMAND,
                        bytes([DataTransferCommand.START_TRANSFER_NO_APPROVAL]),
                        response=True,
                    )
                # Stop on the overall deadline, but also give up early once the
                # device has gone quiet — waiting the full timeout for packets
                # that will never come just holds the connection hostage.
                deadline = time.monotonic() + timeout
                while not finished.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        _LOGGER.warning(
                            "History download hit its %ss deadline: %d packets,"
                            " %d of ~%s bytes. Keeping what arrived.",
                            timeout,
                            packets,
                            len(buffer),
                            wanted if wanted is not None else "?",
                        )
                        break
                    packet_seen.clear()
                    try:
                        await asyncio.wait_for(
                            packet_seen.wait(), min(IDLE_TIMEOUT, remaining)
                        )
                    except TimeoutError:
                        if not finished.is_set():
                            _LOGGER.debug(
                                "Device went quiet after %d packets, %d bytes",
                                packets,
                                len(buffer),
                            )
                        break
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(f"History download failed: {err}") from err
            finally:
                try:
                    await self._client.stop_notify(CHAR_OFFLINE_DATA)
                except (BleakError, EOFError, TimeoutError) as err:
                    _LOGGER.debug("Could not stop the history stream: %s", err)

            expected = (
                expected_record_count(pending, sessions)
                if pending is not None
                else None
            )
            frequency, _ = detect_frequency(bytes(buffer), expected)

            # Re-frame with the detected nibble so the timestamps are anchored.
            framer = StreamFramer(frequency, clock_offset)
            framer.feed(bytes(buffer))

            _LOGGER.debug(
                "History: %d packets, %d of ~%s bytes (%s sessions, ~%s records"
                " expected); framed %d records with header nibble %s",
                packets,
                len(buffer),
                pending,
                sessions,
                expected,
                len(framer.intervals),
                frequency,
            )
            return framer.intervals

    async def async_clear_history(self) -> None:
        """Erase the stored history on the device."""
        await self._async_write(
            CHAR_DATA_COMMAND, bytes([DataTransferCommand.DELETE_DATA])
        )

    async def async_set_mode(self, mode: ConnectionMode) -> None:
        """Switch the device between the posture and MSK programmes.

        The app writes a whole settings preset for this; only the byte that
        distinguishes the two is touched here, so the wearer's own delay,
        pattern and sensitivity survive the switch.
        """
        if not self.connected:
            await self.async_connect()

        async with self._lock:
            if self._client is None:
                raise UprightGo2Error("Not connected")

            current = await self._read(CHAR_GENERAL_SETTING)
            if not current or len(current) <= OFFSET_COMPATIBILITY:
                raise UprightGo2Error("Device did not return general settings")

            payload = bytearray(current)
            payload[OFFSET_COMPATIBILITY] = (
                COMPATIBILITY_MODE_MSK
                if mode is ConnectionMode.MSK
                else COMPATIBILITY_MODE_POSTURE
            )
            try:
                async with asyncio.timeout(GATT_TIMEOUT):
                    await self._client.write_gatt_char(
                        CHAR_GENERAL_SETTING, bytes(payload), response=True
                    )
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(f"Could not switch mode: {err}") from err
            self.data.mode = mode

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
                async with asyncio.timeout(GATT_TIMEOUT):
                    await self._client.write_gatt_char(
                        CHAR_FREESTYLE_SETTING, bytes(payload), response=True
                    )
            except (BleakError, TimeoutError) as err:
                raise UprightGo2Error(f"Could not write settings: {err}") from err
