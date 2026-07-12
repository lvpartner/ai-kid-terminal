import base64
import json

import pytest

from kid_terminal.config import Settings
from kid_terminal.providers import (
    BufferedAudioProvider,
    MockRealtimeProvider,
    ProviderError,
    ProviderSession,
    QwenRealtimeProvider,
    create_provider,
    parse_qwen_event,
)


async def test_hybrid_provider_only_buffers_one_turn_in_memory():
    provider = create_provider(
        Settings(
            ai_provider="hybrid",
            dashscope_api_key="asr-key",
            deepseek_api_key="answer-key",
        )
    )
    assert isinstance(provider, BufferedAudioProvider)
    session = await provider.open({})
    await provider.append_audio(session, b"pcm")
    assert bytes(session.audio) == b"pcm"
    assert [item async for item in provider.respond(session)] == []
    await provider.close(session)
    assert session.audio == b""


async def test_mock_provider_error():
    with pytest.raises(RuntimeError, match="simulated"):
        await MockRealtimeProvider().open({"mock_error": True})


async def test_mock_provider_interrupt():
    provider = MockRealtimeProvider()
    session = await provider.open({})
    await provider.append_audio(session, b"audio")
    await provider.interrupt(session)
    results = [item async for item in provider.respond(session)]
    assert results[-1][0] == "interrupted"


async def test_mock_provider_context_and_new_turn():
    provider = MockRealtimeProvider()
    session = await provider.open({"memory_context": "旧摘要"})
    assert session.context == "旧摘要"
    await provider.interrupt(session)
    await provider.start_turn(session)
    assert not session.interrupted.is_set()


def test_qwen_event_parser():
    assert parse_qwen_event(
        {"type": "response.audio.delta", "delta": base64.b64encode(b"pcm").decode()}
    ) == ("audio", b"pcm")
    assert parse_qwen_event(
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "你好"}
    ) == ("user_text", "你好")
    assert parse_qwen_event({"type": "response.audio.done"}) == ("audio_done", None)
    assert parse_qwen_event({"type": "response.output_item.done"}) == ("output_done", None)
    assert parse_qwen_event({"type": "response.done"})[0] == "done"


def test_qwen_event_parser_rejects_invalid_audio_and_classifies_errors():
    with pytest.raises(ProviderError, match="invalid audio"):
        parse_qwen_event({"type": "response.audio.delta", "delta": "%%%"})
    with pytest.raises(ProviderError) as raised:
        parse_qwen_event({"type": "error", "error": {"code": "rate_limit_exceeded"}})
    assert raised.value.retryable
    with pytest.raises(ProviderError) as common:
        parse_qwen_event({"type": "error", "error": {"code": "COMMON_ERROR"}})
    assert common.value.retryable


class FakeConnection:
    def __init__(
        self,
        events=None,
        failure: Exception | None = None,
        send_failure: Exception | None = None,
    ):
        self.events = list(events or [])
        self.failure = failure
        self.send_failure = send_failure
        self.sent: list[dict] = []

    async def send(self, raw: str):
        if self.send_failure:
            raise self.send_failure
        self.sent.append(json.loads(raw))

    async def recv(self):
        if self.events:
            return json.dumps(self.events.pop(0))
        if self.failure:
            raise self.failure
        raise RuntimeError("fake connection has no more events")

    async def close(self):
        return None


async def test_qwen_reconnects_and_replays_only_before_output(monkeypatch):
    provider = QwenRealtimeProvider(
        Settings(dashscope_api_key="not-a-real-key", qwen_event_timeout_seconds=5)
    )
    failed = FakeConnection(failure=OSError("disconnected"))
    recovered = FakeConnection(
        [
            {"type": "response.audio_transcript.delta", "delta": "恢复成功"},
            {"type": "response.done"},
        ]
    )
    session = ProviderSession(upstream=failed, config={}, audio=bytearray(b"audio"))

    async def restart(target):
        target.upstream = recovered

    monkeypatch.setattr(provider, "_restart_before_output", restart)
    result = [item async for item in provider.respond(session)]
    assert result == [("text", "恢复成功")]
    assert [message["type"] for message in recovered.sent] == [
        "input_audio_buffer.commit",
        "response.create",
    ]


async def test_qwen_does_not_retry_account_rejection(monkeypatch):
    provider = QwenRealtimeProvider(Settings(dashscope_api_key="not-a-real-key"))
    failed = FakeConnection(failure=OSError("Access denied, account not in good standing"))
    session = ProviderSession(upstream=failed, config={}, audio=bytearray(b"audio"))
    restarts = 0

    async def restart(_target):
        nonlocal restarts
        restarts += 1

    monkeypatch.setattr(provider, "_restart_before_output", restart)
    with pytest.raises(ProviderError) as raised:
        _ = [item async for item in provider.respond(session)]
    assert raised.value.code == "account_unavailable"
    assert not raised.value.retryable
    assert restarts == 0
    assert provider.runtime_status == "degraded"


async def test_qwen_user_transcript_does_not_block_retry(monkeypatch):
    provider = QwenRealtimeProvider(Settings(dashscope_api_key="not-a-real-key"))
    failed = FakeConnection(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "最新赛程",
            }
        ],
        failure=OSError("disconnected before answer"),
    )
    recovered = FakeConnection(
        [
            {"type": "response.audio_transcript.delta", "delta": "恢复成功"},
            {"type": "response.done"},
        ]
    )
    session = ProviderSession(upstream=failed, config={}, audio=bytearray(b"audio"))

    async def restart(target):
        target.upstream = recovered

    monkeypatch.setattr(provider, "_restart_before_output", restart)
    result = [item async for item in provider.respond(session)]
    assert result == [("user_text", "最新赛程"), ("text", "恢复成功")]


async def test_qwen_buffers_audio_when_upstream_drops_during_capture(monkeypatch):
    provider = QwenRealtimeProvider(Settings(dashscope_api_key="not-a-real-key"))
    failed = FakeConnection(send_failure=OSError("capture disconnected"))
    recovered = FakeConnection([{"type": "response.done"}])
    session = ProviderSession(upstream=failed, config={})

    async def restart(target):
        target.upstream = recovered
        target.upstream_failed = False

    monkeypatch.setattr(provider, "_restart_before_output", restart)
    await provider.append_audio(session, b"audio")
    assert session.audio == b"audio"
    assert session.upstream_failed
    assert [item async for item in provider.respond(session)] == []


async def test_qwen_temporarily_disables_search_after_common_error(monkeypatch):
    provider = QwenRealtimeProvider(Settings(dashscope_api_key="not-a-real-key"))
    failed = FakeConnection([{"type": "error", "error": {"code": "COMMON_ERROR"}}])
    recovered = FakeConnection([{"type": "response.done"}])
    session = ProviderSession(upstream=failed, config={"web_search": True})
    fallback_search_values = []

    async def restart(target):
        fallback_search_values.append(target.config["web_search"])
        target.upstream = recovered

    monkeypatch.setattr(provider, "_restart_before_output", restart)
    assert [item async for item in provider.respond(session)] == []
    assert fallback_search_values == [False]
    assert session.config["web_search"] is True


async def test_qwen_generates_final_audio_in_existing_session():
    provider = QwenRealtimeProvider(Settings(dashscope_api_key="not-a-real-key"))
    connection = FakeConnection(
        [
            {"type": "response.audio.delta", "delta": base64.b64encode(b"voice").decode()},
            {"type": "response.audio_transcript.delta", "delta": "直接回答"},
            {"type": "response.audio.done"},
            {"type": "response.output_item.done"},
            {"type": "response.done", "response": {"status": "completed"}},
        ]
    )
    session = ProviderSession(upstream=connection, config={})
    result = [
        item async for item in provider.respond_direct(session, "依据证据直接回答", web_search=True)
    ]
    assert result == [("audio", b"voice"), ("text", "直接回答")]
    assert session.config["final_instructions"] == "依据证据直接回答"
    assert session.config["web_search"] is True
    assert [item["type"] for item in connection.sent] == ["session.update", "response.create"]
    assert connection.events == []


async def test_qwen_direct_response_rejects_incomplete_terminal_status():
    provider = QwenRealtimeProvider(Settings(dashscope_api_key="not-a-real-key"))
    connection = FakeConnection(
        [
            {"type": "response.audio.done"},
            {"type": "response.done", "response": {"status": "incomplete"}},
        ]
    )
    session = ProviderSession(upstream=connection, config={})
    with pytest.raises(ProviderError, match="status=incomplete") as raised:
        _ = [item async for item in provider.respond_direct(session, "直接回答", web_search=True)]
    assert raised.value.code == "incomplete_response"


async def test_qwen_direct_response_wraps_connection_close():
    provider = QwenRealtimeProvider(Settings(dashscope_api_key="not-a-real-key"))
    connection = FakeConnection(failure=OSError("upstream closed"))
    session = ProviderSession(upstream=connection, config={})
    with pytest.raises(ProviderError, match="direct response failed"):
        _ = [item async for item in provider.respond_direct(session, "直接回答", web_search=False)]


async def test_qwen_research_is_text_only_and_reports_actual_search_usage():
    provider = QwenRealtimeProvider(Settings(dashscope_api_key="not-a-real-key"))
    connection = FakeConnection(
        [
            {
                "type": "response.text.delta",
                "delta": "SOURCE https://www.bugatti.com/veyron",
            },
            {
                "type": "response.done",
                "response": {
                    "status": "completed",
                    "usage": {"plugins": {"search": {"count": 2, "strategy": "agent"}}},
                },
            },
        ]
    )
    session = ProviderSession(upstream=connection, config={})
    result = await provider.research(session, "只检索来源")
    assert result.search_count == 2
    assert result.strategy == "agent"
    assert result.text == "SOURCE https://www.bugatti.com/veyron"
    assert connection.sent[0]["session"]["modalities"] == ["text"]
    assert connection.sent[0]["session"]["enable_search"] is True
