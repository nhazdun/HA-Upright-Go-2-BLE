"""Coordinator for the Upright GO 2.

Posture and angle arrive as BLE notifications and are pushed straight into the
coordinator; everything else is re-read on the slower interval over the same
connection.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.components.recorder.models import StatisticData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
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
from .history import LiveTracker, hourly_totals, merge_buckets, summarise

try:  # HA 2025.11 replaced has_mean with mean_type
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN_META: dict[str, object] = {"mean_type": StatisticMeanType.NONE}
except ImportError:  # pragma: no cover - older cores
    _MEAN_META = {"has_mean": False}

_LOGGER = logging.getLogger(__name__)

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
        self._slug = address.replace(":", "").lower()
        self._history_task: asyncio.Task[None] | None = None
        self._live = LiveTracker()
        self._offline_today: tuple[int, int] = (0, 0)

    def _handle_notification(self, data: UprightGo2Data) -> None:
        """Push a notification-driven update to the entities.

        Bleak may deliver this from a backend thread, so hop to the event loop.
        """
        self.hass.loop.call_soon_threadsafe(self._apply_notification, data)

    def _apply_notification(self, data: UprightGo2Data) -> None:
        """Bank live posture time, then publish the update."""
        if data.posture is not None:
            self._live.update(data.posture is PostureState.SLOUCH, dt_util.utcnow())
            self._refresh_today(data)
        self.async_set_updated_data(data)

    def _refresh_today(self, data: UprightGo2Data) -> None:
        """Recompute today's totals from stored history plus live time."""
        timezone = dt_util.get_default_time_zone()
        today = dt_util.now().date().isoformat()
        live_slouch, live_upright = self._live.totals_for(today, timezone)
        data.slouching_today = self._offline_today[0] + live_slouch
        data.upright_today = self._offline_today[1] + live_upright

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
            raise UpdateFailed(str(err)) from err

        # Bank live time on every tick, not only when posture changes.
        # POSTURE_STATUS only notifies on a change, so sitting straight for an
        # hour produces no events — and a span that long would then be thrown
        # away as an implausible gap.
        if data.posture is not None:
            self._live.update(data.posture is PostureState.SLOUCH, dt_util.utcnow())
            self._refresh_today(data)

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
        """Download the stored history and fold it into totals and statistics."""
        client = self._get_client()
        # Stamp first: a sync that fails should not be retried on every tick.
        client.data.history_synced = dt_util.utcnow()
        intervals = await client.async_download_history()
        if not intervals:
            client.data.history_synced = dt_util.utcnow()
            return

        timezone = dt_util.get_default_time_zone()
        summary = summarise(intervals, timezone)
        now = dt_util.now()
        today = now.date().isoformat()
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        self._offline_today = (
            summary.slouching.get(today, 0),
            summary.upright.get(today, 0),
        )
        self._refresh_today(client.data)
        client.data.slouching_yesterday = summary.slouching.get(yesterday, 0)
        client.data.upright_yesterday = summary.upright.get(yesterday, 0)
        client.data.history_days = len(summary.days)
        client.data.history_synced = dt_util.utcnow()

        self._live.prune(dt_util.utcnow() - timedelta(days=14))
        await self._async_write_statistics(
            merge_buckets(hourly_totals(intervals, timezone), self._live.buckets)
        )
        self.async_set_updated_data(client.data)
        _LOGGER.debug(
            "Synced %d intervals covering %d day(s)", len(intervals), len(summary.days)
        )

    async def _async_write_statistics(
        self, buckets: dict[object, tuple[int, int]]
    ) -> None:
        """Feed hourly totals to the recorder as external statistics.

        Hourly buckets let the statistics UI roll the data up per day, and
        because they carry real timestamps the history stays correct for
        periods when Home Assistant was not connected at all.
        """
        for key, position, label in (
            ("slouching", 0, "Slouching time"),
            ("upright", 1, "Upright time"),
        ):
            statistic_id = f"{DOMAIN}:{self._slug}_{key}_seconds"

            # Rewrite every bucket rather than appending only new ones. The
            # whole device history is downloaded each time, so recomputing the
            # cumulative sum from scratch backfills days Home Assistant never
            # saw and corrects days an earlier partial download got wrong.
            # Skipping anything older than the last stored hour would leave
            # both of those permanently stale.
            total = 0.0
            stats: list[StatisticData] = []
            for hour in sorted(buckets):
                seconds = buckets[hour][position]
                total += seconds
                stats.append(StatisticData(start=hour, state=seconds, sum=total))

            if not stats:
                continue

            async_add_external_statistics(
                self.hass,
                {
                    **_MEAN_META,
                    "has_sum": True,
                    "name": f"Upright GO 2 {label}",
                    "source": DOMAIN,
                    "statistic_id": statistic_id,
                    "unit_of_measurement": UnitOfTime.SECONDS,
                    "unit_class": "duration",
                },
                stats,
            )

    async def async_shutdown(self) -> None:
        """Drop the connection when the entry unloads."""
        self._live.pause(dt_util.utcnow())
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
