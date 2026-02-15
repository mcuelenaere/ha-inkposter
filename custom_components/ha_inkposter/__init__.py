"""The Inkposter integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
]

InkposterConfigEntry: TypeAlias = ConfigEntry


@dataclass
class RuntimeData:
    """Runtime data for the Inkposter integration."""

    api_client: Any = None
    cloud_coordinator: Any = None
    ble_coordinator: Any = None
    device_info: dict[str, Any] | None = None


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Inkposter component."""
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: InkposterConfigEntry
) -> bool:
    """Set up Inkposter from a config entry."""
    from .api_client import InkposterApiClient
    from .const import (
        CONF_BLE_ADDRESS,
        CONF_EMAIL,
        CONF_FRAME_UUID,
        CONF_PASSWORD,
        CONF_SHARED_KEY,
    )
    from .coordinator import InkposterCloudCoordinator

    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]
    frame_uuid = entry.data[CONF_FRAME_UUID]
    shared_key = entry.data.get(CONF_SHARED_KEY)
    ble_address = entry.data.get(CONF_BLE_ADDRESS)

    api_client = InkposterApiClient(
        hass=hass,
        email=email,
        password=password,
        entry_id=entry.entry_id,
    )

    await api_client.async_ensure_token()

    cloud_coordinator = InkposterCloudCoordinator(
        hass=hass,
        api_client=api_client,
        frame_uuid=frame_uuid,
        entry_id=entry.entry_id,
    )

    runtime_data = RuntimeData(
        api_client=api_client,
        cloud_coordinator=cloud_coordinator,
    )
    entry.runtime_data = runtime_data  # type: ignore[attr-defined]

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = runtime_data

    # Load cached data from disk first.
    await cloud_coordinator.async_load_cached_data()

    # Fetch initial data (non-fatal if device is offline).
    await cloud_coordinator.async_config_entry_first_refresh()

    # Set up optional BLE coordinator.
    if ble_address:
        try:
            from .coordinator import InkposterBleCoordinator

            ble_coordinator = InkposterBleCoordinator(
                hass=hass,
                address=ble_address,
                shared_key=shared_key,
            )
            runtime_data.ble_coordinator = ble_coordinator
        except Exception:
            _LOGGER.debug(
                "Could not set up BLE coordinator for %s", ble_address
            )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start the BLE coordinator AFTER platforms have subscribed.
    if runtime_data.ble_coordinator is not None:
        entry.async_on_unload(runtime_data.ble_coordinator.async_start())

    # Register services (idempotent).
    _async_register_services(hass)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: InkposterConfigEntry
) -> bool:
    """Unload an Inkposter config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services (idempotent -- safe to call multiple times)."""
    import base64

    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    import voluptuous as vol

    from .const import (
        CLOUD_ACTION_REPORT_STATUS,
        CONF_BLE_ADDRESS,
        CONF_FRAME_UUID,
        CONF_SHARED_KEY,
    )

    if hass.services.has_service(DOMAIN, "upload_image_url"):
        return  # Already registered.

    async def _resolve_runtime(call) -> tuple:
        """Find the runtime_data for the targeted entry."""
        # Services may target a specific entity or all entries.
        for entry_id, rd in hass.data.get(DOMAIN, {}).items():
            if hasattr(rd, "api_client") and rd.api_client is not None:
                return entry_id, rd
        raise ValueError("No configured Inkposter integration found")

    async def _trigger_ble_fetch(entry) -> None:
        """Send BLE FETCH to the device to trigger immediate image pull."""
        from homeassistant.components import bluetooth
        from .ble import async_send_command
        from .const import BLE_ACTION_FETCH

        ble_address = entry.data.get(CONF_BLE_ADDRESS)
        shared_key = entry.data.get(CONF_SHARED_KEY)
        if not ble_address:
            return
        ble_device = bluetooth.async_ble_device_from_address(
            hass, ble_address, connectable=True
        )
        if ble_device is None:
            _LOGGER.debug("BLE device %s not reachable for post-upload fetch", ble_address)
            return
        try:
            await async_send_command(
                ble_device, action=BLE_ACTION_FETCH, skey_hex=shared_key
            )
        except Exception:
            _LOGGER.debug("BLE FETCH after upload failed (non-fatal)", exc_info=True)

    async def _handle_upload_image_url(call) -> None:
        url = call.data["url"]
        entry_id, rd = await _resolve_runtime(call)
        entry = hass.config_entries.async_get_entry(entry_id)
        frame_uuid = entry.data.get(CONF_FRAME_UUID) if entry else None
        if not frame_uuid:
            return

        session = async_get_clientsession(hass)
        async with session.get(url) as resp:
            resp.raise_for_status()
            image_bytes = await resp.read()

        await rd.api_client.async_upload_and_poll(
            frame_uuid, image_bytes, "image/jpeg"
        )
        await _trigger_ble_fetch(entry)
        if rd.cloud_coordinator:
            await rd.cloud_coordinator.async_request_refresh()

    async def _handle_upload_image_data(call) -> None:
        b64_data = call.data["image_data"]
        entry_id, rd = await _resolve_runtime(call)
        image_bytes = base64.b64decode(b64_data)

        entry = hass.config_entries.async_get_entry(entry_id)
        frame_uuid = entry.data.get(CONF_FRAME_UUID) if entry else None
        if not frame_uuid:
            return

        await rd.api_client.async_upload_and_poll(
            frame_uuid, image_bytes, "image/jpeg"
        )
        await _trigger_ble_fetch(entry)
        if rd.cloud_coordinator:
            await rd.cloud_coordinator.async_request_refresh()

    async def _handle_ble_action(call, action: int) -> None:
        from homeassistant.components import bluetooth
        from .ble import async_send_command

        for eid in hass.data.get(DOMAIN, {}):
            entry = hass.config_entries.async_get_entry(eid)
            if not entry:
                continue
            ble_address = entry.data.get(CONF_BLE_ADDRESS)
            shared_key = entry.data.get(CONF_SHARED_KEY)
            if not ble_address:
                continue
            ble_device = bluetooth.async_ble_device_from_address(
                hass, ble_address, connectable=True
            )
            if ble_device is None:
                _LOGGER.warning("BLE device %s not reachable", ble_address)
                continue
            await async_send_command(
                ble_device, action=action, skey_hex=shared_key
            )
            break

    async def _handle_fetch(call) -> None:
        from .const import BLE_ACTION_FETCH
        await _handle_ble_action(call, BLE_ACTION_FETCH)

    async def _handle_reboot(call) -> None:
        from .const import BLE_ACTION_REBOOT
        await _handle_ble_action(call, BLE_ACTION_REBOOT)

    async def _handle_ghosting_cleaner(call) -> None:
        from .const import BLE_ACTION_GHOSTING_CLEANER
        await _handle_ble_action(call, BLE_ACTION_GHOSTING_CLEANER)

    async def _handle_refresh_status(call) -> None:
        for eid in hass.data.get(DOMAIN, {}):
            entry = hass.config_entries.async_get_entry(eid)
            if not entry:
                continue
            rd = hass.data[DOMAIN][eid]
            frame_uuid = entry.data.get(CONF_FRAME_UUID)
            if frame_uuid and rd.api_client:
                await rd.api_client.async_send_action(
                    [frame_uuid], [CLOUD_ACTION_REPORT_STATUS]
                )
                if rd.cloud_coordinator:
                    await rd.cloud_coordinator.async_request_refresh()
            break

    hass.services.async_register(
        DOMAIN, "upload_image_url", _handle_upload_image_url,
        schema=vol.Schema(
            {vol.Required("url"): str},
            extra=vol.ALLOW_EXTRA,
        ),
    )
    hass.services.async_register(
        DOMAIN, "upload_image_data", _handle_upload_image_data,
        schema=vol.Schema(
            {vol.Required("image_data"): str},
            extra=vol.ALLOW_EXTRA,
        ),
    )
    hass.services.async_register(DOMAIN, "fetch", _handle_fetch)
    hass.services.async_register(DOMAIN, "reboot", _handle_reboot)
    hass.services.async_register(DOMAIN, "ghosting_cleaner", _handle_ghosting_cleaner)
    hass.services.async_register(DOMAIN, "refresh_status", _handle_refresh_status)
