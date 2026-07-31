"""The Upright GO 2 integration."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .coordinator import UprightGo2ConfigEntry, UprightGo2Coordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: UprightGo2ConfigEntry) -> bool:
    """Set up Upright GO 2 from a config entry."""
    address: str = entry.data[CONF_ADDRESS]

    if not bluetooth.async_ble_device_from_address(hass, address, connectable=True):
        raise ConfigEntryNotReady(
            f"Upright GO 2 {address} is not in range of a connectable Bluetooth"
            " adapter or proxy"
        )

    coordinator = UprightGo2Coordinator(hass, entry, address)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UprightGo2ConfigEntry) -> bool:
    """Unload a config entry, releasing the device's single connection slot."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: UprightGo2ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
