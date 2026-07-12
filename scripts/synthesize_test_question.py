#!/usr/bin/env python3
import argparse
import asyncio
from pathlib import Path

from kid_terminal.config import get_settings
from kid_terminal.text_answer import CosyVoiceSynthesizer


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    synthesizer = CosyVoiceSynthesizer(get_settings())
    audio = bytearray()
    async for chunk in synthesizer.stream(args.text):
        audio.extend(chunk)
    if not audio:
        raise RuntimeError("TTS returned no test audio")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(audio)
    print(f"generated synthetic PCM bytes={len(audio)}")


if __name__ == "__main__":
    asyncio.run(main())
