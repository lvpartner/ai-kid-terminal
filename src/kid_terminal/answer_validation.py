import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .providers import ProviderError

PRECISE_VALUE = re.compile(
    r"(?:\d[\d,.]*|[一二三四五六七八九十百千万亿]+)"
    r"(?:年|月|日|点|时|分|秒|元|美元|欧元|公里|米|厘米|千米|千克|公斤|吨|度|%|％|名|个|次)?"
)


@dataclass(frozen=True)
class AnswerClaim:
    text: str
    source_ids: tuple[str, ...] = ()
    evidence_span: str = ""


@dataclass(frozen=True)
class AnswerEnvelope:
    answer: str
    needs_clarification: bool
    claims: tuple[AnswerClaim, ...] = ()


def _claim_matches_answer(claim: str, answer: str) -> bool:
    compact_claim = re.sub(r"[^\w\u4e00-\u9fff]", "", claim).casefold()
    compact_answer = re.sub(r"[^\w\u4e00-\u9fff]", "", answer).casefold()
    if not compact_claim:
        return False
    return (
        compact_claim in compact_answer
        or SequenceMatcher(None, compact_claim, compact_answer).ratio() >= 0.30
    )


def _evidence_contains_span(evidence: str, span: str) -> bool:
    if span in evidence:
        return True
    compact_span = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]", "", span).casefold()
    compact_evidence = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]", "", evidence).casefold()
    return len(compact_span) >= 4 and compact_span in compact_evidence


def validate_answer(
    envelope: AnswerEnvelope,
    *,
    allowed_source_ids: set[str],
    evidence_required: bool,
    evidence_by_source: dict[str, str] | None = None,
) -> str:
    answer = envelope.answer.strip()
    if not answer or len(answer) > 240:
        raise ProviderError("Answer violated length bounds", retryable=False, code="answer_invalid")
    if envelope.needs_clarification:
        if len(re.findall(r"[？?]", answer)) != 1 or len(answer) > 100:
            raise ProviderError(
                "Clarification must be one short question",
                retryable=False,
                code="answer_invalid",
            )
        return answer

    for claim in envelope.claims:
        if evidence_required and not _claim_matches_answer(claim.text, answer):
            raise ProviderError(
                "Claim ledger does not match answer", retryable=False, code="claim_invalid"
            )
        unknown = set(claim.source_ids) - allowed_source_ids
        if unknown:
            raise ProviderError(
                "Claim references unknown evidence", retryable=False, code="claim_invalid"
            )
        if evidence_required and claim.source_ids:
            span = claim.evidence_span.strip()
            if not span or len(span) > 300:
                raise ProviderError(
                    "Supported claim has no bounded evidence span",
                    retryable=False,
                    code="claim_invalid",
                )
            sources = evidence_by_source or {}
            if not any(
                _evidence_contains_span(sources.get(source_id, ""), span)
                for source_id in claim.source_ids
            ):
                raise ProviderError(
                    "Evidence span is absent from referenced evidence",
                    retryable=False,
                    code="claim_invalid",
                )

    precise_answer = bool(PRECISE_VALUE.search(answer))
    supported_claims = [claim for claim in envelope.claims if claim.source_ids]
    if evidence_required and precise_answer and not supported_claims:
        raise ProviderError(
            "Precise answer has no mapped evidence", retryable=False, code="claim_invalid"
        )
    return answer
