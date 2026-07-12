import httpx

from kid_terminal.config import Settings
from kid_terminal.providers import WebResearchResult
from kid_terminal.web_research import (
    QwenTextSearchClient,
    WebEvidenceRetriever,
    render_web_evidence,
)


async def public_resolver(_host: str) -> tuple[str, ...]:
    return ("93.184.216.34",)


async def test_web_evidence_requires_actual_search_and_source_urls():
    retriever = WebEvidenceRetriever(resolver=public_resolver)
    no_search = await retriever.retrieve(
        "布加迪价格", WebResearchResult(text="", search_count=0, strategy="none")
    )
    assert no_search.status == "search_not_executed"
    no_urls = await retriever.retrieve(
        "布加迪价格", WebResearchResult(text="只有摘要", search_count=1, strategy="agent")
    )
    assert no_urls.status == "missing_source_urls"


async def test_web_evidence_fetches_relevant_public_page_and_caches():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><h1>Bugatti Veyron</h1>"
                "<p>Top speed is 407 km/h.</p><p>The original price was €1.16 million.</p></html>"
            ),
            request=request,
        )

    retriever = WebEvidenceRetriever(
        transport=httpx.MockTransport(handler), resolver=public_resolver
    )
    research = WebResearchResult(
        text=(
            "SOURCE https://www.example.com/bugatti-veyron\n"
            "SOURCE https://cars.example.org/bugatti-veyron"
        ),
        search_count=1,
        strategy="agent",
    )
    first = await retriever.retrieve("Bugatti Veyron speed and price", research)
    second = await retriever.retrieve("Bugatti Veyron speed and price", research)
    assert first.verified and second.verified
    assert calls == 2
    assert "407 km/h" in first.evidence[0].content
    assert "€1.16 million" in first.evidence[0].content
    assert second.evidence[0].cache_hit
    rendered = render_web_evidence(first)
    assert "抓取时间UTC" in rendered
    assert "SHA-256" in rendered


async def test_precise_product_evidence_requires_two_independent_hosts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<p>Bugatti Veyron speed is 407 km/h and price was €1.16 million.</p>",
            request=request,
        )

    retriever = WebEvidenceRetriever(
        transport=httpx.MockTransport(handler), resolver=public_resolver
    )
    result = await retriever.retrieve(
        "Bugatti Veyron speed and price",
        WebResearchResult(
            text="SOURCE https://www.example.com/bugatti", search_count=1, strategy="agent"
        ),
    )
    assert result.status == "insufficient_corroboration"


async def test_web_evidence_rejects_private_targets():
    async def private_resolver(_host: str) -> tuple[str, ...]:
        return ("127.0.0.1",)

    retriever = WebEvidenceRetriever(resolver=private_resolver)
    result = await retriever.retrieve(
        "价格",
        WebResearchResult(
            text="SOURCE https://internal.example/secret",
            search_count=1,
            strategy="agent",
        ),
    )
    assert result.status == "source_validation_failed"


async def test_qwen_text_search_forces_search_and_returns_only_source_urls():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert payload["model"] == "qwen3.5-flash"
        assert payload["search_options"]["forced_search"] is True
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "SOURCE https://www.bugatti.com/veyron\n"
                                "SOURCE https://cars.example/veyron"
                            )
                        }
                    }
                ]
            },
            request=request,
        )

    client = QwenTextSearchClient(
        Settings(environment="test", dashscope_api_key="test-key"),
        transport=httpx.MockTransport(handler),
    )
    result = await client.research("检索布加迪")
    assert result.search_count == 1
    assert result.strategy == "forced_text_search"
    assert result.text.count("SOURCE https://") == 2
