"""Base entity for the Upright GO 2."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import UprightGo2Coordinator


class UprightGo2Entity(CoordinatorEntity[UprightGo2Coordinator]):
    """Common device info and availability handling."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UprightGo2Coordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name="Upright GO 2",
            serial_number=coordinator.data.serial_number,
            sw_version=coordinator.data.firmware_version,
            hw_version=coordinator.data.hardware_version,
        )
