"""Config flow for the Upright GO 2 integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_HISTORY_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_HISTORY_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    KNOWN_LOCAL_NAMES,
    MAX_HISTORY_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_HISTORY_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import UprightGo2ConfigEntry


def _is_supported(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Return True when the advertisement belongs to an Upright GO 2."""
    name = discovery_info.name or ""
    return any(name.startswith(known) for known in KNOWN_LOCAL_NAMES)


class UprightGo2ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Upright GO 2."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered by the Bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        if not _is_supported(discovery_info):
            return self.async_abort(reason="not_supported")

        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm adding the discovered device."""
        assert self._discovery is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovery.name,
                data={CONF_ADDRESS: self._discovery.address},
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._discovery.name,
                "address": self._discovery.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick from the devices Bluetooth has already seen."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._discovered.get(address, address),
                data={CONF_ADDRESS: address},
            )

        current_addresses = self._async_current_ids()
        for discovery in async_discovered_service_info(self.hass, connectable=True):
            if discovery.address in current_addresses or not _is_supported(discovery):
                continue
            self._discovered[discovery.address] = (
                f"{discovery.name} ({discovery.address})"
            )

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(self._discovered)}
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: UprightGo2ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return UprightGo2OptionsFlow()


class UprightGo2OptionsFlow(OptionsFlow):
    """Handle the polling interval option."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_HISTORY_INTERVAL: int(user_input[CONF_HISTORY_INTERVAL]),
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=30,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_HISTORY_INTERVAL,
                        default=options.get(
                            CONF_HISTORY_INTERVAL, DEFAULT_HISTORY_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_HISTORY_INTERVAL,
                            max=MAX_HISTORY_INTERVAL,
                            step=600,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
