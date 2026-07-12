import asyncio
import base64
import binascii
import contextlib
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import websockets

from .config import Settings
from .prompts import SYSTEM_PROMPT
from .qwen_capabilities import qwen_voice

logger = logging.getLogger("kid_terminal")


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, code: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


@dataclass
class ProviderSession:
    audio: bytearray = field(default_factory=bytearray)
    interrupted: asyncio.Event = field(default_factory=asyncio.Event)
    upstream: Any = None
    config: dict[str, Any] = field(default_factory=dict)
    context: str = ""
    upstream_failed: bool = False


@dataclass(frozen=True)
class WebResearchResult:
    text: str
    search_count: int
    strategy: str


def parse_qwen_event(event: dict[str, Any]) -> tuple[str, Any] | None:
    event_type = str(event.get("type", ""))
    if event_type in {"response.audio.delta", "response.output_audio.delta"}:
        try:
            return "audio", base64.b64decode(event["delta"], validate=True)
        except (KeyError, ValueError, binascii.Error) as exc:
            raise ProviderError("Qwen returned invalid audio data") from exc
    if event_type in {
        "response.text.delta",
        "response.audio_transcript.delta",
        "response.output_text.delta",
    }:
        return "text", str(event.get("delta", ""))
    if event_type == "conversation.item.input_audio_transcription.completed":
        return "user_text", str(event.get("transcript", ""))
    if event_type in {"response.audio.done", "response.output_audio.done"}:
        return "audio_done", None
    if event_type == "response.output_item.done":
        return "output_done", None
    if event_type == "response.done":
        return "done", event.get("response", {})
    if event_type == "error":
        error = event.get("error", {})
        code = str(error.get("code", "unknown"))
        retryable = code.lower() in {
            "common_error",
            "rate_limit_exceeded",
            "server_error",
            "service_unavailable",
            "timeout",
        }
        raise ProviderError(f"Qwen upstream error: {code}", retryable=retryable, code=code)
    return None


class RealtimeAIProvider(ABC):
    @abstractmethod
    async def open(self, config: dict[str, Any]) -> ProviderSession: ...

    @abstractmethod
    async def append_audio(self, session: ProviderSession, data: bytes) -> None: ...

    @abstractmethod
    async def respond(self, session: ProviderSession) -> AsyncIterator[tuple[str, Any]]:
        if False:
            yield "", None

    @abstractmethod
    async def interrupt(self, session: ProviderSession) -> None: ...

    @abstractmethod
    async def close(self, session: ProviderSession) -> None: ...

    async def set_context(self, session: ProviderSession, context: str) -> None:
        session.context = context[:4000]

    async def start_turn(self, session: ProviderSession) -> None:
        session.audio.clear()
        session.interrupted.clear()


class MockRealtimeProvider(RealtimeAIProvider):
    async def open(self, config: dict[str, Any]) -> ProviderSession:
        if config.get("mock_error"):
            raise ProviderError("simulated upstream failure", retryable=True)
        return ProviderSession(config=config.copy(), context=str(config.get("memory_context", "")))

    async def append_audio(self, session: ProviderSession, data: bytes) -> None:
        session.audio.extend(data)
        if session.config.get("standalone_transcription"):
            return

    async def respond(self, session: ProviderSession) -> AsyncIterator[tuple[str, Any]]:
        yield "text", "我听到了。这个问题可以先从简单的一步开始。"
        # Deterministic PCM-like data is sufficient to verify streaming and hashing.
        payload = b"MOCK-PCM16-24KHZ-" + bytes(session.audio[:64])
        for offset in range(0, len(payload), 16):
            if session.interrupted.is_set():
                yield "interrupted", None
                return
            await asyncio.sleep(0.01)
            yield "audio", payload[offset : offset + 16]

    async def interrupt(self, session: ProviderSession) -> None:
        session.interrupted.set()

    async def close(self, session: ProviderSession) -> None:
        session.interrupted.set()


class BufferedAudioProvider(RealtimeAIProvider):
    """Keeps one turn in memory; no long-lived model connection is opened."""

    async def open(self, config: dict[str, Any]) -> ProviderSession:
        return ProviderSession(config=config.copy(), context=str(config.get("memory_context", "")))

    async def append_audio(self, session: ProviderSession, data: bytes) -> None:
        session.audio.extend(data)

    async def respond(self, session: ProviderSession) -> AsyncIterator[tuple[str, Any]]:
        if False:
            yield "", None

    async def interrupt(self, session: ProviderSession) -> None:
        session.interrupted.set()

    async def close(self, session: ProviderSession) -> None:
        session.audio.clear()
        session.interrupted.set()


class QwenRealtimeProvider(RealtimeAIProvider):
    """Native adapter following Alibaba Cloud's Qwen Omni Realtime event protocol."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._blocked_until = 0.0
        self._blocked_code: str | None = None
        self._last_success_at = 0.0

    @property
    def runtime_status(self) -> str:
        if time.monotonic() < self._blocked_until:
            return "degraded"
        if self._last_success_at:
            return "ok"
        return "not_checked"

    def _classify_connection_error(self, exc: Exception) -> ProviderError:
        detail = str(exc).lower()
        if any(marker in detail for marker in ("arrearage", "good standing", "access denied")):
            self._blocked_until = time.monotonic() + 60
            self._blocked_code = "account_unavailable"
            return ProviderError(
                "Qwen account is unavailable", retryable=False, code=self._blocked_code
            )
        return ProviderError("Qwen connection closed during response", retryable=True)

    @staticmethod
    def _event(event_type: str, **values: Any) -> str:
        return json.dumps(
            {"event_id": f"event_{uuid.uuid4().hex}", "type": event_type, **values},
            ensure_ascii=False,
        )

    def _url(self) -> str:
        if self.settings.qwen_base_url:
            base_url = self.settings.qwen_base_url.rstrip("/")
        elif self.settings.qwen_workspace_id:
            host = (
                f"{self.settings.qwen_workspace_id}.{self.settings.qwen_region}.maas.aliyuncs.com"
            )
            base_url = f"wss://{host}/api-ws/v1/realtime"
        elif self.settings.qwen_region == "cn-beijing":
            base_url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        else:
            base_url = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
        return f"{base_url}?model={self.settings.qwen_model}"

    async def _connect(self) -> Any:
        if time.monotonic() < self._blocked_until:
            raise ProviderError(
                "Qwen account is temporarily unavailable",
                retryable=False,
                code=self._blocked_code,
            )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await websockets.connect(
                    self._url(),
                    additional_headers={
                        "Authorization": f"Bearer {self.settings.dashscope_api_key}"
                    },
                    max_size=self.settings.ws_max_message_bytes,
                    open_timeout=10,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=10,
                )
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * 2**attempt)
        raise ProviderError(
            "Qwen connection failed after bounded retries", retryable=True
        ) from last_error

    async def _configure(self, session: ProviderSession) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y年%m月%d日")
        if final_instructions := session.config.get("final_instructions"):
            instructions = f"{SYSTEM_PROMPT}\n\n{final_instructions}\n服务器当前日期：{today}。"
        else:
            instructions = f"{SYSTEM_PROMPT}\n服务器当前日期（北京时间）：{today}。"
        if (
            not session.config.get("final_instructions")
            and session.config.get("answer_length", "short") == "short"
        ):
            instructions += "\n当前回答长度策略：简短，最多80个汉字、4句话。"
        elif not session.config.get("final_instructions"):
            instructions += (
                "\n当前回答长度策略：科普详解，约200到350个汉字、6到10句话；"
                "优先保证有趣、具体和可记忆；按问题类型给科学证据或道德行动。"
            )
        if session.context:
            instructions += f"\n\n以下是已脱敏的历史摘要，仅用于保持上下文：\n{session.context}"
        await session.upstream.send(
            self._event(
                "session.update",
                session={
                    "modalities": ["text"]
                    if session.config.get("text_only")
                    else ["text", "audio"],
                    "instructions": instructions,
                    "voice": qwen_voice(
                        self.settings.qwen_model,
                        str(session.config.get("voice", "Ethan")),
                    ),
                    "input_audio_format": "pcm",
                    "output_audio_format": "pcm",
                    "turn_detection": None,
                    "enable_search": bool(session.config.get("web_search", True)),
                    "search_options": {"enable_source": True},
                },
            )
        )

    async def open(self, config: dict[str, Any]) -> ProviderSession:
        if not self.settings.dashscope_api_key:
            raise ProviderError("Qwen API key is not configured")
        session = ProviderSession(
            upstream=await self._connect(),
            config=config.copy(),
            context=str(config.get("memory_context", ""))[:4000],
        )
        await self._configure(session)
        return session

    async def append_audio(self, session: ProviderSession, data: bytes) -> None:
        if len(session.audio) + len(data) > 20 * 1024 * 1024:
            raise ProviderError("Qwen turn audio buffer exceeded 20 MiB")
        session.audio.extend(data)
        if not session.upstream_failed:
            try:
                await session.upstream.send(
                    self._event("input_audio_buffer.append", audio=base64.b64encode(data).decode())
                )
            except (websockets.ConnectionClosed, OSError):
                # Audio is already buffered locally and will be replayed once at speech.stop.
                session.upstream_failed = True

    async def _restart_before_output(self, session: ProviderSession) -> None:
        with contextlib.suppress(Exception):
            await session.upstream.close()
        session.upstream = await self._connect()
        await self._configure(session)
        for offset in range(0, len(session.audio), 64 * 1024):
            await session.upstream.send(
                self._event(
                    "input_audio_buffer.append",
                    audio=base64.b64encode(session.audio[offset : offset + 64 * 1024]).decode(),
                )
            )
        session.upstream_failed = False

    async def respond(self, session: ProviderSession) -> AsyncIterator[tuple[str, Any]]:
        if session.upstream_failed:
            await self._restart_before_output(session)
        emitted = False
        search_fallback = False
        for attempt in range(2):
            try:
                await session.upstream.send(self._event("input_audio_buffer.commit"))
                await session.upstream.send(self._event("response.create"))
                while True:
                    try:
                        raw = await asyncio.wait_for(
                            session.upstream.recv(),
                            timeout=self.settings.qwen_event_timeout_seconds,
                        )
                    except TimeoutError as exc:
                        raise ProviderError(
                            "Qwen response event timed out", retryable=True
                        ) from exc
                    parsed = parse_qwen_event(json.loads(raw))
                    if not parsed:
                        continue
                    kind, value = parsed
                    if kind == "done":
                        self._last_success_at = time.monotonic()
                        self._blocked_until = 0.0
                        self._blocked_code = None
                        search = value.get("usage", {}).get("plugins", {}).get("search", {})
                        logger.info(
                            "Qwen search usage count=%s strategy=%s",
                            search.get("count", 0),
                            search.get("strategy", "none"),
                        )
                        if search_fallback:
                            session.config["web_search"] = True
                            await self._configure(session)
                        return
                    if kind in {"audio", "text"}:
                        emitted = True
                    yield kind, value
            except (ProviderError, websockets.ConnectionClosed, OSError) as exc:
                provider_error = (
                    exc if isinstance(exc, ProviderError) else self._classify_connection_error(exc)
                )
                retryable = provider_error.retryable
                if attempt == 0 and not emitted and retryable:
                    if provider_error.code == "COMMON_ERROR" and session.config.get(
                        "web_search", True
                    ):
                        session.config["web_search"] = False
                        search_fallback = True
                    await self._restart_before_output(session)
                    continue
                if isinstance(exc, ProviderError):
                    if search_fallback:
                        session.config["web_search"] = True
                        session.upstream_failed = True
                    raise
                if search_fallback:
                    session.config["web_search"] = True
                    session.upstream_failed = True
                raise provider_error from exc

    async def set_context(self, session: ProviderSession, context: str) -> None:
        await super().set_context(session, context)
        await self._configure(session)

    async def interrupt(self, session: ProviderSession) -> None:
        session.interrupted.set()
        await session.upstream.send(self._event("response.cancel"))

    async def close(self, session: ProviderSession) -> None:
        if session.upstream:
            await session.upstream.close()

    async def respond_direct(
        self,
        session: ProviderSession,
        instructions: str,
        *,
        web_search: bool,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Generate the final multimodal response in the existing audio conversation."""
        session.config["final_instructions"] = instructions
        session.config["web_search"] = web_search
        session.config["text_only"] = False
        session.interrupted.clear()
        await self._configure(session)
        try:
            await session.upstream.send(self._event("response.create"))
            while True:
                raw = await asyncio.wait_for(
                    session.upstream.recv(), timeout=self.settings.qwen_event_timeout_seconds
                )
                event = json.loads(raw)
                event_type = str(event.get("type", ""))
                if event_type.endswith(".done"):
                    logger.debug("Qwen direct terminal event type=%s", event_type)
                parsed = parse_qwen_event(event)
                if not parsed:
                    continue
                kind, value = parsed
                if kind == "done":
                    status = str(value.get("status", "completed"))
                    if status != "completed":
                        raise ProviderError(
                            f"Qwen direct response ended with status={status}",
                            retryable=True,
                            code="incomplete_response",
                        )
                    search = value.get("usage", {}).get("plugins", {}).get("search", {})
                    logger.info(
                        "Qwen direct search usage count=%s strategy=%s",
                        search.get("count", 0),
                        search.get("strategy", "none"),
                    )
                    self._last_success_at = time.monotonic()
                    self._blocked_until = 0.0
                    self._blocked_code = None
                    return
                if kind in {"audio_done", "output_done"}:
                    continue
                yield kind, value
        except ProviderError:
            raise
        except (TimeoutError, websockets.ConnectionClosed, OSError, json.JSONDecodeError) as exc:
            classified = self._classify_connection_error(exc)
            if classified.retryable:
                classified = ProviderError(
                    f"Qwen direct response failed ({type(exc).__name__})",
                    retryable=True,
                    code=classified.code,
                )
            raise classified from exc

    async def research(self, session: ProviderSession, instructions: str) -> WebResearchResult:
        """Run a silent text-only search pass before final direct-audio generation."""
        session.config["final_instructions"] = instructions
        session.config["web_search"] = True
        session.config["text_only"] = True
        session.interrupted.clear()
        await self._configure(session)
        text_parts: list[str] = []
        try:
            await session.upstream.send(self._event("response.create"))
            while True:
                raw = await asyncio.wait_for(
                    session.upstream.recv(), timeout=self.settings.qwen_event_timeout_seconds
                )
                parsed = parse_qwen_event(json.loads(raw))
                if not parsed:
                    continue
                kind, value = parsed
                if kind == "text":
                    text_parts.append(str(value))
                    if sum(len(part) for part in text_parts) > 12_000:
                        raise ProviderError(
                            "Qwen research response exceeded text limit",
                            retryable=False,
                            code="research_too_large",
                        )
                    continue
                if kind != "done":
                    continue
                status = str(value.get("status", "completed"))
                if status != "completed":
                    raise ProviderError(
                        f"Qwen research response ended with status={status}",
                        retryable=True,
                        code="incomplete_research",
                    )
                search = value.get("usage", {}).get("plugins", {}).get("search", {})
                count = int(search.get("count", 0))
                strategy = str(search.get("strategy", "none"))
                logger.info("Qwen research search usage count=%s strategy=%s", count, strategy)
                return WebResearchResult(
                    text="".join(text_parts)[:12_000],
                    search_count=count,
                    strategy=strategy,
                )
        except ProviderError:
            raise
        except (TimeoutError, websockets.ConnectionClosed, OSError, json.JSONDecodeError) as exc:
            classified = self._classify_connection_error(exc)
            raise ProviderError(
                f"Qwen research response failed ({type(exc).__name__})",
                retryable=classified.retryable,
                code=classified.code,
            ) from exc


def create_provider(settings: Settings) -> RealtimeAIProvider:
    if settings.ai_provider == "hybrid":
        return BufferedAudioProvider()
    if settings.ai_provider == "qwen_realtime":
        return QwenRealtimeProvider(settings)
    return MockRealtimeProvider()
