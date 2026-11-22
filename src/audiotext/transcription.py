"""Local Whisper model setup and audio transcription."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final, Self

from faster_whisper import WhisperModel

from audiotext.settings import Settings

LOG: Final[logging.Logger] = logging.getLogger("audiotext.transcription")


class Transcriber:
    """Owns the local Whisper model and exposes synchronous transcription."""

    def __init__(self, model: WhisperModel) -> None:
        self._model: WhisperModel = model

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        LOG.info(
            "Loading Whisper model %s (%s, %s)",
            settings.whisper_model,
            settings.whisper_device,
            settings.whisper_compute_type,
        )
        return cls(
            WhisperModel(
                settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                cpu_threads=settings.whisper_threads,
                num_workers=1,
            )
        )

    def transcribe(self, audio_path: Path) -> str:
        segments, _info = self._model.transcribe(
            str(audio_path),
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
