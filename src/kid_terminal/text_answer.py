import asyncio
import base64
import binascii
import io
import json
import logging
import wave
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .answer_validation import AnswerClaim, AnswerEnvelope
from .config import Settings
from .providers import ProviderError

logger = logging.getLogger("kid_terminal")
TTS_PATH = "/api/v1/services/audio/tts/SpeechSynthesizer"
CHAT_PATH = "/compatible-mode/v1/chat/completions"
HTTP_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30)


class QwenASRClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=self._base_url(),
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            timeout=httpx.Timeout(30, connect=5),
            transport=transport,
            limits=HTTP_LIMITS,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _base_url(self) -> str:
        if self.settings.qwen_workspace_id:
            return f"https://{self.settings.qwen_workspace_id}.cn-beijing.maas.aliyuncs.com"
        return "https://dashscope.aliyuncs.com"

    @staticmethod
    def _wav(pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(16_000)
            destination.writeframes(pcm)
        return output.getvalue()

    @staticmethod
    def _transcript(content: object) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, dict):
            for key in ("text", "transcript"):
                value = content.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""
        if isinstance(content, list):
            parts = [QwenASRClient._transcript(item) for item in content]
            return "".join(part for part in parts if part).strip()
        return ""

    async def transcribe(self, pcm: bytes) -> str:
        if not pcm:
            raise ProviderError(
                "No speech audio was received", retryable=False, code="transcription_missing"
            )
        audio = base64.b64encode(self._wav(pcm)).decode()
        payload = {
            "model": "qwen3-asr-flash",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": f"data:audio/wav;base64,{audio}"},
                        }
                    ],
                }
            ],
            "stream": False,
            "asr_options": {"language": "zh", "enable_itn": True},
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
            transcript = self._transcript(body["choices"][0]["message"]["content"])
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError) as exc:
            raise ProviderError(
                f"Qwen ASR failed ({type(exc).__name__})",
                retryable=True,
                code="transcription_error",
            ) from exc
        if not transcript:
            raise ProviderError(
                "Qwen ASR returned no transcription",
                retryable=True,
                code="transcription_missing",
            )
        logger.info("Qwen ASR completed chars=%s audio_bytes=%s", len(transcript), len(pcm))
        return transcript[:4000]


class DeepSeekAnswerer:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.deepseek_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            timeout=httpx.Timeout(35, connect=5),
            transport=transport,
            limits=HTTP_LIMITS,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.deepseek_api_key)

    async def answer_envelope(self, instructions: str) -> AnswerEnvelope:
        if not self.enabled:
            raise ProviderError("DeepSeek is not configured", code="deepseek_not_configured")
        payload = {
            "model": self.settings.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是面向小学生的中文事实问答引擎。只回答孩子明确问的字段，默认1到3句，"
                        "你是没有身体的AI语音伙伴，名字叫小助手；不能声称自己会吃饭、睡觉、上厕所"
                        "或放屁。遇到身体、动物叫声和脑筋急转弯可以诚实而轻松地回答。"
                        "孩子常带有‘请问’、停顿、重复、同音字和课文上下文。题名、人名或句子听起来"
                        "不完整时，只问一个容易回答的澄清问题，不要断言它不存在。私人姓名先问是"
                        "同学、家人还是公众人物。明确说‘脑筋急转弯’时先找双关或隐藏前提，不把"
                        "猜测说成事实，也不为了逗趣编造比赛结果。"
                        "当前设备只能听取问题并用合成语音回答，不能播放歌曲、音乐、视频，不能打开"
                        "其他应用或控制现实设备。绝不能说‘我来播放’或声称已经执行这些动作。"
                        "不添加冷知识、未来事件、无关背景或追问。时效、价格、参数、人物身份、数字和"
                        "日期只能使用用户消息中标为已核验的证据；证据不足就用一句话说明无法核实。"
                        "稳定基础知识可以直接回答。claims只列答案中可核验的事实；精确数字、日期、"
                        "价格和名单必须填写证据中真实存在的source_id，不能发明source_id。"
                        "每个有source_id的claim还必须填写evidence_span：从该来源原文逐字复制、"
                        "能直接支撑该claim的最短片段，禁止改写或拼接。"
                        "需要向孩子追问时needs_clarification为true且只问一个问题。只输出JSON对象，"
                        '格式为{"answer":"...","needs_clarification":false,'
                        '"claims":[{"text":"...","source_ids":["..."],'
                        '"evidence_span":"来源原文短片段"}]}。'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"根据以下服务器规则和证据生成最终答案：\n{instructions[:18000]}\n\n"
                        "终检：最多180个中文字符；逐项删除没有证据的精确细节；只输出JSON。"
                    ),
                },
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 500,
            "stream": False,
        }
        try:
            response: httpx.Response | None = None
            for attempt in range(3):
                response = await self._client.post("/chat/completions", json=payload)
                if response.status_code != 429 and response.status_code < 500:
                    break
                await asyncio.sleep(0.5 * 2**attempt)
            assert response is not None
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError) as exc:
            raise ProviderError(
                f"DeepSeek answer failed ({type(exc).__name__})",
                retryable=True,
                code="deepseek_error",
            ) from exc
        try:
            content = str(body["choices"][0]["message"]["content"])
            content = content.strip().removeprefix("```json").removesuffix("```").strip()
            result = json.loads(content)
            answer = str(result["answer"]).strip()
            needs_clarification = bool(result.get("needs_clarification", False))
            raw_claims = result.get("claims", [])
            if not isinstance(raw_claims, list):
                raise TypeError("claims must be a list")
            claims = tuple(
                AnswerClaim(
                    text=str(item["text"]).strip(),
                    source_ids=tuple(str(value) for value in item.get("source_ids", [])),
                    evidence_span=str(item.get("evidence_span", "")).strip(),
                )
                for item in raw_claims
                if isinstance(item, dict)
            )
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "DeepSeek returned an invalid answer", retryable=True, code="deepseek_invalid"
            ) from exc
        if not answer or len(answer) > 240:
            raise ProviderError(
                "DeepSeek answer violated length bounds",
                retryable=False,
                code="deepseek_invalid",
            )
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        logger.info(
            "DeepSeek answer completed model=%s input_tokens=%s output_tokens=%s chars=%s",
            self.settings.deepseek_model,
            usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0,
            usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0,
            len(answer),
        )
        return AnswerEnvelope(
            answer=answer,
            needs_clarification=needs_clarification,
            claims=claims,
        )

    async def answer(self, instructions: str) -> str:
        return (await self.answer_envelope(instructions)).answer


class CosyVoiceSynthesizer:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=self._base_url(),
            headers={
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "X-DashScope-SSE": "enable",
            },
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

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        payload = {
            "model": self.settings.cosyvoice_model,
            "input": {
                "text": text,
                "voice": self.settings.cosyvoice_voice,
                "format": "pcm",
                "sample_rate": 24000,
            },
        }
        try:
            async with self._client.stream("POST", TTS_PATH, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event: dict[str, Any] = json.loads(line[5:].strip())
                    encoded = event.get("output", {}).get("audio", {}).get("data", "")
                    if not encoded:
                        continue
                    try:
                        yield base64.b64decode(str(encoded), validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise ProviderError(
                            "CosyVoice returned invalid audio", code="tts_invalid"
                        ) from exc
        except ProviderError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ProviderError(
                f"CosyVoice failed ({type(exc).__name__})",
                retryable=True,
                code="tts_error",
            ) from exc
