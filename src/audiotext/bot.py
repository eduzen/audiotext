from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias, cast

from telegram import Audio, Document, Message, Update, Voice
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from audiotext.transcription import Transcriber

LOG: Final[logging.Logger] = logging.getLogger("audiotext.bot")
AUDIO_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus",
    ".wav", ".webm", ".wma",
})
MAX_TELEGRAM_MESSAGE: Final[int] = 4096
TelegramAttachment: TypeAlias = Audio | Voice | Document


@dataclass(slots=True)
class BotServices:
    transcriber: Transcriber
    transcription_semaphore: asyncio.Semaphore


def _setting_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw: str | None = os.getenv(name)
    try:
        value: int = int(raw) if raw else default
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


def _audio_attachment(message: Message) -> TelegramAttachment | None:
    if message.audio:
        return message.audio
    if message.voice:
        return message.voice
    document = message.document
    if document and (
        (document.mime_type or "").startswith("audio/")
        or Path(document.file_name or "").suffix.lower() in AUDIO_EXTENSIONS
    ):
        return document
    return None


def _temporary_suffix(attachment: TelegramAttachment) -> str:
    suffix = Path(getattr(attachment, "file_name", "") or "").suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return suffix
    mime = getattr(attachment, "mime_type", None) or ""
    if mime == "audio/ogg":
        return ".ogg"
    if mime == "audio/mpeg":
        return ".mp3"
    if mime == "audio/mp4":
        return ".m4a"
    return ".audio"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Send me an audio file, Telegram voice message, or audio document. "
            "I’ll transcribe it locally and reply with the text."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Supported audio includes MP3, M4A, WAV, FLAC, OGG/Opus, AAC, "
            "WebM, and WMA. The current upload limit is "
            f"{_setting_int('MAX_UPLOAD_MB', 20)} MB."
        )


async def _download_and_transcribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    attachment = _audio_attachment(message)
    if attachment is None:
        await message.reply_text("Please send an audio file or voice message.")
        return

    max_bytes = _setting_int("MAX_UPLOAD_MB", 20) * 1024 * 1024
    size = getattr(attachment, "file_size", None)
    if size is not None and size > max_bytes:
        await message.reply_text(
            f"That file is over the {max_bytes // (1024 * 1024)} MB upload limit."
        )
        return

    services: BotServices = cast(BotServices, context.application.bot_data["services"])
    semaphore: asyncio.Semaphore = services.transcription_semaphore
    if semaphore.locked():
        await message.reply_text("Another audio file is being transcribed. I’ll handle yours next.")

    temp_root = os.getenv("TEMP_DIR")
    if temp_root:
        Path(temp_root).mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        async with semaphore:
            await message.reply_text("Audio received. Transcribing locally…")
            await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
            with tempfile.NamedTemporaryFile(
                prefix="audiotext-", suffix=_temporary_suffix(attachment),
                dir=temp_root, delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
            telegram_file = await context.bot.get_file(attachment.file_id)
            await telegram_file.download_to_drive(custom_path=str(tmp_path))
            text: str = await asyncio.to_thread(services.transcriber.transcribe, tmp_path)
            if not text:
                await message.reply_text("I couldn’t detect any speech in that audio.")
                return
            for chunk in _split_message(text):
                await message.reply_text(chunk)
    except Exception:
        LOG.exception("Transcription failed")
        await message.reply_text(
            "Sorry, I couldn’t transcribe that audio. Please check that it is a valid audio file and try again."
        )
    finally:
        if tmp_path:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def _split_message(text: str, limit: int = MAX_TELEGRAM_MESSAGE) -> list[str]:
    chunks: list[str] = []
    remaining = text.strip()
    while len(remaining) > limit:
        split_at = remaining.rfind(" ", 0, limit)
        if split_at < 1:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is not None:
        LOG.error(
            "Unhandled Telegram update error",
            exc_info=(type(error), error, error.__traceback__),
        )
    else:
        LOG.error("Unhandled Telegram update error: no exception details provided")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN is required. Copy .env.example to .env and set the token.")

    application = Application.builder().token(token).build()
    services: BotServices = BotServices(
        transcriber=Transcriber.from_environment(),
        transcription_semaphore=asyncio.Semaphore(1),
    )
    application.bot_data["services"] = services
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.Document.ALL, _download_and_transcribe))
    application.add_error_handler(_on_error)
    LOG.info("AudioText bot is polling Telegram")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
