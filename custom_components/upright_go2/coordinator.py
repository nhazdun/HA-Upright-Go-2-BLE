"""Coordinator for the Upright GO 2.

Posture and angle arrive as BLE notifications and are pushed straight into the
coordinator; everything else is re-read on the slower interval over the same
connection.

The two posture totals are cumulative counters. Live time is added as it
elapses, and the device's stored history tops up the stretches nothing was
connected for. Home Assistant's recorder turns those counters into per-day
statistics on its own, so no per-day entities are needed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import UprightGo2Client, UprightGo2Data, UprightGo2Error
from .const import (
    ANGLE_HEARTBEAT,
    CONF_ANGLE_DELTA,
    CONF_HISTORY_INTERVAL,
    CONF_POSTURE_DEBOUNCE,
    CONF_SCAN_INTERVAL,
    DEFAULT_ANGLE_DELTA,
    DEFAULT_HISTORY_INTERVAL,
    DEFAULT_POSTURE_DEBOUNCE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ConnectionMode,
    PostureState,
)
from .history import MAX_LIVE_GAP, PostureClock, history_since

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 30
# The device pushes the angle ~15 times a second. Publishing all of it floods
# the recorder; publishing once a second makes the figure visibly lag. Half a
# second is the compromise: motion still reads as live, and the write rate is
# ~7x lower than the device's. See the README for excluding the angle sensor
# from the recorder if database size matters more than its history.
MIN_PUSH_INTERVAL = 0.5
# Longer than any single poll should ever take, short enough that a wedged one
# costs a cycle rather than the rest of the day.
POLL_TIMEOUT = 45.0
# How current the watermark has to be before a charge is allowed to advance it.
# Same bound the clock uses for a plausible live gap.
CHARGE_WATERMARK_GAP = timedelta(seconds=MAX_LIVE_GAP)

type UprightGo2ConfigEntry = ConfigEntry[UprightGo2Coordinator]


class UprightGo2Coordinator(DataUpdateCoordinator[UprightGo2Data]):
    """Keep a live link to the device and surface its state."""

    config_entry: UprightGo2ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: UprightGo2ConfigEntry, address: str
    ) -> None:
        """Initialise the coordinator."""
        self.address = address
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {address}",
            update_interval=timedelta(seconds=interval),
        )
        self._client: UprightGo2Client | None = None
        self._history_interval = timedelta(
            seconds=entry.options.get(CONF_HISTORY_INTERVAL, DEFAULT_HISTORY_INTERVAL)
        )
        self._history_task: asyncio.Task[None] | None = None
        self._clock = PostureClock()
        self._store: Store[dict[str, object]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{address.replace(':', '').lower()}"
        )
        # Cumulative seconds, kept as floats so sub-second credits survive.
        self._slouching = 0.0
        self._upright = 0.0
        self._counted_until: datetime | None = None
        self._last_push: datetime | None = None
        self._last_posture: PostureState | None = None
        self._last_angle: float | None = None
        self._angle_delta = float(
            entry.options.get(CONF_ANGLE_DELTA, DEFAULT_ANGLE_DELTA)
        )
        self._posture_debounce = float(
            entry.options.get(CONF_POSTURE_DEBOUNCE, DEFAULT_POSTURE_DEBOUNCE)
        )

    async def async_load(self) -> None:
        """Restore the counters so a restart does not reset them to zero."""
        stored = await self._store.async_load()
        if not stored:
            return
        self._slouching = float(stored.get("slouching_seconds") or 0.0)
        self._upright = float(stored.get("upright_seconds") or 0.0)
        if counted := stored.get("counted_until"):
            self._counted_until = dt_util.parse_datetime(str(counted))
        _LOGGER.debug(
            "Restored totals: %.0fs slouching, %.0fs upright, counted to %s",
            self._slouching,
            self._upright,
            self._counted_until,
        )

    def _save(self) -> None:
        """Persist the counters, debounced."""
        self._store.async_delay_save(
            lambda: {
                "slouching_seconds": self._slouching,
                "upright_seconds": self._upright,
                "counted_until": (
                    self._counted_until.isoformat() if self._counted_until else None
                ),
            },
            SAVE_DELAY,
        )

    def _publish(self, data: UprightGo2Data) -> None:
        """Copy the counters onto the snapshot the entities read."""
        data.slouching_seconds = round(self._slouching)
        data.upright_seconds = round(self._upright)

    def _tick(self, data: UprightGo2Data, now: datetime) -> None:
        """Credit the time since the last tick against the current posture."""
        if data.posture is None:
            return

        if data.on_charger:
            # On the charger the unit is off your back, so its posture bit
            # describes a device lying on a desk rather than a person. Stop the
            # clock; otherwise a night on the charger lands as eight hours of
            # perfect posture.
            #
            # Discard what the clock still owed rather than banking it: the
            # stretch that ends with the unit on a charger is the stretch
            # during which it came off your back, so it is not time in any
            # posture either.
            self._clock.pause(now)
            slouching = upright = 0.0
        else:
            slouching, upright = self._clock.update(
                data.posture is PostureState.SLOUCH, now
            )

        if slouching or upright:
            self._slouching += slouching
            self._upright += upright
            self._counted_until = now
            self._save()
        elif self._counted_until is None:
            self._counted_until = now
        elif data.on_charger and now - self._counted_until < CHARGE_WATERMARK_GAP:
            # Walk the watermark through the charge so this stretch is not
            # credited later out of the device's own recording. Only while the
            # watermark is already current: if it is far behind, that is a real
            # offline backlog and moving it would throw the backlog away.
            self._counted_until = now
            self._save()

        self._publish(data)

    def _handle_notification(self, data: UprightGo2Data) -> None:
        """Push a notification-driven update to the entities.

        Bleak may deliver this from a backend thread, so hop to the event loop.
        """
        self.hass.loop.call_soon_threadsafe(self._apply_notification, data)

    def _apply_notification(self, data: UprightGo2Data) -> None:
        """Bank live posture time, then decide whether it is worth publishing.

        The device pushes the angle around fifteen times a second. Rate alone
        is not enough of a filter — capping it still wrote every wobble, about
        150k recorder rows a day from this one entity. So the angle has to have
        actually moved before it is published, with a heartbeat so its history
        does not go silent while you sit still.

        Posture is exempt from the rate cap because it is the moment worth
        reacting to, and it is safe to be: the value is debounced upstream, so
        this only ever sees a change that has already held.
        """
        now = dt_util.utcnow()
        self._tick(data, now)

        posture_changed = data.posture is not self._last_posture
        moved = (
            self._last_angle is None
            or data.angle is None
            or abs(data.angle - self._last_angle) >= self._angle_delta
        )
        elapsed = (now - self._last_push).total_seconds() if self._last_push else None
        stale = elapsed is None or elapsed >= ANGLE_HEARTBEAT
        rate_ok = elapsed is None or elapsed >= MIN_PUSH_INTERVAL

        if not (posture_changed or stale or (moved and rate_ok)):
            return

        self._last_posture = data.posture
        self._last_angle = data.angle
        self._last_push = now
        self.async_set_updated_data(data)

    def _get_client(self) -> UprightGo2Client:
        """Return a client bound to the current BLEDevice.

        A connected peripheral stops advertising, so once the link is up the
        address drops out of the Bluetooth manager's cache and the lookup below
        returns nothing. Trusting that would fail every button press and switch
        toggle with "not in range" while the device is plainly streaming — so
        a live client is handed back as-is.
        """
        if self._client is not None and self._client.connected:
            return self._client

        # Not connected: look the device up fresh, since it may have moved
        # between the local adapter and a proxy.
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(
                f"Upright GO 2 {self.address} is not in range of any Bluetooth adapter"
                " or proxy"
            )
        if self._client is None:
            self._client = UprightGo2Client(ble_device, self._handle_notification)
        else:
            self._client.set_ble_device(ble_device)
        self._client.posture_debounce = self._posture_debounce
        return self._client

    async def _async_update_data(self) -> UprightGo2Data:
        """Reconnect if needed and refresh the non-notifying values."""
        client = self._get_client()
        try:
            # A second line of defence behind the per-operation deadlines: the
            # coordinator schedules its next poll only after this one returns,
            # so anything that blocks here indefinitely takes the integration
            # down for good rather than for one cycle.
            async with asyncio.timeout(POLL_TIMEOUT):
                data = await client.async_poll()
        except TimeoutError as err:
            self._clock.pause(dt_util.utcnow())
            await client.async_force_reconnect()
            raise UpdateFailed("Timed out polling the device") from err
        except UprightGo2Error as err:
            # The link is down, so stop the clock rather than banking the
            # outage as posture time once it comes back.
            self._clock.pause(dt_util.utcnow())
            raise UpdateFailed(str(err)) from err

        # Tick here too: POSTURE_STATUS only notifies on a change, so sitting
        # straight for an hour produces no events at all.
        self._tick(data, dt_util.utcnow())

        # Nothing recorded while the unit sits on its charger is worth having,
        # so do not go and fetch it. The manual Sync history button still works
        # for anyone who disagrees.
        due = not data.on_charger and (
            data.history_synced is None
            or dt_util.utcnow() - data.history_synced >= self._history_interval
        )
        if due and (self._history_task is None or self._history_task.done()):
            # Never await this here. A dump can take a minute, and the first
            # refresh runs during setup — awaiting it left the whole entry
            # stuck in setup_in_progress.
            self._history_task = self.config_entry.async_create_background_task(
                self.hass, self._async_sync_history_safe(), f"{DOMAIN} history sync"
            )

        return data

    async def _async_sync_history_safe(self) -> None:
        """Run a history sync without letting failures escape."""
        try:
            await self.async_sync_history()
        except UprightGo2Error as err:
            _LOGGER.debug("History sync failed: %s", err)
        except Exception:  # noqa: BLE001 - background task must not die silently
            _LOGGER.exception("Unexpected error while syncing history")

    async def async_sync_history(self) -> None:
        """Top the counters up with whatever the device recorded while away."""
        client = self._get_client()
        # Stamp first: a sync that fails should not be retried on every tick.
        client.data.history_synced = dt_util.utcnow()
        intervals = await client.async_download_history()
        if not intervals:
            return

        slouching, upright, newest = history_since(intervals, self._counted_until)
        if slouching or upright:
            self._slouching += slouching
            self._upright += upright
            self._counted_until = newest
            self._save()
            _LOGGER.debug(
                "Topped up from device history: +%ds slouching, +%ds upright"
                " (%d intervals, counted to %s)",
                slouching,
                upright,
                len(intervals),
                newest,
            )

        self._publish(client.data)
        self.async_set_updated_data(client.data)

    async def async_shutdown(self) -> None:
        """Drop the connection when the entry unloads."""
        slouching, upright = self._clock.pause(dt_util.utcnow())
        if slouching or upright:
            self._slouching += slouching
            self._upright += upright
            self._counted_until = dt_util.utcnow()
        # Write through immediately; a debounced save may not survive a reload.
        await self._store.async_save(
            {
                "slouching_seconds": self._slouching,
                "upright_seconds": self._upright,
                "counted_until": (
                    self._counted_until.isoformat() if self._counted_until else None
                ),
            }
        )
        await super().async_shutdown()
        if self._client is not None:
            await self._client.async_disconnect()
            self._client = None

    async def async_run(self, action: str, **kwargs: int | bool) -> None:
        """Run a write action, then refresh so entities reflect the result."""
        client = self._get_client()
        try:
            match action:
                case "calibrate":
                    await client.async_calibrate()
                case "clear_calibration":
                    await client.async_clear_calibration()
                case "deep_sleep":
                    await client.async_deep_sleep()
                case "set_vibration":
                    await client.async_set_vibration(bool(kwargs["enabled"]))
                case "update_freestyle":
                    await client.async_update_freestyle(**kwargs)  # type: ignore[arg-type]
                case "sync_history":
                    await self.async_sync_history()
                    return
                case "set_mode":
                    await client.async_set_mode(ConnectionMode(kwargs["mode"]))
                case "clear_history":
                    await client.async_clear_history()
                case _:
                    raise ValueError(f"Unknown action {action}")
        except UprightGo2Error as err:
            raise UpdateFailed(str(err)) from err
        await self.async_request_refresh()
