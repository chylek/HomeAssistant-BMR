"""
Support for BMR HC64 Grid appliance control.
"""

__version__ = "0.7"

import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from . import BmrEntity, BmrCoordinator
from .const import DOMAIN, CONF_DATA_COORDINATOR

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][CONF_DATA_COORDINATOR]
    entities = [
        BmrHdoSensor(
            coordinator,
            BinarySensorEntityDescription(
                name="HDO - nízký tarif",
                key="hdo",
                icon="mdi:home-lightning-bolt",
                translation_key="hdo",
            )
        )
    ]

    async_add_entities(entities)


class BmrHdoSensor(BinarySensorEntity, BmrEntity):
    """ Binary sensor for reporting HDO (low/high electricity tariff).
    """

    def __init__(self, coordinator: BmrCoordinator, description: BinarySensorEntityDescription) -> None:

        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.unique_id}-binary-sensor-hdo"

    @ property
    def is_on(self):
        """ Return the state of the sensor.
        """
        try:
            return bool(self.coordinator.data["hdo"])
        except Exception:
            return None
