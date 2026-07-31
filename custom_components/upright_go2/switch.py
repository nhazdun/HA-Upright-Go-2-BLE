"""Switches for the Upright GO 2."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import UprightGo2ConfigEntry, UprightGo2Coordinator
from .entity import UprightGo2Entity

VIBRATION = SwitchEntityDescription(
    key="vibration",
    translation_key="vibration",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UprightGo2ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switches."""
    async_add_entities([UprightGo2VibrationSwitch(entry.runtime_data, VIBRATION)])


class UprightGo2VibrationSwitch(UprightGo2Entity, SwitchEntity):
    """Turn the slouch buzz on or off."""

    def __init__(
        self,
        coordinator: UprightGo2Coordinator,
        description: SwitchEntityDescription,
    ) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return whether vibration feedback is enabled."""
        return self.coordinator.data.vibration_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable vibration feedback."""
        await self.coordinator.async_run("set_vibration", enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable vibration feedback."""
        await self.coordinator.async_run("set_vibration", enabled=False)
