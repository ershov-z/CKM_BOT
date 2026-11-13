from __future__ import annotations

"""Копирование тейка из нескольких сообщений в админ-чат.

Пользователь может переслать уже опубликованный пост из канала вместе со своим
ответом. У такого поста в тексте/подписи уже есть «Прислано через…» и старые
теги; если скопировать его как есть, хвост попадёт в новый тейк, а при публикации
бот допишет второй футер и новые теги.
"""

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from services.media_bridge import MediaBridge
from services.message_content import extract_media_item
from services.publish_content import entities_within_text, strip_sent_via_footer


def _raw_text(message: Message) -> str:
    return message.text or message.caption or ""


def has_stale_footer(message: Message) -> bool:
    """True, если в тексте/подписи сообщения висит хвост прошлой публикации."""
    raw = _raw_text(message)
    return bool(raw) and strip_sent_via_footer(raw) != raw


def drop_duplicate_messages(messages: list[Message]) -> list[Message]:
    """Убирает повторы из пачки: тот же message_id или тот же текст дважды.

    Двойное нажатие «отправить» даёт два одинаковых сообщения за пару секунд,
    и без этой чистки один текст уходит в админку как «тейк из 2 постов».
    Медиа не трогаем: два одинаковых на вид кадра могут быть намеренными.
    """
    kept: list[Message] = []
    seen_ids: set[int] = set()
    seen_texts: set[str] = set()
    for message in messages:
        if message.message_id in seen_ids:
            continue
        seen_ids.add(message.message_id)
        text = (message.text or "").strip()
        if text and extract_media_item(message) is None:
            if text in seen_texts:
                continue
            seen_texts.add(text)
        kept.append(message)
    return kept


def _split_runs(messages: list[Message]) -> list[list[Message]]:
    """Режет пачку на части: альбом идёт одной частью, остальное поштучно."""
    runs: list[list[Message]] = []
    for message in messages:
        group_id = message.media_group_id
        if group_id and runs and runs[-1][0].media_group_id == group_id:
            runs[-1].append(message)
        else:
            runs.append([message])
    return runs


async def copy_take_to_admin(
    bot: Bot,
    media_bridge: MediaBridge,
    *,
    messages: list[Message],
    from_chat_id: int,
    to_chat_id: int,
) -> list[int]:
    """Копирует сообщения тейка, срезая хвост «Прислано через…» со старых постов."""
    if not any(has_stale_footer(message) for message in messages):
        return await media_bridge.copy_many(
            bot=bot,
            from_chat_id=from_chat_id,
            to_chat_id=to_chat_id,
            message_ids=[message.message_id for message in messages],
        )

    copied_ids: list[int] = []
    clean_batch: list[int] = []

    async def flush_clean() -> None:
        if not clean_batch:
            return
        copied_ids.extend(
            await media_bridge.copy_many(
                bot=bot,
                from_chat_id=from_chat_id,
                to_chat_id=to_chat_id,
                message_ids=list(clean_batch),
            )
        )
        clean_batch.clear()

    for run in _split_runs(messages):
        dirty = next((message for message in run if has_stale_footer(message)), None)
        if dirty is None:
            clean_batch.extend(message.message_id for message in run)
            continue
        await flush_clean()
        cleaned = strip_sent_via_footer(_raw_text(dirty))
        entities = entities_within_text(
            cleaned, list(dirty.entities or dirty.caption_entities or [])
        )
        sent_ids = await _copy_dirty_run(
            bot,
            media_bridge,
            run=run,
            dirty=dirty,
            cleaned=cleaned,
            entities=entities,
            from_chat_id=from_chat_id,
            to_chat_id=to_chat_id,
        )
        if not sent_ids:
            # Не смогли пересобрать без хвоста — лучше копия как есть, чем потеря поста.
            sent_ids = await media_bridge.copy_many(
                bot=bot,
                from_chat_id=from_chat_id,
                to_chat_id=to_chat_id,
                message_ids=[message.message_id for message in run],
            )
        copied_ids.extend(sent_ids)
    await flush_clean()
    return copied_ids


async def _copy_dirty_run(
    bot: Bot,
    media_bridge: MediaBridge,
    *,
    run: list[Message],
    dirty: Message,
    cleaned: str,
    entities: list | None,
    from_chat_id: int,
    to_chat_id: int,
) -> list[int]:
    try:
        if len(run) > 1:
            items = [extract_media_item(message) for message in run]
            if any(item is None for item in items):
                return []
            sent = await media_bridge.send_album(
                bot=bot,
                chat_id=to_chat_id,
                items=items,
                caption=cleaned or None,
                caption_entities=entities,
            )
            ids = [message.message_id for message in sent if message.message_id]
            return ids if len(ids) == len(run) else []
        if dirty.text:
            if not cleaned:
                return []
            sent_text = await bot.send_message(
                chat_id=to_chat_id,
                text=cleaned,
                entities=entities,
            )
            return [sent_text.message_id]
        copied = await bot.copy_message(
            chat_id=to_chat_id,
            from_chat_id=from_chat_id,
            message_id=dirty.message_id,
            caption=cleaned,
            caption_entities=entities,
        )
        return [copied.message_id]
    except TelegramBadRequest:
        return []
