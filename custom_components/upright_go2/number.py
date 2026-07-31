"""Numbers for the Upright GO 2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import UprightGo2Data
from .const import GO_RANGE_MAX, GO_RANGE_MIN
from .coordinator import UprightGo2ConfigEntry, UprightGo2Coordinator
from .entity import UprightGo2Entity


@dataclass(frozen=True, kw_only=True)
class UprightGo2NumberDescription(NumberEntityDescription):
    """Describes an Upright GO 2 number."""

    value_fn: Callable[[UprightGo2Data], int | None]
    setting: str


NUMBERS: tuple[UprightGo2NumberDescription, ...] = (
    UprightGo2NumberDescription(
        key="sensitivity",
        translation_key="sensitivity",
        entity_category=EntityCategory.CONFIG,
        native_min_value=GO_RANGE_MIN,
        native_max_value=GO_RANGE_MAX,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda data: data.sensitivity_range,
        setting="sensitivity_range",
    ),
    UprightGo2NumberDescription(
        key="delay",
        translation_key="delay",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=255,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        value_fn=lambda data: data.delay_seconds,
        setting="delay_seconds",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UprightGo2ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the numbers."""
    coordinator = entry.runtime_data
    async_add_entities(
        UprightGo2Number(coordinator, description) for description in NUMBERS
    )


class UprightGo2Number(UprightGo2Entity, NumberEntity):
    """A freestyle setting exposed as a number."""

    entity_description: UprightGo2NumberDescription

    def __init__(
        self,
        coordinator: UprightGo2Coordinator,
        description: UprightGo2NumberDescription,
    ) -> None:
        """Initialise the number."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        value = self.entity_description.value_fn(self.coordinator.data)
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Write the new value back to the device."""
        await self.coordinator.async_run(
            "update_freestyle", **{self.entity_description.setting: int(value)}
        )
