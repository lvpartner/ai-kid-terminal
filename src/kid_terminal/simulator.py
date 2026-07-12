import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import typer
import websockets
from websockets.typing import Subprotocol

app = typer.Typer(help="Simulated Android voice terminal")


def eid() -> str:
    return str(uuid.uuid4())


async def voice_round(base_url: str, token: str, interrupt: bool, output: Path) -> dict:
    parsed = urlparse(base_url)
    ws_url = f"ws://{parsed.netloc}/v1/device/ws"
    audio = bytearray()
    events: list[str] = []
    session_id = ""
    async with websockets.connect(
        ws_url,
        additional_headers={"Authorization": f"Bearer {token}"},
        subprotocols=[Subprotocol("kid-terminal.v1")],
    ) as ws:
        ready = json.loads(await ws.recv())
        session_id = ready["session_id"]
        await ws.send(json.dumps({"type": "heartbeat", "event_id": eid()}))
        await ws.recv()
        await ws.send(
            json.dumps(
                {
                    "type": "speech.start",
                    "event_id": eid(),
                    "text_hint": "请用简单的话解释为什么天空是蓝色的，电话13800138000不要保存",
                },
                ensure_ascii=False,
            )
        )
        await ws.recv()
        await ws.send(b"SIMULATED-PCM16-AUDIO" * 8)
        await ws.send(json.dumps({"type": "speech.stop", "event_id": eid()}))
        sent_interrupt = False
        while True:
            message = await asyncio.wait_for(ws.recv(), timeout=5)
            if isinstance(message, bytes):
                audio.extend(message)
                continue
            event = json.loads(message)
            events.append(event["type"])
            if interrupt and event["type"] == "ai.response.started" and not sent_interrupt:
                await ws.send(json.dumps({"type": "interrupt", "event_id": eid()}))
                sent_interrupt = True
            if event["type"] == "ai.response.done":
                break
    output.write_bytes(audio)
    return {"session_id": session_id, "events": events, "audio_bytes": len(audio)}


async def run_demo(base_url: str) -> dict:
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if not admin_key:
        raise RuntimeError("ADMIN_API_KEY is required; run make setup and source .env")
    state_dir = Path("data")
    state_dir.mkdir(exist_ok=True)
    headers = {"X-Admin-Key": admin_key}
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as api:
        enrollment = (
            await api.post("/v1/admin/enrollments", headers=headers, json={"label": "simulator"})
        ).json()
        registered_response = await api.post(
            "/v1/enroll",
            json={
                "enrollment_token": enrollment["enrollment_token"],
                "device_name": "simulated-android",
                "app_version": "0.1.0",
                "os_version": "HyperOS-simulated",
            },
        )
        registered_response.raise_for_status()
        registered = registered_response.json()
        token = registered["access_token"]
        device_id = registered["device_id"]
        state_dir.joinpath("simulator-token.json").write_text(
            json.dumps({"device_id": device_id, "access_token": token}), encoding="utf-8"
        )
        device_headers = {"Authorization": f"Bearer {token}"}
        config_response = await api.get("/v1/device/config", headers=device_headers)
        config_response.raise_for_status()
        etag = config_response.headers["etag"]
        cache_response = await api.get(
            "/v1/device/config", headers={**device_headers, "If-None-Match": etag}
        )
        heartbeat = await api.post(
            "/v1/device/heartbeat",
            headers=device_headers,
            json={
                "app_version": "0.1.0",
                "os_version": "HyperOS-simulated",
                "network_type": "wifi",
                "battery_percent": 87,
                "charging": False,
                "ws_state": "connected",
            },
        )
        heartbeat.raise_for_status()

        first = await voice_round(base_url, token, False, state_dir / "mock-reply.pcm")
        interrupted = await voice_round(base_url, token, True, state_dir / "mock-interrupted.pcm")

        telemetry = await api.post(
            "/v1/device/telemetry",
            headers=device_headers,
            json={
                "session_id": first["session_id"],
                "event_type": "crash",
                "severity": "error",
                "first_packet_ms": 42,
                "first_audio_ms": 95,
                "turn_total_ms": 210,
                "reconnect_count": 1,
                "crash_stack": "SyntheticCrash at Simulator:1 phone=13800138000",
                "events": [{"type": "reconnect", "token": "must-not-persist"}],
            },
        )
        telemetry.raise_for_status()
        config = config_response.json()["config"]
        config["speech_rate"] = 1.1
        updated = await api.put("/v1/admin/config", headers=headers, json=config)
        updated.raise_for_status()
        refreshed = await api.get("/v1/device/config", headers=device_headers)

        artifact = b"Harmless test update artifact; not an Android APK.\n"
        metadata = {
            "version_code": 2,
            "version_name": "0.1.1-test",
            "min_android": 26,
            "rollout_percent": 100,
            "release_notes": "E2E harmless test artifact",
        }
        upload = await api.post(
            "/v1/admin/releases",
            headers=headers,
            data={"metadata": json.dumps(metadata)},
            files={"file": ("test-update.bin", artifact, "application/octet-stream")},
        )
        if upload.status_code == 409:
            pass
        else:
            upload.raise_for_status()
        publish = await api.post("/v1/admin/releases/2/publish", headers=headers)
        publish.raise_for_status()
        latest = await api.get(
            "/v1/device/releases/latest",
            headers=device_headers,
            params={"android_api": 35, "current_version_code": 1},
        )
        latest.raise_for_status()
        update = latest.json()["update"]
        downloaded = await api.get(update["download_url"], headers=device_headers)
        downloaded.raise_for_status()
        sha_ok = hashlib.sha256(downloaded.content).hexdigest() == update["sha256"]
        state_dir.joinpath("test-update.bin").write_bytes(downloaded.content)
        status = await api.get(f"/v1/admin/devices/{device_id}/status", headers=headers)
        faults = await api.get("/v1/admin/faults", headers=headers, params={"device_id": device_id})
        return {
            "device_id": device_id,
            "config_cache_304": cache_response.status_code == 304,
            "config_version": refreshed.json()["version"],
            "conversation": first,
            "interrupt": interrupted,
            "telemetry_accepted": telemetry.status_code == 202,
            "fault_count": len(faults.json()),
            "release_sha256_verified": sha_ok,
            "latest_status_available": status.status_code == 200,
        }


@app.command()
def demo(base_url: str = "http://127.0.0.1:8000"):
    """Run enrollment, voice, interrupt, telemetry, config, and update flows."""
    result = asyncio.run(run_demo(base_url))
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
