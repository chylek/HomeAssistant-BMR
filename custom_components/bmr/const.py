from homeassistant.exceptions import HomeAssistantError

DOMAIN = "bmr"
CONF_DATA_COORDINATOR = "coordinator"


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
