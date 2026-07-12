import json
import time
import uuid

from starlette.websockets import WebSocketDisconnect

import kid_terminal.app as app_module
from kid_terminal.providers import MockRealtimeProvider


def event(kind: str, **values):
    return {"type": kind, "event_id": str(uuid.uuid4()), **values}


def ws_headers(token: str):
    return {"Authorization": f"Bearer {token}"}


def complete_round(client, token: str):
    events = []
    audio = bytearray()
    with client.websocket_connect(
        "/v1/device/ws", headers=ws_headers(token), subprotocols=["kid-terminal.v1"]
    ) as ws:
        ready = ws.receive_json()
        ws.send_json(event("speech.start", text_hint="为什么天空是蓝色的"))
        assert ws.receive_json()["type"] == "speech.started"
        ws.send_bytes(b"PCM" * 20)
        ws.send_json(event("speech.stop"))
        while True:
            message = ws.receive()
            if message.get("bytes") is not None:
                audio.extend(message["bytes"])
            elif message.get("text"):
                item = json.loads(message["text"])
                events.append(item["type"])
                if item["type"] == "ai.response.done":
                    break
    return ready, events, audio


def test_websocket_rejects_missing_auth(client):
    try:
        with client.websocket_connect("/v1/device/ws") as ws:
            ws.receive_json()
    except WebSocketDisconnect as exc:
        assert exc.code == 4401


def test_websocket_heartbeat_and_duplicate_id(client, enrolled):
    with client.websocket_connect(
        "/v1/device/ws",
        headers=ws_headers(enrolled["access_token"]),
        subprotocols=["kid-terminal.v1"],
    ) as ws:
        assert ws.receive_json()["type"] == "session.ready"
        item = event("heartbeat")
        ws.send_json(item)
        assert ws.receive_json()["type"] == "heartbeat.ack"
        ws.send_json(item)
        duplicate = ws.receive_json()
        assert duplicate["type"] == "event.ack"
        assert duplicate["duplicate"] is True


def test_websocket_negotiates_low_bandwidth_audio(client, enrolled):
    headers = ws_headers(enrolled["access_token"])
    headers["X-Audio-Codecs"] = "g711_ulaw_8000,pcm_s16le_24000"
    with client.websocket_connect(
        "/v1/device/ws", headers=headers, subprotocols=["kid-terminal.v1"]
    ) as ws:
        ready = ws.receive_json()
        assert ready["audio"]["output"] == "g711_ulaw_8000_mono"


def test_long_audio_is_paced_and_heartbeat_remains_responsive(client, enrolled, monkeypatch):
    class LongAudioProvider(MockRealtimeProvider):
        async def respond(self, session):
            yield "text", "长回答传输测试"
            chunk = b"\x00\x00" * 2_400
            for _ in range(100):
                yield "audio", chunk

    monkeypatch.setattr(app_module, "create_provider", lambda settings: LongAudioProvider())
    headers = ws_headers(enrolled["access_token"])
    headers["X-Audio-Codecs"] = "g711_ulaw_8000,pcm_s16le_24000"
    audio_bytes = 0
    heartbeat_sent = False
    heartbeat_ack = False
    started = time.monotonic()
    with client.websocket_connect(
        "/v1/device/ws", headers=headers, subprotocols=["kid-terminal.v1"]
    ) as ws:
        ws.receive_json()
        ws.send_json(event("speech.start"))
        ws.receive_json()
        ws.send_bytes(b"\x00\x00" * 1_600)
        ws.send_json(event("speech.stop"))
        while True:
            message = ws.receive()
            if message.get("bytes") is not None:
                audio_bytes += len(message["bytes"])
                if audio_bytes >= 8_000 and not heartbeat_sent:
                    ws.send_json(event("heartbeat"))
                    heartbeat_sent = True
                continue
            if not message.get("text"):
                continue
            item = json.loads(message["text"])
            heartbeat_ack |= item["type"] == "heartbeat.ack"
            if item["type"] == "ai.response.done":
                break

    assert audio_bytes == 80_000
    assert time.monotonic() - started >= 9.4
    assert heartbeat_ack


def test_online_device_receives_config_change(client, admin_headers, enrolled):
    with client.websocket_connect(
        "/v1/device/ws",
        headers=ws_headers(enrolled["access_token"]),
        subprotocols=["kid-terminal.v1"],
    ) as ws:
        ws.receive_json()
        device_headers = {"Authorization": f"Bearer {enrolled['access_token']}"}
        config = client.get("/v1/device/config", headers=device_headers).json()["config"]
        config["speech_rate"] = 1.3
        updated = client.put("/v1/admin/config", headers=admin_headers, json=config)
        assert updated.status_code == 200
        notification = ws.receive_json()
        assert notification["type"] == "config.changed"
        assert notification["version"] == updated.json()["version"]


def test_mock_conversation_streams_audio(client, enrolled):
    _, events, audio = complete_round(client, enrolled["access_token"])
    assert "ai.response.started" in events
    assert "ai.text.delta" in events
    assert "ai.response.done" in events
    assert audio.startswith(b"MOCK-PCM16")


def test_user_interrupt(client, enrolled):
    types = []
    with client.websocket_connect(
        "/v1/device/ws",
        headers=ws_headers(enrolled["access_token"]),
        subprotocols=["kid-terminal.v1"],
    ) as ws:
        ws.receive_json()
        ws.send_json(event("speech.start"))
        ws.receive_json()
        ws.send_bytes(b"PCM" * 100)
        ws.send_json(event("speech.stop"))
        while True:
            message = ws.receive()
            if message.get("text"):
                item = json.loads(message["text"])
                types.append(item["type"])
                if item["type"] == "ai.response.started":
                    ws.send_json(event("interrupt"))
                if item["type"] == "interrupt.ack":
                    break
    assert "interrupt.ack" in types
    assert "ai.response.interrupted" in types


def test_interrupt_then_immediately_ask_new_question(client, enrolled):
    second_audio = bytearray()
    with client.websocket_connect(
        "/v1/device/ws",
        headers=ws_headers(enrolled["access_token"]),
        subprotocols=["kid-terminal.v1"],
    ) as ws:
        ws.receive_json()
        ws.send_json(event("speech.start", text_hint="第一个问题"))
        ws.receive_json()
        ws.send_bytes(b"PCM" * 100)
        ws.send_json(event("speech.stop"))
        while True:
            message = ws.receive()
            if not message.get("text"):
                continue
            item = json.loads(message["text"])
            if item["type"] == "ai.response.started":
                ws.send_json(event("interrupt"))
            if item["type"] == "interrupt.ack":
                break

        ws.send_json(event("speech.start", text_hint="第二个问题"))
        assert ws.receive_json()["type"] == "speech.started"
        ws.send_bytes(b"PCM" * 100)
        ws.send_json(event("speech.stop"))
        while True:
            message = ws.receive()
            if message.get("bytes") is not None:
                second_audio.extend(message["bytes"])
                continue
            if message.get("text"):
                item = json.loads(message["text"])
                if item["type"] == "ai.response.done":
                    break
    assert second_audio.startswith(b"MOCK-PCM16")


def test_disconnect_reconnect_and_resume(client, enrolled):
    ready, _, _ = complete_round(client, enrolled["access_token"])
    with client.websocket_connect(
        "/v1/device/ws",
        headers=ws_headers(enrolled["access_token"]),
        subprotocols=["kid-terminal.v1"],
    ) as ws:
        ws.receive_json()
        ws.send_json(event("session.resume", resume_token=ready["resume_token"]))
        resumed = ws.receive_json()
        assert resumed["type"] == "session.resumed"
        assert resumed["session_id"] == ready["session_id"]
