"""Button platform: refresh + the single unified submit button.

``OnvifUserSubmitButton`` reads the management-form entities (stored in
``hass.data[DOMAIN][entry_id]["mgmt"]``) and commits the chosen action:

* 创建  -- requires username + password + level (default Administrator);
          clears name + password after success
* 修改  -- requires a target user; applies the new password when the field is
          non-empty, and only changes the level when it differs from the user's
          current level (so a password-only change never downgrades the level).
          If neither the level changed nor a password was entered -> warning,
          no operation. The username text input is meaningless in this mode
          (ONVIF can't rename): if it holds text on submit, it is cleared and
          the prompt notes it was ignored. Clears password after success.
* 删除  -- requires a target user

Every submission (success, failure or no-op) and the on-demand refresh are
recorded on the ``onvif_user_last_result`` sensor (刷新成功 / 刷新失败) via the
shared ``record_result`` helper in ``report.py``.
The password (and, on create, the username) is cleared from the form after a
successful submit so it is not reused or left sitting in the entity state.
"""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_MODIFY,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    MGMT_NAME,
    MGMT_PASSWORD,
    MGMT_REFRESH,
    MGMT_SUBMIT,
    device_info,
)
from .report import detail_from_response, record_result, _rt, _sys_lang

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator = store["coordinator"]
    device_info_data = store.get("device_info")
    async_add_entities(
        [
            OnvifUserRefreshButton(coordinator, entry, device_info_data=device_info_data),
            OnvifUserSubmitButton(coordinator, entry, device_info_data=device_info_data),
        ],
        True,
    )


class OnvifUserRefreshButton(CoordinatorEntity, ButtonEntity):
    """Press to refresh the user list on demand (no background polling).

    The outcome (刷新成功 / 刷新失败) is also written to the result sensor so the
    refresh button behaves like the other management operations.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "refresh_users"
    _attr_icon = "mdi:refresh"
    _attr_object_id = MGMT_REFRESH

    def __init__(self, coordinator, entry, device_info_data=None):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = device_info(entry, device_info_data)

    async def async_press(self):
        await self.coordinator.async_request_refresh()
        # Record the refresh outcome on the result sensor so "Refresh" behaves
        # like the other operations: Refresh OK / Refresh failed (+ device fault
        # on failure). Text follows the system language (see report._sys_lang).
        lang = _sys_lang(self.hass)
        data = self.coordinator.data
        ok = bool(data and data.get("ok"))
        detail = (
            _rt(lang, "refresh_ok")
            if ok
            else _rt(lang, "refresh_failed", detail=detail_from_response(data, lang))
        )
        record_result(self.hass, self._entry.entry_id, _rt(lang, "refresh_action"), "", ok, detail)


class OnvifUserSubmitButton(CoordinatorEntity, ButtonEntity):
    """Commit the management form: create / modify / delete an ONVIF user."""

    _attr_has_entity_name = True
    _attr_translation_key = "submit"
    _attr_icon = "mdi:check-bold"
    _attr_object_id = MGMT_SUBMIT

    def __init__(self, coordinator, entry, device_info_data=None):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_submit"
        self._attr_device_info = device_info(entry, device_info_data)

    async def async_press(self):
        item = self.hass.data[DOMAIN][self._entry.entry_id]
        client = item["client"]
        mgmt = item.get("mgmt", {})

        action_sel = mgmt.get("action")
        target_sel = mgmt.get("target")
        level_sel = mgmt.get("level")
        name_txt = mgmt.get("name")
        pw_txt = mgmt.get("password")

        action = action_sel.current_option if action_sel else None
        # The target select labels the integration's own login account with a
        # suffix; ``current_target`` strips that suffix and returns the real
        # username (or None when the placeholder is selected).
        target = target_sel.current_target if target_sel else None
        level = level_sel.current_option if level_sel else None
        name = (name_txt.native_value if name_txt else "") or ""
        password = (pw_txt.native_value if pw_txt else "") or ""
        name = name.strip()
        password = password.strip()

        # All result-sensor text follows the *system* language (see report._sys_lang),
        # consistent with the target "（当前登录）" suffix behaviour.
        lang = _sys_lang(self.hass)

        def _report(ok, detail, subject=None):
            # Route through the shared helper so the sensor is looked up fresh on
            # every call (a momentarily-unavailable sensor only drops one write).
            record_result(
                self.hass,
                self._entry.entry_id,
                action or "?",
                subject if subject is not None else (target or name or ""),
                ok,
                detail,
            )

        async def _clear_password():
            if pw_txt is not None:
                try:
                    await pw_txt.async_set_value("")
                except Exception:  # pragma: no cover - best effort
                    pass

        # 非创建模式（修改/删除）下，用户名输入框无意义（ONVIF 不支持改名），
        # 仅用于创建；删除模式中密码也无意义（删除操作不需要密码）。提交时若这些
        # 字段里填了内容，统一清空并在提示里追加「已忽略」说明。
        #   - 删除模式：用户名与密码都不可提交 → 提示为「用户名、密码不可提交」
        #   - 修改模式：仅用户名不可提交（密码可用来改密）→ 提示仅含「用户名」
        # 创建模式不会清空这些字段（它们正是创建所需），也不拼接该提示。
        name_entered = bool(name)
        pw_entered = bool(password)
        if action == ACTION_DELETE:
            ignored_hint = _rt(lang, "ignored_both") if (name_entered or pw_entered) else ""
        else:
            ignored_hint = _rt(lang, "ignored_name") if name_entered else ""

        async def _clear_name():
            if name_entered and name_txt is not None:
                try:
                    await name_txt.async_set_value("")
                except Exception:  # pragma: no cover - best effort
                    pass

        async def _switch_to_modify():
            """删除失败/被拦截时：仅把操作切回修改模式，保留当前选中的目标账户（不重选），
            便于用户直接在原账户上重试。"""
            act = mgmt.get("action")
            if act is not None:
                await act.async_select_option(ACTION_MODIFY)

        async def _snap_to_modify():
            """删除成功时：目标账户已被删除，刷新列表后切回修改模式并选中首个剩余用户。"""
            tgt = mgmt.get("target")
            act = mgmt.get("action")
            users = (
                self.coordinator.data.get("users", [])
                if self.coordinator.data
                else []
            )
            if tgt is not None and act is not None and users:
                first = users[0]["name"]
                await tgt.async_select_option(tgt._label_for(first))  # 选中首个剩余用户并同步级别
                await act.async_select_option(ACTION_MODIFY)            # 删除 -> 修改

        res = None
        if action == ACTION_CREATE:
            if not name or not password or not level:
                _report(False, _rt(lang, "create_need"), subject=name)
                _LOGGER.warning("Create: username, password and level are required")
                return
            res = await client.add_user(name, password, level)
            if not res or not res.get("ok"):
                _report(False, _rt(lang, "create_failed", detail=detail_from_response(res, lang)), subject=name)
                _LOGGER.warning("Failed to create user: %s", detail_from_response(res))
                return
            _report(True, _rt(lang, "create_ok", name=name, level=level), subject=name)
            if name_txt is not None:
                try:
                    await name_txt.async_set_value("")
                except Exception:  # pragma: no cover - best effort
                    pass
            await _clear_password()
            await self.coordinator.async_request_refresh()
            return res

        elif action == ACTION_MODIFY:
            # 非创建模式：用户名无效（ONVIF 不支持改名），仅用于创建。
            # name 是否填写、是否清空、提示后缀均由顶层的 ignored_hint/_clear_name 处理。
            if not target:
                _report(False, _rt(lang, "modify_need_target", ignored=ignored_hint), subject=target)
                _LOGGER.warning("Modify: a target user must be selected")
                await _clear_name()
                return
            # The level select auto-snaps to the selected user's level, so a
            # password-only change never downgrades the level. In non-create mode
            # the target is always a real user (the blank placeholder forces
            # CREATE), so ``level`` is always a valid level here — never None.
            current_level = None
            data = self.coordinator.data if self.coordinator else None
            if data and data.get("ok"):
                for u in data.get("users", []):
                    if u.get("name") == target:
                        current_level = u.get("level")
                        break
            # Detect a level edit by comparing the select against the camera's
            # current level. ``level`` is always present in non-create mode, so we
            # guard on ``current_level`` instead: if the camera level can't be read
            # for some reason, treat it as "no change" rather than spuriously
            # forcing a level edit (and its mandatory password) on the user.
            level_changed = current_level is not None and level != current_level
            pw_changed = bool(password)
            if not level_changed and not pw_changed:
                _report(
                    False,
                    _rt(lang, "modify_noop", ignored=ignored_hint),
                    subject=target,
                )
                _LOGGER.warning("Modify: no level change and no new password, nothing to do")
                await _clear_name()
                return
            # Some cameras' SetUser requires the password; a level-only change is
            # rejected, so demand the password whenever the level is touched.
            if level_changed and not pw_changed:
                _report(
                    False,
                    _rt(lang, "modify_need_pw", ignored=ignored_hint),
                    subject=target,
                )
                _LOGGER.warning("Modifying the level requires a new password (device needs SetUser with password)")
                await _clear_name()
                return
            if level_changed and pw_changed:
                # One SetUser carrying both the new level and the new password.
                r = await client.modify_user(target, level, password)
                ok = bool(r and r.get("ok"))
                done = _rt(lang, "done_level_pw", level=level)
            else:  # pw_changed only
                # Pass the user's current level so the SetUser request carries a
                # complete User struct (the camera requires UserLevel to be present).
                r = await client.modify_user(target, current_level or level, password)
                ok = bool(r and r.get("ok"))
                done = _rt(lang, "done_pw")
            if not ok:
                _report(False, _rt(lang, "modify_failed", detail=detail_from_response(r, lang), ignored=ignored_hint), subject=target)
                _LOGGER.warning("Failed to modify user: %s", detail_from_response(r))
                await _clear_name()
                return
            # 如果修改的正是本集成登录所用的账户，把新密码同步回集成配置
            # 与运行中的 client，避免凭据失效导致集成不可用（免手动重配）。
            # async_update_entry 不会自动重载集成，所以必须同时改 client.password。
            cfg_user = self._entry.data.get(CONF_USERNAME)
            cfg_pw = self._entry.data.get(CONF_PASSWORD)
            synced = ""
            # 仅当：填了密码 + 目标正是集成登录账户 + 新密码与集成当前存的密码不同
            # （即确实改了密码）才写回。重述同密码（password==原配置密码）不算改动，
            # 不写配置、也不提示「已同步」。HA 无法读取相机真实当前密码，能对照的
            # 只有集成配置里登录账户的密码（它即相机真实密码，否则集成连不上）。
            if pw_changed and target == cfg_user and password != cfg_pw:
                client.password = password
                new_data = dict(self._entry.data)
                new_data[CONF_PASSWORD] = password
                self.hass.config_entries.async_update_entry(
                    self._entry, data=new_data
                )
                synced = _rt(lang, "synced_pw")
            _report(True, _rt(lang, "modify_ok", target=target, done=done, synced=synced, ignored=ignored_hint), subject=target)
            await _clear_password()
            await _clear_name()
            await self.coordinator.async_request_refresh()
            return

        elif action == ACTION_DELETE:
            # 非创建模式：用户名无效，提交后清空（见顶层 ignored_hint / _clear_name）。
            if not target:
                _report(False, _rt(lang, "delete_need_target", ignored=ignored_hint), subject=target)
                _LOGGER.warning("Delete: a target user must be selected")
                await _clear_name()
                await _clear_password()
                await _switch_to_modify()
                return
            cfg_user = self._entry.data.get(CONF_USERNAME)
            if target == cfg_user:
                # 禁止删除当前集成登录所用的账户：删掉后集成将无法再向设备
                # 认证，导致所有 ONVIF 操作（列表/改密/删除）全部失效。
                _report(
                    False,
                    _rt(lang, "delete_self", cfg_user=cfg_user, ignored=ignored_hint),
                    subject=target,
                )
                _LOGGER.warning("Delete: refusing to delete the integration's own login account %s", cfg_user)
                await _clear_name()
                await _clear_password()
                await _switch_to_modify()
                return
            res = await client.delete_user(target)
            if not res or not res.get("ok"):
                _report(False, _rt(lang, "delete_failed", detail=detail_from_response(res, lang), ignored=ignored_hint), subject=target)
                _LOGGER.warning("Failed to delete user: %s", detail_from_response(res))
                await _clear_name()
                await _clear_password()
                await _switch_to_modify()
                return
            _report(True, _rt(lang, "delete_ok", target=target, ignored=ignored_hint), subject=target)
            # 删除成功后刷新列表（被删用户已不在），再切回修改模式、选中首个用户
            await self.coordinator.async_request_refresh()
            await _snap_to_modify()
            await _clear_password()
            await _clear_name()
            return res

        else:
            _report(False, _rt(lang, "unknown_action", action=action))
            _LOGGER.warning("Unknown action: %s", action)
            return
