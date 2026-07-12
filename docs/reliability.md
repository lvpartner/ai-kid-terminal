# Reliability Gates

Voice releases are blocked unless `make release-gate` passes. The gate includes formatting, static
analysis, server tests, Android unit tests, Android Lint, and a signed-code-compatible debug build.
Set `REAL_QWEN_GATE=1` for a production release candidate to require a live Qwen voice round.

## Transport Budgets

- First Qwen audio should normally arrive within 2 seconds; a local prompt starts after 1 second.
- Negotiated output is G.711 mu-law at 8 kHz: 8 KB/s, 64 kbit/s, mono.
- The server may burst 4 KB for a 500 ms startup buffer, then must pace at real-time wire speed.
- A 60-second answer must remain below 484 KB of binary audio and retain heartbeat responsiveness.
- The regression suite generates audio faster than real time, verifies pacing, sends a heartbeat
  during output, and requires `ai.response.done` without a slow-client disconnect.

## Failure Matrix

| Failure | Required behavior |
| --- | --- |
| Qwen capture connection drops | Buffer locally, reconnect once, replay once |
| Qwen fails before output | One bounded retry, then local spoken error feedback |
| Device WebSocket closes with any code | Reconnect unless the app is shutting down |
| API deploy during a response | Drain active responses before container replacement |
| Activity or app process exits | WorkManager retains signed update checks |
| APK replaces the running app | `MY_PACKAGE_REPLACED` starts the kiosk again |
| Network is slower than Qwen generation | Compress and pace; never enqueue raw PCM bursts |

## Release Procedure

1. Run `make release-gate` and `REAL_QWEN_GATE=1 make release-gate`.
2. Build and verify the signed APK, then publish it at 100% only for the enrolled pilot device.
3. Confirm the device reports the new version, negotiated codec, first-audio latency, wire bytes,
   frame count, queued PCM bytes, and underruns.
4. Complete a short answer, a 30-60 second searched answer, interruption, network reconnect, and
   unattended update. Do not declare success from a simulator-only run.
5. Keep the previous signed release available as the rollback target.

Full kiosk persistence still requires provisioning the app as Device Owner. A normal installed app
cannot guarantee lock task, background activity launch, or OEM-suppressed process survival.

Update discovery is event-driven on app start, WebSocket connection, and configuration notification.
The persistent WorkManager job runs only every 6 hours as a recovery fallback, avoiding needless
15-minute wakeups while bounding offline update discovery to four checks per day.
