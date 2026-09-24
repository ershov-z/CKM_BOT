from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiogram.exceptions import TelegramBadRequest

from services.album_publish import publish_plain_album
from services.message_content import MediaItem
from services.publish_content import SENT_VIA


def _album_items() -> list[MediaItem]:
    return [
        MediaItem(kind="photo", file_id="p1"),
        MediaItem(kind="photo", file_id="p2"),
    ]


def _bot() -> Mock:
    bot = Mock()
    bot.send_message = AsyncMock()
    bot.edit_message_caption = AsyncMock()
    return bot


def _bridge() -> Mock:
    bridge = Mock()
    bridge.send_album = AsyncMock()
    bridge.copy_many = AsyncMock(return_value=[201, 202])
    return bridge


class AlbumPublishTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_album_once_when_caption_already_has_footer(self) -> None:
        bot = _bot()
        bridge = _bridge()
        composed = f"Мастерская\n\n{SENT_VIA}\n\n#костюмы"
        first = SimpleNamespace(message_id=11, caption=composed, text=None)
        bridge.send_album.return_value = [first, SimpleNamespace(message_id=12)]

        await publish_plain_album(
            bot,
            bridge,
            channel_id=-100,
            source_chat_id=1,
            source_message_ids=[10, 11],
            media_items=_album_items(),
            base_text="Мастерская",
            composed=composed,
            tags="#костюмы",
        )

        bridge.send_album.assert_awaited_once()
        self.assertEqual(bridge.send_album.await_args.kwargs["caption"], composed)
        bridge.copy_many.assert_not_called()
        bot.edit_message_caption.assert_not_called()
        bot.send_message.assert_not_called()

    async def test_edits_caption_when_album_lands_without_footer(self) -> None:
        bot = _bot()
        bridge = _bridge()
        composed = f"Мастерская\n\n{SENT_VIA}\n\n#костюмы"
        first = SimpleNamespace(message_id=11, caption="Мастерская", text=None)
        bridge.send_album.return_value = [first, SimpleNamespace(message_id=12)]

        await publish_plain_album(
            bot,
            bridge,
            channel_id=-100,
            source_chat_id=1,
            source_message_ids=[10, 11],
            media_items=_album_items(),
            base_text="Мастерская",
            composed=composed,
            tags="#костюмы",
        )

        bridge.copy_many.assert_not_called()
        bot.edit_message_caption.assert_awaited_once_with(
            chat_id=-100,
            message_id=11,
            caption=composed,
        )
        bot.send_message.assert_not_called()

    async def test_fallback_copy_does_not_follow_successful_send(self) -> None:
        bot = _bot()
        bridge = _bridge()
        bridge.send_album.return_value = []
        composed = f"Мастерская\n\n{SENT_VIA}\n\n#костюмы"

        await publish_plain_album(
            bot,
            bridge,
            channel_id=-100,
            source_chat_id=1,
            source_message_ids=[10, 11],
            media_items=_album_items(),
            base_text="Мастерская",
            composed=composed,
            tags="#костюмы",
        )

        bridge.copy_many.assert_awaited_once()
        bot.edit_message_caption.assert_awaited_once()
        self.assertEqual(bot.edit_message_caption.await_args.kwargs["caption"], composed)

    async def test_send_album_error_does_not_duplicate_then_copy(self) -> None:
        bot = _bot()
        bridge = _bridge()
        bridge.send_album.side_effect = TelegramBadRequest(
            method=Mock(),
            message="can't parse entities",
        )
        composed = f"Мастерская\n\n{SENT_VIA}\n\n#костюмы"

        await publish_plain_album(
            bot,
            bridge,
            channel_id=-100,
            source_chat_id=1,
            source_message_ids=[10, 11],
            media_items=_album_items(),
            base_text="Мастерская",
            composed=composed,
            tags="#костюмы",
        )

        self.assertEqual(bridge.send_album.await_count, 1)
        self.assertEqual(bridge.copy_many.await_count, 1)

    async def test_long_composed_sends_album_once_then_footer_message(self) -> None:
        bot = _bot()
        bridge = _bridge()
        first = SimpleNamespace(message_id=11, caption="base", text=None)
        bridge.send_album.return_value = [first, SimpleNamespace(message_id=12)]
        base_text = "x" * 900
        extra_tags = "#тейк\n" * 80
        composed = f"{base_text}\n\n{SENT_VIA}\n\n{extra_tags}"
        self.assertGreater(len(composed), 1024)
        self.assertLessEqual(len(base_text), 1024)

        await publish_plain_album(
            bot,
            bridge,
            channel_id=-100,
            source_chat_id=1,
            source_message_ids=[10, 11],
            media_items=_album_items(),
            base_text=base_text,
            composed=composed,
            tags=extra_tags.strip(),
        )

        self.assertEqual(bridge.send_album.await_count, 1)
        self.assertEqual(bridge.send_album.await_args.kwargs["caption"], base_text)
        bridge.copy_many.assert_not_called()
        bot.send_message.assert_awaited_once()
        self.assertIn(SENT_VIA, bot.send_message.await_args.kwargs["text"])

    async def test_missing_file_ids_uses_copy_only(self) -> None:
        bot = _bot()
        bridge = _bridge()
        composed = f"Мастерская\n\n{SENT_VIA}\n\n#костюмы"

        await publish_plain_album(
            bot,
            bridge,
            channel_id=-100,
            source_chat_id=1,
            source_message_ids=[10, 11],
            media_items=[],
            base_text="Мастерская",
            composed=composed,
            tags="#костюмы",
        )

        bridge.send_album.assert_not_called()
        bridge.copy_many.assert_awaited_once()
        bot.edit_message_caption.assert_awaited_once()
