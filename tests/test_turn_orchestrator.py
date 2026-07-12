from kid_terminal.answer_validation import AnswerEnvelope
from kid_terminal.official_sources import OfficialRetrievalResult
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
