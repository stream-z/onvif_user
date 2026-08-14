"""ONVIF User Manager integration.

Exposes ONVIF user management (list / add / modify / delete) as Home Assistant
services, because the camera's own web UI removed the page
and all onvif* JSON-RPC entries. Runs as a native process -> no browser CORS.
"""

import aiohttp
import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import SupportsResponse
from homeassistant.helpers.typing import ConfigType

from .client import OnvifUserClient
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    DOMAIN,
    PLATFORMS,
    SERVICE_ADD,
    SERVICE_DELETE,
    SERVICE_LIST,
    SERVICE_MODIFY,
    MODE_SERVICE,
)
from .coordinator import OnvifUserCoordinator

_LOGGER = logging.getLogger(__name__)

_LEVELS = ["Administrator", "Operator", "User"]

# Standalone services: every call carries the device connection + the ONVIF
# management account (the credentials permitted to call GetUsers/SetUser/...).
# They do NOT depend on a pre-configured config entry, so you can manage any
# ONVIF camera on the fly. Each call builds its own client (and its own aiohttp
# session, closed by the client) — there is no shared state to refresh and no
# bound entity to report into, so the response is returned directly to the
# caller via supports_response.
_COMMON_FIELDS = {
    vol.Required("host"): cv.string,  # camera IP / hostname
    vol.Required("port"): cv.port,  # ONVIF device port (usually 80)
    vol.Required("username"): cv.string,  # ONVIF management account
    vol.Required("password"): cv.string,  # management account password
}

SERVICE_SCHEMAS = {
    SERVICE_LIST: vol.Schema(dict(_COMMON_FIELDS)),
    SERVICE_ADD: vol.Schema(
        {
            **_COMMON_FIELDS,
            vol.Required("target_username"): cv.string,
            vol.Required("target_password"): cv.string,
            vol.Optional("level", default="User"): vol.In(_LEVELS),
        }
    ),
    SERVICE_MODIFY: vol.Schema(
        {
            **_COMMON_FIELDS,
            vol.Required("target_username"): cv.string,
            vol.Optional("target_password"): cv.string,
            vol.Optional("level"): vol.In(_LEVELS),
        }
    ),
    SERVICE_DELETE: vol.Schema(
        {
            **_COMMON_FIELDS,
            vol.Required("target_username"): cv.string,
        }
    ),
}

def _client(call: ServiceCall) -> OnvifUserClient:
    """Build a one-shot client from the call's connection + management creds.

    The client opens (and closes, in ``_post``) its own aiohttp session, so a
    standalone service call carries no shared state and never depends on a
    pre-configured config entry.
    """
    return OnvifUserClient(
        host=call.data["host"],
        port=call.data["port"],
        user=call.data["username"],
        password=call.data["password"],
        session=None,
    )


async def _svc_list(call: ServiceCall):
    return await _client(call).list_users()


async def _svc_add(call: ServiceCall):
    return await _client(call).add_user(
        call.data["target_username"],
        call.data["target_password"],
        call.data.get("level", "User"),
    )


async def _svc_modify(call: ServiceCall):
    client = _client(call)
    username = call.data["target_username"]
    password = call.data.get("target_password")
    level = call.data.get("level")
    if not password and not level:
        return {
            "ok": False,
            "error": "specify at least one of target_password or level",
        }
    return await client.modify_user(username, level=level, password=password)


async def _svc_delete(call: ServiceCall):
    return await _client(call).delete_user(call.data["target_username"])


SERVICE_HANDLERS = {
    SERVICE_LIST: _svc_list,
    SERVICE_ADD: _svc_add,
    SERVICE_MODIFY: _svc_modify,
    SERVICE_DELETE: _svc_delete,
}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration at the component level.

    The standalone ONVIF user-management services are registered here (not in
    ``async_setup_entry``) so that services.yaml descriptions are associated with
    the handlers during component setup — this is what makes the Actions UI form
    render. Registering at component level also means the services work even
    before any camera config entry exists.

    See the official guidance: register services in ``async_setup``/``setup``, not
    in ``async_setup_entry``.
    """
    for name, handler in SERVICE_HANDLERS.items():
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=SERVICE_SCHEMAS[name],
            supports_response=SupportsResponse.OPTIONAL,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    if entry.data.get("mode") == MODE_SERVICE:
        # Services are already registered at the component level (async_setup),
        # so a service-only entry just needs to keep the component loaded. We
        # bind nothing — no client, no coordinator, no entities.
        hass.data[DOMAIN][entry.entry_id] = {}
        return True

    # A "services only" entry is a bootstrap placeholder that merely keeps the
    # component loaded so the standalone services register before any camera is
    # configured. Now that a real device entry exists it is redundant, so remove
    # it. Fire-and-forget (not awaited) to avoid re-entrancy during this entry's
    # own setup; the placeholder carries no entities or sessions to tear down.
    for so_entry in hass.config_entries.async_entries(DOMAIN):
        if (
            so_entry.entry_id != entry.entry_id
            and so_entry.data.get("mode") == MODE_SERVICE
        ):
            _LOGGER.info(
                "Removing redundant 'services only' entry %s (a device is now "
                "configured)",
                so_entry.title,
            )
            hass.async_create_task(
                hass.config_entries.async_remove(so_entry.entry_id)
            )

    session = aiohttp.ClientSession()
    client = OnvifUserClient(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        user=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        session=session,
    )
    # The coordinator holds no polling interval; data is fetched on demand via
    # the refresh button / service / after a write.
    coordinator = OnvifUserCoordinator(hass, entry, client)

    # Probe ONVIF device information before creating entities so every platform
    # can build its DeviceInfo from the live camera data rather than a static
    # placeholder. Failures are non-fatal: we fall back to the placeholder.
    device_info_data = None
    try:
        raw_info = await client.get_device_info()
        if raw_info and raw_info.get("ok"):
            device_info_data = {
                k: v
                for k, v in raw_info.items()
                if k not in ("ok", "status", "raw", "fault")
            }
            _LOGGER.debug("ONVIF device information: %s", device_info_data)
    except Exception as exc:  # pragma: no cover - best effort
        _LOGGER.warning("Failed to fetch ONVIF device information: %s", exc)

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "session": session,
        "coordinator": coordinator,
        "mgmt": {},  # form entities: action/target/level/name/password instances
        "device_info": device_info_data,
    }
    # Forward platforms first; the target-user select reads this coordinator.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Then do the first fetch so every entity (incl. target options) is populated.
    await coordinator.async_config_entry_first_refresh()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Tear down the forwarded platforms FIRST. Without this, a subsequent
    # async_setup_entry (e.g. triggered by an options change / reload) hits
    # "Config entry ... has already been setup!" for sensor/button/text/select.
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    item = hass.data[DOMAIN].pop(entry.entry_id, None)
    if item and item.get("session"):
        await item["session"].close()
    # Services are registered at the component level (async_setup) and persist for
    # the component's lifetime, so they are intentionally NOT removed here. They
    # remain available even with zero config entries (standalone design).
    return unload_ok
