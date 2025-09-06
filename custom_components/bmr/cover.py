from __future__ import annotations

import logging

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BmrCoordinator, BmrEntity
from .const import DOMAIN, CONF_DATA_COORDINATOR

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BMR roller shutters."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][CONF_DATA_COORDINATOR]
    shutters = coordinator.data.get("roller_shutters") or []
    entities = [BmrCover(coordinator, idx) for idx in range(len(shutters))]
    async_add_entities(entities)


class BmrCover(CoverEntity, BmrEntity):
    """Representation of a BMR roller shutter."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.OPEN_TILT
        | CoverEntityFeature.CLOSE_TILT
        | CoverEntityFeature.SET_TILT_POSITION
    )

    def __init__(self, coordinator: BmrCoordinator, shutter_id: int) -> None:
        super().__init__(coordinator)
        self.shutter_id = shutter_id
        data = coordinator.data["roller_shutters"][shutter_id]
        self._attr_name = data["name"]
        self._attr_unique_id = f"{coordinator.unique_id}-shutter-{shutter_id}"

    @property
    def _data(self) -> dict:
        return self.coordinator.data.get("roller_shutters", [{}])[self.shutter_id]

    @property
    def current_cover_position(self) -> int | None:
        return self._data.get("position")

    @property
    def current_cover_tilt_position(self) -> int | None:
        return self._data.get("tilt")

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        if pos is None:
            return None
        return pos == 0

    async def async_open_cover(self, **kwargs) -> None:
        await self.coordinator.client.saveManualChange(self.shutter_id, 100, self.current_cover_tilt_position or 0)
        await self.coordinator.async_request_refresh()

    async def async_close_cover(self, **kwargs) -> None:
        await self.coordinator.client.saveManualChange(self.shutter_id, 0, self.current_cover_tilt_position or 0)
        await self.coordinator.async_request_refresh()

    async def async_set_cover_position(self, **kwargs) -> None:
        position = kwargs.get("position")
        if position is not None:
            await self.coordinator.client.saveManualChange(
                self.shutter_id, int(position), self.current_cover_tilt_position or 0
            )
            await self.coordinator.async_request_refresh()

    async def async_open_cover_tilt(self, **kwargs) -> None:
        await self.coordinator.client.saveManualChange(self.shutter_id, self.current_cover_position or 0, 100)
        await self.coordinator.async_request_refresh()

    async def async_close_cover_tilt(self, **kwargs) -> None:
        await self.coordinator.client.saveManualChange(self.shutter_id, self.current_cover_position or 0, 0)
        await self.coordinator.async_request_refresh()

    async def async_set_cover_tilt_position(self, **kwargs) -> None:
        tilt = kwargs.get("tilt_position")
        if tilt is not None:
            await self.coordinator.client.saveManualChange(self.shutter_id, self.current_cover_position or 0, int(tilt))
            await self.coordinator.async_request_refresh()
