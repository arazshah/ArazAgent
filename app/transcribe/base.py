"""Transcription backend contract."""

from __future__ import annotations

from typing import Protocol


class TranscriptionBackend(Protocol):
    async def transcribe(self, audio_bytes: bytes, language: str) -> str: ...
