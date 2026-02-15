"""Config flow for the Inkposter integration.

Flow: login (email/password) -> select frame -> optional BLE discovery -> done.
Also supports passive BLE discovery via async_step_bluetooth.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .api_client import InkposterApiClient
from .const import (
    BLE_NAME_PREFIX,
    BLE_SERVICE_UUID,
    CONF_BLE_ADDRESS,
    CONF_EMAIL,
    CONF_FRAME_MODEL,
    CONF_FRAME_NAME,
    CONF_FRAME_RESOLUTION,
    CONF_FRAME_UUID,
    CONF_PASSWORD,
    CONF_SERIAL_NUMBER,
    CONF_SHARED_KEY,
    CONF_SHARED_KEY_SEQ,
    DOMAIN,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_UNKNOWN,
    MODEL_ALIAS_MAP,
)

_LOGGER = logging.getLogger(__name__)


def _is_inkposter_device(
    service_info: bluetooth.BluetoothServiceInfoBleak,
) -> bool:
    """Check if a BLE device looks like an Inkposter frame."""
    name = (service_info.name or "").strip()
    if name.startswith(BLE_NAME_PREFIX):
        return True
    uuids = {u.lower() for u in (service_info.service_uuids or [])}
    return BLE_SERVICE_UUID.lower() in uuids


class InkposterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Inkposter."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._email: str = ""
        self._password: str = ""
        self._api_client: InkposterApiClient | None = None
        self._frames: list[dict[str, Any]] = []
        self._selected_frame: dict[str, Any] = {}
        self._ble_address: str = ""

    # ------------------------------------------------------------------
    # Entry point: user-initiated or Bluetooth-discovered
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: enter email and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]

            try:
                api_client = InkposterApiClient(
                    hass=self.hass,
                    email=self._email,
                    password=self._password,
                    entry_id="config_flow_temp",
                )
                await api_client.async_login()
                self._api_client = api_client
                return await self.async_step_select_frame()
            except Exception as err:
                _LOGGER.debug("Login failed: %s", err)
                status = getattr(err, "status", None)
                if status == 401 or status == 403:
                    errors["base"] = ERROR_INVALID_AUTH
                elif status and status >= 500:
                    errors["base"] = ERROR_CANNOT_CONNECT
                else:
                    errors["base"] = ERROR_INVALID_AUTH

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2: select frame from cloud account
    # ------------------------------------------------------------------

    async def async_step_select_frame(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a frame from their cloud account."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_uuid = user_input.get(CONF_FRAME_UUID, "")
            for frame in self._frames:
                if frame["id"] == selected_uuid:
                    self._selected_frame = frame
                    break

            if self._selected_frame:
                # Check if already configured.
                await self.async_set_unique_id(self._selected_frame["id"])
                self._abort_if_unique_id_configured()
                return await self.async_step_ble_discovery()

            errors["base"] = ERROR_UNKNOWN

        # Fetch frames from cloud.
        if not self._frames and self._api_client:
            try:
                self._frames = await self._api_client.async_get_frames()
            except Exception as err:
                _LOGGER.error("Failed to fetch frames: %s", err)
                errors["base"] = ERROR_CANNOT_CONNECT

        if not self._frames:
            return self.async_abort(reason="no_frames")

        # Build options from frames list.
        options = [
            selector.SelectOptionDict(
                value=f["id"],
                label=f"{f.get('frameName', 'Inkposter')} ({f.get('modelName', 'Unknown')})",
            )
            for f in self._frames
        ]

        schema = vol.Schema(
            {
                vol.Required(CONF_FRAME_UUID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

        return self.async_show_form(
            step_id="select_frame",
            data_schema=schema,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 3: optional BLE discovery
    # ------------------------------------------------------------------

    async def async_step_ble_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally discover and link a BLE device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            ble_addr = (user_input.get(CONF_BLE_ADDRESS) or "").strip()
            if ble_addr and ble_addr != "__skip__":
                self._ble_address = ble_addr
            return self._create_entry()

        # Scan for InkP-* BLE devices.
        ble_options: list[selector.SelectOptionDict] = []
        try:
            service_infos = bluetooth.async_discovered_service_info(
                self.hass, connectable=True
            )
            for info in service_infos:
                if _is_inkposter_device(info):
                    label = f"{info.name} ({info.address})" if info.name else info.address
                    ble_options.append(
                        selector.SelectOptionDict(value=info.address, label=label)
                    )
        except Exception:
            _LOGGER.debug("BLE scanning not available")

        # Always offer a skip option.
        ble_options.append(
            selector.SelectOptionDict(value="__skip__", label="Skip (cloud only)")
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_BLE_ADDRESS, default="__skip__"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=ble_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

        return self.async_show_form(
            step_id="ble_discovery",
            data_schema=schema,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Bluetooth passive discovery
    # ------------------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered via Bluetooth."""
        if not _is_inkposter_device(discovery_info):
            return self.async_abort(reason="not_supported")

        # Avoid duplicate discovery flows.
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self.context["title_placeholders"] = {
            "name": (discovery_info.name or "Inkposter").strip(),
        }

        self._ble_address = discovery_info.address
        return await self.async_step_user()

    # ------------------------------------------------------------------
    # Create the config entry
    # ------------------------------------------------------------------

    def _create_entry(self) -> ConfigFlowResult:
        """Create the config entry from gathered data."""
        frame = self._selected_frame
        model_alias = frame.get("modelAlias", "")
        frame_model = MODEL_ALIAS_MAP.get(model_alias, model_alias)
        resolution = frame.get("displayResolution", "")

        data = {
            CONF_EMAIL: self._email,
            CONF_PASSWORD: self._password,
            CONF_FRAME_UUID: frame["id"],
            CONF_FRAME_NAME: frame.get("frameName", "Inkposter"),
            CONF_FRAME_MODEL: frame_model,
            CONF_FRAME_RESOLUTION: resolution,
            CONF_SERIAL_NUMBER: frame.get("serialNumber", ""),
            CONF_SHARED_KEY: frame.get("sharedKey", ""),
            CONF_SHARED_KEY_SEQ: frame.get("sharedKeySequence", ""),
        }

        if self._ble_address:
            data[CONF_BLE_ADDRESS] = self._ble_address

        title = frame.get("frameName", "Inkposter")

        return self.async_create_entry(title=title, data=data)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication."""
