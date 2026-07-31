"""Selects for the Upright GO 2."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import VIBRATION_STRENGTHS, ConnectionMode, VibrationPattern
from .coordinator import UprightGo2ConfigEntry, UprightGo2Coordinator
from .entity import UprightGo2Entity

PATTERN_OPTIONS = [pattern.name.lower() for pattern in VibrationPattern]
STRENGTH_OPTIONS = list(VIBRATION_STRENGTHS)

MODE_OPTIONS = ["posture", "msk"]

MODE = SelectEntityDescription(
    key="mode",
    translation_key="mode",
    options=MODE_OPTIONS,
)
PATTERN = SelectEntityDescription(
    key="vibration_pattern",
    translation_key="vibration_pattern",
    entity_category=EntityCategory.CONFIG,
    options=PATTERN_OPTIONS,
)
STRENGTH = SelectEntityDescription(
    key="vibration_strength",
    translation_key="vibration_strength",
    entity_category=EntityCategory.CONFIG,
    options=STRENGTH_OPTIONS,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UprightGo2ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the selects."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            UprightGo2ModeSelect(coordinator, MODE),
            UprightGo2PatternSelect(coordinator, PATTERN),
            UprightGo2StrengthSelect(coordinator, STRENGTH),
        ]
    )


class UprightGo2ModeSelect(UprightGo2Entity, SelectEntity):
    """Switch between the posture and MSK programmes."""

    def __init__(
        self,
        coordinator: UprightGo2Coordinator,
        description: SelectEntityDescription,
    ) -> None:
        """Initialise the select."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def current_option(self) -> str | None:
        """Return the active programme."""
        mode = self.coordinator.data.mode
        if mode is None:
            return None
        return "msk" if mode is ConnectionMode.MSK else "posture"

    async def async_select_option(self, option: str) -> None:
        """Switch the device to the chosen programme."""
        mode = ConnectionMode.MSK if option == "msk" else ConnectionMode.POSTURE
        await self.coordinator.async_run("set_mode", mode=int(mode))


class UprightGo2PatternSelect(UprightGo2Entity, SelectEntity):
    """Pick the buzz pattern."""

    def __init__(
        self,
        coordinator: UprightGo2Coordinator,
        description: SelectEntityDescription,
    ) -> None:
        """Initialise the select."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def current_option(self) -> str | None:
        """Return the active pattern."""
        pattern = self.coordinator.data.vibration_pattern
        return None if pattern is None else pattern.name.lower()

    async def async_select_option(self, option: str) -> None:
        """Write the chosen pattern."""
        await self.coordinator.async_run(
            "update_freestyle", vibration_pattern=VibrationPattern[option.upper()]
        )


class UprightGo2StrengthSelect(UprightGo2Entity, SelectEntity):
    """Pick the buzz strength."""

    def __init__(
        self,
        coordinator: UprightGo2Coordinator,
        description: SelectEntityDescription,
    ) -> None:
        """Initialise the select."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def current_option(self) -> str | None:
        """Return the closest named strength to the stored value."""
        raw = self.coordinator.data.vibration_strength
        if raw is None:
            return None
        return min(VIBRATION_STRENGTHS, key=lambda k: abs(VIBRATION_STRENGTHS[k] - raw))

    async def async_select_option(self, option: str) -> None:
        """Write the chosen strength."""
        await self.coordinator.async_run(
            "update_freestyle", vibration_strength=VIBRATION_STRENGTHS[option]
        )
