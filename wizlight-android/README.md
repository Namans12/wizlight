# WizLight Android Tablet Sync

Sync up to 2 WiZ bulbs directly from your Android tablet without routing through the PC app.

## What Changed

- Direct local WiZ discovery on the tablet over UDP
- Manual add-by-IP fallback when discovery is blocked
- On-device screen sync using MediaProjection
- Smarter single-bulb cinematic extraction for movies and video
- Left/right zone sync for 2-bulb ambient setups
- Adaptive FPS, smoothing, letterbox rejection, and runtime status in the UI

## Requirements

- Android 7.0+ (API 24+)
- WiZ bulbs on the same Wi-Fi network as the tablet
- `Local communication` enabled in the WiZ app

## Build

```bash
cd wizlight-android
flutter pub get
flutter test
flutter build apk --debug
```

Debug APK output:

```text
build/app/outputs/apk/debug/app-debug.apk
```

## Install

```bash
adb install build/app/outputs/apk/debug/app-debug.apk
```

## Usage

1. Open the app on the tablet.
2. Tap `Discover` to find WiZ bulbs on the local network.
3. If discovery misses a bulb, use `Add IP`.
4. Assign up to 2 bulbs to `Left` and `Right`.
5. Pick `Single` or `Left + Right` mode.
6. Tap `Start Tablet Sync` and grant screen capture permission.

## Tuning

- `Single` mode is best for 1 bulb and is tuned for richer movie ambience.
- `Left + Right` is for 2-bulb ambient setups.
- Raise `Max FPS` for faster matching.
- Raise `Color Boost` for stronger saturation.
- Keep `Ignore Letterbox Bars` on for movies.

## Architecture

```text
lib/
├── models/wiz_bulb.dart
├── screens/home_screen.dart
├── screens/settings_screen.dart
├── services/wiz_bulb_service.dart
├── services/sync_service.dart
├── services/settings_service.dart
└── services/screen_capture_service.dart

android/app/src/main/kotlin/com/wizlight/app/
├── MainActivity.kt
└── ScreenCaptureService.kt
```

## Troubleshooting

### No bulbs discovered

- Confirm `Local communication` is enabled in the WiZ app.
- Make sure the tablet is on the same Wi-Fi as the bulbs.
- Try `Add IP` using the IP shown in the WiZ app.

### Screen capture starts but lights do not react

- Recheck that the bulbs were discovered or added locally in the app.
- Verify the bulbs still respond in the WiZ app on the same Wi-Fi.
- Some DRM-heavy apps can block capture or return black frames.

### Build fails on Windows

- If Gradle/Kotlin cache files get stuck, stop Gradle and clear the app `build/` folder:

```powershell
cd wizlight-android\android
.\gradlew.bat --stop
cd ..
Remove-Item build -Recurse -Force
```
