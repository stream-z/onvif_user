"""Config flow for ONVIF User Manager."""

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

_LOGGER = logging.getLogger(__name__)

from .client import OnvifUserClient
from .const import (
    CONF_HOST as C_HOST,
    CONF_PORT as C_PORT,
    CONF_USERNAME as C_USER,
    CONF_PASSWORD as C_PASS,
    DEFAULT_PORT,
    DOMAIN,
)


class OnvifUserConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a single-camera config entry."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            client = OnvifUserClient(
                host=user_input[C_HOST],
                port=user_input[C_PORT],
                user=user_input[C_USER],
                password=user_input[C_PASS],
            )
            try:
                res = await client.list_users()
            except Exception as exc:  # noqa: BLE001 - surface as connection error
                _LOGGER.exception(
                    "ONVIF probe failed for %s:%s (%s): %s",
                    user_input[C_HOST],
                    user_input[C_PORT],
                    user_input[C_USER],
                    exc,
                )
                res = None

            if res is None:
                errors["base"] = "cannot_connect"
            elif res.get("ok"):
                return self.async_create_entry(
                    title=user_input[C_HOST], data=user_input
                )
            else:
                errors["base"] = "invalid_auth"

        data_schema = vol.Schema(
            {
                vol.Required(C_HOST, default=""): str,
                vol.Required(C_PORT, default=DEFAULT_PORT): int,
                vol.Required(C_USER, default=""): str,
                vol.Required(C_PASS): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        # HA 2024.11+: OptionsFlow instances are created with no arguments;
        # self.config_entry is injected as a property by the base class.
        return OnvifUserOptionsFlow()


class OnvifUserOptionsFlow(config_entries.OptionsFlow):
    """Let the user edit connection settings after setup.

    Connection params are merged back into ``entry.data`` so the client
    (created from ``entry.data`` in ``async_setup_entry``) picks them up on
    reload. The password field is shown blank and only overwrites the stored
    password when actually typed — so users can change IP / user without
    re-entering the password.
    """

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            # Blank password => keep the stored one.
            password = user_input.get(C_PASS) or self.config_entry.data.get(
                C_PASS, ""
            )
            host = user_input[C_HOST]
            port = user_input[C_PORT]
            user = user_input[C_USER]

            # Validate the new connection before committing the change, so a
            # wrong password (or unreachable host) is rejected here instead of
            # being saved and silently breaking every future poll.
            session = aiohttp.ClientSession()
            try:
                probe = OnvifUserClient(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    session=session,
                )
                try:
                    res = await probe.list_users()
                except Exception as exc:  # noqa: BLE001 - surface as connect error
                    _LOGGER.exception(
                        "ONVIF probe failed for %s:%s (%s): %s",
                        host,
                        port,
                        user,
                        exc,
                    )
                    res = None
            finally:
                await session.close()

            if res is None:
                errors["base"] = "cannot_connect"
            elif not res.get("ok"):
                errors["base"] = "invalid_auth"
            else:
                new_data = dict(self.config_entry.data)
                new_data.update(
                    {
                        C_HOST: host,
                        C_PORT: port,
                        C_USER: user,
                        C_PASS: password,
                    }
                )
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                # Reload so the new connection / interval takes effect.
                await self.hass.config_entries.async_reload(
                    self.config_entry.entry_id
                )
                return self.async_create_entry(title=host, data={})

            # Validation failed: keep the user's input (except the password
            # box, which stays blank) so they only need to fix the bad field.
            defaults = user_input
        else:
            defaults = self.config_entry.data

        data_schema = vol.Schema(
            {
                vol.Required(C_HOST, default=defaults.get(C_HOST, "")): str,
                vol.Required(C_PORT, default=defaults.get(C_PORT, DEFAULT_PORT)): int,
                vol.Required(
                    C_USER, default=defaults.get(C_USER, "")
                ): str,
                # Password box is never pre-filled (blank = keep current).
                vol.Optional(C_PASS, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=data_schema, errors=errors
        )
