from datetime import UTC, datetime

import httpx
import pytest

from kid_terminal.official_sources import (
    DEFAULT_SOURCES,
    OfficialEvidence,
    OfficialRetrievalResult,
    OfficialSourceRetriever,
    OfficialSourceSpec,
    render_official_evidence,
    weather_location_query,
)


def source() -> OfficialSourceSpec:
    return OfficialSourceSpec(
        source_id="example",
        title="Official schedule",
        url="https://official.example/schedule",
        question_terms=("赛程",),
        allowed_domains=frozenset({"official.example"}),
        required_content_terms=("Schedule", "Match"),
        max_age_seconds=60,
    )


def test_default_sources_include_auditable_bugatti_factory_evidence():
    bugatti = next(item for item in DEFAULT_SOURCES if item.source_id.startswith("bugatti-"))
    assert bugatti.allowed_domains == frozenset({"bugatti.com"})
    assert "407 km/h" in bugatti.required_content_terms
    assert "€1.16 million" in bugatti.required_content_terms


async def test_official_retrieval_caches_fresh_allowlisted_evidence(monkeypatch):
    now = datetime(2026, 7, 12, 1, 2, 3, tzinfo=UTC)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><h1>Schedule</h1><p>12 July 2026</p><p>Match 1</p></html>",
            request=request,
        )

    retriever = OfficialSourceRetriever(
        (source(),), clock=lambda: now, transport=httpx.MockTransport(handler)
    )
    first = await retriever.retrieve("今天赛程是什么？")
    second = await retriever.retrieve("今天赛程是什么？")
    assert first.verified and second.verified
    assert calls == 1
    assert not first.evidence[0].cache_hit
    assert second.evidence[0].cache_hit
    assert "Match 1" in first.evidence[0].content
    assert len(first.evidence[0].sha256) == 64


async def test_official_retrieval_fails_closed_for_missing_or_failed_source(monkeypatch):
    retriever = OfficialSourceRetriever((source(),))
    assert (await retriever.retrieve("今天有什么新闻？")).status == "not_configured"

    async def fail(_spec):
        raise ValueError("invalid content")

    monkeypatch.setattr(retriever, "_download", fail)
    result = await retriever.retrieve("赛程")
    assert result.status == "fetch_failed"
    assert not result.verified
    assert "不能提供任何可能变化的事实" in render_official_evidence(result)


async def test_official_retrieval_rejects_redirect_outside_allowlist():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "official.example":
            return httpx.Response(
                302,
                headers={"location": "https://attacker.example/schedule"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<p>Schedule Match</p>",
            request=request,
        )

    retriever = OfficialSourceRetriever((source(),), transport=httpx.MockTransport(handler))
    assert (await retriever.retrieve("赛程")).status == "fetch_failed"


def test_official_source_rejects_url_outside_allowlist():
    unsafe = OfficialSourceSpec(**{**source().__dict__, "url": "https://attacker.example/schedule"})
    with pytest.raises(ValueError, match="outside its allowlist"):
        OfficialSourceRetriever((unsafe,))


def test_rendered_verified_evidence_is_auditable():
    now = datetime(2026, 7, 12, 1, 2, 3, tzinfo=UTC)
    evidence = OfficialEvidence(
        source_id="example",
        title="Official schedule",
        url="https://official.example/schedule",
        fetched_at=now,
        expires_at=now,
        sha256="b" * 64,
        content="Schedule: Match 1",
    )
    rendered = render_official_evidence(
        OfficialRetrievalResult(status="verified", evidence=(evidence,))
    )
    assert "抓取时间（UTC）：2026-07-12T01:02:03+00:00" in rendered
    assert "内容SHA-256" in rendered
    assert "Schedule: Match 1" in rendered


def test_weather_location_query_extracts_chinese_and_english_city():
    assert weather_location_query("明天上海的天气怎么样？") == "上海"
    assert weather_location_query("上海明天天气会不会下雨？") == "上海"
    assert weather_location_query("明天上海会下雨吗？") == "上海"
    assert weather_location_query("weather in London tomorrow?") == "london"
    assert weather_location_query("为什么天空是蓝色的？") is None


async def test_structured_weather_retrieval_resolves_city_and_caches():
    now = datetime(2026, 7, 12, 2, 3, 4, tzinfo=UTC)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "geocoding-api.open-meteo.com":
            assert request.url.params["name"] == "上海"
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "results": [
                        {
                            "name": "上海",
                            "admin1": "上海",
                            "country": "中国",
                            "latitude": 31.22222,
                            "longitude": 121.45806,
                            "timezone": "Asia/Shanghai",
                        }
                    ]
                },
                request=request,
            )
        assert request.url.host == "api.open-meteo.com"
        assert request.url.params["timezone"] == "Asia/Shanghai"
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "daily": {
                    "time": ["2026-07-12", "2026-07-13", "2026-07-14"],
                    "weather_code": [2, 61, 3],
                    "temperature_2m_max": [34.1, 32.2, 31.0],
                    "temperature_2m_min": [27.0, 26.1, 25.5],
                    "precipitation_probability_max": [20, 70, 30],
                }
            },
            request=request,
        )

    retriever = OfficialSourceRetriever(
        (), clock=lambda: now, transport=httpx.MockTransport(handler)
    )
    first = await retriever.retrieve("明天上海的天气怎么样？")
    second = await retriever.retrieve("上海明天天气会不会下雨？")
    assert first.verified and second.verified
    assert calls == ["geocoding-api.open-meteo.com", "api.open-meteo.com"]
    assert not first.evidence[0].cache_hit
    assert second.evidence[0].cache_hit
    assert '"date_local": "2026-07-13"' in first.evidence[0].content
    assert '"condition": "小雨"' in first.evidence[0].content
    assert '"precipitation_probability_max_percent": 70' in first.evidence[0].content


async def test_structured_weather_fails_closed_when_city_is_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"results": []},
            request=request,
        )

    retriever = OfficialSourceRetriever((), transport=httpx.MockTransport(handler))
    result = await retriever.retrieve("明天不存在城天气怎么样？")
    assert result.status == "fetch_failed"
    assert not result.verified
