from __future__ import annotations

"""Правка живого поста кейса и служебного control в админ-чате."""

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
from services.publish_content import compose_single_text_with_tags, entities_within_text

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
    """Правит живой пост: text или caption первого контентного сообщения."""
    kind = post_edit_kind(case)
    if kind == "none" or not case.admin_content_message_ids:
        return False
    target_id = case.admin_content_message_ids[0]
    safe_entities = entities_within_text(text, entities) if allow_entities else None
    if kind == "text":
        if not text or len(text) > TEXT_LIMIT:
            return False
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=target_id,
                text=text,
                entities=safe_entities,
            )
            return True
        except TelegramBadRequest as exc:
            return is_not_modified(exc)
    if len(text) > CAPTION_LIMIT:
        return False
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=target_id,
            caption=text or None,
            caption_entities=safe_entities,
        )
        return True
    except TelegramBadRequest as exc:
        return is_not_modified(exc)


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
