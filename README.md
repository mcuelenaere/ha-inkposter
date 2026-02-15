# ha-inkposter

Home Assistant integration for [Inkposter](https://inkposter.com) e-ink frames.

## Features

- **Cloud API**: Authenticate with your Inkposter account, poll frame status (battery, storage, WiFi, firmware), upload images for display
- **BLE**: Discover `InkP-*` devices, read local status, send commands (fetch, reboot, ghosting cleaner)
- **Sensors**: Battery, battery voltage, charging state, WiFi signal, firmware version, storage usage, current image, image transfer progress, firmware update availability
- **Buttons**: Refresh status (cloud), check firmware (cloud), fetch image (BLE), reboot (BLE), ghosting cleaner (BLE)
- **Media Player**: Upload images via `media_player.play_media`
- **Services**: `upload_image_url`, `upload_image_data`, `fetch`, `reboot`, `ghosting_cleaner`, `refresh_status`

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS
2. Install "Inkposter"
3. Restart Home Assistant

### Manual

1. Copy `custom_components/ha_inkposter` to your `config/custom_components/` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for **Inkposter**
3. Enter your Inkposter email and password
4. Select the frame you want to control
5. Optionally link a BLE device for local commands

## Usage

### Display an image

```yaml
service: media_player.play_media
target:
  entity_id: media_player.inkposter_display
data:
  media_content_type: image/jpeg
  media_content_id: /media/local/photos/artwork.jpg
```

### Upload from URL

```yaml
service: ha_inkposter.upload_image_url
data:
  url: "https://example.com/image.jpg"
```

### Trigger fetch via BLE

```yaml
service: ha_inkposter.fetch
```
