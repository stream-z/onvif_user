"""DataUpdateCoordinator for the ONVIF User Manager.

Kept in its own module so ``__init__.py`` can create the instance before the
platforms are forwarded (otherwise the target-user select, which needs the
coordinator, would race with sensor setup that used to create it).

Polling is intentionally disabled: ``update_interval`` is ``None`` so the
coordinator never auto-schedules. Data is only (re)fetched on demand via the
refresh button / ``refresh`` service / after a write operation. This avoids
hammering non-conformant ONVIF firmware and keeps the entity state explicit.
"""

import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import OnvifUserClient

_LOGGER = logging.getLogger(__name__)


class OnvifUserCoordinator(DataUpdateCoordinator):
    """Caches the GetUsers result for every entity; refreshed on demand only."""

    def __init__(self, hass, entry, client: OnvifUserClient):
        super().__init__(
            hass,
            _LOGGER,
            name=f"onvif_user_{entry.entry_id}",
            # No automatic polling — only async_request_refresh() / first refresh.
            update_interval=None,
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self):
        return await self.client.list_users()
