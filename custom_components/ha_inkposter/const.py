"""Constants for the Inkposter integration."""

from __future__ import annotations

# Integration domain
DOMAIN = "ha_inkposter"

# ---------------------------------------------------------------------------
# Cloud API (reverse-engineered from Android APK + Proxyman captures)
# ---------------------------------------------------------------------------

API_BASE = "https://api.inkposter.com/api/v1"

# Signing credentials (Android APK)
CLIENT_ID = "android"
CLIENT_SECRET = "t5L1zS3D5CAZOE66afhWy8oPVEkZaB5p"

# Common request headers
DEFAULT_HEADERS: dict[str, str] = {
    "x-header-country": "BE",
    "x-header-language": "en",
    "x-client-id": CLIENT_ID,
    "x-header-clientid": CLIENT_ID,
}

# Token refresh buffer (refresh 1 hour before expiry)
REFRESH_BUFFER_SECS = 60 * 60

# Conversion polling
POLL_INTERVAL_SECS = 2
POLL_TIMEOUT_SECS = 120

# Cloud coordinator polling interval
CLOUD_POLL_INTERVAL_SECS = 300  # 5 minutes

# ---------------------------------------------------------------------------
# BLE Protocol (reverse-engineered from Android APK)
# ---------------------------------------------------------------------------

BLE_SERVICE_UUID = "706218ee-d3d6-46ad-8080-6eefbacf7dbc"
BLE_STATUS_CHAR_UUID = "aa5a52bb-e560-42b5-be83-7b79f7627f6d"
BLE_COMMAND_CHAR_UUID = "1b5f2d1a-8ff5-459e-a8de-73e13c051a13"

# CCCD for enabling notifications
BLE_CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"

# Command framing
BLE_HEADER_SHORT = 0x01
BLE_DEFAULT_SKEY_HEX = "b716c1d9807b857fcb26f26fab215c6b"

# Status payload length
BLE_STATUS_LEN = 28

# BLE action IDs (JSON "action" field)
BLE_ACTION_FACTORY_RESET = 1
BLE_ACTION_SET_SETTINGS = 2
BLE_ACTION_REBOOT = 3
BLE_ACTION_HELLO = 41
BLE_ACTION_FETCH = 42
BLE_ACTION_NETWORKS = 43
BLE_ACTION_GHOSTING_CLEANER = 44

# BLE connection parameters
BLE_CONNECT_TIMEOUT = 15.0  # seconds
BLE_MTU_SIZE = 512

# BLE device name prefix for discovery
BLE_NAME_PREFIX = "InkP-"

# ---------------------------------------------------------------------------
# Status bitmask flags (from BleInkPosterStatusKt)
# ---------------------------------------------------------------------------

STATUS_FLAG_GENERAL_ERROR = 0x00000001
STATUS_FLAG_BATTERY_LOW = 0x00000002
STATUS_FLAG_BATTERY_CHARGING = 0x00000004
STATUS_FLAG_BATTERY_CHARGING_LOW = 0x00000008
STATUS_FLAG_BATTERY_FULL = 0x00000010
STATUS_FLAG_SECURE_MODE = 0x00000040
STATUS_FLAG_USER_INTERACTION_REQUIRED = 0x00000080
STATUS_FLAG_WIFI_CONNECTION_ERROR = 0x00000100
STATUS_FLAG_WIFI_LINK_OK = 0x00000200
STATUS_FLAG_SERVER_CONNECTION_ERROR = 0x00000400
STATUS_FLAG_SERVER_SOCKET_LINK_OK = 0x00000800
STATUS_FLAG_SYNC_ERROR = 0x00001000
STATUS_FLAG_FW_UPDATE_ERROR = 0x00002000
STATUS_FLAG_FW_UPDATE_READY = 0x00010000
STATUS_FLAG_LAUNCHER_CMD_READY = 0x00020000
STATUS_FLAG_DATETIME_SYNCED = 0x00040000

# ---------------------------------------------------------------------------
# Frame models and resolutions
# ---------------------------------------------------------------------------

FRAME_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "Frame_13_3": (1200, 1600),
    "Frame_28_5": (2160, 3060),
    "Frame_31_5": (2560, 1440),
}

# Model alias mapping (cloud API modelAlias -> our model key)
MODEL_ALIAS_MAP: dict[str, str] = {
    "spectra_13_3": "Frame_13_3",
    "sharp_28_5": "Frame_28_5",
    "spectra_31_5": "Frame_31_5",
}

# BLE model string mapping
BLE_MODEL_MAP: dict[str, str] = {
    "W1330": "Frame_13_3",
    "Frame_13_3": "Frame_13_3",
    "W2850": "Frame_28_5",
    "Frame_28_5": "Frame_28_5",
    "W3150": "Frame_31_5",
    "Frame_31_5": "Frame_31_5",
}

# ---------------------------------------------------------------------------
# Cloud API actions (POST /api/v1/frame/actions)
# ---------------------------------------------------------------------------

CLOUD_ACTION_REPORT_STATUS = "REPORT_FRAME_STATUS"
CLOUD_ACTION_CHECK_FW_UPDATE = "CHECK_FW_UPDATE"

# ---------------------------------------------------------------------------
# Config entry keys
# ---------------------------------------------------------------------------

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_FRAME_UUID = "frame_uuid"
CONF_FRAME_NAME = "frame_name"
CONF_FRAME_MODEL = "frame_model"
CONF_FRAME_RESOLUTION = "frame_resolution"
CONF_SERIAL_NUMBER = "serial_number"
CONF_SHARED_KEY = "shared_key"
CONF_SHARED_KEY_SEQ = "shared_key_sequence"
CONF_BLE_ADDRESS = "ble_address"

# ---------------------------------------------------------------------------
# Error keys
# ---------------------------------------------------------------------------

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_UNKNOWN = "unknown"
