"""Cloud API client for the Inkposter integration.

Handles authentication (login + token refresh with HMAC-SHA256 signing),
frame listing, status polling, image upload/conversion, and frame actions.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
import uuid
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import (
    API_BASE,
    CLIENT_ID,
    CLIENT_SECRET,
    DEFAULT_HEADERS,
    DOMAIN,
    POLL_INTERVAL_SECS,
    POLL_TIMEOUT_SECS,
    REFRESH_BUFFER_SECS,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.api_tokens"

CONVERT_EXTRA_HEADERS = {
    "Upload-Draft-Interop-Version": "6",
    "Upload-Complete": "?1",
}


def _compute_signature(timestamp: int) -> str:
    """Compute HMAC-SHA256 signature for auth endpoints."""
    message = f"{CLIENT_ID}{timestamp}"
    return hmac.new(
        CLIENT_SECRET.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def _signed_params() -> dict[str, str]:
    """Return timestamp + signature query params for auth endpoints."""
    ts = int(time.time() * 1000)
    sig = _compute_signature(ts)
    return {"timestamp": str(ts), "signature": sig}


class InkposterApiClient:
    """Async client for the Inkposter cloud API."""

    def __init__(
        self,
        hass: HomeAssistant,
        email: str,
        password: str,
        entry_id: str,
    ) -> None:
        self._hass = hass
        self._email = email
        self._password = password
        self._entry_id = entry_id

        self._device_id: str = ""
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._expires_at: float = 0.0

        self._refresh_lock = asyncio.Lock()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry_id}"
        )

    # ------------------------------------------------------------------
    # Token persistence
    # ------------------------------------------------------------------

    async def _load_persisted_tokens(self) -> bool:
        """Load tokens from disk. Returns True if valid tokens were loaded."""
        data = await self._store.async_load()
        if not data:
            return False
        self._device_id = data.get("device_id", "")
        self._access_token = data.get("access_token", "")
        self._refresh_token = data.get("refresh_token", "")
        self._expires_at = data.get("expires_at", 0.0)
        return bool(self._access_token and self._expires_at > time.time())

    async def _save_tokens(self) -> None:
        """Persist current tokens to disk."""
        await self._store.async_save(
            {
                "device_id": self._device_id,
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "expires_at": self._expires_at,
            }
        )

    # ------------------------------------------------------------------
    # Auth: login + refresh
    # ------------------------------------------------------------------

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Build headers with current Bearer token."""
        headers = {
            **DEFAULT_HEADERS,
            "Authorization": f"Bearer {self._access_token}",
            "x-header-deviceid": self._device_id,
        }
        if extra:
            headers.update(extra)
        return headers

    async def async_login(self) -> dict[str, Any]:
        """Perform a fresh login. Returns the full response JSON."""
        if not self._device_id:
            self._device_id = str(uuid.uuid4())

        session = async_get_clientsession(self._hass)
        url = f"{API_BASE}/auth/login"
        params = _signed_params()
        body = {
            "email": self._email,
            "password": self._password,
            "deviceId": self._device_id,
        }
        headers = {
            **DEFAULT_HEADERS,
            "Content-Type": "application/json",
            "x-header-deviceid": self._device_id,
        }

        _LOGGER.debug("Inkposter: logging in as %s", self._email)
        resp = await session.post(url, params=params, json=body, headers=headers)
        resp.raise_for_status()
        data = await resp.json()
        self._apply_auth_response(data)
        _LOGGER.debug("Inkposter: login successful (expires_at=%s)", self._expires_at)
        return data

    async def _async_refresh(self) -> None:
        """Refresh the access token using the refresh token."""
        session = async_get_clientsession(self._hass)
        url = f"{API_BASE}/auth/refresh-token"
        headers = {
            **DEFAULT_HEADERS,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._access_token}",
            "x-header-deviceid": self._device_id,
        }
        body = {"deviceId": self._device_id}

        _LOGGER.debug("Inkposter: refreshing access token")
        resp = await session.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = await resp.json()
        self._apply_auth_response(data)
        _LOGGER.debug("Inkposter: token refreshed (expires_at=%s)", self._expires_at)

    def _apply_auth_response(self, data: dict[str, Any]) -> None:
        """Apply tokens from a login/refresh response."""
        self._access_token = data["accessToken"]
        self._refresh_token = data["refreshToken"]
        # expiresIn is a Unix timestamp (seconds), not a duration
        self._expires_at = float(data["expiresIn"])
        self._hass.async_create_task(self._save_tokens())

    def _is_token_expiring_soon(self) -> bool:
        return time.time() >= self._expires_at - REFRESH_BUFFER_SECS

    async def async_ensure_token(self) -> None:
        """Ensure we have a valid access token (load, refresh, or login)."""
        async with self._refresh_lock:
            # Try loading persisted tokens first.
            if not self._access_token:
                if await self._load_persisted_tokens():
                    if not self._is_token_expiring_soon():
                        return
                    # Token loaded but expiring soon -- try refresh.
                    try:
                        await self._async_refresh()
                        return
                    except Exception:
                        _LOGGER.debug("Refresh of persisted token failed, logging in")

            # If we have a token that's still valid, we're good.
            if self._access_token and not self._is_token_expiring_soon():
                return

            # Try refresh if we have a token at all.
            if self._access_token:
                try:
                    await self._async_refresh()
                    return
                except Exception:
                    _LOGGER.debug("Token refresh failed, falling back to login")

            # Last resort: fresh login.
            await self.async_login()

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        """Make an authenticated API request with auto-refresh on 401."""
        await self.async_ensure_token()

        session = async_get_clientsession(self._hass)
        url = f"{API_BASE}/{path.lstrip('/')}"
        req_headers = self._auth_headers(headers)

        resp = await session.request(
            method, url, json=json, data=data, headers=req_headers, params=params
        )

        if resp.status == 401:
            _LOGGER.debug("Got 401 from %s, refreshing token", path)
            async with self._refresh_lock:
                try:
                    await self._async_refresh()
                except Exception:
                    await self.async_login()
            req_headers = self._auth_headers(headers)
            resp = await session.request(
                method, url, json=json, data=data, headers=req_headers, params=params
            )

        resp.raise_for_status()

        if resp.content_type and "json" in resp.content_type:
            return await resp.json()
        return await resp.read()

    # ------------------------------------------------------------------
    # Frames
    # ------------------------------------------------------------------

    async def async_get_frames(self) -> list[dict[str, Any]]:
        """Get the list of user frames from the cloud API."""
        data = await self._async_request("GET", "/user/frames", params={"limit": "100"})
        return data.get("frames", [])

    # ------------------------------------------------------------------
    # Status endpoints
    # ------------------------------------------------------------------

    async def async_get_frame_status(self) -> list[dict[str, Any]]:
        """GET /frame/status -- returns list of {frame_uuid: status_dict}."""
        return await self._async_request("GET", "/frame/status")

    async def async_get_image_status(self) -> list[dict[str, Any]]:
        """GET /frame/image-status -- image transfer progress."""
        return await self._async_request("GET", "/frame/image-status")

    async def async_get_version_check(self) -> list[dict[str, Any]]:
        """GET /frame/version-check -- firmware update availability."""
        return await self._async_request("GET", "/frame/version-check")

    async def async_get_user_profile(self) -> dict[str, Any]:
        """GET /user/profile."""
        return await self._async_request("GET", "/user/profile")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def async_send_action(
        self, frame_uuids: list[str], actions: list[str]
    ) -> dict[str, Any]:
        """POST /frame/actions -- send cloud-side commands to frames."""
        return await self._async_request(
            "POST",
            "/frame/actions",
            json={"frames": frame_uuids, "actions": actions},
            headers={"Content-Type": "application/json"},
        )

    # ------------------------------------------------------------------
    # Image upload + conversion
    # ------------------------------------------------------------------

    async def async_upload_convert(
        self,
        frame_uuid: str,
        image_bytes: bytes,
        media_type: str = "image/jpeg",
        filename: str = "userimage.jpg",
    ) -> dict[str, Any]:
        """Upload an image for conversion (POST /item/convert).

        Returns {"queueId": "..."}.
        """
        import aiohttp

        form = aiohttp.FormData()
        form.add_field("frames[]", frame_uuid)
        form.add_field(
            "file",
            image_bytes,
            filename=filename,
            content_type=media_type,
        )

        await self.async_ensure_token()
        session = async_get_clientsession(self._hass)
        url = f"{API_BASE}/item/convert"
        headers = self._auth_headers(CONVERT_EXTRA_HEADERS)

        _LOGGER.debug(
            "Inkposter: uploading image (%d bytes, %s) to /item/convert",
            len(image_bytes),
            media_type,
        )

        resp = await session.post(url, data=form, headers=headers)

        if resp.status == 401:
            async with self._refresh_lock:
                try:
                    await self._async_refresh()
                except Exception:
                    await self.async_login()
            headers = self._auth_headers(CONVERT_EXTRA_HEADERS)
            resp = await session.post(url, data=form, headers=headers)

        resp.raise_for_status()
        return await resp.json()

    async def async_poll_is_converted(
        self, queue_id: str
    ) -> dict[str, Any]:
        """Poll /item/is-converted until done or timeout.

        Returns the final response: {"status": "...", "message": "...", "item": "..."}.
        """
        url_path = "/item/is-converted"
        start = time.monotonic()
        attempts = 0
        last_response: dict[str, Any] = {}

        while time.monotonic() - start < POLL_TIMEOUT_SECS:
            attempts += 1
            last_response = await self._async_request(
                "POST",
                url_path,
                json={"queueId": queue_id},
                headers={"Content-Type": "application/json"},
            )
            status = last_response.get("status", "")
            _LOGGER.debug(
                "Inkposter: poll attempt %d -- status=%s", attempts, status
            )
            if status != "pending":
                break
            await asyncio.sleep(POLL_INTERVAL_SECS)

        return last_response

    async def async_upload_and_poll(
        self,
        frame_uuid: str,
        image_bytes: bytes,
        media_type: str = "image/jpeg",
    ) -> dict[str, Any]:
        """Upload image, then poll until conversion completes."""
        convert_resp = await self.async_upload_convert(
            frame_uuid, image_bytes, media_type
        )
        queue_id = convert_resp.get("queueId", "")
        if not queue_id:
            return convert_resp
        return await self.async_poll_is_converted(queue_id)
