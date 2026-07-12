import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import websockets
from websockets.typing import Subprotocol


def event(event_type: str) -> str:
    return json.dumps({"type": event_type, "event_id": str(uuid.uuid4())})


async def main() -> None:
    input_path = Path(os.getenv("QWEN_TEST_PCM", "data/qwen-test-input.pcm"))
    if not input_path.is_file():
        raise SystemExit(f"PCM input not found: {input_path}")
    base_url = os.getenv("BASE_URL", "http://127.0.0.1:8000")
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if not admin_key:
        raise SystemExit("ADMIN_API_KEY is required")

    async with httpx.AsyncClient(base_url=base_url, timeout=20) as api:
        enrollment = await api.post(
            "/v1/admin/enrollments",
            headers={"X-Admin-Key": admin_key},
            json={"label": "qwen-direct-e2e", "expires_minutes": 5},
        )
        enrollment.raise_for_status()
        registered = await api.post(
            "/v1/enroll",
            json={
                "enrollment_token": enrollment.json()["enrollment_token"],
                "device_name": "qwen-direct-e2e",
                "app_version": "0.1.0",
                "os_version": "synthetic-pcm-test",
            },
        )
        registered.raise_for_status()
        token = registered.json()["access_token"]

    parsed = urlparse(base_url)
    ws_url = f"ws://{parsed.netloc}/v1/device/ws"
    output = bytearray()
    answer_text = ""
    control_event_counts: dict[str, int] = {}
    started = time.monotonic()
    turn_started = started
    first_audio_ms: int | None = None
    async with websockets.connect(
        ws_url,
        additional_headers={
            "Authorization": f"Bearer {token}",
            "X-Audio-Codecs": "g711_ulaw_8000,pcm_s16le_24000",
        },
        subprotocols=[Subprotocol("kid-terminal.v1")],
        max_size=2 * 1024 * 1024,
    ) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        if ready.get("type") != "session.ready":
            raise RuntimeError("device session did not become ready")
        output_codec = ready["audio"]["output"]
        await ws.send(event("speech.start"))
        await ws.recv()
        pcm = input_path.read_bytes()
        for offset in range(0, len(pcm), 3_200):
            await ws.send(pcm[offset : offset + 3_200])
            await asyncio.sleep(0.1)
        await ws.send(event("speech.stop"))
        turn_started = time.monotonic()
        while True:
            message = await asyncio.wait_for(ws.recv(), timeout=45)
            if isinstance(message, bytes):
                if first_audio_ms is None:
                    first_audio_ms = int((time.monotonic() - turn_started) * 1000)
                output.extend(message)
                continue
            item = json.loads(message)
            event_type = str(item.get("type"))
            control_event_counts[event_type] = control_event_counts.get(event_type, 0) + 1
            if event_type == "ai.text.delta":
                answer_text += str(item.get("text", ""))
            if item.get("type") == "error":
                raise RuntimeError(f"voice E2E failed: {item.get('code')}")
            if item.get("type") == "ai.response.done":
                break

    if not output:
        raise RuntimeError("Qwen returned no audio")
    expected = os.getenv("EXPECTED_ANSWER_SUBSTRING", "")
    if expected and expected not in answer_text:
        raise RuntimeError("answer did not contain the expected synthetic-test result")
    destination = Path(
        "data/qwen-e2e-reply.ulaw"
        if output_codec == "g711_ulaw_8000_mono"
        else "data/qwen-e2e-reply.pcm"
    )
    destination.write_bytes(output)
    print(
        json.dumps(
            {
                "hybrid_audio": True,
                "input_pcm_bytes": input_path.stat().st_size,
                "output_codec": output_codec,
                "output_audio_bytes": len(output),
                "first_audio_ms": first_audio_ms,
                "answer_chars": len(answer_text),
                "expected_answer_matched": bool(expected),
                "control_event_counts": control_event_counts,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            },
            ensure_ascii=False,
        )
    )


asyncio.run(main())
