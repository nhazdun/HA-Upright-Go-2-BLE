"""Binary sensors for the Upright GO 2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import UprightGo2Data
from .const import LOW_BATTERY_VALUE, ChargingState, PostureState
from .coordinator import UprightGo2ConfigEntry, UprightGo2Coordinator
from .entity import UprightGo2Entity


@dataclass(frozen=True, kw_only=True)
class UprightGo2BinarySensorDescription(BinarySensorEntityDescription):
    """Describes an Upright GO 2 binary sensor."""

    value_fn: Callable[[UprightGo2Data], bool | None]
    attrs_fn: Callable[[UprightGo2Data], dict[str, object]] | None = None


BINARY_SENSORS: tuple[UprightGo2BinarySensorDescription, ...] = (
    UprightGo2BinarySensorDescription(
        key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=(
            lambda data: data.charging_state is ChargingState.CHARGING
            if data.charging_state is not None
            else None
        ),
    ),
    UprightGo2BinarySensorDescription(
        key="slouching",
        translation_key="slouching",
        value_fn=(
            lambda data: data.posture is PostureState.SLOUCH
            if data.posture is not None
            else None
        ),
    ),
    UprightGo2BinarySensorDescription(
        key="low_battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=(
            lambda data: data.battery_level <= LOW_BATTERY_VALUE
            if data.battery_level is not None
            else None
        ),
    ),
    # Opt-in: the error decoding is derived from the app but has never been
    # seen against a device that actually reports a fault, so it stays off by
    # default rather than raising a bare "Problem" nobody can act on.
    UprightGo2BinarySensorDescription(
        key="problem",
        translation_key="device_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: bool(data.errors),
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
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        UprightGo2BinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class UprightGo2BinarySensor(UprightGo2Entity, BinarySensorEntity):
    """A binary sensor backed by the polled snapshot."""

    entity_description: UprightGo2BinarySensorDescription

    def __init__(
        self,
        coordinator: UprightGo2Coordinator,
        description: UprightGo2BinarySensorDescription,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the current state."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Return any extra detail this sensor carries."""
        if (attrs_fn := self.entity_description.attrs_fn) is None:
            return None
        return attrs_fn(self.coordinator.data)
