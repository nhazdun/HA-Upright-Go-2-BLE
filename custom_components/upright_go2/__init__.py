"""The Upright GO 2 integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import bluetooth
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import UprightGo2ConfigEntry, UprightGo2Coordinator

_LOGGER = logging.getLogger(__name__)

CARD_URL = f"/{DOMAIN}/upright-go2-card.js"
CARD_REGISTERED = "card_registered"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the dashboard card and load it, without a manual resource entry."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(CARD_REGISTERED):
        return
    data[CARD_REGISTERED] = True

    path = Path(__file__).parent / "www" / "upright-go2-card.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(path), True)]
    )
    # Version the URL so a browser does not keep serving a stale card.
    add_extra_js_url(hass, f"{CARD_URL}?v={_card_version(hass)}")
    _LOGGER.debug("Registered dashboard card at %s", CARD_URL)


def _card_version(hass: HomeAssistant) -> str:
    """Return the integration version, for cache-busting the card URL."""
    from homeassistant.loader import async_get_loaded_integration

    try:
        return async_get_loaded_integration(hass, DOMAIN).version or "0"
    except Exception:  # noqa: BLE001 - a missing version must not break setup
        return "0"


async def async_setup_entry(hass: HomeAssistant, entry: UprightGo2ConfigEntry) -> bool:
    """Set up Upright GO 2 from a config entry."""
    address: str = entry.data[CONF_ADDRESS]

    await _async_register_card(hass)

    if not bluetooth.async_ble_device_from_address(hass, address, connectable=True):
        raise ConfigEntryNotReady(
            f"Upright GO 2 {address} is not in range of a connectable Bluetooth"
            " adapter or proxy"
        )

    coordinator = UprightGo2Coordinator(hass, entry, address)
    await coordinator.async_load()
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
