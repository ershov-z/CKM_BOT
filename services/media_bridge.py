from __future__ import annotations

"""Утилиты для безопасного копирования контента между чатами.

Используем copy_message/copy_messages вместо forward,
чтобы не светить автора исходного сообщения.
"""

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    MessageEntity,
)

from services.message_content import MediaItem

_INPUT_BY_KIND = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "document": InputMediaDocument,
    "animation": InputMediaAnimation,
    "audio": InputMediaAudio,
}


class MediaBridge:
    """Мост между чатами: копируем одно или много сообщений."""

    async def copy_single(
        self,
        bot: Bot,
        from_chat_id: int,
        to_chat_id: int,
        message_id: int,
        reply_markup: InlineKeyboardMarkup | None = None,
        caption: str | None = None,
        caption_entities: list[MessageEntity] | None = None,
    ) -> Message:
        """Копирует одно сообщение (с опциональной клавиатурой).

        Важный аспект анонимности: copy_message отправляет контент "как от бота"
        без ссылки на исходного автора. Поэтому в админ-чат и канал не утекает
        ни профиль пользователя, ни профиль модератора.
        """
        return await bot.copy_message(
            chat_id=to_chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
            caption=caption,
            caption_entities=caption_entities,
        )

    async def copy_many(
        self,
        bot: Bot,
        from_chat_id: int,
        to_chat_id: int,
        message_ids: list[int],
        remove_caption: bool = False,
    ) -> list[int]:
        """Копирует список сообщений и возвращает их новые message_id в целевом чате.

        Здесь тот же принцип анонимности, что и в copy_single: используем copy API,
        а не forward API, чтобы Telegram не показывал карточку исходного отправителя.

        Альбом нужно копировать целиком через copy_messages: если вырвать первый
        элемент отдельным copy_message, Telegram разобьёт группу на одиночные посты.
        remove_caption нужен, когда исходная подпись длиннее лимита Bot API (1024):
        иначе bulk-копирование падает, а поштучный fallback снова рвёт альбом.
        """
        if not message_ids:
            return []
        ordered_ids = sorted(message_ids)
        try:
            result = await bot.copy_messages(
                chat_id=to_chat_id,
                from_chat_id=from_chat_id,
                message_ids=ordered_ids,
                remove_caption=remove_caption or None,
            )
            return [msg_id.message_id for msg_id in result]
        except TelegramBadRequest:
            if not remove_caption:
                try:
                    result = await bot.copy_messages(
                        chat_id=to_chat_id,
                        from_chat_id=from_chat_id,
                        message_ids=ordered_ids,
                        remove_caption=True,
                    )
                    return [msg_id.message_id for msg_id in result]
                except TelegramBadRequest:
                    pass
            copied_ids: list[int] = []
            for msg_id in ordered_ids:
                copy_kwargs: dict[str, str] = {}
                if remove_caption:
                    copy_kwargs["caption"] = ""
                try:
                    message = await bot.copy_message(
                        chat_id=to_chat_id,
                        from_chat_id=from_chat_id,
                        message_id=msg_id,
                        **copy_kwargs,
                    )
                except TelegramBadRequest:
                    message = await bot.copy_message(
                        chat_id=to_chat_id,
                        from_chat_id=from_chat_id,
                        message_id=msg_id,
                        caption="",
                    )
                copied_ids.append(message.message_id)
            return copied_ids

    async def send_album(
        self,
        bot: Bot,
        chat_id: int,
        items: list[MediaItem],
        caption: str | None = None,
        caption_entities: list[MessageEntity] | None = None,
    ) -> list[int]:
        """Публикует альбом одним send_media_group с подписью на первом кадре.

        copy_messages не умеет задать свою подпись, поэтому теги раньше
        дописывали вторым запросом edit_message_caption. Здесь caption
        сразу едет вместе со всеми кадрами.
        """
        media: list[
            InputMediaPhoto
            | InputMediaVideo
            | InputMediaDocument
            | InputMediaAnimation
            | InputMediaAudio
        ] = []
        for item in items:
            media_cls = _INPUT_BY_KIND.get(item.kind)
            if media_cls is None:
                continue
            if not media and caption:
                media.append(
                    media_cls(
                        media=item.file_id,
                        caption=caption,
                        caption_entities=caption_entities,
                    )
                )
            else:
                media.append(media_cls(media=item.file_id))
        if len(media) < 2:
            return []
        result = await bot.send_media_group(chat_id=chat_id, media=media)
        return [msg.message_id for msg in result]
