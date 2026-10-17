import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
    SensorDeviceClass,
)

from homeassistant.const import PERCENTAGE, CONCENTRATION_PARTS_PER_MILLION, UnitOfTemperature
from homeassistant.helpers.typing import StateType
from typing import Optional


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
        BmrSensor(
            coordinator,
            SensorEntityDescription(
                name="CO2",
                key="co2",
                icon="mdi:air-filter",
                device_class=SensorDeviceClass.CO2,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
            ),
            "ventilation", "ppm"
        ),
        BmrSensor(
            coordinator,
            SensorEntityDescription(
                name="Ventilation speed",
                key="ventilation",
                icon="mdi:fan",
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=PERCENTAGE,
                translation_key="ventilation_speed",
            ),
            "ventilation", "power"
        )
    ]
    if coordinator.data and coordinator.data.get("outdoor_temperature") is not None:
        # only units with the WTR01 module report outdoor temperature
        entities.append(
            BmrSensor(
                coordinator,
                SensorEntityDescription(
                    name="Outdoor temperature",
                    key="outdoor_temperature",
                    device_class=SensorDeviceClass.TEMPERATURE,
                    state_class=SensorStateClass.MEASUREMENT,
                    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                    translation_key="outdoor_temperature",
                ),
                "outdoor_temperature", None
            )
        )

    async_add_entities(entities)


class BmrSensor(SensorEntity, BmrEntity):
    """ Sensor for reporting ventilation status and outdoor temperature.
    """

    def __init__(self,
                 coordinator: BmrCoordinator,
                 description: SensorEntityDescription,
                 bmr_data_key: str,
                 bmr_data_subkey: Optional[str]
                 ) -> None:

        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.unique_id}-{description.key}"
        self.bmr_data_key = bmr_data_key
        self.bmr_data_subkey = bmr_data_subkey

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        try:
            value = self.coordinator.data.get(self.bmr_data_key)
            if self.bmr_data_subkey is None:
                return value
            return value.get(self.bmr_data_subkey)
        except Exception:
            return None
