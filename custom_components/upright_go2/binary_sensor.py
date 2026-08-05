"""Binary sensors for the Upright GO 2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import UprightGo2Data
from .const import PostureState
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
        # On the charger, not drawing-current-right-now. A full battery keeps
        # reporting CHARGED while it sits on the dock, and testing only for
        # CHARGING left this off the whole time the unit was docked at 100 %
        # — which is exactly when it is off your back and nothing should be
        # counted. The card reads this to show the charging state.
        value_fn=(
            lambda data: data.on_charger if data.charging_state is not None else None
        ),
        # Keep the raw value visible: "charging" alone cannot tell full-on-dock
        # apart from unplugged, which is what made this hard to see.
        attrs_fn=(
            lambda data: {"charging_state": data.charging_state.name.lower()}
            if data.charging_state is not None
            else {}
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
