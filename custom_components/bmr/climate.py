"""
Support for BMR HC64 Heating Regulation.
"""

import logging
from typing import Optional, Union
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BmrEntity, BmrCoordinator
from .client import BmrCircuitData
from .const import DOMAIN, CONF_DATA_COORDINATOR

from homeassistant.components.climate import ClimateEntity, ClimateEntityDescription, ClimateEntityFeature
from homeassistant.components.climate.const import (
    HVACMode,
    HVACAction,
)

from homeassistant.const import (
    ATTR_TEMPERATURE,
    UnitOfTemperature,
)

PRESET_NORMAL = "Normal"
PRESET_AWAY = "Away"

_LOGGER = logging.getLogger(__name__)

TEMP_MIN = 7.0
TEMP_MAX = 35.0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][CONF_DATA_COORDINATOR]
    num_circuits = await coordinator.client.getNumCircuits()
    names = await coordinator.client.getCircuitNames()
    entities = []
    for i in range(num_circuits):
        name = names[i] if i < len(names) else "-"
        entities.append(
            BmrClimateEntity(
                coordinator,
                i,
                ClimateEntityDescription(
                    name=f"{name}",
                    key=f"climate_{i}",
                )
            )
        )

    async_add_entities(entities)


class BmrClimateEntity(ClimateEntity, BmrEntity):
    """ Entity representing a circuit heated by the BMR HC64 controller unit.

        Usually the room has two temperature sensors (circuits): floor and room
        sensor. Since the heating usually happens using only one element (e.g.
        floor heating cables) the controller will heat the room if BOTH sensors
        report lower current temperature then their target temperature. This is
        not always the case, since e.g. bathroom floor heating and bathroom air
        heating can have each their own heating element.

        For simplicity it is recommended to set the floor sensor to a fixed
        temperature that is almost always higher than the actual floor
        temperature and only limits the maximum temperature suitable for
        your flooring (e.g. 27 degrees). If you on the other hand want to
        keep the floor at a constant temperature you can set the floor sensor
        to your desired temperature and the room sensor to a much higher value.

        This class supports the following HVAC modes:

        - HVACMode.AUTO - Automatic mode. HC64 controller will manage the
          temperature automatically according to its configuration.

        - HVACMode.HEAT/HVACMode.HEAT_COOL - Override the target temperature
          manually. Useful for temporarily increase/decrease the target
          temperature in the room.  Note that this will switch the circuit to a
          special "override" schedule and configure this schedule with the
          target temperature. HVACMode.HEAT_COOL is for water-based circuits
          that can also cool.

        - HVACMode.OFF - Turn off the heating circuit by assigning it to
          "summer mode" and turning the summer mode on.

          NOTE: Make sure to remove all circuits from summer mode when using
          the plugin for the first time. Otherwise any circuits assigned to the
          summer mode will be also turned off when the user switches a
          circuit into the HVACMode.OFF mode.

          NOTE #2: The HC64 controller is slow AF so updates after changing
          something (such as HVAC mode) may take a while to show in Home
          Assistant UI. Even several minutes.
      """

    def __init__(self, coordinator: BmrCoordinator, idx: int, description: ClimateEntityDescription) -> None:

        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.unique_id}-climate-{idx}"
        self._idx = idx
        self._can_cool = False  # TODO configurable
        # check if TURN_OFF is a possible value of ClimateEntityFeature

        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
        if "TURN_OFF" in ClimateEntityFeature.__members__:
            self._enable_turn_on_off_backwards_compatibility = False
            self._attr_supported_features |= ClimateEntityFeature.__members__["TURN_OFF"].value | ClimateEntityFeature.__members__["TURN_ON"].value
        else:
            self._enable_turn_on_off_backwards_compatibility = True

    @property
    def circuit(self) -> Union[BmrCircuitData, None]:
        if (
            self.coordinator.data
            and "circuits" in self.coordinator.data
            and len(self.coordinator.data["circuits"]) > self._idx
           ):
            return self.coordinator.data["circuits"][self._idx]
        else:
            return None

    @property
    def temperature_unit(self):
        """ The unit of temperature measurement for the system.
        """
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self) -> Optional[float]:
        """ Current temperature.
        """
        if self.circuit:
            return self.circuit.get("temperature")
        return None

    @property
    def target_temperature(self):
        """ Currently set target temperature.
        """
        if self.circuit:
            return self.circuit.get("target_temperature")
        return None

    @property
    def min_temp(self):
        return TEMP_MIN  # TODO configurable

    @property
    def max_temp(self):
        return TEMP_MAX  # TODO configurable

    @property
    def hvac_modes(self):
        """ Supported HVAC modes.

        See docs: https://developers.home-assistant.io/docs/core/entity/climate/#hvac-modes
        """
        if self._can_cool:
            return [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT_COOL]
        else:
            return [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT]

    @property
    def hvac_mode(self):
        """ Current HVAC mode.

            Return HVACMode.OFF if the summer mode for the circuit is turned
            on. Summer mode essentially means the circuit is turned off.

            Return HVACMode.HEAT/HVACMode.HEAT_COOL if the user manually
            overrode the target temperature. The override works by reassigning
            the circuit to a special "override" schedule specified in the
            configuration. The target temperature for the "override" schedule
            is set by the set_temperature() method.

            Return HVACMode.AUTO if the controller is managing everything
            automatically according to its configuration.
        """
        if not self.circuit:
            return HVACMode.OFF
        if self.circuit.get("summer_mode"):
            return HVACMode.OFF
        elif self.circuit.get("user_offset"):
            if self._can_cool:
                return HVACMode.HEAT_COOL
            else:
                return HVACMode.HEAT
        else:
            # The controller is managing everything automatically according to
            # configured schedules.
            return HVACMode.AUTO

    async def async_set_hvac_mode(self, hvac_mode: str, automatic: bool = False):
        """ Set HVAC mode.
        """
        if hvac_mode == HVACMode.OFF:
            # Turn on the HVACMode.OFF. This will turn off the heating/cooling
            # of the given circuit. This works by:
            #
            # - Adding the circuit to summer mode
            # - Turning the summer mode ON
            #
            # NOTE: Sometimes (usually) there are also other circuits assigned
            # to summer mode, especially if this plugin is used for the first
            # time. If there are also other circutis assigned to summer mode
            # and summer mode is turned on they will be turned off too. Make
            # sure to remove any circuits from the summer mode manually when
            # using the plugin for the first time.
            await self.coordinator.client.setSummerModeAssignments([self._idx], True)
            await self.coordinator.client.setSummerMode(True)
        elif self.coordinator.data.get("summer_mode"):
            # Turn HVACMode.OFF off and restore normal operation.
            #
            # - Remove the circuit from the summer mode assignments
            # - If there aren't any circuits assigned to summer mode anymore
            #   turn the summer mode OFF.
            assignments = await self.coordinator.client.setSummerModeAssignments([self._idx], True)
            if assignments is not None and not any(assignments):
                await self.coordinator.client.setSummerMode(False)

        if hvac_mode in (HVACMode.HEAT, HVACMode.HEAT_COOL):
            # Turn on the HVACMode.HEAT. This will assign the "override"
            # schedule to the circuit. The "override" schedule is used for
            # setting the custom target temperature (see set_temperature()
            # below).
            if not automatic:  # Don't set the override if the HVAC mode is set automatically by temperature change
                t = self.target_temperature
                _LOGGER.debug(f"Setting HVAC mode to {hvac_mode} with temperature {t}")
                if t is not None:
                    await self.coordinator.client.setTemperatureOverride(self._idx, t)
        else:
            # Turn off the HVACMode.HEAT/HVACMode.HEAT_COOL and restore
            # normal operation.
            #
            # - Assign normal schedules to the circuit
            await self.coordinator.client.removeTemperatureOverride(self._idx)
            if self.circuit and self.circuit.get("user_offset") is not None:
                self.circuit["user_offset"] = 0.0  # imitate immediate change in user offset

        if hvac_mode == HVACMode.AUTO:
            # Turn on the HVACMode.AUTO. Currently this is no-op, as the
            # normal operation is restored in the else branches above.
            pass

    @property
    def hvac_action(self):
        """ What is the climate device currently doing (cooling, heating, idle).
        """
        if not self.circuit:
            return HVACAction.OFF
        if self.circuit.get("summer_mode"):
            return HVACAction.OFF
        elif self.circuit.get("heating"):
            return HVACAction.HEATING
        elif self.circuit.get("cooling"):
            return HVACAction.COOLING
        else:
            return HVACAction.IDLE

    @property
    def preset_modes(self):
        """ Supported preset modes.
        """
        return [PRESET_NORMAL, PRESET_AWAY]

    @property
    def preset_mode(self):
        """ Current preset mode.
        """
        if self.circuit and self.circuit.get("low_mode"):
            return PRESET_AWAY
        else:
            return PRESET_NORMAL

    async def async_set_preset_mode(self, preset_mode: str):
        """ Set preset mode.
        """
        if preset_mode == PRESET_AWAY:
            await self.coordinator.client.setLowModeAssignments([self._idx], True)
            await self.coordinator.client.setLowMode(True)
        else:
            assignments = await self.coordinator.client.setLowModeAssignments([self._idx], False)
            if assignments is not None and not any(assignments):
                await self.coordinator.client.setLowMode(False)

    async def async_set_temperature(self, **kwargs):
        """ Set new target temperature for the circuit. This works by
            modifying the special "override" schedule and assigning the
            schedule to the circuit.

            This is being done to avoid overwriting the normal schedule used
            for HVACMode.AUTO.
        """
        temperature = kwargs.get(ATTR_TEMPERATURE)
        _LOGGER.debug(f"Setting temperature to {temperature}")
        if temperature:
            await self.coordinator.client.setTemperatureOverride(self._idx, temperature)
        if self.hvac_mode not in (HVACMode.HEAT, HVACMode.HEAT_COOL):
            if self._can_cool:
                await self.async_set_hvac_mode(HVACMode.HEAT_COOL, True)
            else:
                await self.async_set_hvac_mode(HVACMode.HEAT, True)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.AUTO)
