"""Sensors for the Upright GO 2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import DEGREE, EntityCategory, PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import UprightGo2Data
from .const import MovementStatus
from .coordinator import UprightGo2ConfigEntry, UprightGo2Coordinator
from .entity import UprightGo2Entity

@dataclass(frozen=True, kw_only=True)
class UprightGo2SensorDescription(SensorEntityDescription):
    """Describes an Upright GO 2 sensor."""

    value_fn: Callable[[UprightGo2Data], str | int | float | datetime | None]
    attrs_fn: Callable[[UprightGo2Data], dict[str, object]] | None = None


SENSORS: tuple[UprightGo2SensorDescription, ...] = (
    UprightGo2SensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.battery_level,
    ),
    UprightGo2SensorDescription(
        key="angle",
        translation_key="angle",
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.angle,
    ),
    UprightGo2SensorDescription(
        key="movement",
        translation_key="movement",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", "moving", "unknown"],
        value_fn=(
            lambda data: data.movement.name.lower()
            if data.movement is not None
            else None
        ),
    ),
    UprightGo2SensorDescription(
        key="slouching_time",
        translation_key="slouching_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda data: data.slouching_seconds,
    ),
    UprightGo2SensorDescription(
        key="upright_time",
        translation_key="upright_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda data: data.upright_seconds,
    ),
    UprightGo2SensorDescription(
        key="history_synced",
        translation_key="history_synced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.history_synced,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UprightGo2ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        UprightGo2Sensor(coordinator, description) for description in SENSORS
    )


class UprightGo2Sensor(UprightGo2Entity, SensorEntity):
    """A sensor backed by one field of the polled snapshot."""

    entity_description: UprightGo2SensorDescription

    def __init__(
        self,
        coordinator: UprightGo2Coordinator,
        description: UprightGo2SensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | float | datetime | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Return any extra detail this sensor carries."""
        if (attrs_fn := self.entity_description.attrs_fn) is None:
            return None
        return attrs_fn(self.coordinator.data)
