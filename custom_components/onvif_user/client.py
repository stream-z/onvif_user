"""Async ONVIF SOAP client for ONVIF cameras with non-conformant user management.

Reuses the exact endpoint, namespaces and Digest flow that were verified
against the E4702 (CMCC / LeChange firmware):

  * endpoint : /onvif/device_service
  * device ns: http://www.onvif.org/ver10/device/wsdl
  * schema ns: http://www.onvif.org/ver10/schema   (tt:)
  * actions  : GetUsers / CreateUsers / SetUser / DeleteUsers
  * auth     : HTTP Digest (handled by aiohttp.DigestAuth, no manual MD5)

The camera's web UI removed the ONVIF user-management page and all
userManager.onvif* JSON-RPC entries, so this SOAP path is the only way.
"""

import hashlib
import logging
import re
import uuid

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEVICE_NS = "http://www.onvif.org/ver10/device/wsdl"
PATH = "/onvif/device_service"

_LEVELS = ("Administrator", "Operator", "User")


def _escape(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _envelope(inner):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
        'xmlns:tt="http://www.onvif.org/ver10/schema">'
        "<s:Body>" + inner + "</s:Body></s:Envelope>"
    )


def _extract_fault(text):
    """Pull a human-readable reason out of a SOAP fault (1.1 or 1.2).

    many ONVIF cameras (non-conformant to the WS-I BP 2.0 fault recommendation) return
    faults as HTTP 200 with a SOAP 1.2 envelope
    (``<s:Fault><s:Reason><s:Text>``), not the HTTP 500 + ``<faultstring>``
    (SOAP 1.1) shape. We capture both, plus the subcode (e.g. ``ter:NotAuthorized``)
    for context.
    """
    if not text:
        return None
    sub = re.search(
        r"<(?:[\w]+:)?Subcode>\s*<(?:[\w]+:)?Value>([^<]+)</(?:[\w]+:)?Value>", text
    )
    reason = re.search(
        r"<(?:[\w]+:)?Text[^>]*>(.*?)</(?:[\w]+:)?Text>", text, re.S
    )
    fs = re.search(r"<faultstring>(.*?)</faultstring>", text, re.S)
    parts = []
    if sub:
        parts.append(sub.group(1).strip())
    if reason:
        parts.append(re.sub(r"\s+", " ", reason.group(1)).strip())
    elif fs:
        parts.append(re.sub(r"\s+", " ", fs.group(1)).strip())
    return " | ".join(p for p in parts if p) or None


def _parse_www_authenticate(header):
    """Parse a ``WWW-Authenticate: Digest ...`` header into a dict.

    aiohttp has no DigestAuth, so we do the RFC 2617 / 7616 handshake by hand.
    """
    if not header or not header.lower().startswith("digest"):
        return None
    parts = {}
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', header):
        parts[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return parts


def _digest_authorization(user, password, method, uri, chal):
    """Build the ``Authorization: Digest ...`` header value from a challenge."""
    realm = chal.get("realm", "")
    nonce = chal.get("nonce", "")
    qop = chal.get("qop", "")
    opaque = chal.get("opaque")
    algorithm = (chal.get("algorithm") or "MD5").upper()

    if algorithm == "MD5-SESS":
        cnonce = uuid.uuid4().hex[:16]
        ha1 = hashlib.md5(
            hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()
            + f":{nonce}:{cnonce}"
        ).hexdigest()
    else:  # plain MD5 (most cameras)
        ha1 = hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()
        cnonce = uuid.uuid4().hex[:16]

    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    nc = "00000001"

    if qop and "auth" in qop:
        resp = hashlib.md5(
            f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode()
        ).hexdigest()
        auth = (
            f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", qop=auth, nc={nc}, cnonce="{cnonce}", response="{resp}"'
        )
    else:
        resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        auth = (
            f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", response="{resp}"'
        )
    if opaque:
        auth += f', opaque="{opaque}"'
    return auth


class OnvifUserClient:
    """Thin async wrapper around the ONVIF DeviceMgmt user operations."""

    def __init__(self, host, port, user, password, session=None, timeout=10):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.timeout = timeout
        self._session = session  # shared session if provided (else own per call)

    @property
    def url(self):
        return f"http://{self.host}:{self.port}{PATH}"

    def _headers(self, action):
        return {
            "Content-Type": "application/soap+xml; charset=utf-8",
            "SOAPAction": f'"{DEVICE_NS}/{action}"',
        }

    async def _post(self, action, inner):
        """POST a SOAP action, performing HTTP Digest auth by hand.

        aiohttp has no DigestAuth helper, so: send once unauthenticated to get
        the 401 challenge, then compute the response and resend.
        """
        body = _envelope(inner).encode("utf-8")
        headers = self._headers(action)
        own = self._session is None
        session = self._session or aiohttp.ClientSession()
        try:
            async with session.post(
                self.url,
                data=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                status = resp.status
                chal_header = resp.headers.get("WWW-Authenticate")
                if status != 401:
                    text = await resp.text()
                    return text, status

            # 401 -> parse the Digest challenge and answer it.
            chal = _parse_www_authenticate(chal_header)
            if not chal:
                _LOGGER.warning(
                    "ONVIF probe got %s without a Digest challenge", status
                )
                return None, f"unexpected auth challenge (status {status})"

            auth = _digest_authorization(
                self.user, self.password, "POST", PATH, chal
            )
            headers2 = dict(headers)
            headers2["Authorization"] = auth

            async with session.post(
                self.url,
                data=body,
                headers=headers2,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp2:
                text = await resp2.text()
                return text, resp2.status
        except aiohttp.ClientError as e:
            return None, f"client error: {e}"
        finally:
            if own:
                await session.close()

    async def list_users(self):
        text, status = await self._post("GetUsers", "<tds:GetUsers/>")
        if status != 200 or text is None:
            return {"ok": False, "status": status, "raw": (text or "")[:2000]}
        users = re.findall(r"<tt:Username>([^<]+)</tt:Username>", text)
        levels = re.findall(r"<tt:UserLevel>([^<]+)</tt:UserLevel>", text)
        out = [
            {"name": u, "level": levels[i] if i < len(levels) else ""}
            for i, u in enumerate(users)
        ]
        order = {"Administrator": 0, "Operator": 1, "User": 2}
        out.sort(key=lambda x: (order.get(x["level"], 9), x["name"].lower()))
        return {"ok": True, "status": 200, "users": out}

    async def add_user(self, username, password, level="User"):
        if level not in _LEVELS:
            return {"ok": False, "error": "level must be Administrator/Operator/User"}
        inner = (
            "<tds:CreateUsers><tt:User>"
            f"<tt:Username>{_escape(username)}</tt:Username>"
            f"<tt:Password>{_escape(password)}</tt:Password>"
            f"<tt:UserLevel>{_escape(level)}</tt:UserLevel>"
            "</tt:User></tds:CreateUsers>"
        )
        text, status = await self._post("CreateUsers", inner)
        fault = _extract_fault(text)
        if text is None:
            return {"ok": False, "status": status, "error": str(status)}
        return {
            "ok": "CreateUsersResponse" in text and fault is None,
            "status": status,
            "raw": text[:2000],
            "fault": fault,
        }

    async def modify_user(self, username, level=None, password=None):
        """Change a user's level and/or password in one SetUser call.

        ONVIF schema: in SetUser the tt:User struct REQUIRES Username + UserLevel;
        Password is OPTIONAL (minOccurs=0). We emit ``<tt:UserLevel>`` whenever
        ``level`` is supplied and ``<tt:Password>`` whenever ``password`` is supplied,
        so the caller may change the level only, the password only, or both at once.
        Vendor behavior diverges:
          - A spec-compliant camera (VERIFIED 2026-08-13): a Password-only SetUser
            (UserLevel omitted) is REJECTED — HTTP 500 InvalidArgVal: missing mandatory
            UserLevel. Pass ``level`` whenever changing the password on such cameras.
          - Some cameras (and some firmwares): deviate by ALSO requiring Password present
            (Password is optional per spec), so a level-only SetUser is REJECTED. Pass
            ``password`` whenever the level is touched on those cameras.
        Re-stating the same level/password is a harmless no-op, and including the optional
        field a stricter camera demands keeps us compatible across both kinds.
        """
        parts = [f"<tt:Username>{_escape(username)}</tt:Username>"]
        if level and level in _LEVELS:
            parts.append(f"<tt:UserLevel>{_escape(level)}</tt:UserLevel>")
        if password:
            parts.append(f"<tt:Password>{_escape(password)}</tt:Password>")
        inner = "<tds:SetUser><tt:User>" + "".join(parts) + "</tt:User></tds:SetUser>"
        text, status = await self._post("SetUser", inner)
        fault = _extract_fault(text)
        if text is None:
            return {"ok": False, "status": status, "error": str(status)}
        return {
            "ok": "SetUserResponse" in text and fault is None,
            "status": status,
            "raw": text[:2000],
            "fault": fault,
        }

    async def delete_user(self, username):
        inner = (
            "<tds:DeleteUsers>"
            f"<tt:Username>{_escape(username)}</tt:Username>"
            "</tds:DeleteUsers>"
        )
        text, status = await self._post("DeleteUsers", inner)
        fault = _extract_fault(text)
        if text is None:
            return {"ok": False, "status": status, "error": str(status)}
        return {
            "ok": "DeleteUsersResponse" in text and fault is None,
            "status": status,
            "raw": text[:2000],
            "fault": fault,
        }

    async def get_device_info(self):
        """Fetch ONVIF GetDeviceInformation (plus vendor extensions if present).

        The standard returns Manufacturer, Model, FirmwareVersion, SerialNumber,
        HardwareId. ONVIF firmware also commonly adds Name, Location
        and DeviceID in the same response. We extract whatever is present and
        let the caller decide which fields to surface.
        """
        text, status = await self._post(
            "GetDeviceInformation", "<tds:GetDeviceInformation/>"
        )
        fault = _extract_fault(text)
        if status != 200 or text is None or fault:
            return {
                "ok": False,
                "status": status,
                "fault": fault,
                "raw": (text or "")[:2000],
            }

        def _get(tag):
            m = re.search(
                rf"<(?:[\w]+:)?{tag}[^>]*>([^<]+)</(?:[\w]+:)?{tag}>", text
            )
            return m.group(1).strip() if m else None

        return {
            "ok": True,
            "status": status,
            "Manufacturer": _get("Manufacturer"),
            "Model": _get("Model"),
            "FirmwareVersion": _get("FirmwareVersion"),
            "SerialNumber": _get("SerialNumber"),
            "HardwareId": _get("HardwareId"),
            "Name": _get("Name"),
            "Location": _get("Location"),
            "DeviceID": _get("DeviceID"),
        }
