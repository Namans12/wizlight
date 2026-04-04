# WizLight Android Companion App

Sync your WiZ smart bulbs with content playing on your Android device.

## Features

- 📱 Real-time screen color sync using MediaProjection API
- 🎨 Same color extraction algorithm as desktop
- ⚡ Configurable FPS (4-30)
- 🔧 Color boost and brightness settings
- 🔌 WebSocket connection to WizLight server

## Requirements

- Android 7.0+ (API 24+)
- Flutter 3.0+
- WizLight server running on your PC

## Setup

### 1. Install Flutter

Follow the [Flutter installation guide](https://docs.flutter.dev/get-started/install).

### 2. Build the app

```bash
cd wizlight-android
flutter pub get
flutter build apk --release
```

The APK will be at `build/app/outputs/flutter-apk/app-release.apk`.

### 3. Install on your device

```bash
adb install build/app/outputs/flutter-apk/app-release.apk
```

Or transfer the APK to your device and install manually.

## Usage

1. **Start the server** on your PC:
   ```bash
   wizlight serve
   ```

2. **Open WizLight Sync** on your Android device

3. **Enter your PC's IP address** in Settings
   - Find your PC's IP with `ipconfig` (Windows) or `ip addr` (Linux)
   - Example: `ws://192.168.1.100:38901`

4. **Tap Connect** to establish connection

5. **Tap Start Sync** to begin screen capture
   - Grant screen capture permission when prompted
   - A notification will appear while syncing

6. **Play a video** and watch your lights!

## Settings

| Setting | Description | Range |
|---------|-------------|-------|
| Server URL | WebSocket server address | ws://IP:38901 |
| FPS | Capture frame rate | 4-30 |
| Color Boost | Saturation enhancement | 1.0-2.0 |
| Min Brightness | Minimum color brightness | 0-128 |
| Auto-connect | Connect on app start | On/Off |

## Architecture

```
lib/
├── main.dart                    # App entry point
├── screens/
│   ├── home_screen.dart         # Main UI
│   └── settings_screen.dart     # Settings UI
└── services/
    ├── sync_service.dart        # WebSocket connection
    ├── settings_service.dart    # Persistent settings
    └── screen_capture_service.dart  # Native bridge

android/.../kotlin/com/wizlight/app/
├── MainActivity.kt              # Flutter activity
└── ScreenCaptureService.kt      # MediaProjection service
```

## Battery Considerations

Screen capture uses significant battery. Tips:

- Use lower FPS (8-12) for better battery life
- Stop sync when not watching videos
- Adaptive FPS automatically reduces rate for static content

## Troubleshooting

### "Connection error"
- Ensure PC and phone are on the same WiFi network
- Check that `wizlight serve` is running
- Verify the IP address is correct
- Check firewall allows port 38901

### "Screen capture failed"
- Grant screen capture permission
- Restart the app if permission was denied
- Some apps (Netflix, banking) block screen capture

### Colors don't match well
- Increase Color Boost for more vivid colors
- Adjust Min Brightness for dark scenes
- Ensure room lighting isn't interfering

## License

MIT License - Same as WizLight main project
