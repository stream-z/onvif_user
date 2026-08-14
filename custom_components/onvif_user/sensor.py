"""Sensor platform: the ONVIF user-list overview + last-operation result.

The coordinator itself lives in ``coordinator.py`` and is created by
``__init__.py`` before platforms are forwarded, so this module just registers
the overview sensor against the already-existing coordinator. The result sensor
is a plain entity whose state is written by the submit button in ``button.py``
(via the ``store["result_sensor"]`` reference) after each operation.
"""

import logging
from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, RESULT_SENSOR, device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator = store["coordinator"]
    device_info_data = store.get("device_info")
    result_sensor = OnvifUserResultSensor(entry, device_info_data=device_info_data)
    # Expose to the submit button so it can record the outcome of each action.
    store["result_sensor"] = result_sensor
    async_add_entities(
        [OnvifUserSensor(coordinator, entry, device_info_data=device_info_data), result_sensor], True
    )


class OnvifUserSensor(CoordinatorEntity, SensorEntity):
    """Shows ONVIF user count as state, name/level map in attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "user_count"
    _attr_icon = "mdi:account-group"
    _attr_object_id = "onvif_users"

    def __init__(self, coordinator, entry, device_info_data=None):
        super().__init__(coordinator)
        self._entry = coordinator.entry
        self._attr_unique_id = f"{entry.entry_id}_users"
        self._attr_device_info = device_info(entry, device_info_data)

    @property
    def available(self):
        data = self.coordinator.data
        return bool(data) and data.get("ok", False)

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data or not data.get("ok"):
            return None
        return len(data.get("users", []))

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data or not data.get("ok"):
            return {
                "users": [],
                "error": (data or {}).get("raw") or "no data yet",
            }
        users = data.get("users", [])
        return {
            "users": [u["name"] for u in users],
            "levels": {u["name"]: u["level"] for u in users},
        }


class OnvifUserResultSensor(SensorEntity):
    """Shows the result of the last management-form submission.

    Its state is written programmatically by the submit button (see
    ``button.py``) through ``set_result``; it is not driven by the coordinator.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "last_operation"
    _attr_icon = "mdi:clipboard-check"
    _attr_object_id = RESULT_SENSOR

    def __init__(self, entry, device_info_data=None):
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_result"
        self._attr_device_info = device_info(entry, device_info_data)
        self._attr_native_value = "—"
        self._attr_extra_state_attributes = {}

    def set_result(self, action, subject, ok, detail):
        """Record one operation outcome (success / failure / no-op)."""
        self._attr_native_value = ("✅ " if ok else "❌ ") + detail
        self._attr_extra_state_attributes = {
            "action": action,
            "subject": subject,
            "success": ok,
            "detail": detail,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.async_write_ha_state()
