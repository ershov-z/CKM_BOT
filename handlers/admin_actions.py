from __future__ import annotations

"""Обработчики кнопок и сообщений админов.

Это центральный модуль модерации:
- генерация/редактирование тегов;
- публикация;
- отклонение;
- ответы пользователю;
- баны и разбаны.
"""

import asyncio
import re
import time
from datetime import datetime
from uuid import uuid4

from aiogram import F, Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, MessageEntity

from config import Settings
from handlers.ui import (
    ban_confirm_keyboard,
    ban_duration_keyboard,
    manual_publish_tags_keyboard,
    moderation_keyboard,
    reject_reasons_keyboard,
    tagged_preview_keyboard,
    unban_confirm_keyboard,
    unban_request_keyboard,
)
from services.ban_store import BanStore
from services.case_store import CaseRecord, CaseStore, RepublishSnapshot
from services.media_bridge import MediaBridge
from services.message_content import (
    content_rejects_caption,
    extract_message_text,
    is_rich_message,
    resolve_content_type,
)
from services.reject_reasons import format_admin_reject_guide, load_reject_reasons
from services.tagging_service import TAG_CATALOG, TaggingService

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
SENT_VIA = "Прислано через @backstage_staff_bot"
CASE_ID_RE = re.compile(r"Кейс `([0-9a-fA-F]{8})`")


def create_admin_router(
    settings: Settings,
    case_store: CaseStore,
    media_bridge: MediaBridge,
    ban_store: BanStore,
    tagging_service: TaggingService,
    reasons_path: str,
) -> Router:
    """Создает роутер админских действий."""
    router = Router(name="admin_actions")
    # Временные буферы для сценария "ответ админа пользователю".
    reply_media_group_buffer: dict[tuple[int, str], list[Message]] = {}
    reply_media_group_tasks: dict[tuple[int, str], asyncio.Task[None]] = {}
    pending_reply_case_id: str | None = None

    def is_allowed_moderator(user_id: int | None) -> bool:
        """Проверяет, что действие выполняет пользователь из whitelist модераторов."""
        return bool(user_id and user_id in settings.admin_user_ids)

    def ban_duration_options() -> dict[str, tuple[str, int | None]]:
        return {
            "1d": ("1 день", 24 * 60 * 60),
            "7d": ("7 дней", 7 * 24 * 60 * 60),
            "30d": ("30 дней", 30 * 24 * 60 * 60),
            "perm": ("навсегда", None),
        }

    tag_catalog_map = {tag.lower(): tag for tag in TAG_CATALOG}

    def normalize_tags(tags: list[str]) -> list[str]:
        """Нормализует список тегов: canonical вид, без дублей, только из каталога."""
        result: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            normalized = tag.strip().lower()
            if not normalized:
                continue
            if not normalized.startswith("#"):
                normalized = f"#{normalized.lstrip('#')}"
            canonical = tag_catalog_map.get(normalized)
            if not canonical:
                continue
            key = canonical.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(canonical)
        return result

    def parse_tags_from_text(text: str) -> list[str]:
        """Вытаскивает #теги из произвольного текста и нормализует их."""
        found = re.findall(r"#[-\wа-яА-ЯёЁ]+", text)
        return normalize_tags(found)

    def tags_block(case: CaseRecord) -> str:
        """Собирает теги в многострочный блок (по одному тегу на строку)."""
        return "\n".join(case.selected_tags).strip()

    def compose_single_text_with_tags(case: CaseRecord) -> str:
        """Склеивает текст поста, подпись «Прислано через…» и блок тегов."""
        base = (case.single_content_text or "").strip()
        tags = tags_block(case)
        parts: list[str] = []
        if base:
            parts.append(base)
        parts.append(SENT_VIA)
        if tags:
            parts.append(tags)
        return "\n\n".join(parts)

    def build_tag_preview_text(case: CaseRecord) -> str:
        """Формирует текст предпросмотра публикации (для рабочей карточки кейса)."""
        base = (case.content_for_tagging or case.single_content_text or "").strip()
        tags = tags_block(case) or "(теги не выбраны)"
        if base:
            body = f"{base}\n\n{tags}"
        else:
            body = tags
        return (
            "Предпросмотр публикации\n\n"
            f"{body}"
        )

    def build_multi_start_text(case: CaseRecord) -> str:
        """Служебная строка 'Начало тейка...' для кейсов из нескольких сообщений."""
        posts_count = len(case.source_message_ids)
        start_text = f"Начало тейка из нескольких постов ({posts_count}) ↓"
        current_tags = tags_block(case)
        if current_tags:
            start_text = f"{start_text}\n\n{current_tags}"
        return start_text

    def media_requires_separate_tags(case: CaseRecord) -> bool:
        """Типы, где безопаснее отправлять теги отдельным сообщением (без подписи)."""
        return case.single_content_type in {"audio", "voice", "video_note"}

    def message_has_sent_via(message: Message | None) -> bool:
        """Проверяет, что в скопированном сообщении уже есть служебная подпись."""
        if message is None:
            return False
        blob = "\n".join(part for part in (message.text, message.caption) if part)
        return SENT_VIA in blob

    async def send_tag_preview(bot: Bot, case: CaseRecord) -> None:
        """Обновляет ОДНО рабочее сообщение-карточку предпросмотра для кейса."""
        preview_text = build_tag_preview_text(case)
        entities: list[MessageEntity] | None = None
        if case.single_content_type == "text" and case.single_content_entities:
            entities = case.single_content_entities

        if case.control_message_id is None:
            created = await bot.send_message(
                chat_id=settings.admin_chat_id,
                text=preview_text,
                entities=entities,
                reply_markup=tagged_preview_keyboard(case.case_id),
            )
            case.control_message_id = created.message_id
            case_store.index_case(case)
            return

        try:
            # Базовый путь: редактируем текст и кнопки уже существующего сообщения.
            await bot.edit_message_text(
                chat_id=settings.admin_chat_id,
                message_id=case.control_message_id,
                text=preview_text,
                entities=entities,
                reply_markup=tagged_preview_keyboard(case.case_id),
            )
        except TelegramBadRequest:
            # fallback: если текст отредактировать нельзя, хотя бы обновим кнопки.
            await bot.edit_message_reply_markup(
                chat_id=settings.admin_chat_id,
                message_id=case.control_message_id,
                reply_markup=tagged_preview_keyboard(case.case_id),
            )

    async def publish_case_with_tags(bot: Bot, case: CaseRecord) -> None:
        """Финальная публикация кейса в канал (с учетом типа контента)."""
        # Основной путь: публикация из лички пользователя (когда chat_id еще в памяти).
        # Fallback после рестарта: публикация из копий в админ-чате.
        # За счет fallback админ может завершить публикацию даже после падения бота,
        # при этом без восстановления прямой связи с пользователем.
        source_chat_id = case.user_chat_id or settings.admin_chat_id
        source_message_ids = (
            case.source_message_ids
            if case.user_chat_id
            else (
                # Предпочитаем чистый список контента (без служебных сообщений).
                case.admin_content_message_ids
                if case.admin_content_message_ids
                # Legacy fallback: старые кейсы могли иметь только общий список.
                else case.admin_message_ids
            )
        )
        if not source_message_ids:
            raise RuntimeError("Не удалось найти сообщения для публикации.")

        base_text = (case.single_content_text or "").strip()
        tags = tags_block(case)
        composed = compose_single_text_with_tags(case)
        entities: list[MessageEntity] | None = (
            case.single_content_entities if case.single_content_entities else None
        )

        async def send_sent_via_then_tags(tags_part: str | None = None) -> None:
            """Досылает подпись «Прислано через…» и теги одним сообщением."""
            if tags_part:
                await bot.send_message(
                    chat_id=settings.publish_channel_id,
                    text=f"{SENT_VIA}\n\n{tags_part}",
                )
                return
            await bot.send_message(chat_id=settings.publish_channel_id, text=SENT_VIA)

        async def copy_single_for_publish(
            caption: str | None = None,
            caption_entities: list[MessageEntity] | None = None,
        ) -> Message | None:
            """Копирует исходный пост. Если caption не принялся — копирует как есть.

            Ответ Telegram для rich-поста с альбомами внутри текста старый aiogram
            может не распарсить, хотя сообщение в канал уже ушло. Тогда возвращаем None.
            """
            if caption is not None:
                try:
                    return await bot.copy_message(
                        chat_id=settings.publish_channel_id,
                        from_chat_id=source_chat_id,
                        message_id=source_message_ids[0],
                        caption=caption,
                        caption_entities=caption_entities,
                    )
                except TelegramBadRequest:
                    pass
                except Exception:
                    return None
            try:
                return await bot.copy_message(
                    chat_id=settings.publish_channel_id,
                    from_chat_id=source_chat_id,
                    message_id=source_message_ids[0],
                )
            except Exception:
                return None

        async def ensure_footer_after_copy(
            copied: Message | None,
            tags_part: str | None,
        ) -> None:
            """Если copy не вшил подпись и теги, досылает их отдельным сообщением."""
            if message_has_sent_via(copied):
                return
            await send_sent_via_then_tags(tags_part)

        async def send_text_followup_after_album(
            base_text: str,
            composed: str,
            entities: list[MessageEntity] | None,
            tags_part: str | None,
        ) -> None:
            """После альбома досылает длинный текст постом, не дробя подпись и теги."""
            if len(composed) <= TEXT_LIMIT:
                await bot.send_message(
                    chat_id=settings.publish_channel_id,
                    text=composed,
                    entities=entities,
                )
                return
            await bot.send_message(
                chat_id=settings.publish_channel_id,
                text=base_text,
                entities=entities,
            )
            await send_sent_via_then_tags(tags_part)

        if case.is_media_group and case.is_composed_multi_post:
            await bot.send_message(
                chat_id=settings.publish_channel_id,
                text=build_multi_start_text(case),
            )
            try:
                await media_bridge.copy_many(
                    bot=bot,
                    from_chat_id=source_chat_id,
                    to_chat_id=settings.publish_channel_id,
                    message_ids=source_message_ids,
                )
            except Exception:
                pass
            await bot.send_message(
                chat_id=settings.publish_channel_id,
                text=f"Конец тейка из нескольких постов ({len(source_message_ids)}) ↑",
            )
            await send_sent_via_then_tags(tags or None)
            return
        if case.is_media_group and not case.is_composed_multi_post:
            # Media group публикуем без маркеров multi: как один "обычный" кейс.
            # Альбом всегда копируем целиком. Нельзя copy_message(первое) + copy_many(остальные):
            # Telegram тогда отклеивает первую картинку от группы.
            if len(base_text) > CAPTION_LIMIT:
                await media_bridge.copy_many(
                    bot=bot,
                    from_chat_id=source_chat_id,
                    to_chat_id=settings.publish_channel_id,
                    message_ids=source_message_ids,
                    remove_caption=True,
                )
                await send_text_followup_after_album(
                    base_text=base_text,
                    composed=composed,
                    entities=entities,
                    tags_part=tags or None,
                )
                return

            if len(composed) <= CAPTION_LIMIT:
                copied_ids = await media_bridge.copy_many(
                    bot=bot,
                    from_chat_id=source_chat_id,
                    to_chat_id=settings.publish_channel_id,
                    message_ids=source_message_ids,
                )
                # Альбом уже скопирован как единая группа; теперь обновляем caption
                # первого элемента, чтобы добавить теги, не ломая grouping.
                if copied_ids:
                    try:
                        await bot.edit_message_caption(
                            chat_id=settings.publish_channel_id,
                            message_id=copied_ids[0],
                            caption=composed,
                            caption_entities=entities,
                        )
                    except TelegramBadRequest:
                        # Если edit caption недоступен для конкретного медиа-типа,
                        # не рвем альбом: публикуем подпись и теги отдельным сообщением.
                        await send_sent_via_then_tags(tags or None)
                else:
                    await send_sent_via_then_tags(tags or None)
                return

            # Исходный caption влезает, а с подписью/тегами уже нет: досылаем отдельно.
            await media_bridge.copy_many(
                bot=bot,
                from_chat_id=source_chat_id,
                to_chat_id=settings.publish_channel_id,
                message_ids=source_message_ids,
            )
            await send_sent_via_then_tags(tags or None)
            return

        if content_rejects_caption(case.single_content_type):
            # Rich-посты с картинками внутри текста и прочие типы без caption:
            # copy_message(caption=...) либо падает, либо копирует оригинал без хвоста.
            copied = await copy_single_for_publish()
            await ensure_footer_after_copy(copied, tags or None)
            return

        if case.single_content_type == "text":
            if case.user_chat_id is None:
                # После рестарта или /again админская копия может быть rich-постом
                # с альбомами внутри текста. send_message оставил бы только текст.
                copied = await copy_single_for_publish()
                await ensure_footer_after_copy(copied, tags or None)
                return
            await bot.send_message(
                chat_id=settings.publish_channel_id,
                text=composed,
                entities=entities,
            )
            return

        async def send_single_media_with_split_parts(
            media_caption: str | None = None,
            media_caption_entities: list[MessageEntity] | None = None,
            text_part: str | None = None,
            tags_part: str | None = None,
        ) -> None:
            """Публикует одиночный медиа-кейс без маркеров мультипоста.

            Используется, когда caption переполняется: сохраняем исходное медиа,
            а текст/подпись/теги переносим в отдельные сообщения после него.
            """
            await copy_single_for_publish(
                caption=media_caption,
                caption_entities=media_caption_entities,
            )
            if text_part:
                await bot.send_message(
                    chat_id=settings.publish_channel_id,
                    text=text_part,
                    entities=entities if text_part == base_text else None,
                )
            await send_sent_via_then_tags(tags_part)

        async def send_single_media_as_multi_take_for_long_base() -> None:
            """Публикует кейс как multi-тейк, если исходный текст медиа не влезает в caption."""
            posts_count = 1
            if base_text:
                posts_count += 1
            if tags:
                posts_count += 1
            await bot.send_message(
                chat_id=settings.publish_channel_id,
                text=f"Начало тейка из нескольких постов ({posts_count}) ↓",
            )
            await bot.copy_message(
                chat_id=settings.publish_channel_id,
                from_chat_id=source_chat_id,
                message_id=source_message_ids[0],
            )
            if base_text:
                await bot.send_message(
                    chat_id=settings.publish_channel_id,
                    text=base_text,
                    entities=entities,
                )
            if tags:
                await bot.send_message(chat_id=settings.publish_channel_id, text=tags)
            await bot.send_message(
                chat_id=settings.publish_channel_id,
                text=f"Конец тейка из нескольких постов ({posts_count}) ↑",
            )
            await bot.send_message(chat_id=settings.publish_channel_id, text=SENT_VIA)

        if media_requires_separate_tags(case):
            await bot.copy_message(
                chat_id=settings.publish_channel_id,
                from_chat_id=source_chat_id,
                message_id=source_message_ids[0],
            )
            await send_sent_via_then_tags(tags or None)
            return

        # Premium-кейс: исходный пользовательский текст уже длиннее лимита caption.
        # Это считается составным постом, поэтому публикуем как multi-тейк с маркерами.
        if len(base_text) > CAPTION_LIMIT:
            await send_single_media_as_multi_take_for_long_base()
            return

        # Если исходная подпись помещалась, но после добавления «Прислано…»/тегов
        # стала слишком длинной: сохраняем исходный caption, остальное — отдельно.
        if len(composed) > CAPTION_LIMIT:
            await send_single_media_with_split_parts(
                media_caption=base_text or None,
                media_caption_entities=entities,
                tags_part=tags or None,
            )
            return

        copied = await copy_single_for_publish(
            caption=composed,
            caption_entities=entities,
        )
        await ensure_footer_after_copy(copied, tags or None)

    async def notify_user_published(bot: Bot, case: CaseRecord) -> None:
        """Уведомляет автора, что его анонимка реально опубликована."""
        if case.user_chat_id is None:
            # После рестарта chat_id автора в памяти уже нет — только публикуем.
            return
        await bot.send_message(
            chat_id=case.user_chat_id,
            text="Ваша анонимка опубликована!",
        )

    async def disable_case_controls(bot: Bot, case: CaseRecord) -> None:
        """Убирает inline-кнопки на рабочем сообщении кейса после завершения."""
        if case.control_message_id is None:
            return
        try:
            await bot.edit_message_reply_markup(
                chat_id=settings.admin_chat_id,
                message_id=case.control_message_id,
                reply_markup=None,
            )
        except TelegramBadRequest:
            return

    async def finalize_case(
        bot: Bot,
        case: CaseRecord,
        status: str,
        note: str,
        send_case_note: bool = True,
    ) -> None:
        """Закрывает кейс: снимает кнопки, меняет статус и пишет сервисную отметку."""
        await disable_case_controls(bot, case)
        # Статус пишем в CaseStore, чтобы кейс исчез из open_cases.json и не лежал там
        # дольше необходимого для модерации времени.
        case_store.mark_done(case.case_id, status)
        if send_case_note:
            note_message = await bot.send_message(
                chat_id=settings.admin_chat_id,
                text=f"Кейс `{case.case_id}`: {note}",
                parse_mode="Markdown",
            )
            case_store.index_case(case, extra_message_ids=[note_message.message_id])

    def snapshot_from_live_case(case: CaseRecord) -> RepublishSnapshot:
        return RepublishSnapshot(
            case_id=case.case_id,
            admin_content_message_ids=list(case.admin_content_message_ids),
            is_media_group=case.is_media_group,
            is_composed_multi_post=case.is_composed_multi_post,
            single_content_text=case.single_content_text,
            single_content_type=case.single_content_type,
            content_for_tagging=case.content_for_tagging,
            selected_tags=list(case.selected_tags),
        )

    def snapshot_from_reply(replied: Message) -> RepublishSnapshot:
        text = extract_message_text(replied)
        content_type = resolve_content_type(replied)
        return RepublishSnapshot(
            case_id="",
            admin_content_message_ids=[replied.message_id],
            is_media_group=bool(replied.media_group_id),
            is_composed_multi_post=False,
            single_content_text=text,
            single_content_type=content_type,
            content_for_tagging=text,
            selected_tags=[],
        )

    def resolve_again_snapshot(replied: Message) -> tuple[RepublishSnapshot, bool]:
        """Ищет слепок для /again: индекс, открытый кейс, id в тексте или сам пост.

        Второй элемент — True, если нашли полный слепок (альбом/мультипост),
        False если восстановили только одно ответное сообщение.
        """
        snapshot = case_store.find_snapshot_by_admin_message(replied.message_id)
        if snapshot and snapshot.admin_content_message_ids:
            return snapshot, True

        live = case_store.find_case_by_admin_message(replied.message_id)
        if live and live.admin_content_message_ids:
            return snapshot_from_live_case(live), True

        note_text = replied.text or ""
        match = CASE_ID_RE.search(note_text)
        if match:
            case_id = match.group(1)
            snapshot = case_store.get_snapshot(case_id)
            if snapshot and snapshot.admin_content_message_ids:
                return snapshot, True
            live = case_store.get_case(case_id)
            if live and live.admin_content_message_ids:
                return snapshot_from_live_case(live), True

        return snapshot_from_reply(replied), False

    def is_again_command(message: Message) -> bool:
        raw = (message.text or message.caption or "").strip()
        if not raw:
            return False
        command = raw.split(maxsplit=1)[0].split("@", 1)[0].lower()
        return command == "/again"

    async def prepare_case_again(message: Message) -> None:
        """Собирает новый открытый кейс из поста, на который ответили /again.

        Контент не копируем: повторная копия rich-поста с альбомами внутри текста
        часто падает на разборе ответа, и карточка с кнопками уже не отправляется.
        Кнопки шлём сразу ответом на исходный пост.
        """
        replied = message.reply_to_message
        if replied is None:
            await message.answer(
                "Ответьте командой /again на пост, который нужно подготовить снова."
            )
            return

        snapshot, complete = resolve_again_snapshot(replied)
        source_ids = snapshot.admin_content_message_ids or [replied.message_id]
        if not source_ids:
            source_ids = [replied.message_id]

        copied_type = snapshot.single_content_type or resolve_content_type(replied)
        copied_text = snapshot.single_content_text or extract_message_text(replied)
        # Один пост с альбомами внутри текста нельзя публиковать через caption —
        # Telegram либо игнорирует хвост, либо ломает вёрстку. Копируем как есть.
        if len(source_ids) == 1 and (
            copied_type != "text"
            or is_rich_message(replied)
            or content_rejects_caption(copied_type)
        ):
            copied_type = "rich_message"

        case_id = uuid4().hex[:8]
        try:
            control = await message.bot.send_message(
                chat_id=settings.admin_chat_id,
                text="Повторная подготовка к отправке. Выберите действия с анонимкой",
                reply_to_message_id=replied.message_id,
                reply_markup=moderation_keyboard(case_id),
            )
        except TelegramBadRequest:
            control = await message.reply(
                "Повторная подготовка к отправке. Выберите действия с анонимкой",
                reply_markup=moderation_keyboard(case_id),
            )

        case = CaseRecord(
            case_id=case_id,
            user_chat_id=None,
            source_message_ids=list(source_ids),
            is_media_group=snapshot.is_media_group and len(source_ids) > 1,
            is_composed_multi_post=snapshot.is_composed_multi_post and len(source_ids) > 1,
            admin_message_ids=[*source_ids, control.message_id],
            admin_content_message_ids=list(source_ids),
            control_message_id=control.message_id,
            content_for_tagging=snapshot.content_for_tagging or copied_text,
            single_content_text=copied_text,
            single_content_type=copied_type,
            selected_tags=normalize_tags(snapshot.selected_tags),
        )
        case_store.add_case(case)
        extra = ""
        if not complete:
            extra = (
                "\nЕсли это был альбом или тейк из нескольких сообщений, "
                "ответьте /again на служебное сообщение кейса — иначе уйдёт только этот пост."
            )
        await message.answer(
            f"Кейс `{case.case_id}` снова подготовлен к отправке.{extra}",
            parse_mode="Markdown",
        )

    @router.message(F.chat.id == settings.admin_chat_id, Command("again"))
    async def on_again_command(message: Message) -> None:
        """Повторно готовит пост к публикации по reply /again."""
        if not message.from_user or message.from_user.is_bot:
            return
        if not is_allowed_moderator(message.from_user.id):
            await message.answer("У вас нет прав на модерацию.")
            return
        await prepare_case_again(message)

    @router.callback_query(F.data.startswith("act:"))
    async def on_action(query: CallbackQuery) -> None:
        """Главный обработчик кнопок первой карточки кейса (act:*)."""
        if not query.from_user:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        parts = (query.data or "").split(":", maxsplit=2)
        if len(parts) != 3:
            await query.answer("Некорректные данные действия.", show_alert=True)
            return
        _, action, case_id = parts
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return

        if action == "publish":
            # Ручная публикация: сначала модератор явно выбирает теги кнопками.
            selected = normalize_tags(case.selected_tags)
            case.selected_tags = selected
            if case.control_message_id is not None:
                await query.bot.edit_message_reply_markup(
                    chat_id=settings.admin_chat_id,
                    message_id=case.control_message_id,
                    reply_markup=manual_publish_tags_keyboard(
                        case.case_id,
                        TAG_CATALOG,
                        case.selected_tags,
                    ),
                )
            await query.answer("Выберите теги и нажмите Опубликовать.")
            return

        if action == "gen_tags":
            # Авто-теги через LLM + построение/обновление предпросмотра.
            try:
                scored = await tagging_service.score_tags(
                    case.content_for_tagging or "Пустое сообщение"
                )
                generated = [item.tag for item in scored if item.score > 7]
                case.selected_tags = normalize_tags(generated)
                if not case.selected_tags:
                    case.selected_tags = ["#тейк"]
            except Exception as exc:
                scored = []
                case.selected_tags = ["#тейк"]
                await query.bot.send_message(
                    chat_id=settings.admin_chat_id,
                    text=(
                        "Не удалось сгенерировать теги через Chad API.\n"
                        f"Причина: {exc}\n"
                        "Использован fallback: #тейк"
                    ),
                )

            case.is_waiting_tag_edit = False
            await send_tag_preview(query.bot, case)
            case_store.persist_case(case)
            if scored:
                score_map = {item.tag.lower(): item.score for item in scored}
                score_lines = "\n".join(
                    f"{tag}: {score_map.get(tag.lower(), 0.0):.1f}" for tag in TAG_CATALOG
                )
                selected_lines = "\n".join(case.selected_tags)
                await query.bot.send_message(
                    chat_id=settings.admin_chat_id,
                    text=(
                        "Лог скоринга тегов:\n"
                        f"{score_lines}\n\n"
                        "Выбраны (score > 7):\n"
                        f"{selected_lines}"
                    ),
                )
            await query.answer("Теги сгенерированы.")
            return

        if action == "reject":
            reasons = load_reject_reasons(reasons_path)
            if case.control_message_id is not None:
                if query.message and query.message.text:
                    case.control_text_backup = query.message.text
                    case.control_entities_backup = list(query.message.entities or [])
                try:
                    await query.bot.edit_message_text(
                        chat_id=settings.admin_chat_id,
                        message_id=case.control_message_id,
                        text=format_admin_reject_guide(reasons),
                        reply_markup=reject_reasons_keyboard(case.case_id, reasons),
                    )
                except TelegramBadRequest:
                    await query.bot.edit_message_reply_markup(
                        chat_id=settings.admin_chat_id,
                        message_id=case.control_message_id,
                        reply_markup=reject_reasons_keyboard(case.case_id, reasons),
                    )
            await query.answer("Выберите причину отклонения.")
            return

        if action == "reply":
            if case.user_chat_id is None:
                # После рестарта reply нельзя выполнить без chat_id автора.
                # Это осознанный privacy trade-off: без persistent chat_id мы
                # жертвуем reply/ban/reject, но сохраняем publish.
                await query.answer(
                    "После рестарта доступна только публикация: chat_id автора не восстановлен.",
                    show_alert=True,
                )
                return
            nonlocal pending_reply_case_id
            pending_reply_case_id = case.case_id
            await query.answer("Отправьте следующее сообщение в чат.")
            await query.bot.send_message(
                chat_id=settings.admin_chat_id,
                text="Режим ответа включён. Напишите следующее сообщение в чат ответом на это сообщение.",
                parse_mode="Markdown",
            )
            return

        if action == "ban":
            if case.user_chat_id is None:
                # Бан технически невозможен без chat_id в живой памяти процесса.
                # Аналогично reply: не восстанавливаем user-id из диска намеренно.
                await query.answer(
                    "После рестарта доступна только публикация: chat_id автора не восстановлен.",
                    show_alert=True,
                )
                return
            if case.control_message_id is not None:
                await query.bot.edit_message_reply_markup(
                    chat_id=settings.admin_chat_id,
                    message_id=case.control_message_id,
                    reply_markup=ban_duration_keyboard(case.case_id),
                )
            await query.answer("Выберите срок бана.")
            return

        await query.answer("Неизвестное действие.", show_alert=True)

    @router.callback_query(F.data.startswith("pub_tag:"))
    async def on_publish_tag_toggle(query: CallbackQuery) -> None:
        """Переключает тег в режиме ручной публикации."""
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        parts = query.data.split(":", maxsplit=2)
        if len(parts) != 3:
            await query.answer("Некорректные данные тегов.", show_alert=True)
            return
        _, case_id, idx_raw = parts
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return
        try:
            idx = int(idx_raw)
            tag = TAG_CATALOG[idx]
        except (ValueError, IndexError):
            await query.answer("Тег не найден.", show_alert=True)
            return

        selected = {item.lower() for item in case.selected_tags}
        key = tag.lower()
        if key in selected:
            selected.remove(key)
        else:
            selected.add(key)
        case.selected_tags = [item for item in TAG_CATALOG if item.lower() in selected]

        if case.control_message_id is not None:
            await query.bot.edit_message_reply_markup(
                chat_id=settings.admin_chat_id,
                message_id=case.control_message_id,
                reply_markup=manual_publish_tags_keyboard(
                    case.case_id,
                    TAG_CATALOG,
                    case.selected_tags,
                ),
            )
        await query.answer("Теги обновлены.")

    @router.callback_query(F.data.startswith("tag_back:"))
    async def on_tag_back(query: CallbackQuery) -> None:
        """Возвращает из предпросмотра тегов к стартовой клавиатуре кейса."""
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, case_id = query.data.split(":", maxsplit=1)
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return
        if case.control_message_id is not None:
            await query.bot.edit_message_reply_markup(
                chat_id=settings.admin_chat_id,
                message_id=case.control_message_id,
                reply_markup=moderation_keyboard(case.case_id),
            )
        await query.answer("Возврат к действиям кейса.")

    @router.callback_query(F.data.startswith("pub_cancel:"))
    async def on_publish_cancel(query: CallbackQuery) -> None:
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, case_id = query.data.split(":", maxsplit=1)
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return
        if case.control_message_id is not None:
            await query.bot.edit_message_reply_markup(
                chat_id=settings.admin_chat_id,
                message_id=case.control_message_id,
                reply_markup=moderation_keyboard(case.case_id),
            )
        await query.answer("Возврат к действиям кейса.")

    @router.callback_query(F.data.startswith("pub_done:"))
    async def on_publish_done(query: CallbackQuery) -> None:
        """Подтверждает публикацию после ручного выбора тегов."""
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, case_id = query.data.split(":", maxsplit=1)
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return
        if not case.selected_tags:
            await query.answer("Выберите хотя бы один тег.", show_alert=True)
            return

        await publish_case_with_tags(query.bot, case)
        await notify_user_published(query.bot, case)
        await finalize_case(
            query.bot,
            case,
            "published",
            f"опубликован с тегами: {tags_block(case)}",
        )
        await query.answer("Опубликовано.")

    @router.callback_query(F.data.startswith("tag_pub:"))
    async def on_tag_publish(query: CallbackQuery) -> None:
        """Публикация из карточки предпросмотра (после генерации/редактирования тегов)."""
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, case_id = query.data.split(":", maxsplit=1)
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return
        if not case.selected_tags:
            await query.answer("Сначала сгенерируйте или отредактируйте теги.", show_alert=True)
            return

        await publish_case_with_tags(query.bot, case)
        await notify_user_published(query.bot, case)
        await finalize_case(
            query.bot,
            case,
            "published",
            f"опубликован с тегами: {tags_block(case)}",
        )
        await query.answer("Опубликовано.")

    @router.callback_query(F.data.startswith("tag_edit:"))
    async def on_tag_edit(query: CallbackQuery) -> None:
        """Открывает интерактивное редактирование тегов кнопками."""
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, case_id = query.data.split(":", maxsplit=1)
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return

        if case.control_message_id is not None:
            await query.bot.edit_message_reply_markup(
                chat_id=settings.admin_chat_id,
                message_id=case.control_message_id,
                reply_markup=manual_publish_tags_keyboard(
                    case.case_id,
                    TAG_CATALOG,
                    case.selected_tags,
                    toggle_prefix="edit_tag",
                    done_callback="edit_done",
                    cancel_callback="edit_cancel",
                    done_text="Сохранить теги",
                ),
            )
        await query.answer("Выберите теги и сохраните.")

    @router.callback_query(F.data.startswith("edit_tag:"))
    async def on_edit_tag_toggle(query: CallbackQuery) -> None:
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        parts = query.data.split(":", maxsplit=2)
        if len(parts) != 3:
            await query.answer("Некорректные данные тегов.", show_alert=True)
            return
        _, case_id, idx_raw = parts
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return
        try:
            idx = int(idx_raw)
            tag = TAG_CATALOG[idx]
        except (ValueError, IndexError):
            await query.answer("Тег не найден.", show_alert=True)
            return

        selected = {item.lower() for item in case.selected_tags}
        key = tag.lower()
        if key in selected:
            selected.remove(key)
        else:
            selected.add(key)
        case.selected_tags = [item for item in TAG_CATALOG if item.lower() in selected]

        if case.control_message_id is not None:
            await query.bot.edit_message_reply_markup(
                chat_id=settings.admin_chat_id,
                message_id=case.control_message_id,
                reply_markup=manual_publish_tags_keyboard(
                    case.case_id,
                    TAG_CATALOG,
                    case.selected_tags,
                    toggle_prefix="edit_tag",
                    done_callback="edit_done",
                    cancel_callback="edit_cancel",
                    done_text="Сохранить теги",
                ),
            )
        await query.answer("Теги обновлены.")

    @router.callback_query(F.data.startswith("edit_done:"))
    async def on_edit_done(query: CallbackQuery) -> None:
        """Сохраняет выбранные теги и обновляет карточку предпросмотра."""
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, case_id = query.data.split(":", maxsplit=1)
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return
        if not case.selected_tags:
            await query.answer("Выберите хотя бы один тег.", show_alert=True)
            return

        await send_tag_preview(query.bot, case)
        case_store.persist_case(case)
        await query.answer("Теги сохранены.")

    @router.callback_query(F.data.startswith("edit_cancel:"))
    async def on_edit_cancel(query: CallbackQuery) -> None:
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, case_id = query.data.split(":", maxsplit=1)
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return
        if case.control_message_id is not None:
            await query.bot.edit_message_reply_markup(
                chat_id=settings.admin_chat_id,
                message_id=case.control_message_id,
                reply_markup=tagged_preview_keyboard(case.case_id),
            )
        await query.answer("Редактирование отменено.")

    @router.callback_query(F.data.startswith("ban_dur:"))
    async def on_ban_duration(query: CallbackQuery) -> None:
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        parts = query.data.split(":", maxsplit=2)
        if len(parts) != 3:
            await query.answer("Некорректные данные бана.", show_alert=True)
            return

        _, case_id, duration_code = parts
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return

        options = ban_duration_options()
        selected = options.get(duration_code)
        if selected is None:
            await query.answer("Срок бана не найден.", show_alert=True)
            return

        await query.bot.edit_message_reply_markup(
            chat_id=settings.admin_chat_id,
            message_id=case.control_message_id,
            reply_markup=ban_confirm_keyboard(case.case_id, duration_code),
        )
        await query.answer(f"Подтвердите бан на {selected[0]}.")

    @router.callback_query(F.data.startswith("ban_back:"))
    async def on_ban_back(query: CallbackQuery) -> None:
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, case_id = query.data.split(":", maxsplit=1)
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return

        await query.bot.edit_message_reply_markup(
            chat_id=settings.admin_chat_id,
            message_id=case.control_message_id,
            reply_markup=moderation_keyboard(case.case_id),
        )
        await query.answer("Возврат к действиям кейса.")

    @router.callback_query(F.data.startswith("ban_cancel:"))
    async def on_ban_cancel(query: CallbackQuery) -> None:
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, case_id = query.data.split(":", maxsplit=1)
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return

        await query.bot.edit_message_reply_markup(
            chat_id=settings.admin_chat_id,
            message_id=case.control_message_id,
            reply_markup=moderation_keyboard(case.case_id),
        )
        await query.answer("Бан отменен.")

    @router.callback_query(F.data.startswith("ban_ok:"))
    async def on_ban_confirm(query: CallbackQuery) -> None:
        """Подтверждает бан, уведомляет пользователя и пишет сервиску в админ-чат."""
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        parts = query.data.split(":", maxsplit=2)
        if len(parts) != 3:
            await query.answer("Некорректные данные подтверждения.", show_alert=True)
            return
        _, case_id, duration_code = parts
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return

        options = ban_duration_options()
        selected = options.get(duration_code)
        if selected is None:
            await query.answer("Срок бана не найден.", show_alert=True)
            return
        duration_label, duration_seconds = selected
        if case.user_chat_id is None:
            # На старых кейсах после рестарта бан недоступен по той же причине.
            await query.answer(
                "После рестарта бан невозможен: chat_id автора не восстановлен.",
                show_alert=True,
            )
            return

        until_ts = None
        if duration_seconds is not None:
            until_ts = time.time() + duration_seconds

        ban_store.ban_user(case.user_chat_id, until_ts)

        if until_ts is None:
            user_notice = (
                "Вы забанены и больше не можете писать боту. "
                "Ваш аккаунт деанонимизирован для модерации."
            )
            admin_note = "пользователь забанен навсегда."
        else:
            until_text = datetime.fromtimestamp(until_ts).strftime("%d.%m.%Y %H:%M")
            user_notice = (
                f"Вы забанены и не можете писать боту до {until_text}. "
                "Ваш аккаунт деанонимизирован для модерации."
            )
            admin_note = f"пользователь забанен на {duration_label} (до {until_text})."

        await query.bot.send_message(chat_id=case.user_chat_id, text=user_notice)
        await finalize_case(query.bot, case, "banned", admin_note)
        await query.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=(
                f"Сервис: пользователь `{case.user_chat_id}` забанен.\n"
                f"[Открыть профиль](tg://user?id={case.user_chat_id})"
            ),
            parse_mode="Markdown",
            reply_markup=unban_request_keyboard(case.user_chat_id),
        )
        await query.answer("Пользователь забанен.")

    @router.callback_query(F.data.startswith("unban_req:"))
    async def on_unban_request(query: CallbackQuery) -> None:
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, user_id_raw = query.data.split(":", maxsplit=1)
        try:
            user_id = int(user_id_raw)
        except ValueError:
            await query.answer("Некорректный user id.", show_alert=True)
            return

        await query.bot.edit_message_reply_markup(
            chat_id=settings.admin_chat_id,
            message_id=query.message.message_id,
            reply_markup=unban_confirm_keyboard(user_id),
        )
        await query.answer("Подтвердите разбан.")

    @router.callback_query(F.data.startswith("unban_cancel:"))
    async def on_unban_cancel(query: CallbackQuery) -> None:
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, user_id_raw = query.data.split(":", maxsplit=1)
        try:
            user_id = int(user_id_raw)
        except ValueError:
            await query.answer("Некорректный user id.", show_alert=True)
            return

        await query.bot.edit_message_reply_markup(
            chat_id=settings.admin_chat_id,
            message_id=query.message.message_id,
            reply_markup=unban_request_keyboard(user_id),
        )
        await query.answer("Возврат к предыдущим кнопкам.")

    @router.callback_query(F.data.startswith("unban_ok:"))
    async def on_unban_confirm(query: CallbackQuery) -> None:
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, user_id_raw = query.data.split(":", maxsplit=1)
        try:
            user_id = int(user_id_raw)
        except ValueError:
            await query.answer("Некорректный user id.", show_alert=True)
            return

        removed = ban_store.unban_user(user_id)
        await query.bot.edit_message_reply_markup(
            chat_id=settings.admin_chat_id,
            message_id=query.message.message_id,
            reply_markup=None,
        )
        if removed:
            await query.bot.send_message(
                chat_id=settings.admin_chat_id,
                text=f"Сервис: пользователь `{user_id}` разбанен.",
                parse_mode="Markdown",
            )
            await query.answer("Пользователь разбанен.")
        else:
            await query.bot.send_message(
                chat_id=settings.admin_chat_id,
                text=f"Сервис: пользователь `{user_id}` не найден в бан-листе.",
                parse_mode="Markdown",
            )
            await query.answer("Пользователь уже не в бане.")

    @router.callback_query(F.data.startswith("rej:"))
    async def on_reject_reason(query: CallbackQuery) -> None:
        """Применяет выбранную причину отклонения и закрывает кейс."""
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        parts = query.data.split(":", maxsplit=2)
        if len(parts) != 3:
            await query.answer("Некорректные данные причины.", show_alert=True)
            return
        _, case_id, reason_idx_raw = parts
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return

        reasons = load_reject_reasons(reasons_path)
        try:
            reason = reasons[int(reason_idx_raw)]
        except (ValueError, IndexError):
            await query.answer("Причина не найдена.", show_alert=True)
            return
        if case.user_chat_id is None:
            # Отклонение отправляет сообщение пользователю, поэтому без chat_id нельзя.
            await query.answer(
                "После рестарта отклонение недоступно: chat_id автора не восстановлен.",
                show_alert=True,
            )
            return

        await query.bot.send_message(
            chat_id=case.user_chat_id,
            text=reason.reply_text,
        )
        await finalize_case(query.bot, case, "rejected", "отклонён.")
        await query.answer("Отклонено.")

    @router.callback_query(F.data.startswith("rej_back:"))
    async def on_reject_back(query: CallbackQuery) -> None:
        """Возвращает из выбора причины отклонения к стартовым действиям кейса."""
        if not query.from_user or not query.data:
            return
        if query.message is None or query.message.chat.id != settings.admin_chat_id:
            await query.answer("Действие доступно только в админ-конфе.", show_alert=True)
            return
        if not is_allowed_moderator(query.from_user.id):
            await query.answer("У вас нет прав на модерацию.", show_alert=True)
            return

        _, case_id = query.data.split(":", maxsplit=1)
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            await query.answer("Кейс уже обработан или не найден.", show_alert=True)
            return
        if case.control_message_id is not None:
            restored_text = case.control_text_backup or "Выберите действия с анонимкой"
            restored_entities = case.control_entities_backup or None
            try:
                await query.bot.edit_message_text(
                    chat_id=settings.admin_chat_id,
                    message_id=case.control_message_id,
                    text=restored_text,
                    entities=restored_entities,
                    reply_markup=moderation_keyboard(case.case_id),
                )
            except TelegramBadRequest:
                await query.bot.edit_message_reply_markup(
                    chat_id=settings.admin_chat_id,
                    message_id=case.control_message_id,
                    reply_markup=moderation_keyboard(case.case_id),
                )
            case.control_text_backup = None
            case.control_entities_backup = []
        await query.answer("Возврат к действиям кейса.")

    async def flush_reply_media_group(group_key: tuple[int, str]) -> None:
        """Обрабатывает ответ админа пользователю, если ответ пришел медиагруппой."""
        nonlocal pending_reply_case_id
        await asyncio.sleep(1.0)
        messages = sorted(
            reply_media_group_buffer.pop(group_key, []),
            key=lambda item: item.message_id,
        )
        reply_media_group_tasks.pop(group_key, None)
        if not messages:
            return

        case_id = pending_reply_case_id
        pending_reply_case_id = None
        if not case_id:
            return
        case = case_store.get_case(case_id)
        if not case or case.status != "open":
            return
        if case.user_chat_id is None:
            # Защита от зависшего режима reply на кейсе, поднятом из open_cases.json.
            await messages[0].bot.send_message(
                chat_id=settings.admin_chat_id,
                text="После рестарта reply недоступен: chat_id автора не восстановлен.",
            )
            return

        await media_bridge.copy_many(
            bot=messages[0].bot,
            from_chat_id=settings.admin_chat_id,
            to_chat_id=case.user_chat_id,
            message_ids=[msg.message_id for msg in messages],
        )
        await messages[0].bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"Сообщение отправлено пользователю! Кейс `{case.case_id}` остается открытым.",
            parse_mode="Markdown",
        )

    @router.message(F.chat.id == settings.admin_chat_id)
    async def on_admin_reply_message(message: Message) -> None:
        """Ловит следующее сообщение модератора в режиме 'Ответить' и отправляет пользователю."""
        nonlocal pending_reply_case_id
        if not message.from_user or message.from_user.is_bot:
            return
        if not is_allowed_moderator(message.from_user.id):
            return
        if is_again_command(message):
            await prepare_case_again(message)
            return

        pending_tag_case_id = case_store.peek_pending_tag_edit(message.from_user.id)
        if pending_tag_case_id:
            case = case_store.get_case(pending_tag_case_id)
            if not case or case.status != "open":
                case_store.pop_pending_tag_edit(message.from_user.id)
                return

            raw_tags = (message.text or message.caption or "").strip()
            parsed_tags = parse_tags_from_text(raw_tags)
            if not parsed_tags:
                await message.answer(
                    "Не удалось распознать теги из каталога. "
                    "Отправьте строку вида: #тейк #вопросы #фест"
                )
                return

            case.selected_tags = parsed_tags
            case.is_waiting_tag_edit = False
            case_store.pop_pending_tag_edit(message.from_user.id)
            await send_tag_preview(message.bot, case)
            await message.answer("Теги обновлены.")
            return

        pending_case_id = pending_reply_case_id
        if not pending_case_id:
            return

        case = case_store.get_case(pending_case_id)
        if not case or case.status != "open":
            pending_reply_case_id = None
            return
        if case.user_chat_id is None:
            pending_reply_case_id = None
            # Очищаем pending и даём понятный сервисный ответ.
            await message.bot.send_message(
                chat_id=settings.admin_chat_id,
                text="После рестарта reply недоступен: chat_id автора не восстановлен.",
            )
            return

        if message.media_group_id:
            sender_key = message.from_user.id if message.from_user else message.sender_chat.id
            key = (sender_key, message.media_group_id)
            reply_media_group_buffer.setdefault(key, []).append(message)
            if key not in reply_media_group_tasks:
                reply_media_group_tasks[key] = asyncio.create_task(
                    flush_reply_media_group(key)
                )
            return

        pending_reply_case_id = None
        await media_bridge.copy_single(
            bot=message.bot,
            from_chat_id=settings.admin_chat_id,
            to_chat_id=case.user_chat_id,
            message_id=message.message_id,
        )
        await message.bot.send_message(
            chat_id=settings.admin_chat_id,
            text=f"Сообщение отправлено пользователю! Кейс `{case.case_id}` остается открытым.",
            parse_mode="Markdown",
        )

    return router
