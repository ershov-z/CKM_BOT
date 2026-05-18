from __future__ import annotations

"""Обработчики сообщений от пользователей в личке бота.

Здесь происходит:
- прием контента;
- склейка “пачек” сообщений в один кейс;
- проверка бана;
- отправка кейса в админ-чат.
"""

import asyncio
from datetime import datetime
import time
from uuid import uuid4

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from config import Settings
from handlers.ui import moderation_keyboard
from services.ban_store import BanStore
from services.case_store import CaseRecord, CaseStore
from services.media_bridge import MediaBridge

CAPTION_LIMIT = 1024


def create_user_router(
    settings: Settings,
    case_store: CaseStore,
    media_bridge: MediaBridge,
    ban_store: BanStore,
    start_message: str,
) -> Router:
    """Создает роутер пользовательских входящих сообщений."""
    router = Router(name="user_messages")
    # Буфер для media_group (альбомов): сначала накапливаем, потом отправляем разом.
    media_group_buffer: dict[tuple[int, str], list[Message]] = {}
    media_group_tasks: dict[tuple[int, str], asyncio.Task[None]] = {}
    # Буфер "обычных" сообщений пользователя, чтобы собрать серию в один кейс.
    pending_user_messages: dict[int, list[Message]] = {}
    pending_user_tasks: dict[int, asyncio.Task[None]] = {}
    # Глобальный lock + таймер: ограничиваем частоту отправки кейсов в админку.
    case_send_lock = asyncio.Lock()
    next_case_send_at = 0.0
    batch_window_seconds = 5.0

    def build_reply_context_text(message: Message) -> str:
        """Строит текст служебного сообщения с контекстом reply (если пользователь отвечал)."""
        replied = message.reply_to_message
        if not replied:
            return "Выберите действия с анонимкой"

        preview = replied.text or replied.caption
        if not preview:
            if replied.photo:
                preview = "[фото]"
            elif replied.video:
                preview = "[видео]"
            elif replied.voice:
                preview = "[голосовое]"
            elif replied.document:
                preview = "[документ]"
            elif replied.sticker:
                preview = "[стикер]"
            else:
                preview = "[медиа/служебное сообщение]"

        preview = preview.strip().replace("\n", " ")
        if len(preview) > 120:
            preview = f"{preview[:117]}..."
        return f"Выберите действия с анонимкой\n\nПользователь ответил на: {preview}"

    def build_tagging_content(messages: list[Message]) -> str:
        """Собирает текст для LLM-тегирования из набора сообщений кейса."""
        chunks: list[str] = []
        for message in messages:
            text = (message.text or message.caption or "").strip()
            if text:
                chunks.append(text)
                continue
            if message.photo:
                chunks.append("[фото]")
            elif message.video:
                chunks.append("[видео]")
            elif message.voice:
                chunks.append("[голосовое]")
            elif message.document:
                file_name = message.document.file_name or "документ"
                chunks.append(f"[документ: {file_name}]")
            elif message.sticker:
                chunks.append("[стикер]")
            else:
                chunks.append("[медиа/служебное сообщение]")
        return "\n".join(chunks).strip()

    async def submit_case(messages: list[Message]) -> None:
        """Формирует и отправляет кейс в админ-чат."""
        nonlocal next_case_send_at
        first = messages[0]
        case_id = uuid4().hex[:8]
        source_message_ids = [msg.message_id for msg in messages]
        keyboard = moderation_keyboard(case_id)
        control_text = build_reply_context_text(first)
        tagging_content = build_tagging_content(messages)
        single_content_text = (first.text or first.caption or "").strip()
        single_content_entities = list(first.entities or first.caption_entities or [])
        single_content_type = first.content_type

        # Отправляем кейсы в админку последовательно, чтобы не ломать порядок сообщений.
        async with case_send_lock:
            now = time.monotonic()
            if now < next_case_send_at:
                await asyncio.sleep(next_case_send_at - now)

            # Одиночный кейс.
            # В админ-чат копируем только контент, а не "пересланное сообщение":
            # это скрывает автора на стороне модерации.
            if len(source_message_ids) == 1:
                if single_content_type != "text" and len(single_content_text) > CAPTION_LIMIT:
                    # Premium-кейс: caption длиннее лимита Bot API.
                    # Показываем админам такой кейс как "мультипост":
                    # медиа без подписи + текст отдельным сообщением.
                    posts_count = 2
                    start_marker = await first.bot.send_message(
                        chat_id=settings.admin_chat_id,
                        text=f"Начало тейка из нескольких постов ({posts_count}) ↓",
                    )
                    copied = await media_bridge.copy_single(
                        bot=first.bot,
                        from_chat_id=first.chat.id,
                        to_chat_id=settings.admin_chat_id,
                        message_id=first.message_id,
                        caption="",
                    )
                    text_message = await first.bot.send_message(
                        chat_id=settings.admin_chat_id,
                        text=single_content_text,
                        entities=single_content_entities or None,
                    )
                    end_marker = await first.bot.send_message(
                        chat_id=settings.admin_chat_id,
                        text=f"Конец тейка из нескольких постов ({posts_count}) ↑",
                    )
                    control = await first.bot.send_message(
                        chat_id=settings.admin_chat_id,
                        text=control_text,
                        reply_markup=keyboard,
                    )
                    case_store.add_case(
                        CaseRecord(
                            case_id=case_id,
                            # chat_id держим только в памяти процесса.
                            # На диск он не сериализуется (см. CaseStore._serialize_case).
                            user_chat_id=first.chat.id,
                            source_message_ids=source_message_ids,
                            is_media_group=False,
                            admin_message_ids=[
                                start_marker.message_id,
                                copied.message_id,
                                text_message.message_id,
                                end_marker.message_id,
                                control.message_id,
                            ],
                            # Контентные сообщения для fallback-публикации после рестарта.
                            admin_content_message_ids=[copied.message_id, text_message.message_id],
                            control_message_id=control.message_id,
                            content_for_tagging=tagging_content,
                            single_content_text=single_content_text,
                            single_content_entities=single_content_entities,
                            single_content_type=single_content_type,
                        )
                    )
                else:
                    copied = await media_bridge.copy_single(
                        bot=first.bot,
                        from_chat_id=first.chat.id,
                        to_chat_id=settings.admin_chat_id,
                        message_id=first.message_id,
                    )
                    control = await first.bot.send_message(
                        chat_id=settings.admin_chat_id,
                        text=control_text,
                        reply_markup=keyboard,
                    )
                    case_store.add_case(
                        CaseRecord(
                            case_id=case_id,
                            # chat_id держим только в памяти процесса.
                            # На диск он не сериализуется (см. CaseStore._serialize_case).
                            user_chat_id=first.chat.id,
                            source_message_ids=source_message_ids,
                            is_media_group=False,
                            admin_message_ids=[copied.message_id, control.message_id],
                            # Нужен fallback-путь публикации после рестарта без user chat_id:
                            # этот ID указывает на контентное сообщение в админ-чате.
                            admin_content_message_ids=[copied.message_id],
                            control_message_id=control.message_id,
                            content_for_tagging=tagging_content,
                            single_content_text=single_content_text,
                            single_content_entities=single_content_entities,
                            single_content_type=single_content_type,
                        )
                    )
            # Кейс из нескольких сообщений.
            # Маркеры "начало/конец" нужны лишь для удобства чтения модераторами
            # и не несут персональных данных пользователя.
            else:
                is_album = bool(first.media_group_id) and all(
                    msg.media_group_id == first.media_group_id for msg in messages
                )
                if is_album:
                    # Telegram media group: в админке показываем как один кейс без маркеров multi.
                    copied_ids: list[int] = []
                    text_message_id: int | None = None
                    if len(single_content_text) > CAPTION_LIMIT:
                        # Длинная подпись premium-поста: безопасно копируем без подписи,
                        # иначе bot API может отклонить caption>1024.
                        first_copied = await media_bridge.copy_single(
                            bot=first.bot,
                            from_chat_id=first.chat.id,
                            to_chat_id=settings.admin_chat_id,
                            message_id=source_message_ids[0],
                            caption="",
                        )
                        copied_ids = [first_copied.message_id]
                        if len(source_message_ids) > 1:
                            rest_copied = await media_bridge.copy_many(
                                bot=first.bot,
                                from_chat_id=first.chat.id,
                                to_chat_id=settings.admin_chat_id,
                                message_ids=source_message_ids[1:],
                            )
                            copied_ids.extend(rest_copied)
                        text_message = await first.bot.send_message(
                            chat_id=settings.admin_chat_id,
                            text=single_content_text,
                            entities=single_content_entities or None,
                        )
                        text_message_id = text_message.message_id
                    else:
                        # Когда подпись в лимите — копируем весь альбом одним вызовом,
                        # чтобы Telegram сохранил группировку media group.
                        copied_ids = await media_bridge.copy_many(
                            bot=first.bot,
                            from_chat_id=first.chat.id,
                            to_chat_id=settings.admin_chat_id,
                            message_ids=source_message_ids,
                        )
                    control = await first.bot.send_message(
                        chat_id=settings.admin_chat_id,
                        text=control_text,
                        reply_markup=keyboard,
                    )
                    admin_message_ids = [*copied_ids]
                    if text_message_id is not None:
                        admin_message_ids.append(text_message_id)
                    admin_message_ids.append(control.message_id)
                    case_store.add_case(
                        CaseRecord(
                            case_id=case_id,
                            # chat_id держим только в памяти процесса.
                            # На диск он не сериализуется (см. CaseStore._serialize_case).
                            user_chat_id=first.chat.id,
                            source_message_ids=source_message_ids,
                            is_media_group=True,
                            is_composed_multi_post=False,
                            admin_message_ids=admin_message_ids,
                            # Для fallback-публикации после рестарта сохраняем только media IDs.
                            admin_content_message_ids=[*copied_ids],
                            control_message_id=control.message_id,
                            content_for_tagging=tagging_content,
                            single_content_text=single_content_text,
                            single_content_entities=single_content_entities,
                            single_content_type=single_content_type,
                        )
                    )
                else:
                    posts_count = len(source_message_ids)
                    start_marker = await first.bot.send_message(
                        chat_id=settings.admin_chat_id,
                        text=f"Начало тейка из нескольких постов ({posts_count}) ↓",
                    )
                    copied_ids = await media_bridge.copy_many(
                        bot=first.bot,
                        from_chat_id=first.chat.id,
                        to_chat_id=settings.admin_chat_id,
                        message_ids=source_message_ids,
                    )
                    end_marker = await first.bot.send_message(
                        chat_id=settings.admin_chat_id,
                        text=f"Конец тейка из нескольких постов ({posts_count}) ↑",
                    )
                    control = await first.bot.send_message(
                        chat_id=settings.admin_chat_id,
                        text=control_text,
                        reply_markup=keyboard,
                    )
                    case_store.add_case(
                        CaseRecord(
                            case_id=case_id,
                            # chat_id держим только в памяти процесса.
                            # На диск он не сериализуется (см. CaseStore._serialize_case).
                            user_chat_id=first.chat.id,
                            source_message_ids=source_message_ids,
                            is_media_group=True,
                            is_composed_multi_post=True,
                            admin_message_ids=[
                                start_marker.message_id,
                                *copied_ids,
                                end_marker.message_id,
                                control.message_id,
                            ],
                            # В мультикейсе берём только контентные сообщения, без маркеров.
                            admin_content_message_ids=[*copied_ids],
                            control_message_id=control.message_id,
                            content_for_tagging=tagging_content,
                            single_content_text=single_content_text,
                            single_content_entities=single_content_entities,
                            single_content_type=single_content_type,
                        )
                    )

            # Подтверждение пользователю, что сообщение принято.
            await first.bot.send_message(
                chat_id=first.chat.id,
                text="Ваша анонимка принята!",
            )

            # Ограничение: не чаще одного нового кейса в 5 секунд.
            next_case_send_at = time.monotonic() + 5.0

    async def flush_user_batch(user_chat_id: int) -> None:
        """Срабатывает после паузы и отправляет накопленную пачку сообщений пользователя."""
        try:
            await asyncio.sleep(batch_window_seconds)
        except asyncio.CancelledError:
            return
        messages = sorted(
            pending_user_messages.pop(user_chat_id, []),
            key=lambda item: item.message_id,
        )
        pending_user_tasks.pop(user_chat_id, None)
        if messages:
            await submit_case(messages)

    async def enqueue_user_messages(messages: list[Message]) -> None:
        """Кладет сообщения в буфер и перезапускает debounce-таймер."""
        if not messages:
            return
        user_chat_id = messages[0].chat.id
        pending_user_messages.setdefault(user_chat_id, []).extend(messages)
        task = pending_user_tasks.get(user_chat_id)
        if task and not task.done():
            task.cancel()
        pending_user_tasks[user_chat_id] = asyncio.create_task(
            flush_user_batch(user_chat_id)
        )

    async def flush_media_group(group_key: tuple[int, str]) -> None:
        """Собирает альбом (media_group) в единый список сообщений."""
        await asyncio.sleep(1.0)
        messages = sorted(
            media_group_buffer.pop(group_key, []),
            key=lambda item: item.message_id,
        )
        media_group_tasks.pop(group_key, None)
        if messages:
            await enqueue_user_messages(messages)

    @router.message(F.chat.type == ChatType.PRIVATE)
    async def on_user_message(message: Message) -> None:
        """Главная точка входа для сообщений пользователя в личке."""
        if not message.from_user or message.from_user.is_bot:
            return
        # /start — это служебная команда знакомства с ботом, в модерацию не отправляем.
        command_text = (message.text or "").strip().lower()
        if command_text.startswith("/start"):
            await message.answer(start_message)
            return
        # Если пользователь в бане — сразу показываем предупреждение и не принимаем кейс.
        if ban_store.is_banned(message.from_user.id):
            ban_until = ban_store.get_ban_until(message.from_user.id)
            if ban_until is None:
                text = "Вы забанены и не можете отправлять сообщения этому боту."
            else:
                until_text = datetime.fromtimestamp(ban_until).strftime("%d.%m.%Y %H:%M")
                text = f"Вы временно забанены до {until_text} и не можете писать боту."
            await message.answer(text)
            return
        # Альбомы обрабатываем через отдельный буфер.
        if message.media_group_id:
            key = (message.chat.id, message.media_group_id)
            media_group_buffer.setdefault(key, []).append(message)
            if key not in media_group_tasks:
                media_group_tasks[key] = asyncio.create_task(
                    flush_media_group(key)
                )
            return
        # Обычное сообщение также идет в буфер пачки.
        await enqueue_user_messages([message])

    @router.edited_message(F.chat.type == ChatType.PRIVATE)
    async def on_user_edited_message(message: Message) -> None:
        """Если пользователь отредактировал сообщение, шлем новую версию в админку."""
        if not message.from_user or message.from_user.is_bot:
            return
        case = case_store.find_open_case_by_user_message(
            user_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        if not case:
            return

        await message.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=(
                f"Сервис: пользователь отредактировал сообщение в кейсе `{case.case_id}`.\n"
                "Новая версия сообщения ниже."
            ),
            parse_mode="Markdown",
        )

        edited_text = (message.text or message.caption or "").strip()
        edited_entities = list(message.entities or message.caption_entities or [])
        # Важный fallback: длинная подпись медиа может не пройти через copy_message.
        if message.content_type != "text" and len(edited_text) > CAPTION_LIMIT:
            await media_bridge.copy_single(
                bot=message.bot,
                from_chat_id=message.chat.id,
                to_chat_id=settings.admin_chat_id,
                message_id=message.message_id,
                caption="",
            )
            await message.bot.send_message(
                chat_id=settings.admin_chat_id,
                text=edited_text,
                entities=edited_entities or None,
            )
            return

        await media_bridge.copy_single(
            bot=message.bot,
            from_chat_id=message.chat.id,
            to_chat_id=settings.admin_chat_id,
            message_id=message.message_id,
        )

    return router
