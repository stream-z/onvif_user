"""Constants for the ONVIF User Manager integration."""

from homeassistant.helpers.entity import DeviceInfo

DOMAIN = "onvif_user"

# Config-flow entry modes. A "service_only" entry carries no device connection
# and exists purely to load the component so the standalone ONVIF user-management
# services are registered without binding any camera.
MODE_SERVICE = "service_only"

# Platforms forwarded by the integration (kept in one place so setup/unload
# always stay symmetric — a mismatch causes "already been setup" on reload).
PLATFORMS = ("sensor", "button", "text", "select")

# Service names (callable from Developer Tools -> Actions, automations, etc.)
SERVICE_LIST = "list_users"
SERVICE_ADD = "add_user"
SERVICE_MODIFY = "modify_user"
SERVICE_DELETE = "delete_user"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

DEFAULT_PORT = 80

# ONVIF UserLevel enum values (matches _LEVELS in client.py / __init__.py).
USER_LEVELS = ("Administrator", "Operator", "User")

# --- Unified management form ---------------------------------------------
# A single form drives create / modify / delete: one submit button reads the
# other form entities (action / target / level selects + name / password texts).
# ``object_id`` is fixed so the Lovelace card can reference each entity by a
# stable id (e.g. ``select.onvif_user_action``).
MGMT_ACTION = "onvif_user_action"      # select  -> create / modify / delete
MGMT_TARGET = "onvif_user_target"      # select  -> existing user
MGMT_LEVEL = "onvif_user_level"        # select  -> Administrator/Operator/User
MGMT_NAME = "onvif_user_name"          # text    -> new username (create)
MGMT_PASSWORD = "onvif_user_password"   # text    -> password (PASSWORD mode)
MGMT_SUBMIT = "onvif_user_submit"      # button  -> commit the form
MGMT_REFRESH = "onvif_user_refresh"    # button  -> immediate refresh
RESULT_SENSOR = "onvif_user_last_result"     # sensor  -> last operation result

# Placeholder option for the target select. SelectEntity shows ``unknown`` when
# ``current_option`` is None, so we use an explicit pseudo-option to represent
# "no target selected" (used while action == create). A single space is used so
# the dropdown shows a blank line instead of the literal text "（未选择）".
# It must never collide with a real camera username.
TARGET_NONE = " "

# Action select options. Values are language-neutral; the displayed label is
# provided by the translation files via entity.select.action.options.
ACTION_CREATE = "create"
ACTION_MODIFY = "modify"
ACTION_DELETE = "delete"
MGMT_ACTIONS = (ACTION_CREATE, ACTION_MODIFY, ACTION_DELETE)


def device_info(entry, info: dict | None = None) -> DeviceInfo:
    """Build the shared DeviceInfo for every entity of this config entry.

    If ``info`` contains fields from ONVIF GetDeviceInformation (and/or vendor-specific
    extensions such as Name / Location / DeviceID) they are used to populate the
    device card. Otherwise we fall back to the static placeholder values so the
    integration still loads even when the camera refuses the probe.
    """
    host = entry.data.get(CONF_HOST, "?")
    port = entry.data.get(CONF_PORT, 80)

    if info:
        name = (
            info.get("Name")
            or info.get("Model")
            or f"UNKNOWN ONVIF Camera ({host}:{port})"
        )
        manufacturer = info.get("Manufacturer") or "UNKNOWN ONVIF"
        model = info.get("Model") or "UNKNOWN ONVIF Camera"
        sw_version = info.get("FirmwareVersion") or "UNKNOWN"
        hw_version = info.get("HardwareId") or "UNKNOWN"
        serial_number = info.get("SerialNumber") or info.get("DeviceID") or "UNKNOWN"
    else:
        name = f"UNKNOWN ONVIF Camera ({host}:{port})"
        manufacturer = "UNKNOWN ONVIF"
        model = "UNKNOWN ONVIF Camera"
        sw_version = hw_version = serial_number = "UNKNOWN"

    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=name,
        manufacturer=manufacturer,
        model=model,
        sw_version=sw_version,
        hw_version=hw_version,
        serial_number=serial_number,
        configuration_url=f"http://{host}:{port}",
    )
