from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message


class MediaBridge:
    async def copy_single(
        self,
        bot: Bot,
        from_chat_id: int,
        to_chat_id: int,
        message_id: int,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
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
        if not message_ids:
            return []
        try:
            result = await bot.copy_messages(
                chat_id=to_chat_id,
                from_chat_id=from_chat_id,
                message_ids=message_ids,
            )
            return [msg_id.message_id for msg_id in result]
        except TelegramBadRequest:
            copied_ids: list[int] = []
            for msg_id in message_ids:
                message = await bot.copy_message(
                    chat_id=to_chat_id,
                    from_chat_id=from_chat_id,
                    message_id=msg_id,
                )
                copied_ids.append(message.message_id)
            return copied_ids
