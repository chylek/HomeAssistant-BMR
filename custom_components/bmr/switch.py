import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BmrCoordinator, BmrEntity
from .const import CONF_DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][CONF_DATA_COORDINATOR]
    entities = [
        BmrAwayModeSwitch(coordinator),
        BmrSummerModeSwitch(coordinator),
    ]

    async_add_entities(entities)


class BmrAwayModeSwitch(SwitchEntity, BmrEntity):
    """Switch entity for controlling the Away mode."""

    def __init__(self, coordinator: BmrCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "BMR Away Mode"
        self._attr_unique_id = f"{coordinator.unique_id}-away-mode"
        self._attr_translation_key = "away_mode"

    @property
    def is_on(self) -> bool:
        """Return the state of the switch."""
        return self.coordinator.data.get("low_mode", {}).get("enabled", False)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the Away mode."""
        await self.coordinator.client.setLowMode(True)
        lm = self.coordinator.data["low_mode"]
        if lm is not None:
            lm["enabled"] = True
            self.async_schedule_update_ha_state()
        else:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the Away mode."""
        await self.coordinator.client.setLowMode(False)
        lm = self.coordinator.data["low_mode"]
        if lm is not None:
            lm["enabled"] = False
            self.async_schedule_update_ha_state()
        else:
            await self.coordinator.async_request_refresh()


class BmrSummerModeSwitch(SwitchEntity, BmrEntity):
    """Switch entity for controlling the Summer mode."""

    def __init__(self, coordinator: BmrCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "BMR Summer Mode"
        self._attr_unique_id = f"{coordinator.unique_id}-summer-mode"
        self._attr_translation_key = "summer_mode"

    @property
    def is_on(self) -> bool:
        """Return the state of the switch."""
        return self.coordinator.data.get("summer_mode", False)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the Summer mode."""
        await self.coordinator.client.setSummerMode(True)
        self.coordinator.data["summer_mode"] = True
        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the Summer mode."""
        await self.coordinator.client.setSummerMode(False)
        self.coordinator.data["summer_mode"] = False
        self.async_schedule_update_ha_state()
