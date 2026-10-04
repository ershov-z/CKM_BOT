from __future__ import annotations

"""Публикация одиночного поста с гарантированным футером."""

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, MessageEntity

from services.case_card import edit_case_post_media, is_not_modified
from services.message_content import MediaItem
from services.publish_content import (
    SENT_VIA,
    entities_within_text,
    message_has_sent_via,
)

_SEND_METHOD_BY_KIND = {
    "photo": ("send_photo", "photo"),
    "video": ("send_video", "video"),
    "document": ("send_document", "document"),
    "animation": ("send_animation", "animation"),
    "audio": ("send_audio", "audio"),
}


async def send_sent_via_then_tags(bot: Bot, channel_id: int, tags: str) -> list[int]:
    if tags:
        sent = await bot.send_message(
            chat_id=channel_id,
            text=f"{SENT_VIA}\n\n{tags}",
        )
        return [sent.message_id]
    sent = await bot.send_message(chat_id=channel_id, text=SENT_VIA)
    return [sent.message_id]


async def ensure_composed_on_message(
    bot: Bot,
    *,
    channel_id: int,
    message: Message | None,
    composed: str,
    tags: str,
    media_item: MediaItem | None = None,
) -> list[int]:
    """Если в посте нет «Прислано через», дописывает text/caption/media или шлёт футер.

    copy_message(caption=...) для текстовых сообщений игнорируется Telegram.
    Поэтому после copy текстового поста нужен edit_message_text, не caption.
    """
    if message_has_sent_via(message):
        return []
    message_id = getattr(message, "message_id", None)
    if message_id is not None:
        # Текстовый пост: caption-edit на нём всегда падает.
        if getattr(message, "text", None) is not None and media_item is None:
            try:
                await bot.edit_message_text(
                    chat_id=channel_id,
                    message_id=message_id,
                    text=composed,
                )
                return []
            except TelegramBadRequest as exc:
                if is_not_modified(exc):
                    return []
        try:
            await bot.edit_message_caption(
                chat_id=channel_id,
                message_id=message_id,
                caption=composed,
            )
            return []
        except TelegramBadRequest as exc:
            if is_not_modified(exc):
                return []
        # MessageId-only ответ без text/caption: всё равно пробуем text-edit.
        if media_item is None:
            try:
                await bot.edit_message_text(
                    chat_id=channel_id,
                    message_id=message_id,
                    text=composed,
                )
                return []
            except TelegramBadRequest as exc:
                if is_not_modified(exc):
                    return []
        if media_item is not None and await edit_case_post_media(
            bot,
            chat_id=channel_id,
            message_id=message_id,
            item=media_item,
            caption=composed,
        ):
            return []
    return await send_sent_via_then_tags(bot, channel_id, tags)


async def send_stored_media_with_composed(
    bot: Bot,
    *,
    channel_id: int,
    item: MediaItem,
    composed: str,
    tags: str,
) -> list[int]:
    """Шлёт одно медиа по file_id с готовой подписью, без copy и без entities."""
    spec = _SEND_METHOD_BY_KIND.get(item.kind)
    if spec is None:
        return []
    method_name, arg_name = spec
    method = getattr(bot, method_name, None)
    if method is None:
        return []
    sent: Message | None
    try:
        sent = await method(
            chat_id=channel_id,
            caption=composed,
            **{arg_name: item.file_id},
        )
    except TelegramBadRequest:
        sent = None
    posted: list[int] = []
    message_id = getattr(sent, "message_id", None)
    if message_id is not None:
        posted.append(message_id)
    posted.extend(
        await ensure_composed_on_message(
            bot,
            channel_id=channel_id,
            message=sent,
            composed=composed,
            tags=tags,
            media_item=item,
        )
    )
    return posted


async def copy_single_with_composed(
    bot: Bot,
    *,
    channel_id: int,
    from_chat_id: int,
    message_id: int,
    composed: str,
    tags: str,
    media_item: MediaItem | None = None,
) -> list[int]:
    """Копирует одно медиа с готовой подписью, без старых caption_entities.

    Если Telegram выложил оригинал без футера — правим тот же пост.
    Второй copy не делаем: иначе в канале появится дубль.
    """
    copied: Message | None
    try:
        copied = await bot.copy_message(
            chat_id=channel_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            caption=composed,
        )
    except TelegramBadRequest:
        try:
            copied = await bot.copy_message(
                chat_id=channel_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
        except Exception:
            copied = None
    except Exception:
        # copy мог уже уйти в канал, повторный copy даст дубль.
        copied = None
    posted: list[int] = []
    copied_id = getattr(copied, "message_id", None)
    if copied_id is not None:
        posted.append(copied_id)
    posted.extend(
        await ensure_composed_on_message(
            bot,
            channel_id=channel_id,
            message=copied,
            composed=composed,
            tags=tags,
            media_item=media_item,
        )
    )
    return posted


async def send_text_with_composed(
    bot: Bot,
    *,
    channel_id: int,
    composed: str,
    tags: str,
    entities: list[MessageEntity] | None = None,
) -> list[int]:
    """Шлёт текстовый пост. Сломанные entities не должны съесть футер."""
    safe_entities = entities_within_text(composed, entities)
    sent: Message | None
    try:
        sent = await bot.send_message(
            chat_id=channel_id,
            text=composed,
            entities=safe_entities,
        )
    except TelegramBadRequest:
        try:
            sent = await bot.send_message(chat_id=channel_id, text=composed)
        except TelegramBadRequest:
            sent = None
    posted: list[int] = []
    if sent is not None:
        posted.append(sent.message_id)
    if message_has_sent_via(sent):
        return posted
    posted.extend(await send_sent_via_then_tags(bot, channel_id, tags))
    return posted
