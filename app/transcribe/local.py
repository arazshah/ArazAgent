"""Local transcription backend via faster-whisper. This is an optional extra
(pip install .[local]), not part of the default install, so the import is
lazy and failure raises a clear, actionable error instead of crashing at
module import time.
"""

from __future__ import annotations

import asyncio
import tempfile
from typing import Any


class LocalTranscriber:
    def __init__(self, model_size: str) -> None:
        self._model_size = model_size
        self._model: Any = None

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "faster-whisper is not installed. Install the optional 'local' "
                    "extra: pip install .[local]"
                ) from exc
            self._model = WhisperModel(self._model_size)
        return self._model

    async def transcribe(self, audio_bytes: bytes, language: str) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio_bytes, language)

    def _transcribe_sync(self, audio_bytes: bytes, language: str) -> str:
        model = self._load_model()
        with tempfile.NamedTemporaryFile(suffix=".oga") as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            segments, _info = model.transcribe(tmp.name, language=language)
            return " ".join(seg.text.strip() for seg in segments).strip()
