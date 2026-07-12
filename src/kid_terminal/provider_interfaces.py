from collections.abc import AsyncIterator
from typing import Protocol

from .answer_validation import AnswerEnvelope


class SpeechRecognizer(Protocol):
    async def transcribe(self, audio: bytes) -> str: ...


class SearchProvider(Protocol):
    async def research(self, question: str): ...


class AnswerGenerator(Protocol):
    async def answer_envelope(self, instructions: str) -> AnswerEnvelope: ...


class SpeechSynthesizer(Protocol):
    def stream(self, text: str) -> AsyncIterator[bytes]: ...
