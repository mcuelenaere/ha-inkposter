"""Data update coordinators for the Inkposter integration.

InkposterCloudCoordinator -- primary, polls the cloud API for frame status.
InkposterBleCoordinator  -- optional, uses ActiveBluetoothDataUpdateCoordinator
                            for BLE presence detection and local status reads.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import InkposterApiClient
from .ble import BleStatusDecoded, async_read_status, decode_status, parse_status_flags
from .const import (
    BLE_STATUS_LEN,
    CLOUD_POLL_INTERVAL_SECS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
CACHE_KEY_PREFIX = f"{DOMAIN}.coordinator_cache"


# ---------------------------------------------------------------------------
# Cloud coordinator (primary)
# ---------------------------------------------------------------------------


class InkposterCloudCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Coordinator that polls the Inkposter cloud API for frame status.

    Data shape stored in self.data:
    {
        "frame_status": { ... },   # from GET /frame/status (for our frame)
        "image_status": { ... },   # from GET /frame/image-status
        "version_check": { ... },  # from GET /frame/version-check
    }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api_client: InkposterApiClient,
        frame_uuid: str,
        entry_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Inkposter Cloud",
            update_interval=timedelta(seconds=CLOUD_POLL_INTERVAL_SECS),
            always_update=False,
        )
        self._api = api_client
        self._frame_uuid = frame_uuid
        self._entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{CACHE_KEY_PREFIX}.{entry_id}"
        )

    @property
    def frame_uuid(self) -> str:
        return self._frame_uuid

    # -- Disk cache ---------------------------------------------------------

    async def async_load_cached_data(self) -> None:
        """Load previously cached data from disk (call once during setup)."""
        cached = await self._store.async_load()
        if cached is not None:
            _LOGGER.debug("Restored cached cloud data from disk")
            self.async_set_updated_data(cached)

    async def _async_save_cache(self) -> None:
        if self.data is not None:
            await self._store.async_save(dict(self.data))

    def async_set_updated_data(self, data: dict[str, Any] | None) -> None:
        """Override to persist non-None data to disk."""
        super().async_set_updated_data(data)
        if data is not None:
            self.hass.async_create_task(self._async_save_cache())

    # -- Fetch -------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any] | None:
        """Poll the cloud API for frame status."""
        try:
            frame_status_list = await self._api.async_get_frame_status()
            image_status_list = await self._api.async_get_image_status()
            version_check_list = await self._api.async_get_version_check()
        except Exception as err:
            raise UpdateFailed(f"Error communicating with Inkposter API: {err}") from err

        # Each response is a list of {frame_uuid: data} dicts.
        frame_status = _extract_frame_data(frame_status_list, self._frame_uuid)
        image_status = _extract_frame_data(image_status_list, self._frame_uuid)
        version_check = _extract_frame_data(version_check_list, self._frame_uuid)

        return {
            "frame_status": frame_status,
            "image_status": image_status,
            "version_check": version_check,
        }


def _extract_frame_data(
    response_list: list[dict[str, Any]], frame_uuid: str
) -> dict[str, Any]:
    """Extract data for a specific frame UUID from the API response list.

    The API returns: [{"uuid1": {...}}, {"uuid2": {...}}]
    or sometimes:   [{"uuid1": {...}, "uuid2": {...}}]
    """
    for item in response_list:
        if isinstance(item, dict) and frame_uuid in item:
            return item[frame_uuid]
    return {}


# ---------------------------------------------------------------------------
# BLE coordinator (optional)
# ---------------------------------------------------------------------------


class InkposterBleCoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Coordinator for BLE presence detection and local status reads."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        address: str,
        shared_key: str | None = None,
    ) -> None:
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            address=address,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_update,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            connectable=True,
        )
        self._shared_key = shared_key
        self._last_ble_status: BleStatusDecoded | None = None
        self._poll_interval = 300.0  # seconds between active polls

    @property
    def last_ble_status(self) -> BleStatusDecoded | None:
        """Return the last decoded BLE status."""
        return self._last_ble_status

    @callback
    def _needs_poll(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        """Determine if we should actively connect for a status read."""
        if self.hass.state != CoreState.running:
            return False
        # Poll if we haven't polled yet, or if enough time has passed.
        if seconds_since_last_poll is None:
            return True
        if seconds_since_last_poll >= self._poll_interval:
            return bool(
                bluetooth.async_ble_device_from_address(
                    self.hass, service_info.device.address, connectable=True
                )
            )
        return False

    async def _async_update(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Active poll: connect and read the 28-byte status characteristic."""
        if service_info.connectable:
            device = service_info.device
        else:
            device = bluetooth.async_ble_device_from_address(
                self.hass, service_info.device.address, True
            )
        if device is None:
            raise RuntimeError(
                f"No connectable device found for {service_info.device.address}"
            )

        try:
            status = await async_read_status(device)
            self._last_ble_status = status
            _LOGGER.debug(
                "BLE status: model=%s fw=%d.%d.%d capacity=%d",
                status.model_str,
                status.fw_major,
                status.fw_minor,
                status.fw_build,
                status.capacity,
            )
        except Exception as err:
            _LOGGER.debug("BLE status read failed: %s", err)
            raise

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle an incoming BLE advertisement."""
        # Must call super() so ActiveBluetoothDataUpdateCoordinator
        # triggers its _needs_poll -> _async_update poll chain.
        super()._async_handle_bluetooth_event(service_info, change)

    @callback
    def _async_handle_unavailable(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Handle the device going out of BLE range."""
        _LOGGER.debug("BLE device %s is now unavailable", service_info.address)
