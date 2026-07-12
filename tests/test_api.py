import hashlib
import json
import uuid
import zipfile
from pathlib import Path

import pytest

from kid_terminal.app import (
    file_sha256,
    is_android_apk,
    recent_turn_context,
    rollout_eligible,
    store_turn,
)
from kid_terminal.audio import G711Ulaw8kEncoder, RealtimeAudioPacer, linear16_to_ulaw
from kid_terminal.config import Settings
from kid_terminal.privacy import redact_private_text, summarize_messages
from kid_terminal.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from kid_terminal.schemas import ConfigUpdate


def event(kind: str, **values):
    return {"type": kind, "event_id": str(uuid.uuid4()), **values}


def test_health_version_and_metrics(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert client.get("/version").json()["protocol_versions"] == [1]
    assert "kid_terminal_http_requests_total" in client.get("/metrics").text


def test_g711_encoder_reduces_pcm_bandwidth_across_chunks():
    encoder = G711Ulaw8kEncoder()
    pcm = b"\x00\x00" * 24_000
    encoded = encoder.encode(pcm[:31]) + encoder.encode(pcm[31:])
    assert len(encoded) == 8_000
    assert set(encoded) == {0xFF}
    assert linear16_to_ulaw(32124) == 0x80
    assert linear16_to_ulaw(-32124) == 0x00


def test_audio_pacer_allows_half_second_then_enforces_wire_rate():
    pacer = RealtimeAudioPacer()
    assert pacer.delay_for(4_000, now=10.0) == 0
    pacer.record_sent(4_000)
    assert pacer.delay_for(4_000, now=10.0) == 0.5
    pacer.record_sent(4_000)
    assert pacer.delay_for(8_000, now=10.5) == 1.0


def test_admin_can_drain_new_responses(client, admin_headers):
    enabled = client.post("/v1/admin/drain", headers=admin_headers, params={"enabled": True}).json()
    assert enabled["draining"] is True
    assert client.get("/v1/admin/activity", headers=admin_headers).status_code == 200
    disabled = client.post(
        "/v1/admin/drain", headers=admin_headers, params={"enabled": False}
    ).json()
    assert disabled["draining"] is False


def test_growth_prompt_is_adaptive_and_versioned():
    assert PROMPT_VERSION == "family-zh-v1"
    assert "准确和诚实优先于有趣、具体和完整" in SYSTEM_PROMPT
    assert "整个回答只问一个最小澄清问题" in SYSTEM_PROMPT
    assert "不能为了让答案显得具体而补全" in SYSTEM_PROMPT
    assert "时效事实优先使用服务器当轮结构化或官方证据" in SYSTEM_PROMPT
    assert "模型记忆" in SYSTEM_PROMPT
    assert "例子、类比、故事、兴趣点、挑战和追问都不是" in SYSTEM_PROMPT
    assert "澄清或证据不足只能1句" in SYSTEM_PROMPT
    assert "不要说“我找一找”" in SYSTEM_PROMPT
    assert "删除所有说不出依据的句子" in SYSTEM_PROMPT


def test_remote_config_rejects_incompatible_qwen_voice():
    with pytest.raises(ValueError, match="voice is not supported"):
        ConfigUpdate(model="qwen3.5-omni-plus-realtime", voice="Cherry")
    assert ConfigUpdate(model="qwen3.5-omni-plus-realtime", voice="Ethan").voice == "Ethan"


@pytest.mark.asyncio
async def test_recent_context_keeps_eight_completed_turns_across_sessions(enrolled):
    from kid_terminal.db import SessionLocal

    async with SessionLocal() as db:
        for index in range(10):
            await store_turn(
                db,
                enrolled["device_id"],
                f"context-session-{index % 2}",
                f"问题{index}",
                f"答案{index}",
            )
        context = await recent_turn_context(db, enrolled["device_id"], turns=8)

    assert "问题0" not in context
    assert "答案1" not in context
    assert "孩子：问题2" in context
    assert "助手：答案9" in context
    assert context.count("孩子：") == 8
    assert context.count("助手：") == 8


def test_android_apk_structure_validation(tmp_path: Path):
    invalid = tmp_path / "invalid.apk"
    invalid.write_bytes(b"not a zip")
    assert not is_android_apk(invalid)

    valid = tmp_path / "valid.apk"
    with zipfile.ZipFile(valid, "w") as archive:
        for name in ("AndroidManifest.xml", "classes.dex", "resources.arsc"):
            archive.writestr(name, b"test")
    assert is_android_apk(valid)


def test_production_rejects_weak_secrets():
    try:
        Settings(environment="production", admin_api_key="weak", token_pepper="weak")
    except ValueError as exc:
        assert "32 characters" in str(exc)
    else:
        raise AssertionError("weak production secrets accepted")


def test_one_time_enrollment(client, admin_headers):
    token = client.post(
        "/v1/admin/enrollments", headers=admin_headers, json={"label": "once"}
    ).json()["enrollment_token"]
    payload = {"enrollment_token": token, "device_name": "one"}
    assert client.post("/v1/enroll", json=payload).status_code == 201
    assert client.post("/v1/enroll", json=payload).status_code == 401


def test_device_token_authentication(client, enrolled):
    headers = {"Authorization": f"Bearer {enrolled['access_token']}"}
    assert client.get("/v1/device/config", headers=headers).status_code == 200
    assert client.get("/v1/device/config").status_code == 401


def test_token_rotation_invalidates_old(client, admin_headers, enrolled):
    old = {"Authorization": f"Bearer {enrolled['access_token']}"}
    rotated = client.post(
        f"/v1/admin/devices/{enrolled['device_id']}/rotate", headers=admin_headers
    ).json()
    assert client.get("/v1/device/config", headers=old).status_code == 401
    new = {"Authorization": f"Bearer {rotated['access_token']}"}
    assert client.get("/v1/device/config", headers=new).status_code == 200


def test_token_revocation(client, admin_headers, enrolled):
    headers = {"Authorization": f"Bearer {enrolled['access_token']}"}
    response = client.post(
        f"/v1/admin/devices/{enrolled['device_id']}/revoke", headers=admin_headers
    )
    assert response.status_code == 200
    assert client.get("/v1/device/config", headers=headers).status_code == 401


def test_unauthorized_admin_request(client):
    assert client.get("/v1/admin/devices").status_code == 401
    assert client.put("/v1/admin/config", json={}).status_code == 401


def test_heartbeat_updates_status(client, admin_headers, enrolled):
    headers = {"Authorization": f"Bearer {enrolled['access_token']}"}
    response = client.post(
        "/v1/device/heartbeat",
        headers=headers,
        json={
            "app_version": "1.2.3",
            "os_version": "Android 15; HyperOS 3.0.3.0.VLCCNXM",
            "manufacturer": "Xiaomi",
            "device_model": "2201123C",
            "security_patch": "2026-05-01",
            "network_type": "wifi",
            "battery_percent": 55,
            "charging": True,
            "ws_state": "connected",
        },
    )
    assert response.status_code == 200
    status = client.get(
        f"/v1/admin/devices/{enrolled['device_id']}/status", headers=admin_headers
    ).json()
    assert status["device"]["app_version"] == "1.2.3"
    assert status["device"]["manufacturer"] == "Xiaomi"
    assert status["device"]["device_model"] == "2201123C"
    assert status["device"]["security_patch"] == "2026-05-01"


def test_remote_config_version_and_etag(client, admin_headers, enrolled):
    device_headers = {"Authorization": f"Bearer {enrolled['access_token']}"}
    before = client.get("/v1/device/config", headers=device_headers)
    assert (
        client.get(
            "/v1/device/config", headers={**device_headers, "If-None-Match": before.headers["etag"]}
        ).status_code
        == 304
    )
    config = before.json()["config"]
    config["speech_rate"] = 1.2
    updated = client.put("/v1/admin/config", headers=admin_headers, json=config)
    assert updated.json()["version"] == before.json()["version"] + 1


def test_telemetry_and_fault_filter(client, admin_headers, enrolled):
    headers = {"Authorization": f"Bearer {enrolled['access_token']}"}
    response = client.post(
        "/v1/device/telemetry",
        headers=headers,
        json={
            "event_type": "crash",
            "severity": "error",
            "crash_stack": "call 13800138000",
            "events": [{"authorization": "secret"}],
        },
    )
    assert response.status_code == 202
    faults = client.get(
        "/v1/admin/faults", headers=admin_headers, params={"device_id": enrolled["device_id"]}
    ).json()
    assert any(item["type"] == "crash" for item in faults)


def test_privacy_redaction():
    value = redact_private_text("手机13800138000，身份证110101199003070011，密码: abc")
    assert "13800138000" not in value
    assert "110101199003070011" not in value
    assert "abc" not in value


def test_automatic_summary_is_bounded_and_redacted():
    summary = summarize_messages([f"第{i}句 13800138000" for i in range(20)], max_chars=120)
    assert summary.startswith("对话摘要")
    assert len(summary) <= 120
    assert "13800138000" not in summary


def test_rollout_is_deterministic():
    device = "device-fixed-id"
    assert rollout_eligible(device, 100)
    assert not rollout_eligible(device, 0)
    assert rollout_eligible(device, 42.5) == rollout_eligible(device, 42.5)


def test_release_publish_select_download_and_hash(client, admin_headers, enrolled):
    artifact = b"safe-test-artifact"
    metadata = {
        "version_code": 1001,
        "version_name": "1.0.1-test",
        "min_android": 26,
        "rollout_percent": 100,
        "channel": "stable",
    }
    upload = client.post(
        "/v1/admin/releases",
        headers=admin_headers,
        data={"metadata": json.dumps(metadata)},
        files={"file": ("test.bin", artifact)},
    )
    assert upload.status_code == 201
    assert upload.json()["sha256"] == hashlib.sha256(artifact).hexdigest()
    assert client.post("/v1/admin/releases/1001/publish", headers=admin_headers).status_code == 200
    device_headers = {"Authorization": f"Bearer {enrolled['access_token']}"}
    latest = client.get(
        "/v1/device/releases/latest",
        headers=device_headers,
        params={"android_api": 35, "current_version_code": 1},
    ).json()["update"]
    downloaded = client.get(latest["download_url"], headers=device_headers)
    assert downloaded.content == artifact
    assert downloaded.headers["x-content-sha256"] == latest["sha256"]
    partial = client.get(latest["download_url"], headers={**device_headers, "Range": "bytes=0-3"})
    assert partial.status_code == 206
    assert partial.content == artifact[:4]
    assert partial.headers["content-range"].startswith("bytes 0-3/")


def test_unpublished_release_is_hidden(client, admin_headers, enrolled):
    client.post(
        "/v1/admin/releases",
        headers=admin_headers,
        data={"metadata": json.dumps({"version_code": 2001, "version_name": "draft"})},
        files={"file": ("draft.bin", b"draft")},
    )
    headers = {"Authorization": f"Bearer {enrolled['access_token']}"}
    assert client.get("/v1/device/releases/2001/download", headers=headers).status_code == 404


def test_beta_release_uses_same_artifact_when_promoted(client, admin_headers, enrolled):
    metadata = {"version_code": 3001, "version_name": "beta", "channel": "beta"}
    upload = client.post(
        "/v1/admin/releases",
        headers=admin_headers,
        data={"metadata": json.dumps(metadata)},
        files={"file": ("beta.bin", b"one-artifact")},
    )
    digest = upload.json()["sha256"]
    assert client.post("/v1/admin/releases/3001/publish", headers=admin_headers).status_code == 200
    device_headers = {"Authorization": f"Bearer {enrolled['access_token']}"}
    params = {"android_api": 35, "current_version_code": 2000}
    assert (
        client.get("/v1/device/releases/latest", headers=device_headers, params=params).json()[
            "update"
        ]
        is None
    )
    changed = client.post(
        f"/v1/admin/devices/{enrolled['device_id']}/update-channel",
        headers=admin_headers,
        json={"channel": "beta"},
    )
    assert changed.json()["update_channel"] == "beta"
    beta = client.get("/v1/device/releases/latest", headers=device_headers, params=params).json()[
        "update"
    ]
    assert beta["sha256"] == digest and beta["channel"] == "beta"
    promoted = client.post("/v1/admin/releases/3001/promote", headers=admin_headers).json()
    assert promoted["channel"] == "stable"
    assert beta["sha256"] == digest


def test_file_hash_streams_large_files(tmp_path):
    path = tmp_path / "large.bin"
    content = b"0123456789abcdef" * 200_000
    path.write_bytes(content)
    assert file_sha256(path, chunk_size=4096) == hashlib.sha256(content).hexdigest()
