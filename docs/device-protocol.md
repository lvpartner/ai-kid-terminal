# Device Protocol v1

Connect to `GET /v1/device/ws` with `Authorization: Bearer <device-token>` and subprotocol `kid-terminal.v1`. The server accepts protocol version 1 and returns `session.ready` with `session_id`, opaque `resume_token`, and audio formats. Tokens must never appear in URL query strings.

## Audio and Events

Input binary frames are PCM signed 16-bit little-endian, 16 kHz, mono. Clients should send
`X-Audio-Codecs: g711_ulaw_8000,pcm_s16le_24000`; the server then returns G.711 mu-law, 8 kHz,
mono binary frames and reports `g711_ulaw_8000_mono` in `session.ready`. This reduces the wire rate
from 384 kbit/s to 64 kbit/s. Clients without that header receive legacy PCM signed 16-bit
little-endian, 24 kHz, mono. Recommended input frames are 20-100 ms. JSON events require a unique
`event_id`; the server retains the latest 1,000 IDs and acknowledges duplicates without replaying them.
The server sends the first compressed frame immediately, permits about 500 ms of initial buffering,
then paces compressed audio at its real-time 8 KB/s playback rate to prevent slow-link queue buildup.

Client sequence:

```json
{"type":"speech.start","event_id":"uuid","text_hint":"optional redacted transcript"}
<binary audio frames>
{"type":"speech.stop","event_id":"uuid"}
```

Server sequence: `speech.started`, `ai.response.started`, zero or more `ai.text.delta` and binary audio frames, then `ai.response.done`. Send `{"type":"interrupt","event_id":"uuid"}` at any time during output; expect `interrupt.ack`, `ai.response.interrupted`, then `ai.response.done`.

Heartbeat is `{"type":"heartbeat","event_id":"uuid"}` and returns `heartbeat.ack`. Send it at the configured interval. A connection idle for 45 seconds closes. To resume context after reconnect, send `session.resume` with the previous `resume_token`; the token is accepted only for the same authenticated device.

## Failure Behavior

Errors use `{"type":"error","code":"...","retryable":true|false}`. Close codes are `4401` authentication, `4400` malformed/idle, `4409` oversized, `4429` rate limit, and `4511` upstream failure. Back off reconnects with jitter, cache the last valid remote config, and never retry indefinitely. Limits default to 1 MiB per message, 50 events/second, a 5-second slow-client write timeout, and a 45-second idle timeout.

Online devices receive `config.changed` with the new version and prompt version after an audited admin update. Fetch the full config through REST with ETag caching; retain the previous valid config if that fetch fails. Version 1 clients should ignore unknown JSON fields and unknown server events.
