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
from .coordinator import UprightGo2ConfigEntry, UprightGo2Coordinator
from .entity import UprightGo2Entity

CHARGING_STATES = ["disconnected", "charging", "charged"]
POSTURE_STATES = ["straight", "slouch"]


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
        key="charging_state",
        translation_key="charging_state",
        device_class=SensorDeviceClass.ENUM,
        options=CHARGING_STATES,
        value_fn=(
            lambda data: CHARGING_STATES[data.charging_state]
            if data.charging_state is not None
            else None
        ),
    ),
    UprightGo2SensorDescription(
        key="posture",
        translation_key="posture",
        device_class=SensorDeviceClass.ENUM,
        options=POSTURE_STATES,
        value_fn=(
            lambda data: POSTURE_STATES[data.posture]
            if data.posture is not None
            else None
        ),
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
        key="slouching_today",
        translation_key="slouching_today",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda data: data.slouching_today,
    ),
    UprightGo2SensorDescription(
        key="upright_today",
        translation_key="upright_today",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda data: data.upright_today,
    ),
    UprightGo2SensorDescription(
        key="history_synced",
        translation_key="history_synced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.history_synced,
    ),
    UprightGo2SensorDescription(
        key="errors",
        translation_key="errors",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: ", ".join(data.errors) if data.errors else "none",
        attrs_fn=lambda data: {
            "errors": data.errors,
            "malfunction": data.malfunction,
            "shutdown_reason": data.shutdown_reason,
            "reset_reasons": data.reset_reasons,
        },
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
