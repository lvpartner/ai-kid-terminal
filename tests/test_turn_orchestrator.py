from datetime import UTC, datetime, timedelta

from kid_terminal.answer_validation import AnswerClaim, AnswerEnvelope
from kid_terminal.official_sources import OfficialEvidence, OfficialRetrievalResult
from kid_terminal.services.turn_orchestrator import SAFE_RESEARCH_FAILURE, TurnOrchestrator
from kid_terminal.web_research import WebEvidenceResult


class FakeKnowledge:
    def search(self, question: str, limit: int = 4):
        return []


class FakeOfficialSources:
    async def retrieve(self, question: str):
        return OfficialRetrievalResult(status="not_configured")


class FakeWebSources:
    async def retrieve(self, question: str, research):
        return WebEvidenceResult(status="no_search_sources")


class FakeWebSearch:
    async def research(self, question: str):
        return type("Research", (), {"text": "", "search_count": 0})()


class FakeAnswerer:
    calls = 0

    async def answer_envelope(self, instructions: str):
        self.calls += 1
        return AnswerEnvelope("熊猫是哺乳动物。", False)


class RetryAnswerer:
    calls = 0

    async def answer_envelope(self, instructions: str):
        self.calls += 1
        claim = "不匹配的声明" if self.calls == 1 else "最高速度是407公里"
        return AnswerEnvelope(
            "最高速度是407公里。",
            False,
            (AnswerClaim(claim, ("factory",), "407 km/h"),),
        )


class VerifiedOfficialSources:
    async def retrieve(self, question: str):
        now = datetime.now(UTC)
        evidence = OfficialEvidence(
            source_id="factory",
            title="factory",
            url="https://example.com",
            fetched_at=now,
            expires_at=now + timedelta(hours=1),
            sha256="0" * 64,
            content="The maximum speed is 407 km/h.",
        )
        return OfficialRetrievalResult(status="verified", evidence=(evidence,))


def orchestrator(answerer: FakeAnswerer) -> TurnOrchestrator:
    return TurnOrchestrator(
        knowledge=FakeKnowledge(),
        official_sources=FakeOfficialSources(),
        web_sources=FakeWebSources(),
        web_search=FakeWebSearch(),
        answerer=answerer,
    )


async def test_stable_question_uses_structured_answer_path() -> None:
    answerer = FakeAnswerer()
    result = await orchestrator(answerer).prepare(
        "熊猫是什么动物？", grade=3, conversation_context=""
    )
    assert result.text == "熊猫是哺乳动物。"
    assert result.evidence_status == "stable_knowledge"
    assert answerer.calls == 1


async def test_current_question_without_verified_evidence_fails_closed() -> None:
    answerer = FakeAnswerer()
    result = await orchestrator(answerer).prepare(
        "今天有什么新闻？", grade=4, conversation_context=""
    )
    assert result.text == SAFE_RESEARCH_FAILURE
    assert result.evidence_status == "evidence_unavailable"
    assert answerer.calls == 0


async def test_media_request_is_intercepted_before_model() -> None:
    answerer = FakeAnswerer()
    result = await orchestrator(answerer).prepare(
        "播放一下 Waka Waka", grade=2, conversation_context=""
    )
    assert "不能播放歌曲" in result.text
    assert result.evidence_status == "capability_boundary"
    assert answerer.calls == 0


async def test_claim_shape_is_retried_once_before_failing_closed() -> None:
    answerer = RetryAnswerer()
    service = TurnOrchestrator(
        knowledge=FakeKnowledge(),
        official_sources=VerifiedOfficialSources(),
        web_sources=FakeWebSources(),
        web_search=FakeWebSearch(),
        answerer=answerer,
    )
    result = await service.prepare("布加迪速度是多少？", grade=3, conversation_context="")
    assert result.text == "最高速度是407公里。"
    assert answerer.calls == 2
