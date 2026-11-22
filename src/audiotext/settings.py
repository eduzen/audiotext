"""Application settings loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_TRANSCRIPTIONS_PER_MINUTE: Final = 3


def _positive_int(name: str, default: int) -> int:
    raw_value: str | None = os.getenv(name)
    try:
        value: int = int(raw_value) if raw_value else default
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value < 1:
        raise RuntimeError(f"{name} must be at least 1")
    return value


def _allowed_user_ids() -> frozenset[int]:
    raw_value = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
    if not raw_value:
        return frozenset()
    try:
        user_ids = frozenset(int(value) for value in raw_value.split(","))
        if any(user_id <= 0 for user_id in user_ids):
            raise ValueError("Telegram user IDs must be positive")
    except ValueError as error:
        raise RuntimeError("ALLOWED_TELEGRAM_USER_IDS must be comma-separated positive IDs") from error
    return user_ids


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    log_level: str
    max_upload_mb: int
    temp_dir: Path | None
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    whisper_threads: int
    database_path: Path
    transcriptions_per_minute: int = DEFAULT_TRANSCRIPTIONS_PER_MINUTE
    allowed_telegram_user_ids: frozenset[int] = frozenset()

    @classmethod
    def from_environment(cls) -> Settings:
        bot_token: str = os.getenv("BOT_TOKEN", "")
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is required. Copy .env.example to .env and set the token.")

        temp_dir_value: str | None = os.getenv("TEMP_DIR")
        return cls(
            bot_token=bot_token,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            max_upload_mb=_positive_int("MAX_UPLOAD_MB", 20),
            temp_dir=Path(temp_dir_value) if temp_dir_value else None,
            whisper_model=os.getenv("WHISPER_MODEL", "tiny"),
            whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
            whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
            whisper_threads=_positive_int("WHISPER_THREADS", 2),
            database_path=Path(os.getenv("DATABASE_PATH") or "data/audiotext.sqlite3"),
            transcriptions_per_minute=_positive_int(
                "TRANSCRIPTIONS_PER_MINUTE", DEFAULT_TRANSCRIPTIONS_PER_MINUTE
            ),
            allowed_telegram_user_ids=_allowed_user_ids(),
        )
