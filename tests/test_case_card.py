from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock

from aiogram.exceptions import TelegramBadRequest

from services.case_card import (
    edit_case_control,
    edit_case_post,
    fit_text_to_limit,
    post_edit_kind,
    preview_post_text,
    show_post_preview,
)
from services.message_content import MediaItem
from services.case_store import CaseRecord
from services.publish_content import SENT_VIA, compose_single_text_with_tags


def _bot() -> Mock:
    bot = Mock()
    bot.edit_message_text = AsyncMock()
    bot.edit_message_caption = AsyncMock()
    bot.edit_message_reply_markup = AsyncMock()
    bot.edit_message_media = AsyncMock()
    return bot


def _case(**kwargs) -> CaseRecord:
    payload = {
        "case_id": "abcd1234",
        "user_chat_id": 1,
        "source_message_ids": [10],
        "is_media_group": False,
        "admin_content_message_ids": [20],
        "control_message_id": 21,
        "single_content_text": "Мастерская",
        "single_content_type": "photo",
        "selected_tags": ["#костюмы", "#фест"],
    }
    payload.update(kwargs)
    return CaseRecord(**payload)


class CaseCardKindTests(unittest.TestCase):
    def test_fit_caption_keeps_footer_and_tags(self) -> None:
        tags = "\n".join(["#тейк", "#фест"])
        text = f"{'A' * 1000}\n\n{SENT_VIA}\n\n{tags}"
        fitted = fit_text_to_limit(text, 1024)
        self.assertLessEqual(len(fitted), 1024)
        self.assertIn(SENT_VIA, fitted)
        self.assertIn("#тейк", fitted)
        self.assertIn("#фест", fitted)

    def test_preview_text_matches_publish_compose(self) -> None:
        case = _case()
        self.assertEqual(preview_post_text(case), compose_single_text_with_tags(case))
        self.assertIn(SENT_VIA, preview_post_text(case))
        self.assertIn("#костюмы", preview_post_text(case))

    def test_kind_caption_for_photo_and_album(self) -> None:
        self.assertEqual(post_edit_kind(_case()), "caption")
        self.assertEqual(
            post_edit_kind(_case(is_media_group=True, single_content_type="photo")),
            "caption",
        )

    def test_kind_text_for_plain_text(self) -> None:
        self.assertEqual(post_edit_kind(_case(single_content_type="text")), "text")

    def test_kind_none_for_multitake_and_voice(self) -> None:
        self.assertEqual(
            post_edit_kind(_case(is_composed_multi_post=True, is_media_group=True)),
            "none",
        )
        self.assertEqual(post_edit_kind(_case(single_content_type="voice")), "none")


class CaseCardEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_control_writes_text_and_keyboard(self) -> None:
        bot = _bot()
        markup = Mock()
        case = _case()
        ok = await edit_case_control(
            bot,
            chat_id=99,
            case=case,
            text="Выберите действия с анонимкой",
            reply_markup=markup,
        )
        self.assertTrue(ok)
        bot.edit_message_text.assert_awaited_once()
        kwargs = bot.edit_message_text.await_args.kwargs
        self.assertEqual(kwargs["message_id"], 21)
        self.assertEqual(kwargs["text"], "Выберите действия с анонимкой")
        self.assertIs(kwargs["reply_markup"], markup)

    async def test_edit_control_ignores_not_modified(self) -> None:
        bot = _bot()
        bot.edit_message_text.side_effect = TelegramBadRequest(
            method=Mock(),
            message="message is not modified",
        )
        ok = await edit_case_control(
            bot,
            chat_id=99,
            case=_case(),
            text="Выберите действия с анонимкой",
        )
        self.assertTrue(ok)

    async def test_edit_post_uses_caption_for_photo(self) -> None:
        bot = _bot()
        case = _case()
        composed = preview_post_text(case)
        ok = await edit_case_post(bot, chat_id=99, case=case, text=composed)
        self.assertTrue(ok)
        bot.edit_message_caption.assert_awaited_once()
        kwargs = bot.edit_message_caption.await_args.kwargs
        self.assertEqual(kwargs["message_id"], 20)
        self.assertEqual(kwargs["caption"], composed)
        bot.edit_message_text.assert_not_called()

    async def test_edit_post_uses_text_for_text_case(self) -> None:
        bot = _bot()
        case = _case(single_content_type="text")
        composed = preview_post_text(case)
        ok = await edit_case_post(bot, chat_id=99, case=case, text=composed)
        self.assertTrue(ok)
        bot.edit_message_text.assert_awaited_once()
        self.assertEqual(bot.edit_message_text.await_args.kwargs["text"], composed)
        bot.edit_message_caption.assert_not_called()

    async def test_edit_post_uses_media_when_caption_missing(self) -> None:
        bot = _bot()
        bot.edit_message_caption.side_effect = TelegramBadRequest(
            method=Mock(),
            message="there is no caption in the message to edit",
        )
        case = _case(media_items=[MediaItem(kind="photo", file_id="file-1")])
        composed = preview_post_text(case)
        ok = await edit_case_post(bot, chat_id=99, case=case, text=composed)
        self.assertTrue(ok)
        bot.edit_message_media.assert_awaited_once()
        media = bot.edit_message_media.await_args.kwargs["media"]
        self.assertEqual(media.caption, composed)

    async def test_show_preview_skips_entities(self) -> None:
        bot = _bot()
        case = _case()
        await show_post_preview(bot, 99, case)
        kwargs = bot.edit_message_caption.await_args.kwargs
        self.assertIsNone(kwargs.get("caption_entities"))
        self.assertEqual(kwargs["caption"], compose_single_text_with_tags(case))
