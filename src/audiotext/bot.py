from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Final, cast

from telegram import (
    Audio,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    Document,
    Message,
    MessageOriginHiddenUser,
    MessageOriginUser,
    Update,
    User,
    Voice,
)
from telegram.constants import ChatAction, ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import BaseRequest

from audiotext.admission import AudioAdmission
from audiotext.history import HISTORY_PAGE_SIZE, History
from audiotext.service import TranscriptionService
from audiotext.settings import Settings
from audiotext.transcription import Transcriber

LOG: Final[logging.Logger] = logging.getLogger("audiotext.bot")
AUDIO_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".aac",
        ".flac",
        ".m4a",
        ".mp3",
        ".oga",
        ".ogg",
        ".opus",
        ".wav",
        ".webm",
        ".wma",
    }
)
MAX_TELEGRAM_MESSAGE: Final[int] = 4096
OWNER_TELEGRAM_ID: Final[int] = 3652654  # eduzen
type TelegramAttachment = Audio | Voice | Document


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
    file_name: str | None = getattr(attachment, "file_name", None)
    suffix: str = Path(file_name or "").suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return suffix
    suffix_by_mime_type: dict[str, str] = {
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
    }
    return suffix_by_mime_type.get(attachment.mime_type or "", ".audio")


def _history(context: ContextTypes.DEFAULT_TYPE) -> History:
    return cast(History, context.application.bot_data["history"])


async def _remember_user(message: Message, history: History) -> int | None:
    user = message.from_user
    if user is None or user.is_bot or message.sender_chat is not None:
        return None
    await asyncio.to_thread(history.remember_user, user.id, user.full_name)
    return user.id


def _forwarded_sender(message: Message) -> tuple[int | None, str | None]:
    origin = message.forward_origin
    if isinstance(origin, MessageOriginUser):
        return origin.sender_user.id, origin.sender_user.full_name
    if isinstance(origin, MessageOriginHiddenUser):
        return None, origin.sender_user_name
    return None, None


async def _reply_in_chunks(message: Message, text: str) -> None:
    for chunk in _split_message(text):
        await message.reply_text(chunk)


async def _reply_with_transcript(message: Message, text: str, sender_name: str | None) -> None:
    if sender_name:
        text = f"{sender_name}: {text}"
    await _reply_in_chunks(message, text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await _remember_user(update.message, _history(context))
        await update.message.reply_text(
            "Send me an audio file, Telegram voice message, or audio document. "
            "I’ll transcribe it locally and reply with the text. I save your Telegram user "
            "details, transcripts, and any original sender details Telegram provides, "
            "but not the audio files. In private chat, use /my_audio to see your history "
            "or /delete_audio ID to remove a saved transcript."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = cast(Settings, context.application.bot_data["settings"])
    if update.message:
        await _remember_user(update.message, _history(context))
        await update.message.reply_text(
            "Supported audio includes MP3, M4A, WAV, FLAC, OGG/Opus, AAC, "
            "WebM, and WMA. The current upload limit is "
            f"{settings.max_upload_mb} MB. In private chat, use /my_audio to see your "
            "history or /delete_audio ID to remove a saved transcript."
        )


def _parse_audio_id(value: str) -> int | None:
    if len(value) <= 19 and value.isdecimal():
        audio_id = int(value)
        if 0 < audio_id < 2**63:
            return audio_id
    return None


async def _private_history_user_id(message: Message, history: History) -> int | None:
    if message.chat.type != ChatType.PRIVATE:
        await message.reply_text("Audio history is only available in a private chat with me.")
        return None
    return await _remember_user(message, history)


async def my_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None:
        return
    history = _history(context)
    user_id = await _private_history_user_id(message, history)
    if user_id is None:
        return

    match context.args or []:
        case [value] if (audio_id := _parse_audio_id(value)) is not None:
            saved = await asyncio.to_thread(history.get_audio, user_id, audio_id)
            if saved is None:
                await message.reply_text("No saved audio with that ID.")
            else:
                await _reply_with_transcript(message, saved.text, saved.sender_name)
            return
        case []:
            before_id = None
        case ["before", value] if (cursor := _parse_audio_id(value)) is not None:
            before_id = cursor
        case _:
            await message.reply_text("Use /my_audio, /my_audio ID, or /my_audio before ID.")
            return

    entries = await asyncio.to_thread(history.list_audio, user_id, before_id)
    if not entries:
        await message.reply_text("No saved audio on this page.")
        return
    lines = ["Your saved transcripts (UTC):"]
    page = entries[:HISTORY_PAGE_SIZE]
    for entry in page:
        line = f"#{entry.audio_id} — {entry.created_at}"
        if entry.sender_name:
            line += f" — {' '.join(entry.sender_name.split())}"
        lines.append(line)
    lines.append("Open with /my_audio ID or delete with /delete_audio ID.")
    if len(entries) > HISTORY_PAGE_SIZE:
        lines.append(f"Older entries: /my_audio before {page[-1].audio_id}")
    await _reply_in_chunks(message, "\n".join(lines))


async def delete_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None:
        return
    history = _history(context)
    user_id = await _private_history_user_id(message, history)
    if user_id is None:
        return
    args = context.args or []
    audio_id = _parse_audio_id(args[0]) if len(args) == 1 else None
    if audio_id is None:
        await message.reply_text("Use /delete_audio ID.")
        return
    deleted = await asyncio.to_thread(history.delete_audio, user_id, audio_id)
    await message.reply_text("Saved transcript deleted." if deleted else "No saved audio with that ID.")


async def _list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None:
        return
    history = _history(context)
    if await _remember_user(message, history) is None:
        return
    users = await asyncio.to_thread(history.list_users)
    lines = [f"Known users: {len(users)} (last seen in UTC)"]
    for user in users:
        name = " ".join(user.display_name.split())
        lines.append(f"{name} — {user.telegram_id} — {user.last_seen_at}")
    await _reply_in_chunks(message, "\n".join(lines))


async def _handle_audio_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.sender_chat is not None:
        return
    user = message.from_user
    if user is None or user.is_bot:
        return

    settings = cast(Settings, context.application.bot_data["settings"])
    allowed_users = settings.allowed_telegram_user_ids
    if allowed_users and user.id not in allowed_users:
        return

    attachment = _audio_attachment(message)
    if attachment is None:
        await message.reply_text("Please send an audio file or voice message.")
        return

    max_bytes: int = settings.max_upload_mb * 1024 * 1024
    size: int | None = attachment.file_size
    if size is not None and size > max_bytes:
        await message.reply_text(
            f"That file is over the {settings.max_upload_mb} MB upload limit."
        )
        return

    admission = cast(AudioAdmission, context.application.bot_data["audio_admission"])
    match admission.reserve(user.id):
        case "limited":
            await message.reply_text(
                f"You’ve reached your per-minute transcription limit "
                f"({settings.transcriptions_per_minute}). Please try again in a moment."
            )
        case "busy":
            await message.reply_text("I’m transcribing another file. Please resend yours shortly.")
        case "accepted":
            context.application.create_task(
                _transcribe_audio(message, attachment, context, settings, user, admission),
                update=update,
            )


async def _transcribe_audio(
    message: Message,
    attachment: TelegramAttachment,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    uploader: User,
    admission: AudioAdmission,
) -> None:
    service = cast(TranscriptionService, context.application.bot_data["transcription_service"])
    history = _history(context)
    tmp_path: Path | None = None
    try:
        await message.reply_text("Audio received. Transcribing locally…")
        await asyncio.to_thread(history.remember_user, uploader.id, uploader.full_name)
        if settings.temp_dir:
            settings.temp_dir.mkdir(parents=True, exist_ok=True)
        await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
        with tempfile.NamedTemporaryFile(
            prefix="audiotext-",
            suffix=_temporary_suffix(attachment),
            dir=settings.temp_dir,
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
        telegram_file = await context.bot.get_file(attachment.file_id)
        await telegram_file.download_to_drive(custom_path=str(tmp_path))
        text: str = await service.transcribe(tmp_path)
        if not text:
            await message.reply_text("I couldn’t detect any speech in that audio.")
            return
        sender_id, sender_name = _forwarded_sender(message)
        await asyncio.to_thread(
            history.save_audio, uploader.id, text, sender_id=sender_id, sender_name=sender_name
        )
        await _reply_with_transcript(message, text, sender_name)
    except Exception:
        LOG.exception("Transcription failed")
        await message.reply_text(
            "Sorry, I couldn’t transcribe that audio. Please check that it is a valid audio file and try again."
        )
    finally:
        admission.release()
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _split_message(text: str, limit: int = MAX_TELEGRAM_MESSAGE) -> list[str]:
    chunks: list[str] = []
    remaining: str = text.strip()
    while len(remaining) > limit:
        split_at: int = remaining.rfind(" ", 0, limit)
        if split_at < 1:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def _on_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is not None:
        LOG.error(
            "Unhandled Telegram update error",
            exc_info=(type(error), error, error.__traceback__),
        )
    else:
        LOG.error("Unhandled Telegram update error: no exception details provided")


async def _sync_command_menu(application: Application) -> None:
    default_commands = [
        BotCommand("start", "Learn how to transcribe audio"),
        BotCommand("help", "See supported formats and upload limits"),
    ]
    private_commands = [
        *default_commands,
        BotCommand("my_audio", "View your saved transcripts"),
        BotCommand("delete_audio", "Delete a saved transcript by ID"),
    ]
    try:
        await application.bot.set_my_commands(default_commands)
        await application.bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    except TelegramError as error:
        LOG.warning("Could not sync Telegram command menus (%s)", type(error).__name__)


def build_application(settings: Settings, *, request: BaseRequest | None = None) -> Application:
    builder = Application.builder().token(settings.bot_token).post_init(_sync_command_menu)
    if request is not None:
        builder.request(request).get_updates_request(request)
    application = builder.build()
    application.bot_data["settings"] = settings
    application.bot_data["history"] = History(settings.database_path)
    application.bot_data["audio_admission"] = AudioAdmission(settings.transcriptions_per_minute)
    application.bot_data["transcription_service"] = TranscriptionService(
        Transcriber.from_settings(settings)
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_audio", my_audio))
    application.add_handler(CommandHandler("delete_audio", delete_audio))
    application.add_handler(
        CommandHandler(
            "users",
            _list_users,
            filters=filters.ChatType.PRIVATE & filters.User(user_id=OWNER_TELEGRAM_ID),
        )
    )
    audio_filter = filters.UpdateType.MESSAGE & (filters.AUDIO | filters.VOICE | filters.Document.ALL)
    application.add_handler(MessageHandler(audio_filter, _handle_audio_upload))
    application.add_error_handler(_on_error)
    return application


def main() -> None:
    try:
        settings: Settings = Settings.from_environment()
    except RuntimeError as error:
        raise SystemExit(str(error)) from error

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    application = build_application(settings)
    LOG.info("AudioText bot is polling Telegram")
    application.run_polling(allowed_updates=[Update.MESSAGE])


if __name__ == "__main__":
    main()
