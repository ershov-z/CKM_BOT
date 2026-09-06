from __future__ import annotations

"""Разбор текста и типа входящего Telegram-сообщения.

Нужен отдельно, потому что посты с картинками внутри текста приходят как
rich_message: старый aiogram может не знать это поле, а caption к такому
сообщению Telegram не применяет.
"""

from typing import Any

from aiogram.types import Message

# Типы, у которых нет обычной подписи: copy_message(caption=...) её молча игнорирует.
NON_CAPTION_CONTENT_TYPES = {
    "rich_message",
    "unknown",
    "sticker",
    "dice",
    "contact",
    "location",
    "venue",
    "game",
    "poll",
    "invoice",
    "successful_payment",
    "giveaway",
    "giveaway_winners",
}


def _model_extra(message: Message) -> dict[str, Any]:
    extra = getattr(message, "model_extra", None)
    return extra if isinstance(extra, dict) else {}


def get_message_field(message: Message, name: str) -> Any:
    """Достаёт поле из модели или из сырого JSON, если aiogram его не типизирует."""
    value = getattr(message, name, None)
    if value is not None:
        return value
    return _model_extra(message).get(name)


def is_rich_message(message: Message) -> bool:
    """True, если это rich-пост (текст с медиа внутри одного сообщения)."""
    return get_message_field(message, "rich_message") is not None


def _collect_strings(payload: Any, chunks: list[str]) -> None:
    if isinstance(payload, str):
        text = payload.strip()
        if text:
            chunks.append(text)
        return
    if isinstance(payload, dict):
        for key in ("text", "alternative_text", "expression", "caption"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip())
        for value in payload.values():
            if isinstance(value, (dict, list)):
                _collect_strings(value, chunks)
        return
    if isinstance(payload, list):
        for item in payload:
            _collect_strings(item, chunks)


def extract_rich_plain_text(rich_payload: Any) -> str:
    """Собирает плоский текст из дерева rich_message для тегирования."""
    chunks: list[str] = []
    _collect_strings(rich_payload, chunks)
    return "\n".join(chunks).strip()


def extract_message_text(message: Message) -> str:
    """Текст/подпись сообщения, с fallback на rich_message."""
    text = (message.text or message.caption or "").strip()
    if text:
        return text
    rich = get_message_field(message, "rich_message")
    if rich is not None:
        if hasattr(rich, "model_dump"):
            rich = rich.model_dump()
        return extract_rich_plain_text(rich)
    return ""


def resolve_content_type(message: Message) -> str:
    """content_type с отдельной меткой для rich-постов."""
    if is_rich_message(message):
        return "rich_message"
    return str(message.content_type)


def content_rejects_caption(content_type: str) -> bool:
    """True, если к сообщению нельзя надежно дописать caption с тегами."""
    return content_type in NON_CAPTION_CONTENT_TYPES
