from __future__ import annotations

"""Правка живого поста кейса и служебного control в админ-чате."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    MessageEntity,
)

from services.case_store import CaseRecord
from services.message_content import MediaItem, content_rejects_caption
from services.publish_content import SENT_VIA, compose_single_text_with_tags, entities_within_text

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096

_INPUT_BY_KIND = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "document": InputMediaDocument,
    "animation": InputMediaAnimation,
    "audio": InputMediaAudio,
}


def is_not_modified(exc: TelegramBadRequest) -> bool:
    """True, если Telegram отказал потому что текст/кнопки уже такие."""
    return "not modified" in str(exc).lower()


def post_edit_kind(case: CaseRecord) -> str:
    """Как править живой пост: text, caption или none."""
    if case.is_composed_multi_post:
        return "none"
    if case.is_media_group:
        return "caption"
    if case.single_content_type == "text":
        return "text"
    if content_rejects_caption(case.single_content_type):
        return "none"
    if case.single_content_type in {"audio", "voice", "video_note"}:
        return "none"
    return "caption"


def preview_post_text(case: CaseRecord) -> str:
    """Та же строка, что уйдёт в канал как подпись/текст поста."""
    return compose_single_text_with_tags(case)


def raw_post_text(case: CaseRecord) -> str:
    """Исходный текст/подпись без футера и тегов."""
    return (case.single_content_text or "").strip()


def fit_text_to_limit(text: str, limit: int) -> str:
    """Ужимает подпись, сохраняя хвост «Прислано через» и теги."""
    if len(text) <= limit:
        return text
    marker = f"\n\n{SENT_VIA}"
    idx = text.find(marker)
    if idx == -1:
        return text[:limit]
    footer = text[idx + 2 :]
    room = limit - len(footer) - 2
    if room < 1:
        return footer[:limit]
    return f"{text[:idx][:room].rstrip()}\n\n{footer}"


async def edit_case_control(
    bot: Bot,
    *,
    chat_id: int,
    case: CaseRecord,
    text: str | None = None,
    entities: list[MessageEntity] | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> bool:
    """Правит служебное сообщение «Выберите действия…». Всегда text + keyboard."""
    if case.control_message_id is None:
        return False
    if text is not None:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=case.control_message_id,
                text=text,
                entities=entities,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return True
        except TelegramBadRequest as exc:
            if is_not_modified(exc):
                return True
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=case.control_message_id,
            reply_markup=reply_markup,
        )
        return True
    except TelegramBadRequest as exc:
        return is_not_modified(exc)


async def edit_case_post(
    bot: Bot,
    *,
    chat_id: int,
    case: CaseRecord,
    text: str,
    entities: list[MessageEntity] | None = None,
    allow_entities: bool = False,
) -> bool:
    """Правит живой пост: caption, затем media, затем text.

    edit_message_caption падает на фото/альбоме без исходной подписи
    («there is no caption in the message to edit»). Тогда ставим подпись
    через edit_message_media с тем же file_id.
    """
    if not case.admin_content_message_ids:
        return False
    if case.is_composed_multi_post:
        return False
    target_id = case.admin_content_message_ids[0]
    kind = post_edit_kind(case)
    if kind == "text":
        methods = ("text", "caption", "media")
    else:
        methods = ("caption", "media", "text")
    safe_entities = entities_within_text(text, entities) if allow_entities else None
    caption_text = fit_text_to_limit(text, CAPTION_LIMIT)
    last_error: TelegramBadRequest | None = None
    for method in methods:
        try:
            if method == "text":
                if not text or len(text) > TEXT_LIMIT:
                    continue
                text_kwargs: dict[str, object] = {
                    "chat_id": chat_id,
                    "message_id": target_id,
                    "text": text,
                }
                if safe_entities:
                    text_kwargs["entities"] = safe_entities
                await bot.edit_message_text(**text_kwargs)
                return True
            if method == "caption":
                caption_kwargs: dict[str, object] = {
                    "chat_id": chat_id,
                    "message_id": target_id,
                    "caption": caption_text,
                }
                if safe_entities:
                    caption_kwargs["caption_entities"] = safe_entities
                await bot.edit_message_caption(**caption_kwargs)
                return True
            if method == "media":
                if not case.media_items:
                    continue
                if await edit_case_post_media(
                    bot,
                    chat_id=chat_id,
                    message_id=target_id,
                    item=case.media_items[0],
                    caption=caption_text,
                ):
                    return True
        except TelegramBadRequest as exc:
            if is_not_modified(exc):
                return True
            last_error = exc
            continue
    if last_error is not None:
        logger.warning(
            "edit_case_post failed case=%s kind=%s: %s",
            case.case_id,
            kind,
            last_error,
        )
    return False


async def show_post_preview(bot: Bot, chat_id: int, case: CaseRecord) -> bool:
    """Ставит на пост составную подпись как в канале."""
    return await edit_case_post(
        bot,
        chat_id=chat_id,
        case=case,
        text=preview_post_text(case),
    )


async def show_post_raw(bot: Bot, chat_id: int, case: CaseRecord) -> bool:
    """Возвращает исходный текст/подпись поста."""
    return await edit_case_post(
        bot,
        chat_id=chat_id,
        case=case,
        text=raw_post_text(case),
        entities=case.single_content_entities or None,
        allow_entities=True,
    )


async def edit_case_post_media(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    item: MediaItem,
    caption: str | None = None,
) -> bool:
    """Меняет кадр на месте. caption=None не затирает подпись, если кадр не первый."""
    media_cls = _INPUT_BY_KIND.get(item.kind)
    if media_cls is None:
        return False
    if caption is None:
        media = media_cls(media=item.file_id)
    else:
        media = media_cls(media=item.file_id, caption=caption or None)
    try:
        await bot.edit_message_media(
            chat_id=chat_id,
            message_id=message_id,
            media=media,
        )
        return True
    except TelegramBadRequest as exc:
        return is_not_modified(exc)
