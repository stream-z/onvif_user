"""Text platform: the unified management-form text inputs (name / password)."""

import logging

from homeassistant.components.text import TextEntity, TextMode

from .const import DOMAIN, MGMT_NAME, MGMT_PASSWORD, device_info

_LOGGER = logging.getLogger(__name__)


class OnvifUserText(TextEntity):
    """Single-line input for the management form (username or password)."""

    _attr_has_entity_name = True
    _attr_native_max = 64
    _attr_native_value = ""

    def __init__(self, entry, object_id, translation_key, mode, device_info_data=None):
        self._entry = entry
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{entry.entry_id}_{object_id}"
        self._attr_object_id = object_id
        self._attr_mode = mode
        # icon 按字段类型区分：用户名用重命名盒、密码用锁
        self._attr_icon = (
            "mdi:lock" if object_id == MGMT_PASSWORD else "mdi:rename-box"
        )
        self._attr_device_info = device_info(entry, device_info_data)

    async def async_set_value(self, value: str) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()


async def async_setup_entry(hass, entry, async_add_entities):
    store = hass.data[DOMAIN][entry.entry_id]
    mgmt = store.setdefault("mgmt", {})
    device_info_data = store.get("device_info")

    name = OnvifUserText(
        entry, MGMT_NAME, "username", TextMode.TEXT,
        device_info_data=device_info_data,
    )
    password = OnvifUserText(
        entry, MGMT_PASSWORD, "password", TextMode.PASSWORD,
        device_info_data=device_info_data,
    )
    mgmt["name"] = name
    mgmt["password"] = password

    async_add_entities([name, password], True)
