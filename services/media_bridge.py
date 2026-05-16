from __future__ import annotations

"""Утилиты для безопасного копирования контента между чатами.

Используем copy_message/copy_messages вместо forward,
чтобы не светить автора исходного сообщения.
"""

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message


class MediaBridge:
    """Мост между чатами: копируем одно или много сообщений."""

    async def copy_single(
        self,
        bot: Bot,
        from_chat_id: int,
        to_chat_id: int,
        message_id: int,
        reply_markup: InlineKeyboardMarkup | None = None,
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
        )

    async def copy_many(
        self,
        bot: Bot,
        from_chat_id: int,
        to_chat_id: int,
        message_ids: list[int],
    ) -> list[int]:
        """Копирует список сообщений и возвращает их новые message_id в целевом чате.

        Здесь тот же принцип анонимности, что и в copy_single: используем copy API,
        а не forward API, чтобы Telegram не показывал карточку исходного отправителя.
        """
        if not message_ids:
            return []
        try:
            # Быстрый путь: bulk API копирования.
            result = await bot.copy_messages(
                chat_id=to_chat_id,
                from_chat_id=from_chat_id,
                message_ids=message_ids,
            )
            return [msg_id.message_id for msg_id in result]
        except TelegramBadRequest:
            # Надежный fallback: копируем по одному, если bulk не сработал.
            copied_ids: list[int] = []
            for msg_id in message_ids:
                message = await bot.copy_message(
                    chat_id=to_chat_id,
                    from_chat_id=from_chat_id,
                    message_id=msg_id,
                )
                copied_ids.append(message.message_id)
            return copied_ids
