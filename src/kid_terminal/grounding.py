import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .answer_policy import (
    build_answer_prompt,
    deterministic_issues,
    official_url_for_question,
    route_question,
)
from .config import Settings
from .knowledge import CurriculumKnowledgeBase

logger = logging.getLogger("kid_terminal")
API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


@dataclass(frozen=True)
class GroundedResult:
    answer: str
    confidence: float
    sources: list[dict[str, str]]
    verified: bool
    issues: list[str]


def parse_model_json(content: str) -> dict[str, Any]:
    cleaned = content.strip().removeprefix("```json").removesuffix("```").strip()
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("grounded model response is not an object")
    return value


class GroundedAnswerService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.kb = CurriculumKnowledgeBase(Path(settings.knowledge_db_path))
        self._cache: dict[tuple[str, int, int], tuple[float, GroundedResult]] = {}

    async def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.settings.dashscope_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(headers=headers, timeout=30) as client:
            response = await client.post(API_URL, json=payload)
            response.raise_for_status()
        return parse_model_json(response.json()["choices"][0]["message"]["content"])

    async def _verify_current(self, question: str, candidate: dict[str, Any]) -> dict[str, Any]:
        prompt = f"""独立核验下面的时效性回答。必须重新搜索第一方官方网站，逐项检查日期、时间、
时区、名单、比赛状态、数字和总数。不能沿用候选答案的记忆或推测。
问题：{question}
候选：{json.dumps(candidate, ensure_ascii=False)}
如果候选有任何错误，输出纠正后的答案。只输出 answer、confidence、claims、sources 四个字段的 JSON。
sources 只能列出本次实际核对的第一方官网 URL；无法得到第一方证据就把 confidence 设为0并拒绝猜测。
"""
        return await self._call(
            {
                "model": self.settings.grounded_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 1200,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
                "enable_search": True,
                "search_options": {"forced_search": True, "enable_source": True},
            }
        )

    async def _official_sources_reachable(self, question: str, result: dict[str, Any]) -> bool:
        urls = [
            str(item.get("url", ""))
            for item in result.get("sources", [])
            if isinstance(item, dict)
            and official_url_for_question(question, str(item.get("url", "")))
        ]
        if not urls:
            return False
        async with httpx.AsyncClient(follow_redirects=True, timeout=5) as client:
            checks = await asyncio.gather(
                *(client.get(url) for url in urls[:3]), return_exceptions=True
            )
        return any(isinstance(item, httpx.Response) and item.is_success for item in checks)

    async def answer(self, question: str, grade: int = 4, version: int = 1) -> GroundedResult:
        cache_key = (question.strip(), grade, version)
        cached = self._cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        evidence = self.kb.search(question, limit=5)
        decision = route_question(question, version)
        prompt = build_answer_prompt(question, grade, evidence, version)
        payload: dict[str, Any] = {
            "model": self.settings.grounded_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 800,
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
        }
        if decision.requires_official_source:
            payload.update(
                {
                    "enable_search": True,
                    "search_options": {"forced_search": True, "enable_source": True},
                }
            )
        issues = ["grounding_not_started"]
        try:
            result = await self._call(payload)
            if decision.verification_passes == 2:
                result = await self._verify_current(question, result)
        except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError) as exc:
            issues = [f"grounding_error:{type(exc).__name__}"]
            logger.warning("grounded answer failed error=%s", type(exc).__name__)
        else:
            issues = deterministic_issues(question, result, decision)
            if decision.requires_official_source and not await self._official_sources_reachable(
                question, result
            ):
                issues.append("official_source_unreachable")
            confidence = float(result.get("confidence", 0))
            if not issues and confidence >= 0.75:
                sources = [
                    {"title": str(item.get("title", "")), "url": str(item.get("url", ""))}
                    for item in result.get("sources", [])
                    if isinstance(item, dict)
                ]
                logger.info(
                    "grounded answer verified route=%s passes=%s sources=%s confidence=%.2f",
                    decision.route.value,
                    decision.verification_passes,
                    len(sources),
                    confidence,
                )
                verified = GroundedResult(
                    answer=str(result["answer"]),
                    confidence=confidence,
                    sources=sources,
                    verified=True,
                    issues=[],
                )
                self._cache[cache_key] = (time.monotonic() + 300, verified)
                return verified
        logger.warning("grounded answer rejected route=%s issues=%s", decision.route.value, issues)
        return GroundedResult(
            answer="这个问题我暂时没有找到足够可靠的官方信息，所以先不乱猜。等核实清楚再告诉你。",
            confidence=0,
            sources=[],
            verified=False,
            issues=issues,
        )
