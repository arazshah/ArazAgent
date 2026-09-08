"""AvalAI (OpenAI-compatible) transcription backend."""

from __future__ import annotations

from openai import AsyncOpenAI

from app.retry import call_with_retries


class AvalAITranscriber:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=60)
        self._model = model

    async def transcribe(self, audio_bytes: bytes, language: str) -> str:
        async def call() -> str:
            resp = await self._client.audio.transcriptions.create(
                model=self._model,
                file=("audio.oga", audio_bytes),
                language=language,
            )
            return resp.text

        return await call_with_retries(call)

    async def aclose(self) -> None:
        await self._client.close()
