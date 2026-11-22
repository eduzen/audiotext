import asyncio
import json
import os
import re
import sqlite3
import tempfile
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from telegram import Update
from telegram.ext import Application
from telegram.request import BaseRequest, RequestData

from audiotext.bot import build_application
from audiotext.history import History
from audiotext.settings import Settings

MP3 = Path(__file__).with_name("fixtures") / "quick-brown-fox.mp3"
BOT_USER = {"id": 42, "is_bot": True, "first_name": "AudioText", "username": "audiotext_test_bot"}
CHAT = {"id": 123, "type": "private"}
OTHER_CHAT = {"id": 456, "type": "private"}
GROUP_CHAT = {"id": -789, "type": "group", "title": "Test group"}
OWNER_ID = 3652654
OWNER_CHAT = {"id": OWNER_ID, "type": "private"}
CHATS = {chat["id"]: chat for chat in (CHAT, OTHER_CHAT, GROUP_CHAT, OWNER_CHAT)}
AUDIO = {
    "file_id": "test-audio",
    "file_unique_id": "test-audio-unique",
    "file_name": MP3.name,
    "mime_type": "audio/mpeg",
    "duration": 3,
    "file_size": MP3.stat().st_size,
}


class FakeTelegram(BaseRequest):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.replies: list[str] = []
        self.command_menus: dict[str, list[str]] = {}

    @property
    def read_timeout(self) -> None:
        return None

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        read_timeout: object = None,
        write_timeout: object = None,
        connect_timeout: object = None,
        pool_timeout: object = None,
    ) -> tuple[int, bytes]:
        endpoint = urlsplit(url).path.rsplit("/", 1)[-1]
        call = (method, endpoint)
        self.calls.append(call)
        params = request_data.parameters if request_data else {}
        match call:
            case ("POST", "getMe"):
                result = BOT_USER
            case ("POST", "setMyCommands"):
                scope = params.get("scope", {"type": "default"})["type"]
                self.command_menus[scope] = [command["command"] for command in params["commands"]]
                result = True
            case ("POST", "sendMessage") if params.get("chat_id") in CHATS:
                text = str(params["text"])
                self.replies.append(text)
                result = {
                    "message_id": 10 + len(self.replies),
                    "date": 1700000000,
                    "chat": CHATS[params["chat_id"]],
                    "from": BOT_USER,
                    "text": text,
                }
            case ("POST", "sendChatAction") if (
                params.get("chat_id") in CHATS and params.get("action") == "typing"
            ):
                result = True
            case ("POST", "getFile") if params.get("file_id") == AUDIO["file_id"]:
                result = {**AUDIO, "file_path": f"music/{MP3.name}"}
            case ("GET", name) if name == MP3.name:
                return 200, MP3.read_bytes()
            case _:
                raise AssertionError(f"Unexpected Telegram request: {call} {params}")
        return 200, json.dumps({"ok": True, "result": result}).encode()


def _settings_for(directory: Path) -> Settings:
    return Settings(
        bot_token="42:offline-test",
        log_level="INFO",
        max_upload_mb=20,
        temp_dir=directory / "downloads",
        whisper_model=os.getenv("AUDIOTEXT_TEST_MODEL", "tiny.en"),
        whisper_device="cpu",
        whisper_compute_type="int8",
        whisper_threads=2,
        database_path=directory / "history.sqlite3",
    )


@asynccontextmanager
async def _running_application(application: Application) -> AsyncIterator[None]:
    await application.start()
    try:
        yield
    finally:
        await application.stop()


class CommandMenuTest(unittest.IsolatedAsyncioTestCase):
    async def test_startup_replaces_default_and_private_command_menus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            telegram.command_menus = {"default": ["old"], "all_private_chats": ["old"]}
            with patch("audiotext.bot.Transcriber.from_settings"):
                application = build_application(_settings_for(Path(directory)), request=telegram)
            async with application:
                assert application.post_init is not None
                await application.post_init(application)

        self.assertEqual(
            telegram.command_menus,
            {
                "default": ["start", "help"],
                "all_private_chats": ["start", "help", "my_audio", "delete_audio"],
            },
        )


class Mp3IntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_audio_and_forwarded_voices_return_transcripts_and_remove_downloads(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = _settings_for(Path(directory))
            temp_dir = Path(directory) / "downloads"
            telegram = FakeTelegram()
            application = await asyncio.to_thread(build_application, settings, request=telegram)
            date = 1700000000
            voice = {key: value for key, value in AUDIO.items() if key != "file_name"}
            cases = [
                ({"audio": AUDIO}, ""),
                (
                    {
                        "voice": voice,
                        "forward_origin": {
                            "type": "user",
                            "date": date - 1,
                            "sender_user": {
                                "id": 456,
                                "is_bot": False,
                                "first_name": "Alex",
                                "last_name": "Rivera",
                            },
                        },
                    },
                    "Alex Rivera: ",
                ),
                (
                    {
                        "voice": voice,
                        "forward_origin": {
                            "type": "hidden_user",
                            "date": date - 1,
                            "sender_user_name": "Sam Lee",
                        },
                    },
                    "Sam Lee: ",
                ),
            ]
            async with application:
                self.assertEqual(telegram.calls, [("POST", "getMe")])
                for message_id, (fields, prefix) in enumerate(cases, start=1):
                    with self.subTest(prefix=prefix):
                        telegram.calls.clear()
                        telegram.replies.clear()
                        update = Update.de_json(
                            {
                                "update_id": message_id,
                                "message": {
                                    "message_id": message_id,
                                    "date": date,
                                    "chat": CHAT,
                                    "from": {"id": 123, "is_bot": False, "first_name": "Test"},
                                    **fields,
                                },
                            },
                            application.bot,
                        )
                        self.assertIsNotNone(update)
                        async with _running_application(application):
                            await application.process_update(update)

                        self.assertEqual(
                            telegram.calls,
                            [
                                ("POST", "sendMessage"),
                                ("POST", "sendChatAction"),
                                ("POST", "getFile"),
                                ("GET", MP3.name),
                                ("POST", "sendMessage"),
                            ],
                        )
                        acknowledgement, transcript = telegram.replies
                        self.assertIn("Transcribing", acknowledgement)
                        self.assertTrue(transcript.startswith(prefix), transcript)
                        speech = transcript.removeprefix(prefix)
                        self.assertNotIn(":", speech)
                        words = " ".join(re.findall(r"[a-z]+", speech.lower()))
                        self.assertIn("quick brown fox", words, transcript)
                        self.assertIn("lazy dog", words, transcript)
                        self.assertEqual(list(temp_dir.iterdir()), [])

            with closing(sqlite3.connect(settings.database_path)) as db:
                self.assertEqual(
                    db.execute("SELECT telegram_id, display_name FROM users").fetchall(),
                    [(123, "Test")],
                )
                self.assertEqual(
                    db.execute(
                        """
                        SELECT uploader_user_id, sender_user_id, sender_name
                        FROM audios ORDER BY id
                        """
                    ).fetchall(),
                    [(123, None, None), (123, 456, "Alex Rivera"), (123, None, "Sam Lee")],
                )


class AudioAdmissionIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejections_do_not_download_and_quota_is_per_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                _settings_for(Path(directory)),
                transcriptions_per_minute=1,
                allowed_telegram_user_ids=frozenset({123, 456}),
            )
            telegram = FakeTelegram()
            started = asyncio.Event()
            finish = asyncio.Event()

            with patch("audiotext.bot.Transcriber.from_settings"):
                application = build_application(settings, request=telegram)

            async def transcribe(_path: Path) -> str:
                started.set()
                await finish.wait()
                return "Transcript"

            async def send(user_id: int, *, is_bot: bool = False, message_id: int) -> None:
                update = Update.de_json(
                    {
                        "update_id": message_id,
                        "message": {
                            "message_id": message_id,
                            "date": 1700000000,
                            "chat": CHATS.get(user_id, CHAT),
                            "from": {"id": user_id, "is_bot": is_bot, "first_name": "Test"},
                            "audio": AUDIO,
                        },
                    },
                    application.bot,
                )
                assert update is not None
                await application.process_update(update)

            async with application:
                with patch("audiotext.bot.TranscriptionService.transcribe", side_effect=transcribe):
                    async with _running_application(application):
                        try:
                            await send(123, is_bot=True, message_id=1)
                            await send(999, message_id=2)
                            self.assertEqual(telegram.calls, [("POST", "getMe")])

                            await send(123, message_id=3)
                            await asyncio.wait_for(started.wait(), timeout=5)
                            self.assertEqual(
                                telegram.replies, ["Audio received. Transcribing locally…"]
                            )
                            await send(123, message_id=4)
                            self.assertIn("per-minute transcription limit (1)", telegram.replies[-1])
                            await send(456, message_id=5)
                            self.assertIn("transcribing another file", telegram.replies[-1])
                            self.assertEqual(telegram.calls.count(("POST", "getFile")), 1)
                        finally:
                            finish.set()
                    self.assertEqual(telegram.replies[-1], "Transcript")

                    async with _running_application(application):
                        await send(456, message_id=6)
                    self.assertEqual(telegram.calls.count(("POST", "getFile")), 2)
                    self.assertEqual(telegram.replies[-1], "Transcript")


class HistoryIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def command(
        self,
        application: Application,
        telegram: FakeTelegram,
        text: str,
        *,
        chat: dict[str, int | str] = CHAT,
        user_id: int = 123,
        expect_reply: bool = True,
    ) -> str:
        message_id = len(telegram.replies) + 1
        update = Update.de_json(
            {
                "update_id": message_id,
                "message": {
                    "message_id": message_id,
                    "date": 1700000000,
                    "chat": chat,
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "text": text,
                    "entities": [
                        {"type": "bot_command", "offset": 0, "length": len(text.split()[0])}
                    ],
                },
            },
            application.bot,
        )
        self.assertIsNotNone(update)
        await application.process_update(update)
        if not expect_reply:
            self.assertEqual(len(telegram.replies), message_id - 1)
            return ""
        self.assertEqual(len(telegram.replies), message_id)
        return telegram.replies[-1]

    async def test_only_owner_can_list_users_in_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = _settings_for(Path(directory))
            history = History(settings.database_path)
            history.remember_user(123, "Uploader")
            history.save_audio(123, "Private transcript", sender_id=456, sender_name="Sender")
            telegram = FakeTelegram()
            with patch("audiotext.bot.Transcriber.from_settings"):
                application = build_application(settings, request=telegram)

            async with application:
                await self.command(application, telegram, "/users", expect_reply=False)
                await self.command(
                    application,
                    telegram,
                    "/users",
                    chat=GROUP_CHAT,
                    user_id=OWNER_ID,
                    expect_reply=False,
                )
                reply = await self.command(
                    application, telegram, "/users", chat=OWNER_CHAT, user_id=OWNER_ID
                )

            self.assertRegex(
                reply, r"^Known users: 1 \(last seen in UTC\)\nUploader — 123 — \d{4}-\d{2}-\d{2}"
            )
            self.assertNotIn("Private transcript", reply)
            self.assertNotIn("Sender", reply)

    async def test_only_uploader_can_list_read_and_delete_in_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = _settings_for(Path(directory))
            history = History(settings.database_path)
            history.remember_user(123, "Uploader")
            history.save_audio(123, "Hello there.", sender_id=456, sender_name="Alex")
            for _ in range(10):
                history.save_audio(123, "Direct upload.", sender_id=None, sender_name=None)
            telegram = FakeTelegram()
            with patch("audiotext.bot.Transcriber.from_settings"):
                application = build_application(settings, request=telegram)

            async with application:
                async def command(text: str) -> str:
                    return await self.command(application, telegram, text)

                recent = await command("/my_audio")
                self.assertIn("#11 ", recent)
                self.assertNotIn("#1 ", recent)
                self.assertIn("/my_audio before 2", recent)
                older = await command("/my_audio before 2")
                self.assertIn("#1 ", older)
                self.assertIn("Alex", older)
                self.assertEqual(await command("/my_audio 1"), "Alex: Hello there.")
                self.assertIn("Use /my_audio", await command("/my_audio 9223372036854775808"))

                for text, expected in (
                    ("/my_audio", "No saved audio on this page."),
                    ("/my_audio 1", "No saved audio with that ID."),
                    ("/delete_audio 1", "No saved audio with that ID."),
                ):
                    with self.subTest(command=text):
                        reply = await self.command(
                            application, telegram, text, chat=OTHER_CHAT, user_id=456
                        )
                        self.assertEqual(reply, expected)

                for text in ("/my_audio", "/my_audio 1", "/delete_audio 1"):
                    reply = await self.command(application, telegram, text, chat=GROUP_CHAT)
                    self.assertIn("only available in a private chat", reply)

                self.assertEqual(await command("/my_audio 1"), "Alex: Hello there.")
                self.assertEqual(await command("/delete_audio 1"), "Saved transcript deleted.")
                self.assertEqual(await command("/my_audio 1"), "No saved audio with that ID.")
