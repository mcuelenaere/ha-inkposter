"""BLE client for the Inkposter integration.

Handles status decoding, HMAC-authenticated command framing, and
sending JSON commands over BLE GATT to InkP-* devices.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import struct
from dataclasses import dataclass
from typing import Any

from .const import (
    BLE_ACTION_FACTORY_RESET,
    BLE_ACTION_FETCH,
    BLE_ACTION_GHOSTING_CLEANER,
    BLE_ACTION_REBOOT,
    BLE_ACTION_SET_SETTINGS,
    BLE_COMMAND_CHAR_UUID,
    BLE_CONNECT_TIMEOUT,
    BLE_DEFAULT_SKEY_HEX,
    BLE_HEADER_SHORT,
    BLE_MODEL_MAP,
    BLE_MTU_SIZE,
    BLE_STATUS_CHAR_UUID,
    BLE_STATUS_LEN,
    STATUS_FLAG_BATTERY_CHARGING,
    STATUS_FLAG_BATTERY_CHARGING_LOW,
    STATUS_FLAG_BATTERY_FULL,
    STATUS_FLAG_BATTERY_LOW,
    STATUS_FLAG_DATETIME_SYNCED,
    STATUS_FLAG_FW_UPDATE_ERROR,
    STATUS_FLAG_FW_UPDATE_READY,
    STATUS_FLAG_GENERAL_ERROR,
    STATUS_FLAG_LAUNCHER_CMD_READY,
    STATUS_FLAG_SECURE_MODE,
    STATUS_FLAG_SERVER_CONNECTION_ERROR,
    STATUS_FLAG_SERVER_SOCKET_LINK_OK,
    STATUS_FLAG_SYNC_ERROR,
    STATUS_FLAG_USER_INTERACTION_REQUIRED,
    STATUS_FLAG_WIFI_CONNECTION_ERROR,
    STATUS_FLAG_WIFI_LINK_OK,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status decoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BleStatusDecoded:
    """Decoded 28-byte BLE status payload."""

    company_id: int
    msg_seq: int
    version: int
    capacity: int
    wifi_quality: int
    key_seq: int
    status_original: int
    jobs: int
    fw_major: int
    fw_minor: int
    fw_build: int
    model_str: str


@dataclass(frozen=True)
class BleStatusFlags:
    """Boolean flags decoded from the status bitmask."""

    general_error: bool
    battery_low: bool
    battery_charging: bool
    battery_charging_low: bool
    battery_full: bool
    secure_mode: bool
    user_interaction_required: bool
    wifi_connection_error: bool
    wifi_link_ok: bool
    server_connection_error: bool
    server_socket_link_ok: bool
    sync_error: bool
    fw_update_error: bool
    fw_update_ready: bool
    launcher_cmd_ready: bool
    datetime_synced: bool


def decode_status(raw: bytes) -> BleStatusDecoded:
    """Decode a 28-byte BLE status characteristic value."""
    if len(raw) < BLE_STATUS_LEN:
        raise ValueError(f"Expected {BLE_STATUS_LEN} bytes, got {len(raw)}")

    company_id, msg_seq = struct.unpack_from("<HH", raw, 0)
    version = raw[4]
    capacity = raw[5]
    wifi_quality = raw[6]
    key_seq = raw[7]
    status_original = struct.unpack_from("<I", raw, 8)[0]
    jobs = struct.unpack_from("<I", raw, 12)[0]
    fw_packed = struct.unpack_from("<I", raw, 16)[0]
    model_raw = raw[20:28]
    model_str = model_raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")

    fw_major = (fw_packed >> 24) & 0xFF
    fw_minor = (fw_packed >> 16) & 0xFF
    fw_build = fw_packed & 0xFFFF

    return BleStatusDecoded(
        company_id=company_id,
        msg_seq=msg_seq,
        version=version,
        capacity=capacity,
        wifi_quality=wifi_quality,
        key_seq=key_seq,
        status_original=status_original,
        jobs=jobs,
        fw_major=fw_major,
        fw_minor=fw_minor,
        fw_build=fw_build,
        model_str=model_str,
    )


def parse_status_flags(status_original: int) -> BleStatusFlags:
    """Parse the status bitmask into boolean flags."""
    def b(mask: int) -> bool:
        return (status_original & mask) != 0

    return BleStatusFlags(
        general_error=b(STATUS_FLAG_GENERAL_ERROR),
        battery_low=b(STATUS_FLAG_BATTERY_LOW),
        battery_charging=b(STATUS_FLAG_BATTERY_CHARGING),
        battery_charging_low=b(STATUS_FLAG_BATTERY_CHARGING_LOW),
        battery_full=b(STATUS_FLAG_BATTERY_FULL),
        secure_mode=b(STATUS_FLAG_SECURE_MODE),
        user_interaction_required=b(STATUS_FLAG_USER_INTERACTION_REQUIRED),
        wifi_connection_error=b(STATUS_FLAG_WIFI_CONNECTION_ERROR),
        wifi_link_ok=b(STATUS_FLAG_WIFI_LINK_OK),
        server_connection_error=b(STATUS_FLAG_SERVER_CONNECTION_ERROR),
        server_socket_link_ok=b(STATUS_FLAG_SERVER_SOCKET_LINK_OK),
        sync_error=b(STATUS_FLAG_SYNC_ERROR),
        fw_update_error=b(STATUS_FLAG_FW_UPDATE_ERROR),
        fw_update_ready=b(STATUS_FLAG_FW_UPDATE_READY),
        launcher_cmd_ready=b(STATUS_FLAG_LAUNCHER_CMD_READY),
        datetime_synced=b(STATUS_FLAG_DATETIME_SYNCED),
    )


def resolve_model(model_str: str) -> str | None:
    """Map a BLE model string to our canonical model key."""
    return BLE_MODEL_MAP.get(model_str)


def format_firmware_version(decoded: BleStatusDecoded) -> str:
    """Format firmware version string."""
    return f"{decoded.fw_major}.{decoded.fw_minor}.{decoded.fw_build}"


# ---------------------------------------------------------------------------
# Command framing (HMAC-SHA256 authenticated)
# ---------------------------------------------------------------------------


def _hmac4_sha256(key: bytes, msg_seq: int, header_and_payload: bytes) -> bytes:
    """Compute the 4-byte HMAC used in BLE command frames."""
    seq_le16 = struct.pack("<H", msg_seq & 0xFFFF)
    digest = hmac.new(key, seq_le16 + header_and_payload, hashlib.sha256).digest()
    return digest[:4]


def build_command_frame(
    payload: bytes,
    msg_seq: int,
    skey_hex: str | None = None,
) -> bytes:
    """Build a BLE command frame: header(1) + payload(N) + hmac(4)."""
    key = bytes.fromhex(skey_hex or BLE_DEFAULT_SKEY_HEX)
    hp = bytes([BLE_HEADER_SHORT]) + payload
    mac = _hmac4_sha256(key, msg_seq, hp)
    return hp + mac


def build_json_command(
    action: int,
    extra: dict[str, Any] | None = None,
    msg_seq: int = 0,
    skey_hex: str | None = None,
) -> bytes:
    """Build a framed BLE command from a JSON action payload."""
    obj: dict[str, Any] = {"action": action}
    if extra:
        obj.update(extra)
    payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return build_command_frame(payload, msg_seq, skey_hex)


# ---------------------------------------------------------------------------
# Convenience command builders
# ---------------------------------------------------------------------------


def cmd_fetch(msg_seq: int = 0, skey_hex: str | None = None) -> bytes:
    """Build a FETCH command (action=42)."""
    return build_json_command(BLE_ACTION_FETCH, msg_seq=msg_seq, skey_hex=skey_hex)


def cmd_reboot(msg_seq: int = 0, skey_hex: str | None = None) -> bytes:
    """Build a REBOOT command (action=3)."""
    return build_json_command(BLE_ACTION_REBOOT, msg_seq=msg_seq, skey_hex=skey_hex)


def cmd_ghosting_cleaner(msg_seq: int = 0, skey_hex: str | None = None) -> bytes:
    """Build a GHOSTING_CLEANER command (action=44)."""
    return build_json_command(
        BLE_ACTION_GHOSTING_CLEANER, msg_seq=msg_seq, skey_hex=skey_hex
    )


def cmd_factory_reset(msg_seq: int = 0, skey_hex: str | None = None) -> bytes:
    """Build a FACTORY_RESET command (action=1)."""
    return build_json_command(
        BLE_ACTION_FACTORY_RESET, msg_seq=msg_seq, skey_hex=skey_hex
    )


def cmd_set_settings(
    *,
    user: str | None = None,
    token: str | None = None,
    api_env_type: int | str | None = None,
    ssid: str | None = None,
    passwd: str | None = None,
    msg_seq: int = 0,
    skey_hex: str | None = None,
) -> bytes:
    """Build a SET_SETTINGS command (action=2)."""
    extra: dict[str, Any] = {}
    if user is not None:
        extra["user"] = user
    if token is not None:
        extra["token"] = token
    if api_env_type is not None:
        extra["apiEnvType"] = (
            str(api_env_type) if isinstance(api_env_type, int) else api_env_type
        )
    if ssid is not None:
        extra["ssid"] = ssid
    if passwd is not None:
        extra["passwd"] = passwd
    return build_json_command(
        BLE_ACTION_SET_SETTINGS, extra=extra, msg_seq=msg_seq, skey_hex=skey_hex
    )


# ---------------------------------------------------------------------------
# High-level BLE operations (using bleak)
# ---------------------------------------------------------------------------


async def async_read_status(
    ble_device: Any,
    *,
    timeout: float = BLE_CONNECT_TIMEOUT,
) -> BleStatusDecoded:
    """Connect to a BLE device and read the status characteristic.

    Uses bleak-retry-connector for reliable connections.
    Creates a fresh BleakClient per HA Bluetooth best practices.
    """
    from bleak import BleakClient
    from bleak_retry_connector import establish_connection

    client = await establish_connection(BleakClient, ble_device, ble_device.address)

    try:
        raw = await client.read_gatt_char(BLE_STATUS_CHAR_UUID)
        return decode_status(bytes(raw))
    finally:
        await client.disconnect()


async def async_send_command(
    ble_device: Any,
    command_bytes: bytes | None = None,
    *,
    action: int | None = None,
    extra: dict[str, Any] | None = None,
    skey_hex: str | None = None,
    timeout: float = BLE_CONNECT_TIMEOUT,
) -> BleStatusDecoded | None:
    """Connect, read status (for msg_seq), build + send a command, read status again.

    There are two ways to call this:
    1. Pass ``action`` (and optionally ``extra``/``skey_hex``) to have the
       command built with the correct msg_seq read from the device.
    2. Pass pre-built ``command_bytes`` (legacy; msg_seq won't be correct
       unless the caller already obtained it).

    Returns the post-command status, or None if the read fails.
    """
    import asyncio as _asyncio

    from bleak import BleakClient
    from bleak_retry_connector import establish_connection

    client = await establish_connection(BleakClient, ble_device, ble_device.address)
    # NOTE: Do NOT call client.pair() -- the Android app doesn't pair,
    # and on macOS pairing enables link-layer encryption which corrupts
    # writes from the device's perspective.

    try:
        # Read status before command to get the current msg_seq.
        raw_before = await client.read_gatt_char(BLE_STATUS_CHAR_UUID)
        status_before = decode_status(bytes(raw_before))
        flags = parse_status_flags(status_before.status_original)
        _LOGGER.debug(
            "BLE status before command: msg_seq=%d, model=%s, secure_mode=%s",
            status_before.msg_seq,
            status_before.model_str,
            flags.secure_mode,
        )

        # If device is NOT in secure mode, use the default key
        # (matching Android app behavior from BleCommandSender).
        if not flags.secure_mode:
            skey_hex = None

        # The Android app checks launcherCmdReady before sending any command.
        # If not ready, disconnect, wait 3s, reconnect, and check again.
        if not flags.launcher_cmd_ready:
            _LOGGER.debug(
                "BLE device not ready (launcherCmdReady=false), retrying..."
            )
            await client.disconnect()
            await _asyncio.sleep(3.0)

            client = await establish_connection(
                BleakClient, ble_device, ble_device.address
            )
            raw_retry = await client.read_gatt_char(BLE_STATUS_CHAR_UUID)
            status_before = decode_status(bytes(raw_retry))
            flags = parse_status_flags(status_before.status_original)
            _LOGGER.debug(
                "BLE status after retry: msg_seq=%d, launcher_cmd_ready=%s",
                status_before.msg_seq,
                flags.launcher_cmd_ready,
            )

            if not flags.launcher_cmd_ready:
                await client.disconnect()
                raise RuntimeError(
                    "Device not ready for commands (launcherCmdReady=false)"
                )

            # Re-check secure mode on the fresh status.
            if not flags.secure_mode:
                skey_hex = None

        # Build command with correct msg_seq if action is provided.
        if action is not None:
            command_bytes = build_json_command(
                action,
                extra=extra,
                msg_seq=status_before.msg_seq,
                skey_hex=skey_hex,
            )

        if command_bytes is None:
            raise ValueError("Either command_bytes or action must be provided")

        # Write command. The Android app uses WRITE_TYPE_NO_RESPONSE, but
        # on macOS CoreBluetooth, write-with-response is more reliable.
        await client.write_gatt_char(
            BLE_COMMAND_CHAR_UUID, command_bytes, response=True
        )

        # Brief pause, then read status after command.
        await _asyncio.sleep(1.0)
        raw_after = await client.read_gatt_char(BLE_STATUS_CHAR_UUID)
        return decode_status(bytes(raw_after))
    finally:
        await client.disconnect()
