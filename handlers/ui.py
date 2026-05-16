from __future__ import annotations

"""Фабрики inline-клавиатур для админских действий.

Модуль отделен от бизнес-логики, чтобы проще менять интерфейс кнопок.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.reject_reasons import RejectReason


def moderation_keyboard(case_id: str) -> InlineKeyboardMarkup:
    """Главная клавиатура кейса в админ-чате."""
    # В callback_data кладём только short case_id, а не user_id/chat_id:
    # кнопки нельзя использовать для deanonymization автора.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Опубликовать", callback_data=f"act:publish:{case_id}"
                ),
                InlineKeyboardButton(
                    text="Сгенерировать теги", callback_data=f"act:gen_tags:{case_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Отклонить", callback_data=f"act:reject:{case_id}"
                ),
                InlineKeyboardButton(
                    text="Ответить", callback_data=f"act:reply:{case_id}"
                ),
                InlineKeyboardButton(
                    text="Забанить", callback_data=f"act:ban:{case_id}"
                ),
            ]
        ]
    )


def reject_reasons_keyboard(
    case_id: str,
    reasons: list[RejectReason],
) -> InlineKeyboardMarkup:
    """Клавиатура с причинами отклонения (одна кнопка = одна причина)."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx, reason in enumerate(reasons):
        rows.append(
            [
                InlineKeyboardButton(
                    text=reason.button_name[:64],
                    callback_data=f"rej:{case_id}:{idx}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=f"rej_back:{case_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tagged_preview_keyboard(case_id: str) -> InlineKeyboardMarkup:
    """Кнопки для работы с предпросмотром тегов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Опубликовать",
                    callback_data=f"tag_pub:{case_id}",
                ),
                InlineKeyboardButton(
                    text="Редактировать теги",
                    callback_data=f"tag_edit:{case_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"tag_back:{case_id}",
                )
            ],
        ]
    )


def manual_publish_tags_keyboard(
    case_id: str,
    tags: list[str],
    selected_tags: list[str],
    toggle_prefix: str = "pub_tag",
    done_callback: str = "pub_done",
    cancel_callback: str = "pub_cancel",
    done_text: str = "Опубликовать",
    cancel_text: str = "Назад",
) -> InlineKeyboardMarkup:
    """Универсальная клавиатура мультивыбора тегов.

    Используется в двух сценариях:
    - ручная публикация без LLM;
    - редактирование уже выбранных тегов.
    """
    selected = {tag.lower() for tag in selected_tags}
    rows: list[list[InlineKeyboardButton]] = []
    for idx, tag in enumerate(tags):
        marker = "✅ " if tag.lower() in selected else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{tag}",
                    callback_data=f"{toggle_prefix}:{case_id}:{idx}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=done_text,
                callback_data=f"{done_callback}:{case_id}",
            ),
            InlineKeyboardButton(
                text=cancel_text,
                callback_data=f"{cancel_callback}:{case_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ban_duration_keyboard(case_id: str) -> InlineKeyboardMarkup:
    """Выбор длительности бана."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1 день",
                    callback_data=f"ban_dur:{case_id}:1d",
                ),
                InlineKeyboardButton(
                    text="7 дней",
                    callback_data=f"ban_dur:{case_id}:7d",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="30 дней",
                    callback_data=f"ban_dur:{case_id}:30d",
                ),
                InlineKeyboardButton(
                    text="Навсегда",
                    callback_data=f"ban_dur:{case_id}:perm",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"ban_cancel:{case_id}",
                )
            ],
        ]
    )


def ban_confirm_keyboard(case_id: str, duration_code: str) -> InlineKeyboardMarkup:
    """Подтверждение бана после выбора срока."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить бан",
                    callback_data=f"ban_ok:{case_id}:{duration_code}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"ban_back:{case_id}",
                )
            ],
        ]
    )


def unban_request_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Кнопка запуска процедуры разбана."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Разбанить",
                    callback_data=f"unban_req:{user_id}",
                )
            ]
        ]
    )


def unban_confirm_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Подтверждение разбана."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить разбан",
                    callback_data=f"unban_ok:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"unban_cancel:{user_id}",
                )
            ],
        ]
    )
