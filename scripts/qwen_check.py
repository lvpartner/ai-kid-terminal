import asyncio
from pathlib import Path

from kid_terminal.config import get_settings
from kid_terminal.providers import ProviderError, QwenRealtimeProvider


async def main() -> None:
    settings = get_settings()
    if not settings.dashscope_api_key:
        raise SystemExit("Set DASHSCOPE_API_KEY in .env")
    provider = QwenRealtimeProvider(settings)
    received = 0
    source = Path("data/qwen-test-input.pcm")
    if not source.is_file():
        raise SystemExit("Generate data/qwen-test-input.pcm with make qwen-voice-e2e first")
    session = None
    try:
        session = await provider.open({"voice": "Ethan", "web_search": False})
        audio = source.read_bytes()
        for offset in range(0, len(audio), 64 * 1024):
            await provider.append_audio(session, audio[offset : offset + 64 * 1024])
        async for kind, value in provider.respond(session):
            if kind == "audio":
                received += len(value)
    except ProviderError as exc:
        raise SystemExit(
            f"Qwen realtime generation failed category={exc.code or 'unknown'}; "
            "check account status and server logs. "
            "Credentials were not logged."
        ) from None
    finally:
        if session:
            await provider.close(session)
    if not received:
        raise RuntimeError("Qwen connected but returned no audio")
    print("Qwen realtime generation succeeded; credentials were not logged.")


asyncio.run(main())
