# Android Integration

Enroll once using a short-lived code and store the returned device token in Android Keystore-backed encrypted storage. Never place tokens in URLs or logs. Fetch `/v1/device/config` with `If-None-Match`; retain the last valid response when offline. Send REST and WebSocket bearer authentication from the kiosk process only.

Capture 16 kHz mono PCM16 and play 24 kHz mono PCM16 according to [device protocol](device-protocol.md). Use acoustic echo cancellation, request microphone focus, maintain a heartbeat, use bounded exponential reconnect with jitter, and send `interrupt` as soon as intentional new speech is detected. Do not persist raw audio.

Report only bounded, redacted device events. Crash reports must remove app secrets and child content. Device Owner provisioning, lock task mode, boot startup, network recovery, microphone permission UX, battery optimization exemption, and physical escape/recovery procedures belong in the Android phase.

For updates, compare `versionCode`, Android API compatibility, size, and SHA-256 before installation. The app must also pin the expected Android signing certificate digest and reject any APK signed by another key. Use PackageInstaller under Device Owner policy; never execute arbitrary downloaded code or shell commands.

The implementation is in `android/` with application ID `com.aikid.terminal`. Build and verify it with `make android-test`. It uses API 26 minimum, target API 36, compile API 37, Android Keystore AES-GCM token storage, OkHttp WebSocket, and a single full-screen voice button.

See [device test matrix](device-matrix.md). Registration and heartbeat report
only non-unique platform metadata (manufacturer, model, OS build, security patch), battery state,
and network type. Never request or report IMEI, serial number, phone number, or account identifiers.

## Signed Release Flow

The package is `com.aikid.terminal`. Its signing key is stored outside the repository in
`~/.config/ai-kid-terminal/` with mode `0600`; losing this key makes in-place upgrades impossible.
Build and publish later versions with the same key:

```bash
make android-release VERSION_CODE=2 VERSION_NAME=0.1.1
make android-publish VERSION_CODE=2 VERSION_NAME=0.1.1 NOTES="Pilot fixes" ROLLOUT=100
make android-personal-release VERSION_CODE=2 VERSION_NAME=0.1.1
make android-personal-publish VERSION_NAME=0.1.1
```

Public releases keep versioned GitHub download URLs. The personal production APK is atomically
replaced at `/install/ai-kid-terminal.apk`, so its download URL remains stable across versions. The
personal build requires and verifies its production server URL before it can be published.

Public builds do not embed a server or binding token; the runtime setup screen accepts both. The client
downloads only published compatible releases, verifies size, SHA-256, and the pinned signing
certificate, then commits through `PackageInstaller`. Device Owner supports unattended updates and
strong kiosk policy; compatible self-updates can also request installation without user action.

Always handle `STATUS_PENDING_USER_ACTION` because Android or an OEM policy can require confirmation.
WorkManager checks for a release immediately, every 6 hours as a fallback, and after a connection or
remote configuration
notification. The unique, network-constrained work survives process death and device reboot without
starting duplicate downloads. `MY_PACKAGE_REPLACED` starts the kiosk again after Android replaces the
running APK.
