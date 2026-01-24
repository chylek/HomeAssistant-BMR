from homeassistant.exceptions import HomeAssistantError
import aiohttp

DOMAIN = "bmr"
CONF_DATA_COORDINATOR = "coordinator"
CONF_CAN_COOL = "can_cool"
CONF_SUMMER_MODE_EXCLUSIVE = "summer_mode_exclusive"
TIMEOUT = aiohttp.ClientTimeout(total=60)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
