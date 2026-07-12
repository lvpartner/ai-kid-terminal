#!/usr/bin/env python3
"""Create unverified navigation outlines linked to official source PDFs.

The generated text helps select search terms. It must never be represented as source evidence.
"""

import asyncio
import json
import os
from pathlib import Path

import httpx

API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


async def build_one(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, source: dict, output: Path
):
    destination = output / f"{source['id']}.txt"
    if destination.exists() or source.get("public_summary"):
        return
    prompt = f"""联网检索并整理这份官方文件：{source["title"]}。
官方来源：{source["authority"]}，原文地址：{source["url"]}。
请创建用于检索的完整课程内容索引，覆盖文件中的课程理念、核心素养、总目标、分学段目标、
课程内容主题、学业质量、教学建议和评价建议。逐项标明适用年级或学段，保留重要数字和术语。
只能依据该文件，不加入常识或其他版本教材。不要大段照抄原文，每项用准确摘要表达。
输出纯文本，使用清晰的分级标题和条目，尽量覆盖所有主题，目标3000到6000个汉字。
"""
    payload = {
        "model": "qwen3.5-plus",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 4000,
        "enable_search": True,
        "search_options": {"forced_search": True, "search_strategy": "max"},
    }
    async with semaphore:
        response = await client.post(API_URL, json=payload)
        response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"].strip()
    if len(text) < 1000:
        raise RuntimeError(f"outline for {source['id']} is too short")
    destination.write_text(text)
    print(f"outlined {source['id']} chars={len(text)}")


async def main() -> None:
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        raise SystemExit("DASHSCOPE_API_KEY is required")
    sources = json.loads(Path("knowledge/sources.json").read_text())
    output = Path("knowledge/outlines")
    output.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(headers=headers, timeout=240) as client:
        semaphore = asyncio.Semaphore(4)
        await asyncio.gather(*(build_one(client, semaphore, source, output) for source in sources))


if __name__ == "__main__":
    asyncio.run(main())
