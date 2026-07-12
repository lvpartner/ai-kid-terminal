import logging
import time
from dataclasses import dataclass

from ..answer_policy import (
    AnswerRoute,
    build_realtime_answer_instructions,
    deterministic_capability_response,
    question_needs_web_research,
    route_question,
)
from ..answer_validation import validate_answer
from ..knowledge import CurriculumKnowledgeBase
from ..official_sources import OfficialSourceRetriever, render_official_evidence
from ..provider_interfaces import AnswerGenerator, SearchProvider
from ..providers import ProviderError
from ..web_research import (
    WebEvidenceResult,
    WebEvidenceRetriever,
    build_research_instructions,
    render_web_evidence,
)

logger = logging.getLogger("kid_terminal")
SAFE_RESEARCH_FAILURE = "这个问题我暂时没有找到可靠资料，所以先不乱猜。"
SAFE_ANSWER_FAILURE = "我刚才没能可靠地组织好答案，请你稍后再问一次。"


@dataclass(frozen=True)
class PreparedAnswer:
    text: str
    evidence_status: str
    source_ids: tuple[str, ...] = ()


class TurnOrchestrator:
    def __init__(
        self,
        *,
        knowledge: CurriculumKnowledgeBase,
        official_sources: OfficialSourceRetriever,
        web_sources: WebEvidenceRetriever,
        web_search: SearchProvider,
        answerer: AnswerGenerator,
    ) -> None:
        self.knowledge = knowledge
        self.official_sources = official_sources
        self.web_sources = web_sources
        self.web_search = web_search
        self.answerer = answerer

    async def prepare(
        self,
        question: str,
        *,
        grade: int,
        conversation_context: str,
    ) -> PreparedAnswer:
        if capability_answer := deterministic_capability_response(question):
            return PreparedAnswer(capability_answer, "capability_boundary")

        started = time.monotonic()
        decision = route_question(question)
        product_research = question_needs_web_research(question)
        official_result = await self.official_sources.retrieve(question)
        official_elapsed = time.monotonic()
        official_status = official_result.status
        official_evidence = render_official_evidence(official_result)

        web_result = WebEvidenceResult(status="not_needed")
        needs_web = official_status != "verified" and (
            product_research
            or (decision.route == AnswerRoute.CURRENT and official_status == "not_configured")
        )
        if needs_web:
            web_result = WebEvidenceResult(status="research_error")
            for attempt in range(2):
                try:
                    research = await self.web_search.research(build_research_instructions(question))
                    web_result = await self.web_sources.retrieve(question, research)
                except ProviderError as exc:
                    logger.warning(
                        "web research failed attempt=%s code=%s",
                        attempt + 1,
                        exc.code or "unknown",
                    )
                if web_result.verified:
                    break
        research_elapsed = time.monotonic()

        strict_evidence = decision.route == AnswerRoute.CURRENT or product_research
        if strict_evidence and not official_result.verified and not web_result.verified:
            return PreparedAnswer(SAFE_RESEARCH_FAILURE, "evidence_unavailable")

        source_ids = {
            *(item.source_id for item in official_result.evidence),
            *(f"web-{item.sha256[:12]}" for item in web_result.evidence),
        }
        evidence_by_source = {
            **{item.source_id: item.content for item in official_result.evidence},
            **{f"web-{item.sha256[:12]}": item.content for item in web_result.evidence},
        }
        prompt = build_realtime_answer_instructions(
            question,
            grade,
            self.knowledge.search(question, limit=4),
            version=1,
            conversation_context=conversation_context,
            official_evidence=official_evidence,
            web_evidence=render_web_evidence(web_result),
        )
        try:
            answer = ""
            for attempt in range(2):
                envelope = await self.answerer.answer_envelope(prompt)
                try:
                    answer = validate_answer(
                        envelope,
                        allowed_source_ids=source_ids,
                        evidence_required=strict_evidence,
                        evidence_by_source=evidence_by_source,
                    )
                    break
                except ProviderError as exc:
                    if attempt or exc.code != "claim_invalid":
                        raise
                    logger.info("answer validation retry reason=%s", str(exc))
                    prompt += (
                        "\n上次结构校验失败。重新输出时，每个claims.text必须逐字复制answer中的"
                        "一个连续事实片段；evidence_span必须逐字复制对应来源中的连续原文。"
                    )
        except ProviderError as exc:
            logger.warning(
                "answer preparation failed code=%s reason=%s",
                exc.code or "unknown",
                str(exc),
            )
            return PreparedAnswer(SAFE_ANSWER_FAILURE, "answer_validation_failed")
        logger.info(
            "turn stages route=%s official_ms=%s research_ms=%s answer_ms=%s sources=%s",
            decision.route.value,
            round((official_elapsed - started) * 1000),
            round((research_elapsed - official_elapsed) * 1000),
            round((time.monotonic() - research_elapsed) * 1000),
            len(source_ids),
        )
        status = "verified" if strict_evidence else "stable_knowledge"
        return PreparedAnswer(answer, status, tuple(sorted(source_ids)))
