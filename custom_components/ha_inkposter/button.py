"""Button entities for the Inkposter integration.

BLE command buttons (fetch, reboot, ghosting cleaner) and cloud action buttons.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CLOUD_ACTION_CHECK_FW_UPDATE,
    CLOUD_ACTION_REPORT_STATUS,
    CONF_BLE_ADDRESS,
    CONF_FRAME_MODEL,
    CONF_FRAME_NAME,
    CONF_FRAME_UUID,
    CONF_SERIAL_NUMBER,
    CONF_SHARED_KEY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Inkposter buttons from a config entry."""
    frame_uuid = entry.data[CONF_FRAME_UUID]
    frame_name = entry.data.get(CONF_FRAME_NAME, "Inkposter")
    serial_number = entry.data.get(CONF_SERIAL_NUMBER, "")
    frame_model = entry.data.get(CONF_FRAME_MODEL, "")
    ble_address = entry.data.get(CONF_BLE_ADDRESS)
    shared_key = entry.data.get(CONF_SHARED_KEY)

    device_info = DeviceInfo(
        identifiers={(DOMAIN, frame_uuid)},
        name=frame_name,
        manufacturer="Inkposter",
        model=frame_model,
        serial_number=serial_number or None,
    )

    buttons: list[ButtonEntity] = [
        InkposterRefreshStatusButton(hass, entry, device_info, frame_uuid),
        InkposterCheckFirmwareButton(hass, entry, device_info, frame_uuid),
    ]

    # BLE buttons require a BLE address.
    if ble_address:
        buttons.extend(
            [
                InkposterFetchButton(
                    hass, entry, device_info, frame_uuid, ble_address, shared_key
                ),
                InkposterRebootButton(
                    hass, entry, device_info, frame_uuid, ble_address, shared_key
                ),
                InkposterGhostingCleanerButton(
                    hass, entry, device_info, frame_uuid, ble_address, shared_key
                ),
            ]
        )

    async_add_entities(buttons, update_before_add=True)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class InkposterBaseButton(ButtonEntity):
    """Base class for Inkposter buttons."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        frame_uuid: str,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._device_info = device_info
        self._frame_uuid = frame_uuid

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info


# ---------------------------------------------------------------------------
# Cloud action buttons
# ---------------------------------------------------------------------------


class InkposterRefreshStatusButton(InkposterBaseButton):
    """Button to request the device to report its status to the cloud."""

    _attr_name = "Refresh Status"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass, entry, device_info, frame_uuid) -> None:
        super().__init__(hass, entry, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_refresh_status"

    async def async_press(self) -> None:
        """Send REPORT_FRAME_STATUS action via cloud API."""
        runtime_data = self.hass.data[DOMAIN][self._entry.entry_id]
        api_client = runtime_data.api_client
        await api_client.async_send_action(
            [self._frame_uuid], [CLOUD_ACTION_REPORT_STATUS]
        )
        # Trigger a coordinator refresh to pick up new data.
        coordinator = runtime_data.cloud_coordinator
        await coordinator.async_request_refresh()


class InkposterCheckFirmwareButton(InkposterBaseButton):
    """Button to check for firmware updates via the cloud."""

    _attr_name = "Check Firmware Update"
    _attr_icon = "mdi:update"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass, entry, device_info, frame_uuid) -> None:
        super().__init__(hass, entry, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_check_firmware"

    async def async_press(self) -> None:
        """Send CHECK_FW_UPDATE action via cloud API."""
        runtime_data = self.hass.data[DOMAIN][self._entry.entry_id]
        api_client = runtime_data.api_client
        await api_client.async_send_action(
            [self._frame_uuid], [CLOUD_ACTION_CHECK_FW_UPDATE]
        )
        coordinator = runtime_data.cloud_coordinator
        await coordinator.async_request_refresh()


# ---------------------------------------------------------------------------
# BLE command buttons
# ---------------------------------------------------------------------------


class InkposterBleButton(InkposterBaseButton):
    """Base class for buttons that send BLE commands."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        frame_uuid: str,
        ble_address: str,
        shared_key: str | None,
    ) -> None:
        super().__init__(hass, entry, device_info, frame_uuid)
        self._ble_address = ble_address
        self._shared_key = shared_key

    async def _async_send_ble_action(self, action: int, extra: dict | None = None) -> None:
        """Send a BLE command by action ID (reads msg_seq from device first)."""
        from homeassistant.components import bluetooth

        from .ble import async_send_command

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._ble_address, connectable=True
        )

        if ble_device is None:
            _LOGGER.warning(
                "BLE device %s not reachable for command", self._ble_address
            )
            return

        await async_send_command(
            ble_device,
            action=action,
            extra=extra,
            skey_hex=self._shared_key,
        )


class InkposterFetchButton(InkposterBleButton):
    """Button to trigger the device to fetch new images from the cloud."""

    _attr_name = "Fetch Image"
    _attr_icon = "mdi:cloud-download"

    def __init__(self, hass, entry, device_info, frame_uuid, ble_address, shared_key) -> None:
        super().__init__(hass, entry, device_info, frame_uuid, ble_address, shared_key)
        self._attr_unique_id = f"{frame_uuid}_ble_fetch"

    async def async_press(self) -> None:
        from .const import BLE_ACTION_FETCH

        await self._async_send_ble_action(BLE_ACTION_FETCH)


class InkposterRebootButton(InkposterBleButton):
    """Button to reboot the device via BLE."""

    _attr_name = "Reboot"
    _attr_icon = "mdi:restart"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass, entry, device_info, frame_uuid, ble_address, shared_key) -> None:
        super().__init__(hass, entry, device_info, frame_uuid, ble_address, shared_key)
        self._attr_unique_id = f"{frame_uuid}_ble_reboot"

    async def async_press(self) -> None:
        from .const import BLE_ACTION_REBOOT

        await self._async_send_ble_action(BLE_ACTION_REBOOT)


class InkposterGhostingCleanerButton(InkposterBleButton):
    """Button to run the ghosting cleaner via BLE."""

    _attr_name = "Ghosting Cleaner"
    _attr_icon = "mdi:monitor-clean"

    def __init__(self, hass, entry, device_info, frame_uuid, ble_address, shared_key) -> None:
        super().__init__(hass, entry, device_info, frame_uuid, ble_address, shared_key)
        self._attr_unique_id = f"{frame_uuid}_ble_ghosting_cleaner"

    async def async_press(self) -> None:
        from .const import BLE_ACTION_GHOSTING_CLEANER

        await self._async_send_ble_action(BLE_ACTION_GHOSTING_CLEANER)
