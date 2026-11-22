"""Local Whisper model setup and audio transcription."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final, Self

from faster_whisper import WhisperModel

LOG: Final[logging.Logger] = logging.getLogger("audiotext.transcription")


def _positive_int_setting(name: str, default: int) -> int:
    raw_value: str | None = os.getenv(name)
    try:
        value: int = int(raw_value) if raw_value else default
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value < 1:
        raise RuntimeError(f"{name} must be at least 1")
    return value


class Transcriber:
    """Owns the local Whisper model and exposes synchronous transcription."""

    def __init__(self, model: WhisperModel) -> None:
        self._model: WhisperModel = model

    @classmethod
    def from_environment(cls) -> Self:
        model_name: str = os.getenv("WHISPER_MODEL", "tiny")
        device: str = os.getenv("WHISPER_DEVICE", "cpu")
        compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
        threads: int = _positive_int_setting("WHISPER_THREADS", 2)
        LOG.info("Loading Whisper model %s (%s, %s)", model_name, device, compute_type)
        model: WhisperModel = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=threads,
            num_workers=1,
        )
        return cls(model)

    def transcribe(self, audio_path: Path) -> str:
        segments, _info = self._model.transcribe(
            str(audio_path),
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
