from datetime import UTC, datetime, timedelta

from kid_terminal.answer_validation import AnswerClaim, AnswerEnvelope
from kid_terminal.official_sources import OfficialEvidence, OfficialRetrievalResult
from kid_terminal.services.turn_orchestrator import SAFE_RESEARCH_FAILURE, TurnOrchestrator
from kid_terminal.web_research import WebEvidence, WebEvidenceResult


class FakeKnowledge:
    def search(self, question: str, limit: int = 4):
        return []

    def search_topics(self, question: str, grade: int, limit: int = 6):
        return []


class FakeOfficialSources:
    async def retrieve(self, question: str):
        return OfficialRetrievalResult(status="not_configured")


class FailedOfficialSources:
    async def retrieve(self, question: str):
        return OfficialRetrievalResult(status="fetch_failed")


class FakeWebSources:
    async def retrieve(self, question: str, research):
        return WebEvidenceResult(status="no_search_sources")


class VerifiedNonOfficialWebSources:
    async def retrieve(self, question: str, research):
        now = datetime.now(UTC)
        return WebEvidenceResult(
            status="verified",
            evidence=(
                WebEvidence(
                    url="https://example.com/article",
                    fetched_at=now,
                    expires_at=now + timedelta(minutes=10),
                    sha256="1" * 64,
                    content="这是一段未经权威机构发布的内容。",
                ),
            ),
        )


class FakeWebSearch:
    calls = 0

    async def research(self, question: str):
        self.calls += 1
        return type("Research", (), {"text": "", "search_count": 0})()


class FakeAnswerer:
    calls = 0
    last_instructions = ""

    async def answer_envelope(self, instructions: str):
        self.calls += 1
        self.last_instructions = instructions
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
    assert "不要求当轮官方来源；不得因此拒答" in answerer.last_instructions
    assert "整个回答只能说" not in answerer.last_instructions


async def test_current_question_without_verified_evidence_fails_closed() -> None:
    answerer = FakeAnswerer()
    result = await orchestrator(answerer).prepare(
        "今天有什么新闻？", grade=4, conversation_context=""
    )
    assert result.text == SAFE_RESEARCH_FAILURE
    assert result.evidence_status == "evidence_unavailable"
    assert answerer.calls == 0


async def test_current_question_falls_back_to_web_when_official_fetch_fails() -> None:
    answerer = FakeAnswerer()
    web_search = FakeWebSearch()
    service = TurnOrchestrator(
        knowledge=FakeKnowledge(),
        official_sources=FailedOfficialSources(),
        web_sources=FakeWebSources(),
        web_search=web_search,
        answerer=answerer,
    )
    result = await service.prepare("明天天气怎么样？", grade=3, conversation_context="")
    assert result.text == SAFE_RESEARCH_FAILURE
    assert web_search.calls == 2


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


async def test_serious_question_rejects_non_authoritative_web_only() -> None:
    answerer = FakeAnswerer()
    service = TurnOrchestrator(
        knowledge=FakeKnowledge(),
        official_sources=FailedOfficialSources(),
        web_sources=VerifiedNonOfficialWebSources(),
        web_search=FakeWebSearch(),
        answerer=answerer,
    )
    result = await service.prepare("儿童发烧吃什么药？", grade=3, conversation_context="")
    assert result.evidence_status == "authoritative_evidence_unavailable"
    assert result.text == SAFE_RESEARCH_FAILURE
    assert answerer.calls == 0
