"""Shared result-recording helpers for the ONVIF User Manager integration.

The unified "提交" (submit) button (``button.py``) is the only entry point that
writes to the ``上次操作结果`` (last operation) result sensor, so its state stays
consistent across interactive edits. The standalone services (``__init__.py``:
``add_user`` / ``modify_user`` / ``delete_user``) do NOT write the sensor — they
return their result directly to the caller via ``supports_response``.

:func:`record_result` looks up the live ``result_sensor`` from
``hass.data[DOMAIN][entry_id]`` on every call (so a setup/reload race that leaves
the sensor momentarily unavailable only drops that one write instead of a
stale captured reference silently suppressing every future write).
"""

import logging

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _sys_lang(hass) -> str:
    """Return ``zh`` or ``en`` based on the *system* (backend) language.

    Sensor *state* text is localised by the system language, not the per-user
    profile language. HA only localises entity name/options through
    ``translation_key``; arbitrary strings an integration writes to a sensor
    state are not covered, so we keep our own table keyed off the backend
    language. (The user accepts this follows the system language, exactly like
    the target "（当前登录）" suffix.)
    """
    lang = (hass.config.language or "en").lower()
    return "zh" if lang.startswith("zh") else "en"


# Result-sensor message templates. Keys are stable identifiers; values are
# ``str.format`` templates. ``{ignored}`` / ``{synced}`` / ``{done}`` are
# themselves translated sub-strings produced by :func:`_rt` and concatenated at
# the call sites. Add a language by extending each inner dict.
RESULT_MESSAGES = {
    "fault_no_response": {
        "zh": "无响应（网络/超时）",
        "en": "No response (network/timeout)",
    },
    "fault_http": {
        "zh": "HTTP {status} 设备错误: {fault}",
        "en": "HTTP {status} device error: {fault}",
    },
    "fault_http_raw": {
        "zh": "HTTP {status} 响应: {raw}",
        "en": "HTTP {status} response: {raw}",
    },
    "create_need": {
        "zh": "创建: 需要用户名、密码和级别",
        "en": "Create: username, password and level are required",
    },
    "create_failed": {
        "zh": "创建失败: {detail}",
        "en": "Create failed: {detail}",
    },
    "create_ok": {
        "zh": "创建用户 {name}（{level}）成功",
        "en": "Created user {name} ({level})",
    },
    "modify_need_target": {
        "zh": "修改: 需要选择目标用户{ignored}",
        "en": "Modify: select a target user{ignored}",
    },
    "modify_noop": {
        "zh": "修改: 级别未变且未填写新密码，无操作{ignored}",
        "en": "Modify: no level change and no new password, nothing done{ignored}",
    },
    "modify_need_pw": {
        "zh": "修改级别需同时填写密码（设备要求 SetUser 带密码）{ignored}",
        "en": "Changing the level also requires a password (device needs SetUser with password){ignored}",
    },
    "modify_failed": {
        "zh": "修改失败: {detail}{ignored}",
        "en": "Modify failed: {detail}{ignored}",
    },
    "modify_ok": {
        "zh": "修改 {target} 成功：{done}{synced}{ignored}",
        "en": "Modified {target}: {done}{synced}{ignored}",
    },
    "delete_need_target": {
        "zh": "删除: 需要选择目标用户{ignored}",
        "en": "Delete: select a target user{ignored}",
    },
    "delete_self": {
        "zh": "删除: 不能删除当前集成登录账户（{cfg_user}），否则集成将失效{ignored}",
        "en": "Delete: cannot delete the integration's own login account ({cfg_user}); the integration would break{ignored}",
    },
    "delete_failed": {
        "zh": "删除失败: {detail}{ignored}",
        "en": "Delete failed: {detail}{ignored}",
    },
    "delete_ok": {
        "zh": "删除用户 {target} 成功{ignored}（操作已切回修改模式）",
        "en": "Deleted user {target}{ignored} (switched back to Modify mode)",
    },
    "unknown_action": {
        "zh": "未知操作: {action}",
        "en": "Unknown action: {action}",
    },
    "ignored_both": {
        "zh": "（当前模式用户名、密码不可提交，已忽略）",
        "en": "(current mode: username & password not submitted, ignored)",
    },
    "ignored_name": {
        "zh": "（当前模式用户名不可提交，已忽略）",
        "en": "(current mode: username not submitted, ignored)",
    },
    "synced_pw": {
        "zh": "（已同步更新本集成登录密码）",
        "en": "(synced integration login password updated)",
    },
    "done_level_pw": {
        "zh": "级别→{level}，密码已更新",
        "en": "level→{level}, password updated",
    },
    "done_pw": {
        "zh": "密码已更新",
        "en": "password updated",
    },
    "refresh_action": {
        "zh": "刷新",
        "en": "Refresh",
    },
    "refresh_ok": {
        "zh": "🔄 刷新成功",
        "en": "🔄 Refresh OK",
    },
    "refresh_failed": {
        "zh": "🔄 刷新失败: {detail}",
        "en": "🔄 Refresh failed: {detail}",
    },
}


def _rt(lang: str, key: str, **kwargs) -> str:
    """Pick a result-sensor message template for ``lang`` and fill placeholders.

    Falls back to the English template (then to the raw key) if a language or
    key is missing, so a partial translation table never raises.
    """
    tmpl = RESULT_MESSAGES.get(key, {}).get(lang)
    if tmpl is None:
        tmpl = RESULT_MESSAGES.get(key, {}).get("en", key)
    try:
        return tmpl.format(**kwargs)
    except (KeyError, IndexError):
        return tmpl


def detail_from_response(r, lang=None):
    """Turn a client response dict into a readable fault string.

    ``lang`` is ``zh`` / ``en`` (defaults to ``en`` so log lines stay English).
    The dict comes from ``OnvifUserClient`` methods and carries either a parsed
    SOAP fault (``fault`` + ``status``) or a raw body (``raw`` + ``status``).
    The device-originated ``fault`` / ``raw`` text is inserted verbatim and is
    not translated (it comes straight from the camera firmware).
    """
    lang = lang or "en"
    if not r:
        return _rt(lang, "fault_no_response")
    # Prefer the parsed SOAP fault (1.1 <faultstring> / 1.2 <s:Text>).
    fault = r.get("fault")
    if fault:
        return _rt(lang, "fault_http", status=r.get("status"), fault=fault[:200])
    raw = (r.get("raw") or "").strip()
    if raw:
        return _rt(lang, "fault_http_raw", status=r.get("status"), raw=raw[:200])
    return f"HTTP {r.get('status')} {r.get('error')}"


def record_result(hass, entry_id, action, subject, ok, detail):
    """Write one operation outcome to the ``上次操作结果`` result sensor.

    No-op (logs a debug line) when the sensor has not been set up yet.
    """
    store = hass.data.get(DOMAIN, {}).get(entry_id)
    rs = store.get("result_sensor") if store else None
    if rs is None:
        _LOGGER.debug(
            "result_sensor not ready for entry %s; skipping record (%s)",
            entry_id,
            action,
        )
        return
    rs.set_result(action, subject, ok, detail)
