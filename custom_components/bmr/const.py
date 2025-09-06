from homeassistant.exceptions import HomeAssistantError
import aiohttp

DOMAIN = "bmr"
CONF_DATA_COORDINATOR = "coordinator"
CONF_CAN_COOL = "can_cool"
CONF_TILT_STEPS = "tilt_steps"
TIMEOUT = aiohttp.ClientTimeout(total=60)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
