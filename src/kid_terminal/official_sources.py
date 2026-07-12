import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from .text_answer import HTTP_LIMITS

logger = logging.getLogger("kid_terminal")
MAX_RESPONSE_BYTES = 2_000_000
MAX_PROMPT_CHARS = 6_000
USER_AGENT = "AIKidTerminal-OfficialSource/0.2"
WEATHER_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_DOMAINS = frozenset({"open-meteo.com"})
WEATHER_CACHE_SECONDS = 900

WMO_WEATHER = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "有雾",
    48: "有雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    56: "轻微冻毛毛雨",
    57: "较强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻微冻雨",
    67: "较强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷雨",
    96: "雷雨伴小冰雹",
    99: "雷雨伴较强冰雹",
}


@dataclass(frozen=True)
class OfficialSourceSpec:
    source_id: str
    title: str
    url: str
    question_terms: tuple[str, ...]
    allowed_domains: frozenset[str]
    required_content_terms: tuple[str, ...]
    max_age_seconds: int = 300
    days_before: int = 0
    days_after: int = 0


@dataclass(frozen=True)
class OfficialEvidence:
    source_id: str
    title: str
    url: str
    fetched_at: datetime
    expires_at: datetime
    sha256: str
    content: str
    cache_hit: bool = False


@dataclass(frozen=True)
class OfficialRetrievalResult:
    status: str
    evidence: tuple[OfficialEvidence, ...] = ()

    @property
    def verified(self) -> bool:
        return self.status == "verified" and bool(self.evidence)


DEFAULT_SOURCES = (
    OfficialSourceSpec(
        source_id="bugatti-veyron-factory-history",
        title="Bugatti Veyron legends and factory specifications",
        url=(
            "https://newsroom.bugatti.com/press-releases/"
            "bugatti-veyron-legends-the-modern-day-hyper-sports-car"
        ),
        question_terms=("布加迪", "威龙", "Bugatti", "Veyron"),
        allowed_domains=frozenset({"bugatti.com"}),
        required_content_terms=("407 km/h", "€1.16 million"),
        max_age_seconds=86_400,
    ),
    OfficialSourceSpec(
        source_id="fifa-world-cup-2026-schedule",
        title="FIFA World Cup 2026 match schedule and results",
        url=(
            "https://api.fifa.com/api/v3/calendar/matches"
            "?idCompetition=17&idSeason=285023&count=100&language=en"
        ),
        question_terms=("世界杯", "FIFA", "world cup"),
        allowed_domains=frozenset({"fifa.com"}),
        required_content_terms=("FIFA World Cup", "Results"),
        max_age_seconds=180,
        days_before=1,
        days_after=14,
    ),
)


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._hidden += 1
        elif tag in {"p", "li", "h1", "h2", "h3", "h4", "br", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._hidden = max(0, self._hidden - 1)
        elif tag in {"p", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts)
        lines = (re.sub(r"\s+", " ", line).strip() for line in value.splitlines())
        return "\n".join(line for line in lines if line)


def _host_allowed(url: str, domains: frozenset[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and parsed.port in {None, 443}
        and any(host == domain or host.endswith(f".{domain}") for domain in domains)
    )


def _as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def weather_location_query(question: str) -> str | None:
    lowered = question.lower()
    if not any(marker in question for marker in ("天气", "下雨", "气温", "温度")) and (
        "weather" not in lowered
    ):
        return None
    if "weather" in lowered:
        match = re.search(r"weather\s+(?:in|for)\s+([a-z][a-z .'-]{1,60})", lowered)
        if not match:
            return None
        value = re.sub(r"\b(?:today|tomorrow|tonight)\b", "", match.group(1))
        return value.strip(" .?!") or None
    value = question
    for marker in (
        "请问",
        "告诉我",
        "今天",
        "明天",
        "后天",
        "天气预报",
        "天气",
        "气温",
        "温度",
        "怎么样",
        "如何",
        "会不会",
        "会下雨",
        "是否",
        "下雨",
    ):
        value = value.replace(marker, "")
    value = re.sub(r"[的呀啊吗呢？?！!，,。\s]", "", value)
    return value[:60] if len(value) >= 2 else None


def extract_source_text(body: bytes, content_type: str) -> str:
    decoded = body.decode("utf-8", errors="replace")
    if "json" in content_type.lower():
        value = json.loads(decoded)
        if isinstance(value, dict) and isinstance(value.get("Results"), list):
            matches = []
            for item in value["Results"]:
                if not isinstance(item, dict):
                    continue
                home = _as_dict(item.get("Home"))
                away = _as_dict(item.get("Away"))
                stages = item.get("StageName")
                stage = (
                    [
                        entry.get("Description")
                        for entry in stages
                        if isinstance(entry, dict) and entry.get("Description")
                    ]
                    if isinstance(stages, list)
                    else []
                )
                matches.append(
                    {
                        "id": item.get("IdMatch"),
                        "date_utc": item.get("Date"),
                        "local_date": item.get("LocalDate"),
                        "stage": stage,
                        "home": home.get("ShortClubName"),
                        "away": away.get("ShortClubName"),
                        "home_score": item.get("HomeTeamScore"),
                        "away_score": item.get("AwayTeamScore"),
                        "status": item.get("MatchStatus"),
                    }
                )
            return json.dumps(
                {"competition": "FIFA World Cup", "Results": matches}, ensure_ascii=False
            )
        return json.dumps(value, ensure_ascii=False)
    if "html" not in content_type.lower():
        return re.sub(r"\s+", " ", decoded).strip()
    parser = _VisibleTextParser()
    parser.feed(decoded)
    return parser.text()


def _date_markers(now: datetime) -> tuple[str, ...]:
    return (
        now.strftime("%Y-%m-%d"),
        now.strftime("%B %-d, %Y"),
        now.strftime("%-d %B %Y"),
        f"{now.year}年{now.month}月{now.day}日",
    )


def _relevant_excerpt(text: str, spec: OfficialSourceSpec, now: datetime) -> str:
    if text.startswith("{") or text.startswith("["):
        return text[:MAX_PROMPT_CHARS]
    lines = text.splitlines()
    if not lines:
        return ""
    terms = tuple(term.lower() for term in spec.required_content_terms)
    date_markers = _date_markers(now)
    ranked: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        score = sum(2 for term in terms if term in lowered)
        score += sum(20 for marker in date_markers if marker.lower() in lowered)
        if score:
            ranked.append((score, index))
    selected: set[int] = set()
    for _, index in sorted(ranked, reverse=True)[:40]:
        selected.update(range(max(0, index - 1), min(len(lines), index + 2)))
    excerpt = "\n".join(lines[index] for index in sorted(selected))
    return excerpt[:MAX_PROMPT_CHARS]


class OfficialSourceRetriever:
    def __init__(
        self,
        sources: tuple[OfficialSourceSpec, ...] = DEFAULT_SOURCES,
        *,
        clock: Callable[[], datetime] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.sources = sources
        self._clock = clock or (lambda: datetime.now(UTC))
        self._transport = transport
        self._cache: dict[str, tuple[float, OfficialEvidence]] = {}
        self._weather_cache: dict[str, tuple[float, OfficialEvidence]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(6, connect=3),
            transport=transport,
            headers={"User-Agent": USER_AGENT},
            limits=HTTP_LIMITS,
        )
        for source in sources:
            if not _host_allowed(source.url, source.allowed_domains):
                raise ValueError(
                    f"official source URL is outside its allowlist: {source.source_id}"
                )
            if source.max_age_seconds < 1:
                raise ValueError(f"official source max age must be positive: {source.source_id}")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _allowed_get(
        self,
        url: str,
        domains: frozenset[str],
        *,
        params: dict[str, object] | None = None,
        accept: str,
    ) -> httpx.Response:
        current = str(httpx.URL(url, params=params)) if params else url
        for redirect_count in range(4):
            if not _host_allowed(current, domains):
                raise ValueError("official source target is outside its allowlist")
            response = await self._client.get(current, headers={"Accept": accept})
            if not response.is_redirect:
                response.raise_for_status()
                return response
            location = response.headers.get("location")
            if not location or redirect_count == 3:
                raise ValueError("official source exceeded redirect limit")
            current = str(response.url.join(location))
        raise ValueError("official source exceeded redirect limit")

    def _matching_sources(self, question: str) -> tuple[OfficialSourceSpec, ...]:
        lowered = question.lower()
        return tuple(
            source
            for source in self.sources
            if any(term.lower() in lowered for term in source.question_terms)
        )

    def _resolved_url(self, source: OfficialSourceSpec) -> str:
        if not source.days_before and not source.days_after:
            return source.url
        today = self._clock().date()
        date_from = today - timedelta(days=source.days_before)
        date_to = today + timedelta(days=source.days_after)
        return str(
            httpx.URL(source.url).copy_merge_params(
                {
                    "from": f"{date_from.isoformat()}T00:00:00Z",
                    "to": f"{date_to.isoformat()}T23:59:59Z",
                }
            )
        )

    async def _download(self, source: OfficialSourceSpec) -> OfficialEvidence:
        source_url = self._resolved_url(source)
        response = await self._allowed_get(
            source_url,
            source.allowed_domains,
            accept="text/html,text/plain,application/json",
        )
        content_type = response.headers.get("content-type", "").lower()
        if not any(kind in content_type for kind in ("html", "text", "json")):
            raise ValueError("official source returned an unsupported content type")
        body = response.content
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("official source response exceeded size limit")
        text = extract_source_text(body, content_type)
        if not all(term.lower() in text.lower() for term in source.required_content_terms):
            raise ValueError("official source content markers were missing")
        now = self._clock()
        excerpt = _relevant_excerpt(text, source, now)
        if not excerpt:
            raise ValueError("official source contained no relevant evidence")
        return OfficialEvidence(
            source_id=source.source_id,
            title=source.title,
            url=str(response.url),
            fetched_at=now,
            expires_at=now + timedelta(seconds=source.max_age_seconds),
            sha256=hashlib.sha256(body).hexdigest(),
            content=excerpt,
        )

    async def _get_one(self, source: OfficialSourceSpec) -> OfficialEvidence:
        cached = self._cache.get(source.source_id)
        if cached and cached[0] > time.monotonic():
            return replace(cached[1], cache_hit=True)
        lock = self._locks.setdefault(source.source_id, asyncio.Lock())
        async with lock:
            cached = self._cache.get(source.source_id)
            if cached and cached[0] > time.monotonic():
                return replace(cached[1], cache_hit=True)
            evidence = await self._download(source)
            self._cache[source.source_id] = (
                time.monotonic() + source.max_age_seconds,
                evidence,
            )
            return evidence

    async def _download_weather(self, location_query: str) -> OfficialEvidence:
        geocoding = await self._allowed_get(
            WEATHER_GEOCODING_URL,
            WEATHER_DOMAINS,
            params={"name": location_query, "count": 1, "language": "zh", "format": "json"},
            accept="application/json",
        )
        geocoding_body = geocoding.content
        if len(geocoding_body) > MAX_RESPONSE_BYTES:
            raise ValueError("weather geocoding response exceeded size limit")
        location_data = geocoding.json()
        results = location_data.get("results", []) if isinstance(location_data, dict) else []
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise ValueError("weather location was not found")
        location = results[0]
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        timezone = location.get("timezone")
        if not isinstance(latitude, int | float) or not isinstance(longitude, int | float):
            raise ValueError("weather location coordinates were invalid")
        if not isinstance(timezone, str) or not timezone:
            raise ValueError("weather location timezone was invalid")
        forecast = await self._allowed_get(
            WEATHER_FORECAST_URL,
            WEATHER_DOMAINS,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "timezone": timezone,
                "forecast_days": 3,
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max"
                ),
            },
            accept="application/json",
        )
        forecast_body = forecast.content
        if len(forecast_body) > MAX_RESPONSE_BYTES:
            raise ValueError("weather forecast response exceeded size limit")
        forecast_data = forecast.json()

        daily = forecast_data.get("daily", {}) if isinstance(forecast_data, dict) else {}
        if not isinstance(daily, dict):
            raise ValueError("weather forecast daily data was missing")
        fields = (
            "time",
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_probability_max",
        )
        arrays: dict[str, list[object]] = {}
        for field in fields:
            value = daily.get(field)
            if not isinstance(value, list) or len(value) < 2:
                raise ValueError("weather forecast fields were incomplete")
            arrays[field] = value
        days = []
        for index in range(min(3, len(arrays["time"]))):
            code_value = arrays["weather_code"][index]
            code = int(code_value) if isinstance(code_value, int | float) else -1
            days.append(
                {
                    "date_local": arrays["time"][index],
                    "condition": WMO_WEATHER.get(code, f"天气代码{code}"),
                    "temperature_max_c": arrays["temperature_2m_max"][index],
                    "temperature_min_c": arrays["temperature_2m_min"][index],
                    "precipitation_probability_max_percent": arrays[
                        "precipitation_probability_max"
                    ][index],
                }
            )
        location_name = str(location.get("name") or location_query)
        admin1 = str(location.get("admin1") or "")
        country = str(location.get("country") or "")
        content = json.dumps(
            {
                "location": location_name,
                "admin1": admin1,
                "country": country,
                "timezone": timezone,
                "forecast_type": "model forecast, not observation",
                "days": days,
            },
            ensure_ascii=False,
        )
        now = self._clock()
        return OfficialEvidence(
            source_id="open-meteo-weather-forecast",
            title="Open-Meteo structured weather forecast",
            url=str(forecast.url),
            fetched_at=now,
            expires_at=now + timedelta(seconds=WEATHER_CACHE_SECONDS),
            sha256=hashlib.sha256(geocoding_body + forecast_body).hexdigest(),
            content=content[:MAX_PROMPT_CHARS],
        )

    async def _retrieve_weather(self, location_query: str) -> OfficialRetrievalResult:
        cache_key = location_query.casefold()
        cached = self._weather_cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            evidence = replace(cached[1], cache_hit=True)
        else:
            evidence = await self._download_weather(location_query)
            self._weather_cache[cache_key] = (
                time.monotonic() + WEATHER_CACHE_SECONDS,
                evidence,
            )
        logger.info(
            "official weather retrieval status=verified source=%s fetched_at=%s cache_hit=%s",
            evidence.source_id,
            evidence.fetched_at.isoformat(),
            evidence.cache_hit,
        )
        return OfficialRetrievalResult(status="verified", evidence=(evidence,))

    async def retrieve(self, question: str) -> OfficialRetrievalResult:
        location_query = weather_location_query(question)
        if location_query:
            try:
                return await self._retrieve_weather(location_query)
            except (httpx.HTTPError, ValueError, OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "official weather retrieval status=failed error=%s", type(exc).__name__
                )
                return OfficialRetrievalResult(status="fetch_failed")
        matches = self._matching_sources(question)
        if not matches:
            logger.warning("official source retrieval status=not_configured")
            return OfficialRetrievalResult(status="not_configured")
        results = await asyncio.gather(
            *(self._get_one(source) for source in matches), return_exceptions=True
        )
        evidence = tuple(item for item in results if isinstance(item, OfficialEvidence))
        if len(evidence) != len(matches):
            error_types = sorted(
                {type(item).__name__ for item in results if isinstance(item, BaseException)}
            )
            logger.warning(
                "official source retrieval status=failed sources=%s errors=%s",
                [source.source_id for source in matches],
                error_types,
            )
            return OfficialRetrievalResult(status="fetch_failed")
        logger.info(
            "official source retrieval status=verified sources=%s fetched_at=%s cache_hits=%s",
            [item.source_id for item in evidence],
            [item.fetched_at.isoformat() for item in evidence],
            sum(item.cache_hit for item in evidence),
        )
        return OfficialRetrievalResult(status="verified", evidence=evidence)


def render_official_evidence(result: OfficialRetrievalResult) -> str:
    if not result.verified:
        return (
            f"服务端官方来源核验状态：{result.status}。本轮没有可用的官方证据。"
            "整个回答只能说：‘这个问题我暂时无法从官方来源核实，所以先不乱猜。’"
            "不能提供任何可能变化的事实、数字、名单、赛果或日期，也不添加挑战或追问。"
        )
    sections = []
    for item in result.evidence:
        sections.append(
            f"source_id：{item.source_id}\n来源：{item.title}\nURL：{item.url}\n"
            f"抓取时间（UTC）：{item.fetched_at.isoformat()}\n"
            f"证据失效时间（UTC）：{item.expires_at.isoformat()}\n"
            f"内容SHA-256：{item.sha256}\n官方页面摘录：\n{item.content}"
        )
    return "\n\n".join(sections)
