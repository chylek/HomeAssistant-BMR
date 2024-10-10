"""Config flow for BMR integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant import config_entries

from .const import DOMAIN, CannotConnect
from .client import Bmr, AuthException

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_URL): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    # TODO validate the data can be used to set up a connection.

    # If your PyPI package is not built with async, pass your methods
    # to the executor:
    # await hass.async_add_executor_job(
    #     your_validate_func, data[CONF_USERNAME], data[CONF_PASSWORD]
    # )
    # client = Client(data[CONF_USERNAME], data[CONF_PASSWORD], session)
    session = async_get_clientsession(hass)
    client = Bmr(data[CONF_URL], data[CONF_USERNAME], data[CONF_PASSWORD], session)

    num_circuits = await client.getNumCircuits()
    circuit_names = await client.getCircuitNames()

    # Return info that you want to store in the config entry.
    return {
        "num_circuits": num_circuits,
        "circuit_names": circuit_names,
    }


class EnyaqConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for myenyaq."""

    VERSION = 1

    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                if user_input[CONF_URL].endswith("/"):
                    user_input[CONF_URL] = user_input[CONF_URL][:-1]
                additional = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except AuthException:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                user_input.update(additional)
                return self.async_create_entry(title="BMR", data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
