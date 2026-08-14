# ONVIF User Manager

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Manage ONVIF users (list / add / set password / set level / delete) on any
standard ONVIF camera — including models whose web UI has **stripped the ONVIF
user page** (common on custom Dahua / Hikvision / Axis firmware). The integration
talks to the camera's `:80` SOAP API with HTTP Digest auth as a **native process**,
so no browser, no CORS, and no extra relay is involved.

> 适用于任何标准 ONVIF 摄像头，尤其是那些 Web 管理界面被裁剪掉 ONVIF 用户页的定制固件（常见于大华、海康、Axis 等）。集成以原生进程直连摄像头的 `:80` SOAP 接口、使用 HTTP Digest 认证，不经过浏览器、无 CORS、无中转。

---

## Features

- **Unified management form** in the entity card: pick an action (create / modify /
  delete), pick the target user, set level + password, hit **Submit**.
- **Standalone services** for automations / Developer Tools — no config entry needed.
- **Live user list** (`select.onvif_user_target`) refreshed on demand (refresh button / service / after a write).
- **Last operation result** sensor with bilingual, system-language-aware messages.
- **Config flow** (UI setup) + options flow (edit connection without re-entering password).
- **Bilingual**: Simplified Chinese (`zh-Hans`) and English (`en`).

---

## Installation

### Via HACS (recommended)

1. Add this repo as a **Custom repository** in HACS (category: *Integration*).
2. Install **ONVIF User Manager**.
3. Restart Home Assistant.

### Manual

Copy `custom_components/onvif_user/` into your `config/custom_components/` directory
and restart Home Assistant.

> 安装后需**完整重启** Home Assistant（重载不足以让 services 与翻译生效）。
> A full restart (not just reload) is required after install.

---

## Configuration

**Settings → Devices & Services → Add Integration → ONVIF User Manager**

Provide the camera's ONVIF credentials — the **management account** (an
`Administrator`-level user) that is allowed to call `GetUsers` / `SetUser`. This is
the password you set via the `:80` SOAP `SetUser` call or ONVIF Device Manager,
**not** the camera's web-admin password if those two are separate.

The connection can later be edited from the integration's **Options** (the password
field may be left blank to keep the current one).

---

## Entities

Once configured, the following entities are created (object IDs are fixed):

| Entity | Type | Description (EN / 中文) |
|---|---|---|
| `sensor.onvif_user_user_count` | sensor | Number of ONVIF users on the camera / 摄像头上的用户数量 |
| `sensor.onvif_user_last_result` | sensor | Last operation result message / 上次操作结果 |
| `select.onvif_user_action` | select | Action: create / modify / delete / 操作 |
| `select.onvif_user_level` | select | User level: Administrator / Operator / User / 级别 |
| `select.onvif_user_target` | select | Target user to modify / delete (auto-filled) / 目标用户 |
| `text.onvif_user_username` | text | New username (create) / 用户名 |
| `text.onvif_user_password` | text | Password for the operation / 密码 |
| `button.onvif_user_submit` | button | Commit the form / 提交 |
| `button.onvif_user_refresh` | button | Immediate refresh of the user list / 刷新 |

The **Submit** button reads the other form entities and performs the chosen action.
The **`（当前登录）` / ` (current login)`** suffix marks the account you used to set
up the integration in the target dropdown.

---

## Services

All four services work **standalone** (no config entry required) and return their
result directly via the Actions response. Common fields (the camera connection +
management account) are required by every service:

| Field | Required | Notes |
|---|---|---|
| `host` | yes | Camera IP / hostname |
| `port` | yes | ONVIF device port (usually `80`) |
| `username` | yes | ONVIF management account |
| `password` | yes | Management account password |

### `onvif_user.list_users`
List all ONVIF users on the camera.

### `onvif_user.add_user`
Create a new user.

| Field | Required | Notes |
|---|---|---|
| `target_username` | yes | New username |
| `target_password` | yes | New password |
| `level` | no | `Administrator` / `Operator` / `User` (default `User`) |

### `onvif_user.modify_user`
Change a user's level and/or password.

| Field | Required | Notes |
|---|---|---|
| `target_username` | yes | Existing username |
| `target_password` | no | New password |
| `level` | no | `Administrator` / `Operator` / `User` |

> Provide **at least one** of `target_password` / `level`. Some cameras require both
> when changing either, so it's safest to always provide both.
> 部分摄像头在更改密码或级别时要求同时提交二者，最稳妥的做法是两者都填。

### `onvif_user.delete_user`
Delete a user — **irreversible**.

| Field | Required | Notes |
|---|---|---|
| `target_username` | yes | Existing username |

---

## Notes / Limitations

- The integration fetches from the device on demand (refresh button / service / after a
  write); it does **not** run a continuous interval. `iot_class` is `local_polling`.
- The last-operation-result sensor text follows the **system** language
  (`Settings → General → Language`), not the per-user profile language.
- Entity friendly names are frozen at creation time using the system language; to
  rename, delete and re-add the integration.
- Credentials are stored in the HA config entry (encrypted at rest) and used only to
  talk to the camera. No data leaves your network.

---

## Changelog

### v1.1.0

- **Service-only setup mode**: the config flow now offers a *Register services only*
  option that creates an empty entry purely to load the integration, so the four
  standalone services (`list_users` / `add_user` / `modify_user` / `delete_user`) are
  registered before any camera is configured. The redundant service-only entry is
  removed automatically once a real camera entry exists, and its *Configure* button is
  hidden (HA 2024.11+).
- **Bilingual config flow & options flow** (Simplified Chinese `zh-Hans` + English `en`),
  plus a localised *Setup mode* selector.
- **Entry title follows the system language** (e.g. `ONVIF 用户管理（仅服务）` on a
  Chinese system, `ONVIF User Manager (services only)` otherwise).

### v1.0.0

- Initial release: unified management form, standalone services, live user list,
  last-operation result sensor, on-demand refresh.

---

## License

MIT
