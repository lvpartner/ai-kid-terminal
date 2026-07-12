# Device Test Matrix

## Xiaomi 12 Baseline

The first physical target is a China-market Xiaomi 12 (`2201123C`, hardware `V1`) with
Android 15 and HyperOS build `3.0.3.0.VLCCNXM`. Its security patch level is `2026-05-01` and
kernel baseline is `5.10.236-android12-9-00003-gfb24cf99ad97-ab14313284`.

Relevant capacity is 12 GB RAM plus 4 GB memory extension, 256 GB storage (202.9 GB currently
available), a 4500 mAh battery, Snapdragon 8-class CPU, and a 2400 x 1080 display. The voice
terminal does not require or request camera access. IMEI, serial number, phone number, baseband
identity, and other unique hardware identifiers must not be collected, stored, or logged.

Before production enrollment, verify on the physical device:

- Factory reset permits `dpm set-device-owner` before any Xiaomi or Google account is added.
- HyperOS autostart and battery policies do not suspend heartbeat, WebSocket, microphone, or audio.
- Lock task survives reboot and provides a documented administrator recovery path.
- Microphone capture is 16 kHz mono PCM16 and speaker playback is 24 kHz mono PCM16 without echo.
- Wi-Fi loss/recovery, interruption, forced update, signing-certificate rejection, and rollback work.
- Debug builds are removed before provisioning the APK signed by the permanent release certificate.
