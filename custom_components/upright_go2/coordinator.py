"""Polling coordinator for the Upright GO 2."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UprightGo2Client, UprightGo2Data, UprightGo2Error
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type UprightGo2ConfigEntry = ConfigEntry[UprightGo2Coordinator]


class UprightGo2Coordinator(DataUpdateCoordinator[UprightGo2Data]):
    """Poll the device over BLE on a fixed interval."""

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
            self._client = UprightGo2Client(ble_device)
        else:
            self._client.set_ble_device(ble_device)
        return self._client

    async def _async_update_data(self) -> UprightGo2Data:
        """Fetch a fresh snapshot from the device."""
        client = self._get_client()
        try:
            return await client.async_poll()
        except UprightGo2Error as err:
            raise UpdateFailed(str(err)) from err

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
                case _:
                    raise ValueError(f"Unknown action {action}")
        except UprightGo2Error as err:
            raise UpdateFailed(str(err)) from err
        await self.async_request_refresh()
