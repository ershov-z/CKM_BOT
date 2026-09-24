from __future__ import annotations

"""Публикация обычного альбома в канал (не мультипост)."""

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from services.media_bridge import MediaBridge
from services.message_content import MediaItem
from services.publish_content import SENT_VIA, message_has_sent_via

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096


def _message_ids(messages: list[Message]) -> list[int]:
    return [msg.message_id for msg in messages if msg.message_id]


async def _send_footer(bot: Bot, channel_id: int, tags: str) -> list[int]:
    if tags:
        sent = await bot.send_message(
            chat_id=channel_id,
            text=f"{SENT_VIA}\n\n{tags}",
        )
        return [sent.message_id]
    sent = await bot.send_message(chat_id=channel_id, text=SENT_VIA)
    return [sent.message_id]


async def publish_plain_album(
    bot: Bot,
    media_bridge: MediaBridge,
    *,
    channel_id: int,
    source_chat_id: int,
    source_message_ids: list[int],
    media_items: list[MediaItem],
    base_text: str,
    composed: str,
    tags: str,
    caption_limit: int = CAPTION_LIMIT,
    text_limit: int = TEXT_LIMIT,
) -> list[int]:
    """Публикует альбом одним send_media_group и гарантирует футер.

    copy_messages не умеет задать свою подпись. copy_message(первое) +
    copy_many(остальные) отклеивает первую картинку. Поэтому основной путь —
    send_album. Если caption не прилип, дописываем edit'ом, а не второй копией.
    """
    posted: list[int] = []

    async def send_stored_album(caption: str | None = None) -> list[Message]:
        if len(media_items) <= 1:
            return []
        try:
            return await media_bridge.send_album(
                bot=bot,
                chat_id=channel_id,
                items=media_items,
                caption=caption,
            )
        except TelegramBadRequest:
            return []

    async def ensure_footer(first: Message | None) -> None:
        if message_has_sent_via(first):
            return
        if first is not None and len(composed) <= caption_limit:
            try:
                await bot.edit_message_caption(
                    chat_id=channel_id,
                    message_id=first.message_id,
                    caption=composed,
                )
                return
            except TelegramBadRequest:
                pass
        posted.extend(await _send_footer(bot, channel_id, tags))

    if len(base_text) > caption_limit:
        sent = await send_stored_album()
        posted.extend(_message_ids(sent))
        if not sent:
            posted.extend(
                await media_bridge.copy_many(
                    bot=bot,
                    from_chat_id=source_chat_id,
                    to_chat_id=channel_id,
                    message_ids=source_message_ids,
                    remove_caption=True,
                )
            )
        followup = await bot.send_message(
            chat_id=channel_id,
            text=composed if len(composed) <= text_limit else base_text,
        )
        posted.append(followup.message_id)
        if len(composed) > text_limit:
            posted.extend(await _send_footer(bot, channel_id, tags))
        return posted

    if len(composed) <= caption_limit:
        sent = await send_stored_album(caption=composed)
        posted.extend(_message_ids(sent))
        if sent:
            await ensure_footer(sent[0])
            return posted
        copied_ids = await media_bridge.copy_many(
            bot=bot,
            from_chat_id=source_chat_id,
            to_chat_id=channel_id,
            message_ids=source_message_ids,
        )
        posted.extend(copied_ids)
        if copied_ids:
            try:
                await bot.edit_message_caption(
                    chat_id=channel_id,
                    message_id=copied_ids[0],
                    caption=composed,
                )
            except TelegramBadRequest:
                posted.extend(await _send_footer(bot, channel_id, tags))
        else:
            posted.extend(await _send_footer(bot, channel_id, tags))
        return posted

    sent = await send_stored_album(caption=base_text or None)
    posted.extend(_message_ids(sent))
    if not sent:
        posted.extend(
            await media_bridge.copy_many(
                bot=bot,
                from_chat_id=source_chat_id,
                to_chat_id=channel_id,
                message_ids=source_message_ids,
            )
        )
    posted.extend(await _send_footer(bot, channel_id, tags))
    return posted
