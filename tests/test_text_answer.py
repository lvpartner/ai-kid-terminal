import base64
import json

import httpx
import pytest

from kid_terminal.config import Settings
from kid_terminal.providers import ProviderError
from kid_terminal.text_answer import CosyVoiceSynthesizer, DeepSeekAnswerer, QwenASRClient


def settings() -> Settings:
    return Settings(
        environment="test",
        deepseek_api_key="test-deepseek-key",
        deepseek_model="deepseek-v4-flash",
        dashscope_api_key="test-dashscope-key",
    )


async def test_deepseek_answer_is_json_bounded_and_disables_thinking():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-deepseek-key"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert "同音字" in payload["messages"][0]["content"]
        assert "脑筋急转弯" in payload["messages"][0]["content"]
        assert "不能播放歌曲" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answer":"上海明天有雷雨。","needs_clarification":false,'
                                '"claims":[{"text":"上海明天有雷雨",'
                                '"source_ids":["weather-shanghai"]}]}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8},
            },
            request=request,
        )

    answerer = DeepSeekAnswerer(settings(), transport=httpx.MockTransport(handler))
    envelope = await answerer.answer_envelope("只使用结构化天气证据")
    assert envelope.answer == "上海明天有雷雨。"
    assert envelope.claims[0].source_ids == ("weather-shanghai",)


async def test_deepseek_answer_rejects_invalid_or_oversized_output():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer":"' + "长" * 241 + '"}'}}]},
            request=request,
        )

    answerer = DeepSeekAnswerer(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="length bounds"):
        await answerer.answer("证据")


async def test_cosyvoice_streams_raw_pcm_chunks():
    first = b"\x01\x02\x03\x04"
    second = b"\x05\x06"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["input"]["format"] == "pcm"
        lines = [
            "data: "
            + json.dumps({"output": {"audio": {"data": base64.b64encode(first).decode()}}}),
            "data: "
            + json.dumps({"output": {"audio": {"data": base64.b64encode(second).decode()}}}),
            "",
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="\n".join(lines),
            request=request,
        )

    synthesizer = CosyVoiceSynthesizer(settings(), transport=httpx.MockTransport(handler))
    assert [chunk async for chunk in synthesizer.stream("测试")] == [first, second]


async def test_standalone_qwen_asr_wraps_pcm_as_wav():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3-asr-flash"
        value = payload["messages"][0]["content"][0]["input_audio"]["data"]
        assert value.startswith("data:audio/wav;base64,")
        wav = base64.b64decode(value.split(",", 1)[1])
        assert wav.startswith(b"RIFF") and b"WAVE" in wav[:16]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "熊猫为什么是国宝？"}}]},
            request=request,
        )

    client = QwenASRClient(settings(), transport=httpx.MockTransport(handler))
    assert await client.transcribe(b"\x00\x00" * 1_600) == "熊猫为什么是国宝？"


async def test_standalone_qwen_asr_extracts_structured_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "一加一"},
                                {"type": "text", "text": "等于几？"},
                            ]
                        }
                    }
                ]
            },
            request=request,
        )

    client = QwenASRClient(settings(), transport=httpx.MockTransport(handler))
    assert await client.transcribe(b"\x00\x00" * 1_600) == "一加一等于几？"


async def test_standalone_qwen_asr_rejects_empty_audio():
    client = QwenASRClient(settings())
    with pytest.raises(ProviderError) as raised:
        await client.transcribe(b"")
    assert raised.value.code == "transcription_missing"
