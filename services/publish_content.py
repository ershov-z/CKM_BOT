from __future__ import annotations

"""Сборка подписи публикации: исходный текст, «Прислано через…», теги."""

from aiogram.types import Message

from services.case_store import CaseRecord

SENT_VIA = "Прислано через @backstage_staff_bot"


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


def message_has_sent_via(message: Message | None) -> bool:
    """True, если в тексте или подписи уже есть служебная строка."""
    if message is None:
        return False
    blob = "\n".join(part for part in (message.text, message.caption) if part)
    return SENT_VIA in blob
