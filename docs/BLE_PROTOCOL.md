# InkPoster / InkP-* BLE Protocol (reverse engineered)

This document is derived from the decompiled Android APK sources (smali + jadx),
the `BleCommandSender`, `BleFrameInitializeHelper`, `BleScanner`, `BleInkPosterStatusKt`,
and `BleCommands` classes, and validated against a real device via the ha-inkposter
Home Assistant integration.

## Device discovery

- **Advertised name prefix**: devices are identified by the Bluetooth name starting with `InkP-`.
- **Manufacturer specific data**: the scanner reads manufacturer payload(s) and parses them via `BleInkPosterPayload.Companion.safeParseInkPosterPayload(...)`.
  - The payload is expected to be **26 bytes** (without the company ID bytes).

## GATT surface (services & characteristics)

The app code uses 1 custom service and at least 2 characteristics:

- **InkP primary service**: `706218ee-d3d6-46ad-8080-6eefbacf7dbc`
- **InkP status characteristic (read/notify)**: `aa5a52bb-e560-42b5-be83-7b79f7627f6d`
- **InkP command characteristic (write)**: `1b5f2d1a-8ff5-459e-a8de-73e13c051a13`

## Connection flow

Both `BleFrameInitializeHelper` and `BleCommandSender` implement the same high-level flow:

- Connect to device via `connectGatt(...)`
- Request **MTU 512** via `BluetoothGatt.requestMtu(512)`
- Discover services (`discoverServices()`) if needed
- Read the status characteristic (expects **28 bytes**)
- For the "initializer" path (`BleFrameInitializeHelper`), notifications are enabled for status:
  - CCCD UUID **`00002902-0000-1000-8000-00805f9b34fb`**
  - Writes `BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE`

**Important**: Do NOT call `pair()` before BLE operations. The Android app does not pair.
On macOS, pairing can enable link-layer encryption that interferes with writes.

## Status payload

- **Characteristic**: InkP status characteristic
- **Operation**: `readCharacteristic(...)` and (for initializer) notifications
- **Length**: the code enforces `EXPECTED_STATUS_LEN = 28`
- **Parsing**: `BleInkPosterPayload.Companion.safeParseInkPosterPayload(statusBytes)` which internally strips the first 2 bytes (company id) and parses the remaining 26 bytes.

### 28-byte status layout (decoded)

Byte order: **little-endian** for `u16` and `u32` fields.

| Offset | Size | Type | Field | Notes |
|---:|---:|---|---|---|
| 0 | 2 | `u16` | `companyId` | Present on the **status characteristic** (and possibly other sources). The parser discards it. |
| 2 | 2 | `u16` | `msgSeq` | Used for authenticating commands (see Command framing). Increments when a command is successfully executed. |
| 4 | 1 | `u8` | `version` | Parser requires this to be `1`. |
| 5 | 1 | `u8` | `capacity` | Battery percentage (0-100). |
| 6 | 1 | `u8` | `wifiQuality` | WiFi signal quality (0-100). |
| 7 | 1 | `u8` | `keySeq` | Active key index. Correlates with `sharedKeySequence` from the cloud API (`GET /api/v1/user/frames`). |
| 8 | 4 | `u32` | `statusOriginal` | Bitmask → booleans (see Status bitmask mapping below). |
| 12 | 4 | `u32` | `jobs` | Job queue depth. |
| 16 | 4 | `u32` | `fwVersionPacked` | Parsed as `fwMajor = (v >> 24)`, `fwMinor = (v >> 16)`, `fwBuild = (v & 0xFFFF)`. |
| 20 | 8 | bytes | `modelStr` | UTF-8 string padded with `0x00`, e.g. `W1330`, `W2850`, `W3150`. |

### Model mapping

- `W1330` or `Frame_13_3` → 13.3" frame (1200x1600)
- `W2850` or `Frame_28_5` → 28.5" frame (2160x3060)
- `W3150` or `Frame_31_5` → 31.5" frame (2560x1440)
- anything else → Unknown

## Command framing

All commands written to the command characteristic are framed the same way:

- **Write type**: The Android app uses `WRITE_TYPE_NO_RESPONSE`, but on macOS CoreBluetooth, **write-with-response is required** for reliable delivery. Without it, writes are silently dropped.
- **Frame format**:

```
| 1 byte header | N bytes payload | 4 bytes mac |
```

- The "header" constant is: **`HEADER_SHORT = 0x01`**
- The **MAC** is computed as:

```
mac = HMAC_SHA256(key = activeSkey, data = seqLE16 || (header || payload))
mac4 = mac[0..3]   // only the first 4 bytes are appended
```

Where:
- `seqLE16` is the current `msgSeq` from the most recent status read, encoded as a **little-endian 16-bit** integer.
- `activeSkey` is the active shared key (see below).

### Shared key (`activeSkey`)

- The code accepts a **shared key string** (`sharedKey`) and converts it from **hex** to bytes.
- If no shared key is provided, it uses a built-in default key:
  - **`DEFAULT_SKEY = b716c1d9807b857fcb26f26fab215c6b`** (hex, 16 bytes)
- In practice, devices use a **per-device shared key** returned by the InkPoster cloud API (`GET /api/v1/user/frames` returns a `sharedKey` field) and a sequence/index (`sharedKeySequence`). The device's status payload includes a `keySeq` byte; this correlates with `sharedKeySequence`.

#### Key selection logic (critical for commands to be accepted)

The Android app (`BleCommandSender`) selects the active key in two stages:

1. **Before connecting** (source: `BleCommandSender$connect$2.smali` lines 230-246):
   If `sharedKey` is not null, set `activeSkey = hexStringToByteArray(sharedKey)`.

2. **After reading status** (source: `BleCommandSender$connect$2$gatt$1.smali` lines 356-372):
   Parse the status bitmask and check `secureMode` (bit `0x00000040`).
   If `secureMode` is **false**, reset `activeSkey = DEFAULT_SKEY`, regardless of the per-device key.

In pseudocode:

```
activeSkey = hexToBytes(sharedKey) if sharedKey else DEFAULT_SKEY
connect(device)
status = readStatus()
if not status.secureMode:
    activeSkey = DEFAULT_SKEY    # ignore per-device key
# all subsequent commands use activeSkey for HMAC
```

**This means**: if a device is NOT in secure mode, the default key must be used for HMAC authentication. Sending commands with the per-device shared key will cause the device to silently reject them (the LED may flash acknowledging the BLE write, but the command is not executed).

### Command readiness (`launcherCmdReady`)

Before sending any BLE command, the Android app checks the `launcherCmdReady` flag (bit `0x00020000` in the status bitmask). This flag indicates the device's firmware launcher is ready to accept commands. All command methods (`sendFetchCommand`, `sendRebootCommand`, `sendFactoryResetCommand`, `sendGhostingCleanerCommand`) perform this check.

If `launcherCmdReady` is **false**, the app follows a retry flow (source: `BleCommandSender$sendFetchCommand$2.smali` lines 870-1277):

1. Log "Device not ready for commands"
2. Wait 3 seconds (`delay(3000)`)
3. Disconnect from the device
4. Wait 1 second (`delay(1000)`)
5. Reconnect and read status again
6. If `launcherCmdReady` is now true, proceed with the command
7. If still false, fail with "Device still not ready after firmware update wait"

**This means**: sending a command while `launcherCmdReady` is false will cause the device to silently ignore it. The LED may blink (acknowledging the BLE write) but the command is not executed by the firmware.

## Commands (JSON payloads)

When the payload is JSON, it is UTF-8 encoded and placed in the `payload` field of the frame described above (with header `0x01`).

The app uses `org.json.JSONObject` and the key **`"action"`** as the primary opcode selector.

### Action IDs (from `BleCommands`)

| Action ID | Name | Description |
|---:|---|---|
| 1 | `FACTORY_RESET` | Factory reset the device |
| 2 | `SET_SETTINGS` | Set WiFi, user/token, or other settings |
| 3 | `REBOOT` | Reboot the device |
| 41 | `HELLO` | Hello/keepalive (may use empty payload) |
| 42 | `FETCH` | Tell the device to fetch new content from the cloud |
| 43 | `NETWORKS` | Unknown (likely WiFi network scan) |
| 44 | `GHOSTING_CLEANER` | Run the e-ink ghosting cleaner |

### JSON schemas

#### Factory reset

```json
{"action": 1}
```

#### Reboot

```json
{"action": 3}
```

#### Fetch

```json
{"action": 42}
```

#### Ghosting cleaner

```json
{"action": 44}
```

#### Wi-Fi settings (SET_SETTINGS)

```json
{
  "action": 2,
  "apiEnvType": "<env-id-as-string>",
  "ssid": "<wifi-ssid>",
  "passwd": "<wifi-password>"
}
```

#### User + token update (also SET_SETTINGS / action=2)

```json
{
  "action": 2,
  "user": "<user-id>",
  "token": "<token>",
  "apiEnvType": <env-id-as-int>
}
```

> Note: `action=2` is used for multiple "set settings" payload shapes; the schema is inferred by which fields are present.

## Status bitmask mapping (from `BleInkPosterStatusKt`)

The status object includes a bitmask integer that maps to boolean flags.

| Bit | Flag | Notes |
|---:|---|---|
| `0x00000001` | `generalError` | |
| `0x00000002` | `batteryLow` | |
| `0x00000004` | `batteryCharging` | |
| `0x00000008` | `batteryChargingLow` | |
| `0x00000010` | `batteryFull` | |
| `0x00000040` | `secureMode` | Determines which HMAC key to use |
| `0x00000080` | `userInteractionRequired` | |
| `0x00000100` | `wifiConnectionError` | |
| `0x00000200` | `wifiLinkOk` | |
| `0x00000400` | `serverConnectionError` | |
| `0x00000800` | `serverSocketLinkOk` | |
| `0x00001000` | `syncError` | |
| `0x00002000` | `fwUpdateError` | |
| `0x00010000` | `fwUpdateReady` | |
| `0x00020000` | `launcherCmdReady` | Must be true before sending commands |
| `0x00040000` | `dateTimeSynced` | |

Bits not listed above appear unused/reserved.
