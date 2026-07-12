from pathlib import Path

from kid_terminal.config import Settings
from kid_terminal.grounding import GroundedAnswerService


async def test_grounding_fails_closed_after_bounded_errors(monkeypatch, tmp_path: Path):
    service = GroundedAnswerService(
        Settings(dashscope_api_key="test-key", knowledge_db_path=tmp_path / "missing.db")
    )
    calls = 0

    async def fail(_payload):
        nonlocal calls
        calls += 1
        raise ValueError("malformed provider response")

    monkeypatch.setattr(service, "_call", fail)
    result = await service.answer("今天世界杯还有哪几场？")
    assert calls == 1
    assert not result.verified
    assert "先不乱猜" in result.answer
    assert result.issues == ["grounding_error:ValueError"]


async def test_grounding_caches_verified_repeat(monkeypatch, tmp_path: Path):
    service = GroundedAnswerService(
        Settings(dashscope_api_key="test-key", knowledge_db_path=tmp_path / "missing.db")
    )
    calls = 0

    async def succeed(_payload):
        nonlocal calls
        calls += 1
        return {"answer": "一颗会旅行的种子。", "confidence": 0.9, "claims": [], "sources": []}

    monkeypatch.setattr(service, "_call", succeed)
    first = await service.answer("讲一个想象故事")
    second = await service.answer("讲一个想象故事")
    assert first.verified and second.verified
    assert calls == 1


async def test_reasoning_does_not_force_web_search(monkeypatch, tmp_path: Path):
    service = GroundedAnswerService(
        Settings(dashscope_api_key="test-key", knowledge_db_path=tmp_path / "missing.db")
    )
    payloads = []

    async def succeed(payload):
        payloads.append(payload)
        return {
            "answer": "3+5=8，可以用8-5=3验算。",
            "confidence": 0.9,
            "claims": [{"claim": "3+5=8", "evidence": "直接计算"}],
            "sources": [],
        }

    monkeypatch.setattr(service, "_call", succeed)
    result = await service.answer("3+5等于多少，怎么验算？")
    assert result.verified
    assert "enable_search" not in payloads[0]
    assert payloads[0]["enable_thinking"] is False
