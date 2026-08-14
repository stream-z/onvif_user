"""Select platform: the unified management-form selects.

* action  -- what the submit button should do (create / modify / delete)
* target  -- pick an existing user (options refreshed from the coordinator)
* level   -- Administrator / Operator / User (used by create & modify)

All three are fixed entities (no per-account reconciliation). The submit button
in ``button.py`` reads their ``current_option`` to decide what to do.

Cross-links between the three selects implement the form's UX rules:

* action == 创建  -> target shows the placeholder option (no real target user),
  and the coordinator refresh will NOT re-select the first user while in create.
* entering 修改/删除 with no real target selected -> target snaps to the first
  user.
* selecting a *real* target user (while in 创建) -> action auto-switches to 修改.
* level diverging from the selected target's *current* level (in modify/delete
  mode) -> action auto-switches to 修改.
* selecting a target -> level snaps to that user's current level.
* on a coordinator refresh, the level select re-snaps to the selected user's
  current level *only while it is still "following" the last synced value*.
  This surfaces external changes (e.g. made via ODM) after clicking refresh,
  without clobbering a pending in-form level edit.

The target select always keeps ``TARGET_NONE`` in its option list so its
``current_option`` is never ``None`` (a SelectEntity with ``current_option=None``
renders as ``unknown`` in the UI, which is what we want to avoid).
"""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_MODIFY,
    CONF_USERNAME,
    DOMAIN,
    MGMT_ACTION,
    MGMT_ACTIONS,
    MGMT_LEVEL,
    MGMT_TARGET,
    TARGET_NONE,
    USER_LEVELS,
    device_info,
)

_LOGGER = logging.getLogger(__name__)

# Suffix appended to the label of the account the integration logs in with.
#
# The text follows the *system* language (``hass.config.language`` -> 设置→系统→常规→语言),
# NOT each user's profile language: a select entity's ``options`` are global entity state
# served identically to every user, and only static option values can be localised through
# ``translations/*.json``. Dynamic usernames can never enter that file, so a per-user-profile
# localisation is impossible by design -- a documented, deliberate limitation.


class OnvifUserActionSelect(SelectEntity):
    """What the submit button should do."""

    _attr_has_entity_name = True
    _attr_options = list(MGMT_ACTIONS)
    _attr_object_id = MGMT_ACTION

    # Dynamic icon per the currently selected management action.
    _ACTION_ICONS = {
        ACTION_CREATE: "mdi:account-plus",
        ACTION_MODIFY: "mdi:account-edit",
        ACTION_DELETE: "mdi:account-minus",
    }

    @property
    def icon(self) -> str | None:
        return self._ACTION_ICONS.get(self.current_option, "mdi:gesture-tap-button")

    def __init__(self, entry, device_info_data=None):
        self._entry = entry
        self._target_entity = None
        self._level_entity = None
        self._attr_translation_key = "action"
        self._attr_unique_id = f"{entry.entry_id}_action"
        self._attr_current_option = ACTION_CREATE
        self._attr_device_info = device_info(entry, device_info_data)

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()
        target = self._target_entity
        if target is None:
            return
        if option == ACTION_CREATE:
            # 创建没有目标用户 -> target 回到占位（空），不要显示 unknown
            if target._attr_current_option != TARGET_NONE:
                target._attr_current_option = TARGET_NONE
                target.async_write_ha_state()
            # 进入创建：level 重置为默认级别，不要停在上一账户的级别上
            level_ent = self._level_entity
            if level_ent is not None and level_ent.current_option != USER_LEVELS[0]:
                await level_ent.async_select_option(USER_LEVELS[0])
        elif option in (ACTION_MODIFY, ACTION_DELETE):
            # 进入 修改/删除 时若没有真实目标，自动选中第一个用户，避免死路
            cur = target._attr_current_option
            if cur is None or cur == TARGET_NONE:
                first_real = next(
                    (o for o in target._attr_options if o != TARGET_NONE), None
                )
                if first_real is not None:
                    await target.async_select_option(first_real)


class OnvifUserLevelSelect(SelectEntity):
    """New / target level used by create and modify."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:shield-account"
    _attr_options = list(USER_LEVELS)
    _attr_object_id = MGMT_LEVEL

    def __init__(self, entry, device_info_data=None):
        self._entry = entry
        self._target_entity = None
        self._action_entity = None
        self._coordinator = None
        # The level this select was last snapped to (via target switch / first
        # load / a refresh while "following"). Used to tell a pending in-form
        # edit apart from a still-following value, so a coordinator refresh can
        # surface external changes (e.g. done via ODM) without clobbering edits.
        self._last_synced_level = None
        self._attr_translation_key = "level"
        self._attr_unique_id = f"{entry.entry_id}_level"
        self._attr_current_option = "Administrator"
        self._attr_device_info = device_info(entry, device_info_data)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._coordinator is not None:
            self.async_on_remove(
                self._coordinator.async_add_listener(self._on_coordinator_update)
            )

    @callback
    def _on_coordinator_update(self) -> None:
        """Re-snap to the selected user's *current* level on refresh.

        Only re-syncs while the level select is still "following" the value it
        was last snapped to (``_last_synced_level``). If the user manually
        diverged it (a pending modify edit), we keep their value so a background
        refresh can't clobber it. This is what makes an external change via ODM
        show up after clicking refresh.
        """
        target = self._target_entity
        if target is None:
            return
        name = target.current_target
        if not name or name == TARGET_NONE:
            return
        actual = target._current_level_of(name)
        if not actual or actual not in USER_LEVELS:
            return
        if actual == self._attr_current_option:
            # Already aligned (e.g. after a successful submit) -> just re-anchor.
            self._last_synced_level = actual
            return
        # Diverged from the last synced value? Then it's a pending edit -> keep.
        if self._last_synced_level is not None and self._attr_current_option != self._last_synced_level:
            return
        # Still following the old value but the camera moved -> re-snap.
        self._attr_current_option = actual
        self._last_synced_level = actual
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()
        # 当 level 与当前 target 账户的级别不一致时，自动把 action 切到「修改」。
        # 创建模式下 target 为占位（无真实账户），不会触发。
        target = self._target_entity
        action = self._action_entity
        if target is None or action is None:
            return
        if action._attr_current_option == ACTION_CREATE:
            return
        name = target.current_target
        if not name or name == TARGET_NONE:
            return
        current = target._current_level_of(name)
        if current and option != current and action._attr_current_option != ACTION_MODIFY:
            await action.async_select_option(ACTION_MODIFY)


class OnvifUserTargetSelect(CoordinatorEntity, SelectEntity):
    """Pick the existing user that 'modify' / 'delete' act on.

    Options always include ``TARGET_NONE`` (a placeholder for "no selection") so
    the entity never shows ``unknown``. Options are kept in sync with the live
    user list via a coordinator listener. Selecting a *real* user also snaps the
    level select to that user's *current* level, so the form always reflects the
    real state of the chosen account.

    The account that the integration itself uses to log in is pinned to the top
    of the list and marked with the *system*-language suffix (e.g. ``admin（当前登录）``
    in a Chinese system UI, ``admin (current login)`` in an English one) so it is easy
    to spot. The suffix follows the system language and is rebuilt live when that
    language changes (see ``async_added_to_hass`` / ``_on_core_config_updated``).
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-switch"
    _attr_options = [TARGET_NONE]
    _attr_object_id = MGMT_TARGET

    def __init__(self, coordinator, entry, level_entity=None, device_info_data=None):
        super().__init__(coordinator)
        self._entry = entry
        self._level_entity = level_entity
        self._action_entity = None
        self._attr_current_option = TARGET_NONE
        self._attr_translation_key = "target"
        self._attr_unique_id = f"{entry.entry_id}_target"
        self._attr_device_info = device_info(entry, device_info_data)
        self._refresh_options()

    def _current_suffix(self) -> str:
        """Localised marker for the integration's own login account (system language).

        Falls back to the Chinese form before ``hass`` is attached (during
        ``__init__``), where ``self.hass`` is still ``None`` and would otherwise
        raise ``AttributeError``.
        """
        if self.hass is None:
            return "（当前登录）"
        lang = (self.hass.config.language or "").lower()
        if lang.startswith("zh"):
            return "（当前登录）"
        return " (current login)"

    def _label_for(self, value: str) -> str:
        """Return the UI label for a real username (marks the login account)."""
        if value and value == self._entry.data.get(CONF_USERNAME):
            return f"{value}{self._current_suffix()}"
        return value

    def _value_for(self, label: str) -> str:
        """Return the real username from a UI label (strips the current-account marker).

        Only the *current* language's suffix is recognised -- older variants are no
        longer stripped, so a clean re-add (delete + setup) is expected after an
        upgrade that changes the marker text.
        """
        if label and label != TARGET_NONE:
            suf = self._current_suffix()
            if label.endswith(suf):
                return label[: -len(suf)]
        return label

    @property
    def current_target(self) -> str | None:
        """The real username currently selected (``None`` when the placeholder is selected)."""
        cur = self._attr_current_option
        if not cur or cur == TARGET_NONE:
            return None
        return self._value_for(cur)

    def _real_names(self):
        data = self.coordinator.data
        if not data or not data.get("ok"):
            return []
        names = [u["name"] for u in data.get("users", [])]
        # Pin the integration's own login account to the top of the list so it
        # is easy to find in the dropdown.
        cfg_user = self._entry.data.get(CONF_USERNAME)
        if cfg_user and cfg_user in names:
            names.remove(cfg_user)
            names.insert(0, cfg_user)
        return names

    @callback
    def _refresh_options(self):
        """Rebuild the option list; keep a valid selection (never ``unknown``).

        ``TARGET_NONE`` is always kept in the option list, so ``current_option``
        is always a member of ``options`` and the UI never renders ``unknown``.

        The desired selection is derived from the *current* action state and the
        live user list, so this also self-corrects when the action entity gets
        wired after construction (its value is unknown during ``__init__``).
        """
        real = self._real_names()
        options = [TARGET_NONE] + [self._label_for(n) for n in real]

        action = self._action_entity
        action_opt = action._attr_current_option if action else None
        cur_label = self._attr_current_option
        cur_value = self._value_for(cur_label)

        if action_opt == ACTION_CREATE or not real:
            desired = TARGET_NONE
        elif cur_value in real:
            desired = self._label_for(cur_value)  # keep selection, refresh suffix
        else:
            desired = self._label_for(real[0])

        if options == self._attr_options and cur_label == desired:
            return
        self._attr_options = options
        if cur_label != desired:
            self._attr_current_option = desired
        self.async_write_ha_state()

    def _current_level_of(self, name):
        if name == TARGET_NONE:
            return None
        data = self.coordinator.data
        if not data or not data.get("ok"):
            return None
        for u in data.get("users", []):
            if u.get("name") == name:
                return u.get("level")
        return None

    async def _sync_level(self):
        """Snap the level select to the selected user's current level.

        Called when the target *selection* changes (or on first load). Also
        records ``_last_synced_level`` on the level entity so a later
        coordinator refresh knows the level is "following" the real value and may
        re-snap it after an external change (e.g. via ODM).

        We re-fetch the user list first so the snap reflects the camera's
        *current* state — including any change made out-of-band via ODM since the
        last manual refresh. This restores the pre-polling behaviour where
        switching targets always showed the live level: back then the coordinator
        was kept fresh by the background poll, but now that polling is gone the
        data is only as recent as the last refresh, so we must pull on switch.
        """
        level_ent = self._level_entity
        if level_ent is None:
            return
        target = self.current_target
        if not target or target == TARGET_NONE:
            return
        # Pull fresh data so the snap reflects out-of-band (ODM) changes too.
        if self.coordinator is not None:
            await self.coordinator.async_request_refresh()
        lvl = self._current_level_of(target)
        if lvl and lvl in USER_LEVELS:
            level_ent._last_synced_level = lvl
            if lvl != level_ent.current_option:
                await level_ent.async_select_option(lvl)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # ``self.hass`` is available now -> the language-aware suffix is correct.
        self._refresh_options()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._refresh_options)
        )
        # Rebuild option labels when the system language changes (no restart needed).
        self.async_on_remove(
            self.hass.bus.async_listen("core_config_updated", self._on_core_config_updated)
        )
        # Initial snap: show the first user's level on load (if any).
        await self._sync_level()

    @callback
    def _on_core_config_updated(self, _event) -> None:
        """System language (or other core config) changed -> refresh option labels."""
        self._refresh_options()

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()

        action = self._action_entity
        # 关键顺序：先把 action 从「创建」切到「修改」*再* 触发 _sync_level。
        # 否则 _sync_level 内的 coordinator 刷新会先跑 _refresh_options，此刻
        # action 仍是「创建」-> 强制 target 回 TARGET_NONE -> 随后 action 切
        # 「修改」又因 target 为空而自动选首用户，表现为「选第二个账户跳回第一个」。
        # 先让 target 持有真实账户再切 action，action 切「修改」时就不会自动选首用户。
        if (
            option != TARGET_NONE
            and action is not None
            and action._attr_current_option == ACTION_CREATE
        ):
            await action.async_select_option(ACTION_MODIFY)

        if option != TARGET_NONE:
            await self._sync_level()
        elif (
            option == TARGET_NONE
            and action is not None
            and action._attr_current_option != ACTION_CREATE
        ):
            # target 变回「未选择」-> 回到创建模式
            await action.async_select_option(ACTION_CREATE)


async def async_setup_entry(hass, entry, async_add_entities):
    store = hass.data[DOMAIN][entry.entry_id]
    mgmt = store.setdefault("mgmt", {})
    device_info_data = store.get("device_info")

    coordinator = store.get("coordinator")
    target = None
    if coordinator is not None:
        target = OnvifUserTargetSelect(coordinator, entry, device_info_data=device_info_data)
        mgmt["target"] = target

    action = OnvifUserActionSelect(entry, device_info_data=device_info_data)
    level = OnvifUserLevelSelect(entry, device_info_data=device_info_data)
    level._coordinator = coordinator
    mgmt["action"] = action
    mgmt["level"] = level

    # Wire cross-references so the form rules can react to each other.
    action._target_entity = target
    action._level_entity = level
    level._target_entity = target
    level._action_entity = action
    if target is not None:
        target._level_entity = level
        target._action_entity = action

    entities = [action, level]
    if target is not None:
        entities.append(target)

    async_add_entities(entities, True)
