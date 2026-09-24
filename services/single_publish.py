from __future__ import annotations

"""Публикация одиночного поста с гарантированным футером."""

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, MessageEntity

from services.publish_content import (
    SENT_VIA,
    entities_within_text,
    message_has_sent_via,
)


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
) -> list[int]:
    """Если в посте нет «Прислано через», дописывает caption или шлёт футер."""
    if message_has_sent_via(message):
        return []
    if message is not None:
        try:
            await bot.edit_message_caption(
                chat_id=channel_id,
                message_id=message.message_id,
                caption=composed,
            )
            return []
        except TelegramBadRequest:
            pass
    return await send_sent_via_then_tags(bot, channel_id, tags)


async def copy_single_with_composed(
    bot: Bot,
    *,
    channel_id: int,
    from_chat_id: int,
    message_id: int,
    composed: str,
    tags: str,
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
    if copied is not None:
        posted.append(copied.message_id)
    posted.extend(
        await ensure_composed_on_message(
            bot,
            channel_id=channel_id,
            message=copied,
            composed=composed,
            tags=tags,
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
