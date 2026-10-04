from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import MessageEntity

from services.message_content import MediaItem
from services.publish_content import SENT_VIA, entities_within_text
from services.single_publish import (
    copy_single_with_composed,
    send_stored_media_with_composed,
    send_text_with_composed,
)


def _bot() -> Mock:
    bot = Mock()
    bot.copy_message = AsyncMock()
    bot.edit_message_caption = AsyncMock()
    bot.edit_message_media = AsyncMock()
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock()
    return bot


class EntitiesWithinTests(unittest.TestCase):
    def test_keeps_entities_that_fit_composed(self) -> None:
        composed = f"hello\n\n{SENT_VIA}\n\n#тейк"
        kept = entities_within_text(
            composed,
            [MessageEntity(type="bold", offset=0, length=5)],
        )
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertEqual(len(kept), 1)

    def test_drops_entities_that_overrun_text(self) -> None:
        self.assertIsNone(
            entities_within_text(
                "hi",
                [MessageEntity(type="bold", offset=0, length=20)],
            )
        )


class CopySinglePublishTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_one_copy_when_caption_already_has_footer(self) -> None:
        bot = _bot()
        composed = f"фото\n\n{SENT_VIA}\n\n#тейк"
        bot.copy_message.return_value = SimpleNamespace(
            message_id=50,
            caption=composed,
            text=None,
        )

        await copy_single_with_composed(
            bot,
            channel_id=-100,
            from_chat_id=1,
            message_id=10,
            composed=composed,
            tags="#тейк",
        )

        self.assertEqual(bot.copy_message.await_count, 1)
        self.assertEqual(bot.copy_message.await_args.kwargs["caption"], composed)
        self.assertNotIn("caption_entities", bot.copy_message.await_args.kwargs)
        bot.edit_message_caption.assert_not_called()
        bot.send_message.assert_not_called()

    async def test_edits_same_post_when_copy_drops_footer(self) -> None:
        bot = _bot()
        composed = f"фото\n\n{SENT_VIA}\n\n#тейк"
        bot.copy_message.return_value = SimpleNamespace(
            message_id=50,
            caption="фото",
            text=None,
        )

        await copy_single_with_composed(
            bot,
            channel_id=-100,
            from_chat_id=1,
            message_id=10,
            composed=composed,
            tags="#тейк",
        )

        self.assertEqual(bot.copy_message.await_count, 1)
        bot.edit_message_caption.assert_awaited_once_with(
            chat_id=-100,
            message_id=50,
            caption=composed,
        )
        bot.send_message.assert_not_called()

    async def test_bad_request_falls_back_to_one_plain_copy_then_edit(self) -> None:
        bot = _bot()
        composed = f"фото\n\n{SENT_VIA}\n\n#тейк"
        bot.copy_message.side_effect = [
            TelegramBadRequest(method=Mock(), message="can't parse entities"),
            SimpleNamespace(message_id=50, caption="фото", text=None),
        ]

        await copy_single_with_composed(
            bot,
            channel_id=-100,
            from_chat_id=1,
            message_id=10,
            composed=composed,
            tags="#тейк",
        )

        self.assertEqual(bot.copy_message.await_count, 2)
        bot.edit_message_caption.assert_awaited_once()
        bot.send_message.assert_not_called()

    async def test_message_id_only_copy_still_edits_caption(self) -> None:
        bot = _bot()
        composed = f"фото\n\n{SENT_VIA}\n\n#тейк"
        bot.copy_message.return_value = SimpleNamespace(message_id=50)

        await copy_single_with_composed(
            bot,
            channel_id=-100,
            from_chat_id=1,
            message_id=10,
            composed=composed,
            tags="#тейк",
        )

        bot.edit_message_caption.assert_awaited_once()
        bot.send_message.assert_not_called()

    async def test_uses_media_edit_when_caption_cannot_be_added(self) -> None:
        bot = _bot()
        composed = f"фото\n\n{SENT_VIA}\n\n#тейк"
        bot.copy_message.return_value = SimpleNamespace(
            message_id=50,
            caption=None,
            text=None,
        )
        bot.edit_message_caption.side_effect = TelegramBadRequest(
            method=Mock(),
            message="there is no caption in the message to edit",
        )

        await copy_single_with_composed(
            bot,
            channel_id=-100,
            from_chat_id=1,
            message_id=10,
            composed=composed,
            tags="#тейк",
            media_item=MediaItem(kind="photo", file_id="file-1"),
        )

        bot.edit_message_media.assert_awaited_once()
        bot.send_message.assert_not_called()

    async def test_text_copy_edits_message_text_not_separate_footer(self) -> None:
        bot = _bot()
        bot.edit_message_text = AsyncMock()
        composed = f"правила косплея\n\n{SENT_VIA}\n\n#тейк"
        bot.copy_message.return_value = SimpleNamespace(
            message_id=50,
            caption=None,
            text="правила косплея",
        )
        bot.edit_message_caption.side_effect = TelegramBadRequest(
            method=Mock(),
            message="there is no caption in the message to edit",
        )

        await copy_single_with_composed(
            bot,
            channel_id=-100,
            from_chat_id=1,
            message_id=10,
            composed=composed,
            tags="#тейк",
        )

        bot.edit_message_text.assert_awaited_once_with(
            chat_id=-100,
            message_id=50,
            text=composed,
        )
        bot.send_message.assert_not_called()


class SendStoredMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_photo_with_composed_caption(self) -> None:
        bot = _bot()
        composed = f"фото\n\n{SENT_VIA}\n\n#тейк"
        bot.send_photo.return_value = SimpleNamespace(
            message_id=50,
            caption=composed,
            text=None,
        )

        posted = await send_stored_media_with_composed(
            bot,
            channel_id=-100,
            item=MediaItem(kind="photo", file_id="file-1"),
            composed=composed,
            tags="#тейк",
        )

        self.assertEqual(posted, [50])
        self.assertEqual(bot.send_photo.await_args.kwargs["photo"], "file-1")
        self.assertEqual(bot.send_photo.await_args.kwargs["caption"], composed)
        bot.copy_message.assert_not_called()
        bot.send_message.assert_not_called()


class SendTextPublishTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_without_entities_and_keeps_footer(self) -> None:
        bot = _bot()
        composed = f"привет\n\n{SENT_VIA}\n\n#тейк"
        bot.send_message.side_effect = [
            TelegramBadRequest(method=Mock(), message="can't parse entities"),
            SimpleNamespace(message_id=9, text=composed, caption=None),
        ]

        await send_text_with_composed(
            bot,
            channel_id=-100,
            composed=composed,
            tags="#тейк",
            entities=[MessageEntity(type="bold", offset=0, length=99)],
        )

        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(bot.send_message.await_args.kwargs["text"], composed)
        self.assertNotIn("entities", bot.send_message.await_args.kwargs)
