from homeassistant.exceptions import HomeAssistantError

DOMAIN = "bmr"
CONF_DATA_COORDINATOR = "coordinator"
CONF_CAN_COOL = "can_cool"


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
