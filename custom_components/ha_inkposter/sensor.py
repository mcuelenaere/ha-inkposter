"""Sensor entities for the Inkposter integration.

Data is sourced primarily from the cloud API (InkposterCloudCoordinator).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfElectricPotential
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_FRAME_MODEL,
    CONF_FRAME_NAME,
    CONF_FRAME_UUID,
    CONF_SERIAL_NUMBER,
    DOMAIN,
)
from .coordinator import InkposterCloudCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Inkposter sensors from a config entry."""
    runtime_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InkposterCloudCoordinator = runtime_data.cloud_coordinator

    frame_uuid = entry.data[CONF_FRAME_UUID]
    frame_name = entry.data.get(CONF_FRAME_NAME, "Inkposter")
    serial_number = entry.data.get(CONF_SERIAL_NUMBER, "")
    frame_model = entry.data.get(CONF_FRAME_MODEL, "")

    device_info = DeviceInfo(
        identifiers={(DOMAIN, frame_uuid)},
        name=frame_name,
        manufacturer="Inkposter",
        model=frame_model,
        serial_number=serial_number or None,
    )

    sensors: list[SensorEntity] = [
        InkposterBatterySensor(coordinator, device_info, frame_uuid),
        InkposterBatteryVoltageSensor(coordinator, device_info, frame_uuid),
        InkposterChargingSensor(coordinator, device_info, frame_uuid),
        InkposterWifiSignalSensor(coordinator, device_info, frame_uuid),
        InkposterFirmwareVersionSensor(coordinator, device_info, frame_uuid),
        InkposterStorageSensor(coordinator, device_info, frame_uuid),
        InkposterCurrentImageSensor(coordinator, device_info, frame_uuid),
        InkposterImageTransferSensor(coordinator, device_info, frame_uuid),
        InkposterFirmwareUpdateSensor(coordinator, device_info, frame_uuid),
    ]

    # Add BLE diagnostic sensor if BLE coordinator is available.
    ble_coordinator = runtime_data.ble_coordinator
    if ble_coordinator is not None:
        sensors.append(
            InkposterBleSecureModeSensor(ble_coordinator, device_info, frame_uuid)
        )

    async_add_entities(sensors, update_before_add=False)


class InkposterBaseSensor(CoordinatorEntity[InkposterCloudCoordinator], SensorEntity):
    """Base class for Inkposter cloud sensors."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: InkposterCloudCoordinator,
        device_info: DeviceInfo,
        frame_uuid: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_info = device_info
        self._frame_uuid = frame_uuid

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def available(self) -> bool:
        """Available if we have any cached data."""
        return self.coordinator.data is not None

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator and apply initial data."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    def _frame_status(self) -> dict[str, Any]:
        """Get the frame_status dict from coordinator data."""
        if self.coordinator.data is None:
            return {}
        return self.coordinator.data.get("frame_status", {})

    def _image_status(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        return self.coordinator.data.get("image_status", {})

    def _version_check(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        return self.coordinator.data.get("version_check", {})


class InkposterBatterySensor(InkposterBaseSensor):
    """Battery capacity sensor (percentage)."""

    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:battery"

    def __init__(self, coordinator, device_info, frame_uuid) -> None:
        super().__init__(coordinator, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_battery"

    @callback
    def _handle_coordinator_update(self) -> None:
        status = self._frame_status()
        self._attr_native_value = status.get("batteryCapacity")
        super()._handle_coordinator_update()


class InkposterBatteryVoltageSensor(InkposterBaseSensor):
    """Battery voltage sensor."""

    _attr_name = "Battery Voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator, device_info, frame_uuid) -> None:
        super().__init__(coordinator, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_battery_voltage"

    @callback
    def _handle_coordinator_update(self) -> None:
        status = self._frame_status()
        val = status.get("batteryVoltage")
        self._attr_native_value = round(val, 3) if val is not None else None
        super()._handle_coordinator_update()


class InkposterChargingSensor(InkposterBaseSensor):
    """Charging state sensor."""

    _attr_name = "Charging"
    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator, device_info, frame_uuid) -> None:
        super().__init__(coordinator, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_charging"

    @callback
    def _handle_coordinator_update(self) -> None:
        status = self._frame_status()
        charging = status.get("isCharging")
        self._attr_native_value = "Charging" if charging else "Not charging"
        self._attr_icon = (
            "mdi:battery-charging" if charging else "mdi:battery"
        )
        super()._handle_coordinator_update()


class InkposterWifiSignalSensor(InkposterBaseSensor):
    """WiFi signal strength sensor (percentage, as reported by cloud API)."""

    _attr_name = "WiFi Signal"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:wifi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device_info, frame_uuid) -> None:
        super().__init__(coordinator, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_wifi_signal"

    @callback
    def _handle_coordinator_update(self) -> None:
        status = self._frame_status()
        self._attr_native_value = status.get("wifiSignalStrength")
        super()._handle_coordinator_update()


class InkposterFirmwareVersionSensor(InkposterBaseSensor):
    """Firmware version sensor."""

    _attr_name = "Firmware Version"
    _attr_icon = "mdi:chip"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device_info, frame_uuid) -> None:
        super().__init__(coordinator, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_firmware_version"

    @callback
    def _handle_coordinator_update(self) -> None:
        status = self._frame_status()
        self._attr_native_value = status.get("firmwareVersion", "Unknown")
        super()._handle_coordinator_update()


class InkposterStorageSensor(InkposterBaseSensor):
    """Storage usage sensor."""

    _attr_name = "Storage"
    _attr_icon = "mdi:harddisk"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device_info, frame_uuid) -> None:
        super().__init__(coordinator, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_storage"

    @callback
    def _handle_coordinator_update(self) -> None:
        status = self._frame_status()
        total = status.get("storageVolume", 0)
        free = status.get("storageFreeVolume", 0)
        if total > 0:
            used = total - free
            pct = round((used / total) * 100, 1)
            self._attr_native_value = f"{pct}%"
            self._attr_extra_state_attributes = {
                "usage_percent": pct,
                "free_bytes": free,
                "total_bytes": total,
                "used_bytes": used,
                "free_formatted": _format_bytes(free),
                "total_formatted": _format_bytes(total),
            }
        else:
            self._attr_native_value = "Unknown"
            self._attr_extra_state_attributes = {}
        super()._handle_coordinator_update()


class InkposterCurrentImageSensor(InkposterBaseSensor):
    """Currently displayed image sensor."""

    _attr_name = "Current Image"
    _attr_icon = "mdi:image"

    def __init__(self, coordinator, device_info, frame_uuid) -> None:
        super().__init__(coordinator, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_current_image"

    @callback
    def _handle_coordinator_update(self) -> None:
        status = self._frame_status()
        items = status.get("displayedItems", [])
        if items:
            item_id = items[0].get("itemId", "Unknown")
            self._attr_native_value = item_id
            self._attr_extra_state_attributes = {
                "private": items[0].get("private", False),
                "card_id": items[0].get("cardId"),
            }
        else:
            self._attr_native_value = "None"
            self._attr_extra_state_attributes = {}
        super()._handle_coordinator_update()


class InkposterImageTransferSensor(InkposterBaseSensor):
    """Image transfer progress sensor."""

    _attr_name = "Image Transfer"
    _attr_icon = "mdi:cloud-upload"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device_info, frame_uuid) -> None:
        super().__init__(coordinator, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_image_transfer"

    @callback
    def _handle_coordinator_update(self) -> None:
        img_status = self._image_status()
        progress = img_status.get("progress")
        if progress is not None:
            sent = img_status.get("sentToEpd", 0)
            error = img_status.get("error", 0)
            if error:
                self._attr_native_value = "Error"
            elif sent:
                self._attr_native_value = "Complete"
            else:
                self._attr_native_value = f"{progress}%"
            self._attr_extra_state_attributes = {
                "progress": progress,
                "sent_to_epd": sent,
                "error": error,
                "attempt": img_status.get("attemptNo"),
            }
        else:
            self._attr_native_value = "Idle"
            self._attr_extra_state_attributes = {}
        super()._handle_coordinator_update()


class InkposterFirmwareUpdateSensor(InkposterBaseSensor):
    """Firmware update availability sensor."""

    _attr_name = "Firmware Update"
    _attr_icon = "mdi:update"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device_info, frame_uuid) -> None:
        super().__init__(coordinator, device_info, frame_uuid)
        self._attr_unique_id = f"{frame_uuid}_firmware_update"

    @callback
    def _handle_coordinator_update(self) -> None:
        vc = self._version_check()
        available = vc.get("newVersionAvailable", False)
        self._attr_native_value = "Available" if available else "Up to date"
        version = vc.get("version", "")
        self._attr_extra_state_attributes = {
            "new_version_available": available,
            "new_version": version if version else None,
            "release_notes": vc.get("releaseNotes") or None,
        }
        super()._handle_coordinator_update()


# ---------------------------------------------------------------------------
# BLE diagnostic sensor (not backed by cloud coordinator)
# ---------------------------------------------------------------------------


class InkposterBleSecureModeSensor(SensorEntity):
    """Diagnostic sensor showing whether the device is in BLE secure mode."""

    _attr_has_entity_name = True
    _attr_name = "BLE Secure Mode"
    _attr_icon = "mdi:shield-lock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = True  # Polls the BLE coordinator's cached status.

    def __init__(self, ble_coordinator, device_info: DeviceInfo, frame_uuid: str) -> None:
        self._ble_coordinator = ble_coordinator
        self._device_info = device_info
        self._attr_unique_id = f"{frame_uuid}_ble_secure_mode"

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def available(self) -> bool:
        return self._ble_coordinator.last_ble_status is not None

    async def async_update(self) -> None:
        from .ble import parse_status_flags

        status = self._ble_coordinator.last_ble_status
        if status is None:
            self._attr_native_value = "Unknown"
            self._attr_extra_state_attributes = {}
            return

        flags = parse_status_flags(status.status_original)
        self._attr_native_value = "Enabled" if flags.secure_mode else "Disabled"
        self._attr_extra_state_attributes = {
            "secure_mode": flags.secure_mode,
            "key_seq": status.key_seq,
            "msg_seq": status.msg_seq,
            "model": status.model_str,
            "firmware": f"{status.fw_major}.{status.fw_minor}.{status.fw_build}",
            "capacity": status.capacity,
            "wifi_quality": status.wifi_quality,
            "status_flags_raw": hex(status.status_original),
        }


def _format_bytes(b: int) -> str:
    """Format bytes to human-readable string."""
    if b >= 1024**3:
        return f"{round(b / (1024**3), 2)} GB"
    if b >= 1024**2:
        return f"{round(b / (1024**2), 1)} MB"
    if b >= 1024:
        return f"{round(b / 1024, 1)} KB"
    return f"{b} B"
