import pytest

from kid_terminal.answer_validation import AnswerClaim, AnswerEnvelope, validate_answer
from kid_terminal.providers import ProviderError


def test_precise_external_claim_requires_known_source() -> None:
    envelope = AnswerEnvelope(
        answer="最高速度是407公里每小时。",
        needs_clarification=False,
        claims=(
            AnswerClaim(
                text="最高速度是407公里每小时",
                source_ids=("bugatti-factory",),
                evidence_span="407公里每小时",
            ),
        ),
    )
    assert (
        validate_answer(
            envelope,
            allowed_source_ids={"bugatti-factory"},
            evidence_required=True,
            evidence_by_source={"bugatti-factory": "官方标称最高速度407公里每小时。"},
        )
        == envelope.answer
    )


def test_precise_external_claim_rejects_missing_or_unknown_source() -> None:
    missing = AnswerEnvelope("价格是100万元。", False)
    with pytest.raises(ProviderError, match="no mapped evidence"):
        validate_answer(missing, allowed_source_ids=set(), evidence_required=True)

    unknown = AnswerEnvelope(
        "价格是100万元。",
        False,
        (AnswerClaim("价格是100万元", ("invented",)),),
    )
    with pytest.raises(ProviderError, match="unknown evidence"):
        validate_answer(unknown, allowed_source_ids={"official"}, evidence_required=True)


def test_clarification_is_one_short_question() -> None:
    assert (
        validate_answer(
            AnswerEnvelope("你问的是哪一本书？", True),
            allowed_source_ids=set(),
            evidence_required=False,
        )
        == "你问的是哪一本书？"
    )
    with pytest.raises(ProviderError, match="one short question"):
        validate_answer(
            AnswerEnvelope("你问哪本书？它是谁写的？", True),
            allowed_source_ids=set(),
            evidence_required=False,
        )


def test_external_claim_rejects_fabricated_evidence_span() -> None:
    envelope = AnswerEnvelope(
        "最高速度是407公里每小时。",
        False,
        (AnswerClaim("最高速度是407公里每小时", ("factory",), "不存在的原文"),),
    )
    with pytest.raises(ProviderError, match="absent from referenced evidence"):
        validate_answer(
            envelope,
            allowed_source_ids={"factory"},
            evidence_required=True,
            evidence_by_source={"factory": "The top speed is 407 km/h."},
        )


def test_stable_math_accepts_equivalent_claim_wording_without_external_source() -> None:
    envelope = AnswerEnvelope(
        "等于二。",
        False,
        (AnswerClaim("一加一等于二"),),
    )
    assert (
        validate_answer(envelope, allowed_source_ids=set(), evidence_required=False) == "等于二。"
    )
