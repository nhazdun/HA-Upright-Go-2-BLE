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
from homeassistant.helpers import entity_registry as er

from .binary_sensor import BINARY_SENSORS
from .button import BUTTONS
from .const import DOMAIN
from .coordinator import UprightGo2ConfigEntry, UprightGo2Coordinator
from .number import NUMBERS
from .select import MODE, PATTERN, STRENGTH
from .sensor import SENSORS
from .switch import VIBRATION

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


def _async_forget_removed_entities(
    hass: HomeAssistant, entry: UprightGo2ConfigEntry, address: str
) -> None:
    """Drop registry entries for entities this version no longer creates.

    Home Assistant keeps an entity in the registry after the integration stops
    creating it, so every entity dropped along the way — the per-day totals, the
    duplicate charging and posture sensors, the unverified error pair — lingered
    on the device page as "unavailable". That reads as a broken integration.

    The valid set is derived from the same descriptions the platforms use, so
    this needs no maintenance as entities come and go.
    """
    valid = {description.key for description in SENSORS}
    valid |= {description.key for description in BINARY_SENSORS}
    valid |= {description.key for description in BUTTONS}
    valid |= {description.key for description in NUMBERS}
    valid |= {MODE.key, PATTERN.key, STRENGTH.key, VIBRATION.key}
    known = {f"{address}_{key}" for key in valid}

    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id in known:
            continue
        _LOGGER.info(
            "Removing %s: no longer provided by this integration", entity.entity_id
        )
        registry.async_remove(entity.entity_id)


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
    versioned = f"{CARD_URL}?v={_card_version(hass)}"

    # Loading it as an extra module alone leaves a race: the dashboard can
    # render before the module has evaluated, and a card whose element is not
    # defined yet shows "Configuration error" until the page is reloaded.
    # Lovelace waits for its own resources before rendering, so register there
    # too — the browser caches the module by URL, so it is fetched once.
    if not await _async_register_lovelace_resource(hass, versioned):
        add_extra_js_url(hass, versioned)
    _LOGGER.debug("Registered dashboard card at %s", versioned)


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Add the card to Lovelace's resources, replacing any older version.

    Returns False if Lovelace is not in storage mode or its API has moved, so
    the caller can fall back to a plain frontend module.
    """
    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None)
        if resources is None:
            return False
        if hasattr(resources, "async_get_info"):
            await resources.async_get_info()

        existing = [
            item
            for item in resources.async_items()
            if str(item.get("url", "")).startswith(CARD_URL)
        ]
        if any(item.get("url") == url for item in existing):
            return True

        for item in existing:  # a stale version of our own card
            await resources.async_delete_item(item["id"])
        await resources.async_create_item({"res_type": "module", "url": url})
    except Exception as err:  # noqa: BLE001 - never block setup on the frontend
        _LOGGER.debug("Could not register the Lovelace resource: %s", err)
        return False
    return True


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
    _async_forget_removed_entities(hass, entry, address)

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
