"""Coordinator for the Upright GO 2.

Posture and angle arrive as BLE notifications and are pushed straight into the
coordinator; everything else is re-read on the slower interval over the same
connection.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
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
)
from .history import hourly_totals, summarise

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

    def _handle_notification(self, data: UprightGo2Data) -> None:
        """Push a notification-driven update to the entities.

        Bleak may deliver this from a backend thread, so hop to the event loop.
        """
        self.hass.loop.call_soon_threadsafe(self.async_set_updated_data, data)

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

        due = data.history_synced is None or (
            dt_util.utcnow() - data.history_synced >= self._history_interval
        )
        if due:
            try:
                await self.async_sync_history()
            except UprightGo2Error as err:
                # The live values are already in hand; a failed history sync
                # should not mark the whole update as failed.
                _LOGGER.debug("History sync failed: %s", err)

        return data

    async def async_sync_history(self) -> None:
        """Download the stored history and fold it into totals and statistics."""
        client = self._get_client()
        intervals = await client.async_download_history()
        if not intervals:
            client.data.history_synced = dt_util.utcnow()
            return

        timezone = dt_util.get_default_time_zone()
        summary = summarise(intervals, timezone)
        today = dt_util.now().date().isoformat()
        client.data.slouching_today = summary.slouching.get(today, 0)
        client.data.upright_today = summary.upright.get(today, 0)
        client.data.history_synced = dt_util.utcnow()

        await self._async_write_statistics(hourly_totals(intervals, timezone))
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
            last = await get_instance(self.hass).async_add_executor_job(
                get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
            )

            total = 0.0
            latest = None
            if rows := (last or {}).get(statistic_id):
                total = float(rows[0].get("sum") or 0.0)
                latest = rows[0].get("start")

            stats: list[StatisticData] = []
            for hour in sorted(buckets):
                if latest is not None and hour.timestamp() <= latest:
                    continue
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
                },
                stats,
            )

    async def async_shutdown(self) -> None:
        """Drop the connection when the entry unloads."""
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
