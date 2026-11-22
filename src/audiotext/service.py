"""Application-level transcription orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path

from audiotext.transcription import Transcriber


class TranscriptionService:
    """Runs local transcription without blocking the async Telegram bot."""

    def __init__(self, transcriber: Transcriber) -> None:
        self._transcriber: Transcriber = transcriber
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(1)

    async def transcribe(self, audio_path: Path) -> str:
        async with self._semaphore:
            return await asyncio.to_thread(self._transcriber.transcribe, audio_path)
