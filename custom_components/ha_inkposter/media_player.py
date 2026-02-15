"""Media player entity for the Inkposter integration.

Supports media_player.play_media to upload images to the Inkposter cloud,
which then pushes them to the frame.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_FRAME_MODEL,
    CONF_FRAME_NAME,
    CONF_FRAME_RESOLUTION,
    CONF_FRAME_UUID,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    FRAME_RESOLUTIONS,
)
from .coordinator import InkposterCloudCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Inkposter media player."""
    runtime_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InkposterCloudCoordinator = runtime_data.cloud_coordinator

    frame_uuid = entry.data[CONF_FRAME_UUID]
    frame_name = entry.data.get(CONF_FRAME_NAME, "Inkposter")
    serial_number = entry.data.get(CONF_SERIAL_NUMBER, "")
    frame_model = entry.data.get(CONF_FRAME_MODEL, "")
    frame_resolution = entry.data.get(CONF_FRAME_RESOLUTION, "")

    device_info = DeviceInfo(
        identifiers={(DOMAIN, frame_uuid)},
        name=frame_name,
        manufacturer="Inkposter",
        model=frame_model,
        serial_number=serial_number or None,
    )

    async_add_entities(
        [
            InkposterMediaPlayer(
                coordinator=coordinator,
                device_info=device_info,
                entry=entry,
                frame_uuid=frame_uuid,
                frame_model=frame_model,
                frame_resolution=frame_resolution,
            )
        ],
        update_before_add=False,
    )


class InkposterMediaPlayer(
    CoordinatorEntity[InkposterCloudCoordinator], MediaPlayerEntity
):
    """Media player entity for an Inkposter frame."""

    _attr_has_entity_name = True
    _attr_name = "Display"
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = MediaPlayerEntityFeature.PLAY_MEDIA
    _attr_media_content_type = MediaType.IMAGE
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: InkposterCloudCoordinator,
        device_info: DeviceInfo,
        entry: ConfigEntry,
        frame_uuid: str,
        frame_model: str,
        frame_resolution: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_info = device_info
        self._entry = entry
        self._frame_uuid = frame_uuid
        self._frame_model = frame_model
        self._frame_resolution = frame_resolution
        self._attr_unique_id = f"{frame_uuid}_media_player"

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def state(self) -> MediaPlayerState:
        if self.coordinator.data is None:
            return MediaPlayerState.OFF
        return MediaPlayerState.IDLE

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    async def async_play_media(
        self,
        media_type: MediaType | str,
        media_id: str,
        **kwargs: Any,
    ) -> None:
        """Upload an image to the Inkposter cloud for display on the frame.

        media_id can be:
        - A local file path (e.g. /media/local/photo.jpg)
        - A URL (handled by downloading first)
        """
        runtime_data = self.hass.data[DOMAIN][self._entry.entry_id]
        api_client = runtime_data.api_client

        image_bytes = await self._async_load_image(media_id)
        if image_bytes is None:
            _LOGGER.error("Could not load image from %s", media_id)
            return

        # Resize if we know the target resolution.
        image_bytes = await self._async_resize_image(image_bytes)

        _LOGGER.info(
            "Uploading image (%d bytes) to Inkposter frame %s",
            len(image_bytes),
            self._frame_uuid,
        )

        await api_client.async_upload_and_poll(
            self._frame_uuid, image_bytes, "image/jpeg"
        )

        # Refresh coordinator to pick up new status.
        await self.coordinator.async_request_refresh()

    async def _async_load_image(self, media_id: str) -> bytes | None:
        """Load image bytes from a file path or URL."""
        import aiohttp

        if media_id.startswith(("http://", "https://")):
            session = aiohttp.ClientSession()
            try:
                async with session.get(media_id) as resp:
                    if resp.status == 200:
                        return await resp.read()
            finally:
                await session.close()
            return None

        # Try as a local file path.
        try:
            return await self.hass.async_add_executor_job(
                self._read_file, media_id
            )
        except Exception as err:
            _LOGGER.debug("Could not read file %s: %s", media_id, err)
            return None

    @staticmethod
    def _read_file(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    async def _async_resize_image(self, image_bytes: bytes) -> bytes:
        """Resize the image to the frame's resolution using Pillow."""
        target = self._get_target_resolution()
        if target is None:
            return image_bytes

        target_w, target_h = target

        def _resize(data: bytes) -> bytes:
            from PIL import Image

            img = Image.open(BytesIO(data))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            # Cover fit: resize so the image covers the target, then center-crop.
            src_ratio = img.width / img.height
            tgt_ratio = target_w / target_h

            if src_ratio > tgt_ratio:
                new_h = target_h
                new_w = int(target_h * src_ratio)
            else:
                new_w = target_w
                new_h = int(target_w / src_ratio)

            img = img.resize((new_w, new_h), Image.LANCZOS)

            # Center crop to exact target size.
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            img = img.crop((left, top, left + target_w, top + target_h))

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=95)
            return buf.getvalue()

        return await self.hass.async_add_executor_job(_resize, image_bytes)

    def _get_target_resolution(self) -> tuple[int, int] | None:
        """Get the target resolution for this frame."""
        # Try from the frame_model constant map first.
        if self._frame_model in FRAME_RESOLUTIONS:
            return FRAME_RESOLUTIONS[self._frame_model]
        # Try parsing the resolution string (e.g. "2560x1440").
        if self._frame_resolution and "x" in self._frame_resolution:
            parts = self._frame_resolution.split("x")
            try:
                return (int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                pass
        return None
