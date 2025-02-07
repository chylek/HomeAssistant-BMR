from __future__ import annotations
from datetime import timedelta
import logging

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_PASSWORD, CONF_USERNAME, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, CONF_DATA_COORDINATOR
from .client import Bmr, BmrAllData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.CLIMATE,
]


# TODO Update entry annotation
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # load overrides from storage:
    store = Store(hass, 1, f"bmr_overrides_{entry.entry_id}")
    overrides = await store.async_load()
    client = Bmr(entry.data[CONF_URL], entry.data[CONF_USERNAME],
                 entry.data[CONF_PASSWORD], async_get_clientsession(hass), overrides, store)

    coordinator = BmrCoordinator(hass, client)
    coordinator.unique_id = await client.getUniqueId()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {CONF_DATA_COORDINATOR: coordinator}

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


# TODO Update entry annotation
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


class BmrCoordinator(DataUpdateCoordinator[BmrAllData]):
    """My custom coordinator."""

    def __init__(self, hass: HomeAssistant, client: Bmr) -> None:
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name=DOMAIN,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(minutes=1)
        )
        self.client = client
        self.unique_id = "unk"

    async def _async_update_data(self) -> BmrAllData:
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        # Note: asyncio.TimeoutError and aiohttp.ClientError are already
        # handled by the data update coordinator.
        async with async_timeout.timeout(30):
            data = await self.client.getAllData()
            return data


class BmrEntity(CoordinatorEntity[BmrCoordinator]):
    """Defines a base BMR entity."""

    def __init__(self, coordinator: BmrCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.coordinator.unique_id)},
            "name": "BMR H64",
            "manufacturer": "BMR",
        }
