"""Config flow for ONVIF User Manager."""

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
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
    MODE_SERVICE,
)

# Config-flow entry modes. "device" binds a camera and creates UI entities;
# "service_only" just loads the component so the standalone services register.
MODE_DEVICE = "device"


class OnvifUserConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a single-camera config entry."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        # Once any entry already exists the integration component is loaded and
        # its standalone services are registered, so offering the "services
        # only" mode again is redundant — go straight to the device form. The
        # mode choice is only meaningful on the very first entry (zero entries),
        # and re-appears automatically if every entry is later removed.
        if self._async_current_entries():
            return await self.async_step_device()

        if user_input is not None:
            mode = user_input.get("mode", MODE_DEVICE)
            if mode == MODE_SERVICE:
                # No device binding — this entry only loads the component so the
                # standalone services are registered. Skips the probe entirely.
                # The title is frozen at creation time and follows the *system*
                # language (profile language cannot translate entry titles), the
                # same convention used by the target "(current login)" suffix.
                title = (
                    "ONVIF 用户管理（仅服务）"
                    if (self.hass.config.language or "").lower().startswith("zh")
                    else "ONVIF User Manager (services only)"
                )
                return self.async_create_entry(
                    title=title,
                    data={"mode": MODE_SERVICE},
                )
            # Device mode: continue to the connection form.
            return await self.async_step_device()
        data_schema = vol.Schema(
            {
                vol.Required("mode", default=MODE_DEVICE): SelectSelector(
                    SelectSelectorConfig(
                        options=[MODE_DEVICE, MODE_SERVICE],
                        translation_key="setup_mode",
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema)

    async def async_step_device(self, user_input=None):
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
            step_id="device", data_schema=data_schema, errors=errors
        )

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry):
        # HA decides whether to show the Configure button via this method, NOT
        # via async_get_options_flow returning None. (The default implementation
        # only checks if the subclass overrode async_get_options_flow, so the
        # button would always show otherwise.) A service-only entry has no bound
        # device, so there is nothing to configure — hide the button for it.
        return config_entry.data.get("mode") != MODE_SERVICE

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        # Always return an OptionsFlow instance. The button is hidden by
        # async_supports_options_flow above, so for a service-only entry this is
        # never started from the UI. Returning an instance (instead of None)
        # avoids a 500 if the flow is ever started by a direct API call — then
        # async_step_init aborts cleanly via the getattr guard.
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
        # Defensive fallback: modern HA already hides the Configure button for a
        # service-only entry (see async_supports_options_flow, which returns False
        # for that mode), but if the button is ever shown anyway we must not touch
        # the (empty) entry.data — that would raise and bubble up as a 500. The
        # getattr guard also keeps pre-2024.11 OptionsFlow (no self.config_entry
        # injection) from crashing here.
        entry = getattr(self, "config_entry", None)
        if entry is not None and entry.data.get("mode") == MODE_SERVICE:
            return self.async_abort(reason="no_options")
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
