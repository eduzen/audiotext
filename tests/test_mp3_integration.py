import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from telegram import Update
from telegram.request import BaseRequest, RequestData

from audiotext.bot import build_application
from audiotext.settings import Settings

MP3 = Path(__file__).with_name("fixtures") / "quick-brown-fox.mp3"
BOT_USER = {"id": 42, "is_bot": True, "first_name": "AudioText", "username": "audiotext_test_bot"}
CHAT = {"id": 123, "type": "private"}
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
            case ("POST", "sendMessage") if params.get("chat_id") == CHAT["id"]:
                text = str(params["text"])
                self.replies.append(text)
                result = {
                    "message_id": 10 + len(self.replies),
                    "date": 1700000000,
                    "chat": CHAT,
                    "from": BOT_USER,
                    "text": text,
                }
            case ("POST", "sendChatAction") if (
                params.get("chat_id") == CHAT["id"] and params.get("action") == "typing"
            ):
                result = True
            case ("POST", "getFile") if params.get("file_id") == AUDIO["file_id"]:
                result = {**AUDIO, "file_path": f"music/{MP3.name}"}
            case ("GET", name) if name == MP3.name:
                return 200, MP3.read_bytes()
            case _:
                raise AssertionError(f"Unexpected Telegram request: {call} {params}")
        return 200, json.dumps({"ok": True, "result": result}).encode()


class Mp3IntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_audio_attachment_returns_transcript_and_removes_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory) / "downloads"
            settings = Settings(
                bot_token="42:offline-test",
                log_level="INFO",
                max_upload_mb=20,
                temp_dir=temp_dir,
                whisper_model=os.getenv("AUDIOTEXT_TEST_MODEL", "tiny.en"),
                whisper_device="cpu",
                whisper_compute_type="int8",
                whisper_threads=2,
            )
            telegram = FakeTelegram()
            application = await asyncio.to_thread(build_application, settings, request=telegram)
            async with application:
                update = Update.de_json(
                    {
                        "update_id": 1,
                        "message": {
                            "message_id": 1,
                            "date": 1700000000,
                            "chat": CHAT,
                            "from": {"id": 123, "is_bot": False, "first_name": "Test"},
                            "audio": AUDIO,
                        },
                    },
                    application.bot,
                )
                self.assertIsNotNone(update)
                await application.process_update(update)

            self.assertEqual(
                telegram.calls,
                [
                    ("POST", "getMe"),
                    ("POST", "sendMessage"),
                    ("POST", "sendChatAction"),
                    ("POST", "getFile"),
                    ("GET", MP3.name),
                    ("POST", "sendMessage"),
                ],
            )
            acknowledgement, transcript = telegram.replies
            self.assertIn("Transcribing", acknowledgement)
            words = " ".join(re.findall(r"[a-z]+", transcript.lower()))
            self.assertIn("quick brown fox", words, transcript)
            self.assertIn("lazy dog", words, transcript)
            self.assertEqual(list(temp_dir.iterdir()), [])
