"""SQLite storage for users and their transcript history."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final, NamedTuple

HISTORY_PAGE_SIZE: Final[int] = 10


class AudioSummary(NamedTuple):
    audio_id: int
    created_at: str
    sender_name: str | None


class SavedTranscript(NamedTuple):
    text: str
    sender_name: str | None


class History:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS audios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uploader_user_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    sender_user_id INTEGER,
                    sender_name TEXT,
                    transcript TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS audios_by_uploader
                    ON audios (uploader_user_id, id DESC);
            """)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def remember_user(self, user_id: int, display_name: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO users (telegram_id, display_name) VALUES (?, ?)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (user_id, display_name),
            )

    def save_audio(
        self,
        uploader_id: int,
        text: str,
        *,
        sender_id: int | None,
        sender_name: str | None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO audios (uploader_user_id, transcript, sender_user_id, sender_name)
                VALUES (?, ?, ?, ?)
                """,
                (uploader_id, text, sender_id, sender_name),
            )

    def list_audio(self, uploader_id: int, before_id: int | None) -> list[AudioSummary]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, created_at, sender_name FROM audios
                WHERE uploader_user_id = ? AND (? IS NULL OR id < ?)
                ORDER BY id DESC LIMIT ?
                """,
                (uploader_id, before_id, before_id, HISTORY_PAGE_SIZE + 1),
            )
            return [AudioSummary(*row) for row in rows]

    def get_audio(self, uploader_id: int, audio_id: int) -> SavedTranscript | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT transcript, sender_name FROM audios WHERE uploader_user_id = ? AND id = ?",
                (uploader_id, audio_id),
            ).fetchone()
            return SavedTranscript(*row) if row else None

    def delete_audio(self, uploader_id: int, audio_id: int) -> bool:
        with self._connect() as db:
            return db.execute(
                "DELETE FROM audios WHERE uploader_user_id = ? AND id = ?",
                (uploader_id, audio_id),
            ).rowcount == 1
