"""
Support for roller shutters and blinds controlled by BMR HC64.
"""
import logging
from typing import Optional

from homeassistant.components.cover import ATTR_POSITION, ATTR_TILT_POSITION, CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BmrCoordinator, BmrEntity
from .client import (
    BMR_SHUTTER_CLOSED,
    BMR_SHUTTER_INTERMEDIATE,
    BMR_SHUTTER_OPEN,
    BMR_SHUTTER_SLITS,
    BmrRollerShutterData,
)
from .const import CONF_DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the cover platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][CONF_DATA_COORDINATOR]
    shutters = (coordinator.data or {}).get("shutters") or []
    async_add_entities(BmrCover(coordinator, shutter) for shutter in shutters if shutter["enabled"])


def ha_position_to_bmr(position: int) -> int:
    """Map HA position (0 closed - 100 open) to one of the four positions the controller accepts."""
    if position > 90:
        return BMR_SHUTTER_OPEN
    elif position > 45:
        return BMR_SHUTTER_INTERMEDIATE
    elif position > 15:
        return BMR_SHUTTER_SLITS
    return BMR_SHUTTER_CLOSED


class BmrCover(CoverEntity, BmrEntity):
    """Roller shutter, or blinds if the slats can be tilted.

    The controller can only be told to go to one of four positions (open, closed, slits,
    intermediate), but it reports the actual position in percent. Tilt is 0 (horizontal) - 10
    (vertical) on the controller, mapped to 100 - 0 in HA.
    """

    def __init__(self, coordinator: BmrCoordinator, shutter: BmrRollerShutterData) -> None:
        super().__init__(coordinator)
        self._id = shutter["id"]
        self._attr_name = shutter["name"]
        self._attr_unique_id = f"{coordinator.unique_id}-cover-{self._id}"
        self._attr_device_class = CoverDeviceClass.BLIND if shutter["has_tilt"] else CoverDeviceClass.SHUTTER
        self._attr_supported_features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.SET_POSITION
        )
        if shutter["has_tilt"]:
            self._attr_supported_features |= (
                CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT | CoverEntityFeature.SET_TILT_POSITION
            )

    @property
    def shutter(self) -> Optional[BmrRollerShutterData]:
        for shutter in (self.coordinator.data or {}).get("shutters") or []:
            if shutter["id"] == self._id:
                return shutter
        return None

    @property
    def available(self) -> bool:
        return super().available and self.shutter is not None

    @property
    def current_cover_position(self) -> Optional[int]:
        if self.shutter is None:
            return None
        # ponytail: assumes the controller reports % closed (0 = fully open, like its position 0 = open);
        # untested on real hardware, flip this if a fully open shutter shows as closed in HA
        return 100 - self.shutter["position"]

    @property
    def current_cover_tilt_position(self) -> Optional[int]:
        if self.shutter is None or not self.shutter["has_tilt"]:
            return None
        return 100 - self.shutter["tilt"] * 10

    @property
    def is_closed(self) -> Optional[bool]:
        position = self.current_cover_position
        return None if position is None else position == 0

    @property
    def extra_state_attributes(self):
        return {"error": self.shutter["error"] if self.shutter else None}

    async def _move(self, bmr_pos: Optional[int] = None, bmr_tilt: Optional[int] = None) -> None:
        """Send position and tilt together, keeping whichever one is not being changed."""
        shutter = self.shutter or {}
        if bmr_pos is None:
            bmr_pos = shutter.get("requested_position", BMR_SHUTTER_OPEN)
        if bmr_tilt is None:
            bmr_tilt = shutter.get("tilt", 0)
        await self.coordinator.client.saveManualChange(self._id, bmr_pos, bmr_tilt)
        await self.coordinator.async_request_refresh()

    async def async_open_cover(self, **kwargs) -> None:
        await self._move(bmr_pos=BMR_SHUTTER_OPEN)

    async def async_close_cover(self, **kwargs) -> None:
        await self._move(bmr_pos=BMR_SHUTTER_CLOSED)

    async def async_set_cover_position(self, **kwargs) -> None:
        await self._move(bmr_pos=ha_position_to_bmr(kwargs[ATTR_POSITION]))

    async def async_open_cover_tilt(self, **kwargs) -> None:
        await self._move(bmr_tilt=0)

    async def async_close_cover_tilt(self, **kwargs) -> None:
        await self._move(bmr_tilt=10)

    async def async_set_cover_tilt_position(self, **kwargs) -> None:
        await self._move(bmr_tilt=round((100 - kwargs[ATTR_TILT_POSITION]) / 10))
