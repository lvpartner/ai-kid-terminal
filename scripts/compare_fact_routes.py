#!/usr/bin/env python3
"""Compare the installed family pipeline and an independent text route on 60 fact questions."""

import argparse
import asyncio
import base64
import json
import os
import re
import statistics
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx
import websockets
from websockets.typing import Subprotocol

from kid_terminal.providers import WebResearchResult
from kid_terminal.web_research import WebEvidenceRetriever

CHAT_PATH = "/compatible-mode/v1/chat/completions"
TTS_PATH = "/api/v1/services/audio/tts/SpeechSynthesizer"


@dataclass(frozen=True)
class FactCase:
    id: str
    category: str
    question: str
    reference: str


@dataclass
class RouteResult:
    answer: str = ""
    error: str = ""
    first_audio_seconds: float | None = None
    total_seconds: float | None = None
    audio_bytes: int = 0
    asr_text: str = ""
    asr_seconds: float | None = None
    model_seconds: float | None = None
    validation_seconds: float | None = None
    tts_first_seconds: float | None = None
    tts_total_seconds: float | None = None
    source_count: int = 0
    validated_source_count: int = 0
    evidence_status: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


def cases() -> list[FactCase]:
    values = [
        ("science", "水的化学式是什么？", "水的化学式是H₂O，每个水分子含两个氢原子和一个氧原子。"),
        ("science", "真空中光速大约是多少？", "真空光速精确值是每秒299792458米，约每秒30万公里。"),
        ("science", "声音能在真空里传播吗？", "不能；声音需要物质介质传播。"),
        ("science", "标准大气压下纯水多少摄氏度沸腾？", "标准大气压下纯水沸点是100摄氏度。"),
        ("science", "太阳是一颗行星还是恒星？", "太阳是恒星。"),
        ("science", "地球唯一的天然卫星叫什么？", "地球唯一的天然卫星是月球。"),
        ("science", "元素周期表中金的符号是什么？", "金的元素符号是Au。"),
        ("science", "太阳系最大的行星是哪颗？", "木星是太阳系最大的行星。"),
        ("science", "植物光合作用主要吸收什么气体？", "植物光合作用主要吸收二氧化碳，并释放氧气。"),
        ("science", "DNA的中文全称是什么？", "DNA的中文全称是脱氧核糖核酸。"),
        ("geography", "世界上最大的海洋是什么？", "太平洋是世界面积最大的海洋。"),
        ("geography", "澳大利亚的首都是什么？", "澳大利亚首都是堪培拉，不是悉尼。"),
        ("geography", "巴西的首都是什么？", "巴西首都是巴西利亚，不是里约热内卢。"),
        ("geography", "赤道的纬度是多少度？", "赤道纬度是0度。"),
        ("geography", "撒哈拉沙漠主要位于哪个洲？", "撒哈拉沙漠主要位于非洲北部。"),
        ("geography", "中国最长的河流是哪一条？", "长江是中国最长的河流。"),
        ("geography", "按陆地面积计算，世界最大的国家是哪个？", "俄罗斯是陆地面积最大的国家。"),
        ("geography", "日本使用的货币叫什么？", "日本货币是日元。"),
        ("geography", "世界最高峰是什么？", "按海拔计算，珠穆朗玛峰是世界最高峰。"),
        ("geography", "地球通常分为几个大洲？", "中国常用地理口径把地球分为七大洲。"),
        ("history", "中国历史上第一位皇帝是谁？", "通常指秦始皇嬴政，他建立秦朝并采用皇帝称号。"),
        ("history", "活字印刷术通常认为是谁发明的？", "中国北宋的毕昇发明了泥活字印刷术。"),
        ("history", "第二次世界大战在哪一年结束？", "第二次世界大战在1945年结束。"),
        ("history", "联合国在哪一年成立？", "联合国成立于1945年。"),
        ("culture", "《蒙娜丽莎》的作者是谁？", "《蒙娜丽莎》由列奥纳多·达·芬奇创作。"),
        ("culture", "《哈姆雷特》的作者是谁？", "《哈姆雷特》的作者是威廉·莎士比亚。"),
        ("culture", "兵马俑位于中国哪个城市？", "秦始皇兵马俑位于陕西省西安市。"),
        ("culture", "诺贝尔奖以谁的名字命名？", "诺贝尔奖以阿尔弗雷德·诺贝尔的名字命名。"),
        ("culture", "《静夜思》的作者是谁？", "《静夜思》的作者是唐代诗人李白。"),
        (
            "culture",
            "公历闰年的基本判断规则是什么？",
            "年份能被4整除通常是闰年；整百年还必须能被400整除。",
        ),
        ("technology", "二进制使用哪两个数字？", "二进制使用0和1。"),
        ("technology", "HTTP和HTTPS的默认端口分别是什么？", "HTTP默认端口80，HTTPS默认端口443。"),
        ("technology", "RAM断电后通常还能保留数据吗？", "普通易失性RAM断电后通常不能保留数据。"),
        ("technology", "Python语言最初由谁创造？", "Python最初由吉多·范罗苏姆创造。"),
        ("technology", "USB-C接口正反都能插吗？", "USB-C连接器采用可正反插的对称设计。"),
        (
            "technology",
            "飞机的黑匣子通常是什么颜色？",
            "飞行记录器外壳通常是醒目的橙色，不是黑色。",
        ),
        (
            "technology",
            "GPS定位通常至少需要几颗卫星？",
            "三维定位并校正接收机时钟通常至少需要4颗卫星。",
        ),
        ("technology", "第一代iPhone在哪一年发布？", "第一代iPhone于2007年发布。"),
        (
            "technology",
            "安卓系统主要由哪家公司推动开发？",
            "Android主要由Google和开放手机联盟推动开发。",
        ),
        (
            "technology",
            "布加迪威龙普通版和Super Sport的速度、当年价格有什么区别？",
            "普通威龙约407公里每小时、当年约125万美元；Super Sport纪录约431公里每小时、"
            "售价约240万美元，具体口径随版本市场而异。",
        ),
        ("biology", "鲸鱼是鱼类还是哺乳动物？", "鲸鱼是哺乳动物，用肺呼吸并哺乳幼崽。"),
        (
            "biology",
            "蝙蝠是不是唯一能持续主动飞行的哺乳动物？",
            "是；蝙蝠是唯一能持续主动飞行的哺乳动物。",
        ),
        ("biology", "章鱼有几颗心脏？", "章鱼有三颗心脏。"),
        ("biology", "蜘蛛通常有几条腿？", "蜘蛛通常有8条腿。"),
        ("biology", "企鹅主要生活在北半球还是南半球？", "野生企鹅主要生活在南半球。"),
        ("biology", "竹子属于树还是草？", "竹子属于禾本科草本植物，也就是草类。"),
        ("biology", "真菌属于植物吗？", "不属于；现代生物分类把真菌列为独立于植物的类群。"),
        (
            "biology",
            "成年人通常有多少颗恒牙？",
            "包含智齿时成年人通常有32颗恒牙；有些人智齿缺失或未萌出。",
        ),
        ("biology", "血液为什么看起来是红色的？", "红色主要来自红细胞中的血红蛋白。"),
        ("biology", "青蛙属于哪一类动物？", "青蛙属于两栖动物。"),
        (
            "health",
            "抗生素能治疗普通病毒性感冒吗？",
            "抗生素针对细菌，对普通病毒性感冒无效，除非医生确认合并细菌感染。",
        ),
        ("health", "6到12岁儿童通常每晚建议睡多久？", "权威睡眠建议通常为每24小时9到12小时。"),
        ("health", "洗手时用肥皂搓洗至少多久比较合适？", "常见公共卫生建议是用肥皂搓洗至少20秒。"),
        ("health", "人体最大的器官是什么？", "按表面积和重量通常认为皮肤是人体最大的器官。"),
        (
            "health",
            "晒太阳时SPF主要表示防哪种紫外线？",
            "SPF主要衡量对引起晒伤的UVB防护，不完整代表UVA防护。",
        ),
        ("current", "现在的联合国秘书长是谁？", "截至2026年，联合国秘书长是安东尼奥·古特雷斯。"),
        (
            "current",
            "2026年男子世界杯由哪些国家共同举办？",
            "2026年男子世界杯由加拿大、墨西哥和美国共同举办。",
        ),
        ("current", "目前世界最高的建筑是什么？", "截至2026年，世界最高建筑仍是迪拜哈利法塔。"),
        ("current", "现在中国国家主席是谁？", "截至2026年，中国国家主席是习近平。"),
        (
            "current",
            "现在世界人口大约有多少？",
            "2026年世界人口约为82亿量级，实时估算会随来源和日期变化。",
        ),
    ]
    return [
        FactCase(id=f"fact-{index:02d}", category=category, question=question, reference=reference)
        for index, (category, question, reference) in enumerate(values, 1)
    ]


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percent))
    return round(ordered[index], 3)


def normalize(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).lower()


class Benchmark:
    def __init__(self, output: Path, concurrency: int) -> None:
        self.output = output
        self.concurrency = concurrency
        self.api_key = os.environ["DASHSCOPE_API_KEY"]
        self.workspace = os.environ.get("QWEN_WORKSPACE_ID", "")
        self.base = (
            f"https://{self.workspace}.cn-beijing.maas.aliyuncs.com"
            if self.workspace
            else "https://dashscope.aliyuncs.com"
        )
        self.headers = {"Authorization": f"Bearer {self.api_key}"}
        self.web_retriever = WebEvidenceRetriever()

    async def synthesize_question(
        self, client: httpx.AsyncClient, case: FactCase, directory: Path
    ) -> tuple[Path, Path]:
        response: httpx.Response | None = None
        for attempt in range(6):
            response = await client.post(
                self.base + TTS_PATH,
                json={
                    "model": "cosyvoice-v3-flash",
                    "input": {
                        "text": case.question,
                        "voice": "longanyang",
                        "format": "wav",
                        "sample_rate": 24000,
                    },
                },
                timeout=30,
            )
            if response.status_code != 429 and response.status_code < 500:
                break
            await asyncio.sleep(min(15, 1.5 * 2**attempt))
        assert response is not None
        response.raise_for_status()
        url = response.json()["output"]["audio"]["url"]
        audio = await client.get(url, timeout=30)
        audio.raise_for_status()
        wav = directory / f"{case.id}.wav"
        pcm = directory / f"{case.id}.pcm"
        wav.write_bytes(audio.content)
        process = await asyncio.create_subprocess_exec(
            "sox",
            str(wav),
            "-t",
            "raw",
            "-r",
            "16000",
            "-c",
            "1",
            "-b",
            "16",
            "-e",
            "signed-integer",
            str(pcm),
        )
        if await process.wait():
            raise RuntimeError(f"sox failed for {case.id}")
        return wav, pcm

    async def prepare_audio(
        self, items: list[FactCase], directory: Path
    ) -> dict[str, tuple[Path, Path]]:
        semaphore = asyncio.Semaphore(min(2, self.concurrency))
        async with httpx.AsyncClient(headers=self.headers) as client:

            async def one(case: FactCase) -> tuple[str, tuple[Path, Path]]:
                async with semaphore:
                    return case.id, await self.synthesize_question(client, case, directory)

            return dict(await asyncio.gather(*(one(case) for case in items)))

    async def enroll(self, client: httpx.AsyncClient, label: str) -> str:
        admin_key = os.environ["ADMIN_API_KEY"]
        enrollment = await client.post(
            "http://127.0.0.1:8000/v1/admin/enrollments",
            headers={"X-Admin-Key": admin_key},
            json={"label": label, "expires_minutes": 30},
        )
        enrollment.raise_for_status()
        registered = await client.post(
            "http://127.0.0.1:8000/v1/enroll",
            json={
                "enrollment_token": enrollment.json()["enrollment_token"],
                "device_name": label,
                "app_version": "benchmark",
                "os_version": "synthetic-cosyvoice",
            },
        )
        registered.raise_for_status()
        return str(registered.json()["access_token"])

    async def run_route_a_group(
        self, items: list[FactCase], audio: dict[str, tuple[Path, Path]], group: int
    ) -> dict[str, RouteResult]:
        results: dict[str, RouteResult] = {}
        async with httpx.AsyncClient(timeout=30) as client:
            token = await self.enroll(client, f"fact-route-a-{group}")
        async with websockets.connect(
            "ws://127.0.0.1:8000/v1/device/ws",
            additional_headers={
                "Authorization": f"Bearer {token}",
                "X-Audio-Codecs": "g711_ulaw_8000,pcm_s16le_24000",
            },
            subprotocols=[Subprotocol("kid-terminal.v1")],
            max_size=2 * 1024 * 1024,
        ) as ws:
            await asyncio.wait_for(ws.recv(), timeout=15)
            for case in items:
                result = RouteResult()
                try:
                    await ws.send(
                        json.dumps({"type": "speech.start", "event_id": str(uuid.uuid4())})
                    )
                    await asyncio.wait_for(ws.recv(), timeout=10)
                    pcm = audio[case.id][1].read_bytes()
                    for offset in range(0, len(pcm), 32_000):
                        await ws.send(pcm[offset : offset + 32_000])
                    await ws.send(
                        json.dumps({"type": "speech.stop", "event_id": str(uuid.uuid4())})
                    )
                    started = time.monotonic()
                    text_parts: list[str] = []
                    while True:
                        message = await asyncio.wait_for(ws.recv(), timeout=120)
                        if isinstance(message, bytes):
                            if result.first_audio_seconds is None:
                                result.first_audio_seconds = time.monotonic() - started
                            result.audio_bytes += len(message)
                            continue
                        event = json.loads(message)
                        if event.get("type") == "ai.text.delta":
                            text_parts.append(str(event.get("text", "")))
                        elif event.get("type") == "error":
                            raise RuntimeError(str(event.get("code", "unknown")))
                        elif event.get("type") == "ai.response.done":
                            break
                    result.answer = "".join(text_parts)
                    result.total_seconds = time.monotonic() - started
                except Exception as exc:
                    result.error = type(exc).__name__
                results[case.id] = result
        return results

    async def run_route_a(
        self, items: list[FactCase], audio: dict[str, tuple[Path, Path]]
    ) -> dict[str, RouteResult]:
        groups = [items[index : index + 10] for index in range(0, len(items), 10)]
        semaphore = asyncio.Semaphore(min(3, self.concurrency))

        async def one(group: list[FactCase], index: int) -> dict[str, RouteResult]:
            async with semaphore:
                return await self.run_route_a_group(group, audio, index)

        packets = await asyncio.gather(*(one(group, index) for index, group in enumerate(groups)))
        return {key: value for packet in packets for key, value in packet.items()}

    async def transcribe(self, client: httpx.AsyncClient, wav: Path) -> tuple[str, float]:
        data = base64.b64encode(wav.read_bytes()).decode()
        started = time.monotonic()
        payload = {
            "model": "qwen3-asr-flash",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": f"data:audio/wav;base64,{data}"},
                        }
                    ],
                }
            ],
            "stream": False,
            "asr_options": {"language": "zh", "enable_itn": True},
        }
        response: httpx.Response | None = None
        for attempt in range(5):
            response = await client.post(self.base + CHAT_PATH, json=payload, timeout=30)
            if response.status_code != 429 and response.status_code < 500:
                break
            await asyncio.sleep(min(12, 2**attempt))
        assert response is not None
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"]), time.monotonic() - started

    async def text_answer(
        self, client: httpx.AsyncClient, question: str
    ) -> tuple[dict[str, Any], float, dict[str, int]]:
        prompt = f"""你是儿童事实问答引擎。联网搜索后用简洁中文回答：{question}
要求：只写有来源支持的事实；精确数字说明口径；冲突则说明；80到180字。
只输出JSON：{{"answer":"...","sources":[{{"title":"...","url":"https://..."}}]}}。"""
        started = time.monotonic()
        payload = {
            "model": "qwen3.5-flash",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 700,
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
            "enable_search": True,
            "search_options": {"forced_search": True, "enable_source": True},
        }
        response: httpx.Response | None = None
        for attempt in range(5):
            response = await client.post(self.base + CHAT_PATH, json=payload, timeout=45)
            if response.status_code != 429 and response.status_code < 500:
                break
            await asyncio.sleep(min(12, 2**attempt))
        assert response is not None
        response.raise_for_status()
        elapsed = time.monotonic() - started
        body = response.json()
        content = str(body["choices"][0]["message"]["content"])
        content = content.strip().removeprefix("```json").removesuffix("```").strip()
        usage = body.get("usage", {})
        return (
            json.loads(content),
            elapsed,
            {
                "input": int(usage.get("prompt_tokens", 0)),
                "output": int(usage.get("completion_tokens", 0)),
            },
        )

    async def stream_tts(self, client: httpx.AsyncClient, text: str) -> tuple[float, float, int]:
        for attempt in range(5):
            started = time.monotonic()
            first: float | None = None
            audio_bytes = 0
            retry = False
            async with client.stream(
                "POST",
                self.base + TTS_PATH,
                headers={"X-DashScope-SSE": "enable"},
                json={
                    "model": "cosyvoice-v3-flash",
                    "input": {
                        "text": text,
                        "voice": "longanyang",
                        "format": "wav",
                        "sample_rate": 24000,
                    },
                },
                timeout=45,
            ) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    retry = True
                else:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        event = json.loads(line[5:].strip())
                        chunk = event.get("output", {}).get("audio", {}).get("data", "")
                        if chunk:
                            decoded = base64.b64decode(chunk)
                            audio_bytes += len(decoded)
                            if first is None:
                                first = time.monotonic() - started
            if not retry:
                return first or 0.0, time.monotonic() - started, audio_bytes
            await asyncio.sleep(min(12, 2**attempt))
        raise RuntimeError("TTS retries exhausted")

    async def run_route_b_one(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        case: FactCase,
        audio: dict[str, tuple[Path, Path]],
    ) -> tuple[str, RouteResult]:
        result = RouteResult()
        async with semaphore:
            try:
                transcript, result.asr_seconds = await self.transcribe(client, audio[case.id][0])
                result.asr_text = transcript
                value, result.model_seconds, usage = await self.text_answer(client, transcript)
                result.answer = str(value.get("answer", ""))
                sources = value.get("sources", [])
                urls = [
                    str(source.get("url", ""))
                    for source in sources
                    if isinstance(source, dict)
                    and str(source.get("url", "")).startswith("https://")
                ]
                result.source_count = len(urls)
                result.input_tokens = usage["input"]
                result.output_tokens = usage["output"]
                validation_started = time.monotonic()
                research = WebResearchResult(
                    text="\n".join(f"SOURCE {url}" for url in urls),
                    search_count=1 if urls else 0,
                    strategy="forced",
                )
                evidence = await self.web_retriever.retrieve(transcript, research)
                result.validation_seconds = time.monotonic() - validation_started
                result.evidence_status = evidence.status
                result.validated_source_count = len(evidence.evidence)
                (
                    result.tts_first_seconds,
                    result.tts_total_seconds,
                    result.audio_bytes,
                ) = await self.stream_tts(client, result.answer)
                result.first_audio_seconds = sum(
                    value or 0
                    for value in (
                        result.asr_seconds,
                        result.model_seconds,
                        result.validation_seconds,
                        result.tts_first_seconds,
                    )
                )
                result.total_seconds = sum(
                    value or 0
                    for value in (
                        result.asr_seconds,
                        result.model_seconds,
                        result.validation_seconds,
                        result.tts_total_seconds,
                    )
                )
            except Exception as exc:
                result.error = type(exc).__name__
        return case.id, result

    async def run_route_b(
        self, items: list[FactCase], audio: dict[str, tuple[Path, Path]]
    ) -> dict[str, RouteResult]:
        semaphore = asyncio.Semaphore(self.concurrency)
        async with httpx.AsyncClient(headers=self.headers) as client:
            return dict(
                await asyncio.gather(
                    *(self.run_route_b_one(client, semaphore, case, audio) for case in items)
                )
            )

    async def judge_batch(
        self,
        client: httpx.AsyncClient,
        items: list[FactCase],
        route_a: dict[str, RouteResult],
        route_b: dict[str, RouteResult],
    ) -> list[dict[str, Any]]:
        packet = []
        mapping: dict[str, bool] = {}
        for index, case in enumerate(items):
            swapped = index % 2 == 1
            mapping[case.id] = swapped
            answers = (
                [route_b[case.id].answer, route_a[case.id].answer]
                if swapped
                else [route_a[case.id].answer, route_b[case.id].answer]
            )
            packet.append(
                {
                    "id": case.id,
                    "question": case.question,
                    "reference": case.reference,
                    "answer_x": answers[0],
                    "answer_y": answers[1],
                }
            )
        prompt = """你是严格事实评测员。参考答案定义必须命中的核心事实，但不要惩罚正确补充。
分别评估X和Y，不猜测答案背后的路线。每个答案输出：accuracy整数0到5、completeness整数0到2、
unsupported_precise_claims整数、wrong_claims数组、refusal布尔、useful布尔。
accuracy=5表示核心事实全对且没有错误；3表示部分正确；0表示错误或无答案。
只输出JSON对象，字段evaluations，每项含id、x、y。题目如下：\n""" + json.dumps(
            packet, ensure_ascii=False
        )
        response = await client.post(
            self.base + CHAT_PATH,
            json={
                "model": "qwen3.5-plus",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 3000,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        response.raise_for_status()
        content = str(response.json()["choices"][0]["message"]["content"])
        value = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
        output = []
        for evaluation in value.get("evaluations", []):
            case_id = str(evaluation["id"])
            swapped = mapping[case_id]
            output.append(
                {
                    "id": case_id,
                    "route_a": evaluation["y"] if swapped else evaluation["x"],
                    "route_b": evaluation["x"] if swapped else evaluation["y"],
                }
            )
        return output

    async def judge(
        self,
        items: list[FactCase],
        route_a: dict[str, RouteResult],
        route_b: dict[str, RouteResult],
    ) -> dict[str, dict[str, Any]]:
        groups = [items[index : index + 5] for index in range(0, len(items), 5)]
        semaphore = asyncio.Semaphore(3)
        async with httpx.AsyncClient(headers=self.headers) as client:

            async def one(group: list[FactCase]) -> list[dict[str, Any]]:
                async with semaphore:
                    return await self.judge_batch(client, group, route_a, route_b)

            packets = await asyncio.gather(*(one(group) for group in groups))
        return {item["id"]: item for packet in packets for item in packet}

    def report(
        self,
        items: list[FactCase],
        route_a: dict[str, RouteResult],
        route_b: dict[str, RouteResult],
        judgments: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        def route_summary(name: str, results: dict[str, RouteResult]) -> dict[str, Any]:
            valid = [result for result in results.values() if not result.error]
            scores = [float(judgments[case.id][name]["accuracy"]) for case in items]
            unsupported = [
                int(judgments[case.id][name]["unsupported_precise_claims"]) for case in items
            ]
            refusal = [bool(judgments[case.id][name]["refusal"]) for case in items]
            useful = [bool(judgments[case.id][name]["useful"]) for case in items]
            first = [result.first_audio_seconds for result in valid if result.first_audio_seconds]
            total = [result.total_seconds for result in valid if result.total_seconds]
            return {
                "completed": len(valid),
                "mean_accuracy_0_to_5": round(statistics.mean(scores), 3),
                "perfect_accuracy_rate": round(
                    sum(score == 5 for score in scores) / len(scores), 3
                ),
                "useful_rate": round(sum(useful) / len(useful), 3),
                "refusal_rate": round(sum(refusal) / len(refusal), 3),
                "unsupported_precise_claims": sum(unsupported),
                "first_audio_p50_seconds": percentile([float(value) for value in first], 0.5),
                "first_audio_p95_seconds": percentile([float(value) for value in first], 0.95),
                "total_p50_seconds": percentile([float(value) for value in total], 0.5),
                "total_p95_seconds": percentile([float(value) for value in total], 0.95),
            }

        similarities = [
            SequenceMatcher(
                None, normalize(case.question), normalize(route_b[case.id].asr_text)
            ).ratio()
            for case in items
        ]
        categories: dict[str, dict[str, float]] = {}
        for category in sorted({case.category for case in items}):
            selected = [case for case in items if case.category == category]
            categories[category] = {
                "route_a_accuracy": round(
                    statistics.mean(judgments[case.id]["route_a"]["accuracy"] for case in selected),
                    3,
                ),
                "route_b_accuracy": round(
                    statistics.mean(judgments[case.id]["route_b"]["accuracy"] for case in selected),
                    3,
                ),
            }
        return {
            "question_count": len(items),
            "method": "same CosyVoice synthetic Mandarin input; blind paired Qwen3.5-Plus judge",
            "route_a": route_summary("route_a", route_a),
            "route_b": route_summary("route_b", route_b),
            "route_b_asr_similarity_mean": round(statistics.mean(similarities), 4),
            "route_b_validated_source_rate": round(
                sum(route_b[case.id].validated_source_count > 0 for case in items) / len(items), 3
            ),
            "categories": categories,
        }

    async def run(self) -> None:
        items = cases()
        if len(items) < 50:
            raise ValueError("benchmark must contain at least 50 questions")
        self.output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fact-route-audio-") as temporary:
            print(f"preparing_audio count={len(items)}", flush=True)
            audio = await self.prepare_audio(items, Path(temporary))
            route_a_checkpoint = self.output / "route-a-checkpoint.json"
            if route_a_checkpoint.exists() and os.environ.get("RERUN_ROUTE_A") != "1":
                route_a = {
                    case_id: RouteResult(**value)
                    for case_id, value in json.loads(route_a_checkpoint.read_text()).items()
                }
                print("loaded_route_a_checkpoint", flush=True)
            else:
                print("running_route_a", flush=True)
                route_a = await self.run_route_a(items, audio)
                route_a_checkpoint.write_text(
                    json.dumps(
                        {case_id: asdict(result) for case_id, result in route_a.items()},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            route_b_checkpoint = self.output / "route-b-checkpoint.json"
            if route_b_checkpoint.exists():
                route_b = {
                    case_id: RouteResult(**value)
                    for case_id, value in json.loads(route_b_checkpoint.read_text()).items()
                }
                print("loaded_route_b_checkpoint", flush=True)
            else:
                print("running_route_b", flush=True)
                route_b = await self.run_route_b(items, audio)
                route_b_checkpoint.write_text(
                    json.dumps(
                        {case_id: asdict(result) for case_id, result in route_b.items()},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
        print("judging", flush=True)
        judgments = await self.judge(items, route_a, route_b)
        records = []
        for case in items:
            records.append(
                {
                    "case": asdict(case),
                    "route_a": asdict(route_a[case.id]),
                    "route_b": asdict(route_b[case.id]),
                    "judge": judgments[case.id],
                }
            )
        report = self.report(items, route_a, route_b, judgments)
        (self.output / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
        (self.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()
    if not os.environ.get("DASHSCOPE_API_KEY") or not os.environ.get("ADMIN_API_KEY"):
        raise SystemExit("DASHSCOPE_API_KEY and ADMIN_API_KEY are required")
    asyncio.run(Benchmark(args.output, args.concurrency).run())


if __name__ == "__main__":
    main()
