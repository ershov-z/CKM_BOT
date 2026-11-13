from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from services.message_content import needs_caption_split
from services.publish_content import SENT_VIA, strip_sent_via_footer
from services.take_copy import copy_take_to_admin, drop_duplicate_messages

OLD_TAIL = f"\n\n{SENT_VIA}\n\n#имхо\n#тейк\n#щитпост"


def _msg(message_id: int, **fields: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "message_id": message_id,
        "text": None,
        "caption": None,
        "entities": None,
        "caption_entities": None,
        "media_group_id": None,
        "photo": None,
        "video": None,
        "animation": None,
        "document": None,
        "audio": None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


def _photo(file_id: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(file_id=file_id)]


def _bot() -> Mock:
    bot = Mock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=900))
    bot.copy_message = AsyncMock(return_value=SimpleNamespace(message_id=901))
    return bot


def _bridge() -> Mock:
    bridge = Mock()
    bridge.copy_many = AsyncMock(side_effect=lambda **kw: [i + 100 for i in kw["message_ids"]])
    bridge.send_album = AsyncMock()
    return bridge


class StripFooterTests(unittest.TestCase):
    def test_strips_trailing_footer_and_tags(self) -> None:
        self.assertEqual(strip_sent_via_footer(f"интересно{OLD_TAIL}"), "интересно")

    def test_strips_footer_without_tags(self) -> None:
        self.assertEqual(strip_sent_via_footer(f"текст\n\n{SENT_VIA}"), "текст")

    def test_keeps_footer_quoted_mid_text(self) -> None:
        text = f"почему пишут {SENT_VIA} под каждым постом?"
        self.assertEqual(strip_sent_via_footer(text), text)

    def test_keeps_plain_text(self) -> None:
        self.assertEqual(strip_sent_via_footer("просто #тейк"), "просто #тейк")


class CopyTakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_clean_take_is_copied_in_one_batch(self) -> None:
        bot = _bot()
        bridge = _bridge()
        messages = [_msg(1, text="раз"), _msg(2, text="два")]

        ids = await copy_take_to_admin(
            bot, bridge, messages=messages, from_chat_id=5, to_chat_id=-1
        )

        self.assertEqual(ids, [101, 102])
        bridge.copy_many.assert_awaited_once()
        bot.send_message.assert_not_called()

    async def test_forwarded_album_loses_old_footer_and_tags(self) -> None:
        bot = _bot()
        bridge = _bridge()
        bridge.send_album.return_value = [
            SimpleNamespace(message_id=301),
            SimpleNamespace(message_id=302),
        ]
        messages = [
            _msg(1, text="а с того, что не зная броду"),
            _msg(2, media_group_id="g", photo=_photo("p1"), caption=f"интересно{OLD_TAIL}"),
            _msg(3, media_group_id="g", photo=_photo("p2")),
        ]

        ids = await copy_take_to_admin(
            bot, bridge, messages=messages, from_chat_id=5, to_chat_id=-1
        )

        self.assertEqual(ids, [101, 301, 302])
        self.assertEqual(bridge.copy_many.await_args.kwargs["message_ids"], [1])
        self.assertEqual(bridge.send_album.await_args.kwargs["caption"], "интересно")

    async def test_forwarded_text_post_loses_old_footer(self) -> None:
        bot = _bot()
        bridge = _bridge()
        messages = [_msg(1, text="мой ответ"), _msg(2, text=f"старый пост{OLD_TAIL}")]

        ids = await copy_take_to_admin(
            bot, bridge, messages=messages, from_chat_id=5, to_chat_id=-1
        )

        self.assertEqual(ids, [101, 900])
        self.assertEqual(bot.send_message.await_args.kwargs["text"], "старый пост")

    async def test_forwarded_single_photo_gets_clean_caption(self) -> None:
        bot = _bot()
        bridge = _bridge()
        messages = [
            _msg(1, photo=_photo("p1"), caption=f"подпись{OLD_TAIL}"),
            _msg(2, text="мой ответ"),
        ]

        ids = await copy_take_to_admin(
            bot, bridge, messages=messages, from_chat_id=5, to_chat_id=-1
        )

        self.assertEqual(ids, [901, 102])
        self.assertEqual(bot.copy_message.await_args.kwargs["caption"], "подпись")


class DropDuplicateTests(unittest.TestCase):
    def test_same_text_sent_twice_becomes_one_message(self) -> None:
        kept = drop_duplicate_messages([_msg(1, text="длинный тейк"), _msg(2, text="длинный тейк ")])
        self.assertEqual([m.message_id for m in kept], [1])

    def test_same_message_id_is_kept_once(self) -> None:
        kept = drop_duplicate_messages([_msg(1, text="а"), _msg(1, text="а")])
        self.assertEqual(len(kept), 1)

    def test_different_texts_and_media_stay(self) -> None:
        messages = [
            _msg(1, text="раз"),
            _msg(2, text="два"),
            _msg(3, photo=_photo("p1")),
            _msg(4, photo=_photo("p1")),
        ]
        self.assertEqual(len(drop_duplicate_messages(messages)), 4)

class CaptionSplitTests(unittest.TestCase):
    def test_long_rich_post_is_not_split(self) -> None:
        self.assertFalse(needs_caption_split("rich_message", "а" * 1300))
        self.assertFalse(needs_caption_split("unknown", "а" * 1300))

    def test_long_text_is_not_split(self) -> None:
        self.assertFalse(needs_caption_split("text", "а" * 1300))

    def test_long_photo_caption_is_split(self) -> None:
        self.assertTrue(needs_caption_split("photo", "а" * 1300))
        self.assertFalse(needs_caption_split("photo", "а" * 100))

if __name__ == "__main__":
    unittest.main()
