import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx

from .config import Settings
from .official_sources import extract_source_text
from .providers import WebResearchResult
from .text_answer import HTTP_LIMITS

logger = logging.getLogger("kid_terminal")
MAX_SOURCE_BYTES = 1_000_000
MAX_SOURCE_CHARS = 3_000
URL_PATTERN = re.compile(r"https://[^\s<>\]\[(){}\"'，。]+", re.IGNORECASE)
CHAT_PATH = "/compatible-mode/v1/chat/completions"


@dataclass(frozen=True)
class WebEvidence:
    url: str
    fetched_at: datetime
    expires_at: datetime
    sha256: str
    content: str
    cache_hit: bool = False


@dataclass(frozen=True)
class WebEvidenceResult:
    status: str
    evidence: tuple[WebEvidence, ...] = ()

    @property
    def verified(self) -> bool:
        return self.status == "verified" and bool(self.evidence)


def build_research_instructions(question: str) -> str:
    return f"""只为下面的问题执行联网资料检索，不回答孩子，不生成音频：
{question}

查找最多5个直接支撑答案的公开HTTPS页面。优先顺序：制造商或机构官网、政府或国际组织、
公认专业媒体。价格必须区分车型版本、年份、币种以及新车指导价或当前二手报价；性能参数必须
区分量产版、特殊版本和实测或官方标称。不同来源冲突时全部保留，不自行裁决。

每个来源严格输出一行，格式为：
SOURCE https://完整URL
不要输出没有实际打开的URL，不要使用搜索结果页、聚合转载页或无法访问的页面。
"""


def _question_terms(question: str) -> set[str]:
    terms = {term.lower() for term in re.findall(r"[A-Za-z0-9]{2,}", question)}
    compact = re.sub(r"\s+", "", question)
    terms.update(compact[index : index + 2] for index in range(max(0, len(compact) - 1)))
    terms.update({"price", "speed", "mph", "km/h", "million", "价格", "售价", "速度"})
    return terms


def _requires_corroboration(question: str) -> bool:
    lowered = question.lower()
    return any(
        marker in lowered
        for marker in ("价格", "售价", "多少钱", "速度", "参数", "price", "cost", "speed")
    )


def _excerpt(text: str, question: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    terms = _question_terms(question)
    ranked: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        score = sum(len(term) for term in terms if term in lowered)
        if re.search(r"(?:\d|\$|€|£|km/h|mph)", line, re.IGNORECASE):
            score += 5
        if score:
            ranked.append((score, index))
    selected: set[int] = set()
    for _, index in sorted(ranked, reverse=True)[:20]:
        selected.update(range(max(0, index - 1), min(len(lines), index + 2)))
    if not selected:
        return ""
    return "\n".join(lines[index] for index in sorted(selected))[:MAX_SOURCE_CHARS]


async def _resolve_host(host: str) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return tuple({str(record[4][0]) for record in records})


def _public_addresses(addresses: tuple[str, ...]) -> bool:
    if not addresses:
        return False
    return all(ipaddress.ip_address(address).is_global for address in addresses)


class WebEvidenceRetriever:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str], Awaitable[tuple[str, ...]]] = _resolve_host,
    ) -> None:
        self._resolver = resolver
        self._cache: dict[str, tuple[float, WebEvidence]] = {}
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(6, connect=3),
            transport=transport,
            headers={"User-Agent": "AIKidTerminal-WebEvidence/0.3"},
            limits=HTTP_LIMITS,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _safe_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            return False
        try:
            return _public_addresses(await self._resolver(parsed.hostname))
        except (OSError, ValueError):
            return False

    async def _fetch(self, url: str, question: str) -> WebEvidence:
        if not await self._safe_url(url):
            raise ValueError("research URL is not a safe public HTTPS target")
        current_url = url
        for redirect_count in range(4):
            if not await self._safe_url(current_url):
                raise ValueError("research URL is not a safe public HTTPS target")
            async with self._client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location or redirect_count == 3:
                        raise ValueError("research source exceeded redirect limit")
                    current_url = str(response.url.join(location))
                    continue
                response.raise_for_status()
                final_url = str(response.url)
                content_type = response.headers.get("content-type", "").lower()
                if not any(kind in content_type for kind in ("html", "text", "json")):
                    raise ValueError("research source returned unsupported content")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_SOURCE_BYTES:
                        raise ValueError("research source exceeded size limit")
                    chunks.append(chunk)
                break
        else:  # pragma: no cover - loop always exits through break or exception
            raise ValueError("research source exceeded redirect limit")
        body = b"".join(chunks)
        content = _excerpt(extract_source_text(body, content_type), question)
        if not content:
            raise ValueError("research source had no question-relevant content")
        now = datetime.now(UTC)
        return WebEvidence(
            url=final_url,
            fetched_at=now,
            expires_at=now + timedelta(minutes=10),
            sha256=hashlib.sha256(body).hexdigest(),
            content=content,
        )

    async def retrieve(self, question: str, research: WebResearchResult) -> WebEvidenceResult:
        if research.search_count < 1:
            return WebEvidenceResult(status="search_not_executed")
        urls = tuple(dict.fromkeys(URL_PATTERN.findall(research.text)))[:5]
        if not urls:
            return WebEvidenceResult(status="missing_source_urls")
        results: list[WebEvidence | BaseException] = []
        for url in urls:
            cached = self._cache.get(url)
            if cached and cached[0] > time.monotonic():
                results.append(replace(cached[1], cache_hit=True))
                continue
            try:
                fetched = await self._fetch(url, question)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                results.append(exc)
                continue
            self._cache[url] = (time.monotonic() + 600, fetched)
            results.append(fetched)
        evidence_items = tuple(item for item in results if isinstance(item, WebEvidence))[:3]
        if not evidence_items:
            logger.warning("web evidence retrieval status=source_validation_failed")
            return WebEvidenceResult(status="source_validation_failed")
        hosts = {(urlparse(item.url).hostname or "").lower() for item in evidence_items}
        if _requires_corroboration(question) and len(hosts) < 2:
            logger.warning("web evidence retrieval status=insufficient_corroboration")
            return WebEvidenceResult(status="insufficient_corroboration")
        logger.info(
            "web evidence retrieval status=verified sources=%s hosts=%s fetched_at=%s "
            "digests=%s search_count=%s cache_hits=%s",
            len(evidence_items),
            sorted(hosts),
            [item.fetched_at.isoformat() for item in evidence_items],
            [item.sha256[:12] for item in evidence_items],
            research.search_count,
            sum(item.cache_hit for item in evidence_items),
        )
        return WebEvidenceResult(status="verified", evidence=evidence_items)


class QwenTextSearchClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=self._base_url(),
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            timeout=httpx.Timeout(45, connect=5),
            transport=transport,
            limits=HTTP_LIMITS,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _base_url(self) -> str:
        if self.settings.qwen_workspace_id:
            return f"https://{self.settings.qwen_workspace_id}.cn-beijing.maas.aliyuncs.com"
        return "https://dashscope.aliyuncs.com"

    async def research(self, instructions: str) -> WebResearchResult:
        payload = {
            "model": "qwen3.5-flash",
            "messages": [{"role": "user", "content": instructions}],
            "enable_thinking": False,
            "max_tokens": 700,
            "enable_search": True,
            "search_options": {"forced_search": True, "enable_source": True},
        }
        try:
            response: httpx.Response | None = None
            for attempt in range(3):
                response = await self._client.post(CHAT_PATH, json=payload)
                if response.status_code != 429 and response.status_code < 500:
                    break
                await asyncio.sleep(0.5 * 2**attempt)
            assert response is not None
            response.raise_for_status()
            body = response.json()
            content = str(body["choices"][0]["message"]["content"])
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError) as exc:
            logger.warning("Qwen text search failed error=%s", type(exc).__name__)
            return WebResearchResult(text="", search_count=0, strategy="text_search_error")
        urls = tuple(dict.fromkeys(URL_PATTERN.findall(content)))[:5]
        logger.info("Qwen text search completed source_urls=%s", len(urls))
        return WebResearchResult(
            text="\n".join(f"SOURCE {url}" for url in urls),
            search_count=1 if urls else 0,
            strategy="forced_text_search",
        )


def render_web_evidence(result: WebEvidenceResult) -> str:
    if not result.verified:
        return (
            f"服务端联网证据状态：{result.status}。不能使用联网结果中的事实或数字；"
            "如果问题依赖外部资料，只能说暂时无法可靠核实。"
        )
    sections = []
    for index, item in enumerate(result.evidence, 1):
        sections.append(
            f"[联网证据{index}] source_id：web-{item.sha256[:12]}\nURL：{item.url}\n"
            f"抓取时间UTC：{item.fetched_at.isoformat()}\n"
            f"失效时间UTC：{item.expires_at.isoformat()}\nSHA-256：{item.sha256}\n{item.content}"
        )
    return "\n\n".join(sections)
