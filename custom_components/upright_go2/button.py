"""Buttons for the Upright GO 2."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import UprightGo2ConfigEntry, UprightGo2Coordinator
from .entity import UprightGo2Entity


@dataclass(frozen=True, kw_only=True)
class UprightGo2ButtonDescription(ButtonEntityDescription):
    """Describes an Upright GO 2 button."""

    action: str


BUTTONS: tuple[UprightGo2ButtonDescription, ...] = (
    UprightGo2ButtonDescription(
        key="calibrate",
        translation_key="calibrate",
        action="calibrate",
    ),
    UprightGo2ButtonDescription(
        key="clear_calibration",
        translation_key="clear_calibration",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        action="clear_calibration",
    ),
    UprightGo2ButtonDescription(
        key="deep_sleep",
        translation_key="deep_sleep",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        action="deep_sleep",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UprightGo2ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        UprightGo2Button(coordinator, description) for description in BUTTONS
    )


class UprightGo2Button(UprightGo2Entity, ButtonEntity):
    """A button that writes a single command to the device."""

    entity_description: UprightGo2ButtonDescription

    def __init__(
        self,
        coordinator: UprightGo2Coordinator,
        description: UprightGo2ButtonDescription,
    ) -> None:
        """Initialise the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Send the command."""
        await self.coordinator.async_run(self.entity_description.action)
