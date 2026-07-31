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
    CONF_HISTORY_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_HISTORY_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PostureState,
)
from .history import PostureClock, history_since

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 30

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
        self._publish(data)

    def _handle_notification(self, data: UprightGo2Data) -> None:
        """Push a notification-driven update to the entities.

        Bleak may deliver this from a backend thread, so hop to the event loop.
        """
        self.hass.loop.call_soon_threadsafe(self._apply_notification, data)

    def _apply_notification(self, data: UprightGo2Data) -> None:
        """Bank live posture time, then publish the update."""
        self._tick(data, dt_util.utcnow())
        self.async_set_updated_data(data)

    def _get_client(self) -> UprightGo2Client:
        """Return a client bound to the current BLEDevice.

        The device may hop between the local adapter and a proxy, so the
        BLEDevice is looked up fresh rather than cached.
        """
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
        return self._client

    async def _async_update_data(self) -> UprightGo2Data:
        """Reconnect if needed and refresh the non-notifying values."""
        client = self._get_client()
        try:
            data = await client.async_poll()
        except UprightGo2Error as err:
            # The link is down, so stop the clock rather than banking the
            # outage as posture time once it comes back.
            self._clock.pause(dt_util.utcnow())
            raise UpdateFailed(str(err)) from err

        # Tick here too: POSTURE_STATUS only notifies on a change, so sitting
        # straight for an hour produces no events at all.
        self._tick(data, dt_util.utcnow())

        due = data.history_synced is None or (
            dt_util.utcnow() - data.history_synced >= self._history_interval
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
                case "clear_history":
                    await client.async_clear_history()
                case _:
                    raise ValueError(f"Unknown action {action}")
        except UprightGo2Error as err:
            raise UpdateFailed(str(err)) from err
        await self.async_request_refresh()
