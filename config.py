from __future__ import annotations

"""Загрузка и проверка настроек бота из переменных окружения.

Этот модуль нужен для того, чтобы:
1) централизованно прочитать все настройки;
2) проверить, что обязательные параметры заданы;
3) вернуть удобный объект Settings для остального кода.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    """Все ключевые настройки приложения в одном объекте."""

    # Токен Telegram-бота от BotFather.
    bot_token: str
    # ID чата администраторов (где идет модерация).
    admin_chat_id: int
    # ID канала, куда публикуются финальные посты.
    publish_channel_id: int
    # Список user_id модераторов, которым разрешены действия кнопок.
    admin_user_ids: set[int]
    # Дополнительная подпись к публикации (legacy-поле, по проекту может не использоваться).
    post_signature: str
    # Ключ Chad API для генерации тегов.
    chad_api_key: str
    # Базовый URL OpenAI-compatible API Chad.
    chad_base_url: str
    # Имя модели для тегирования (например, gpt-5-nano).
    chad_model: str


def _require(name: str) -> str:
    """Читает обязательную переменную окружения и падает с понятной ошибкой, если её нет."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable `{name}` is required.")
    return value


def _parse_admin_ids(raw: str) -> set[int]:
    """Преобразует строку вида '1,2,3' в set[int] с проверкой корректности."""
    parsed: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            parsed.add(int(token))
        except ValueError as exc:
            raise RuntimeError(
                "ADMIN_USER_IDS must contain comma-separated integer IDs."
            ) from exc
    if not parsed:
        raise RuntimeError("ADMIN_USER_IDS cannot be empty.")
    return parsed


def load_settings() -> Settings:
    """Загружает .env из корня проекта и возвращает валидированный объект Settings."""
    # Используем абсолютный путь к .env, чтобы запуск не зависел от текущей рабочей директории.
    project_env = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=project_env if project_env.exists() else None)

    return Settings(
        bot_token=_require("BOT_TOKEN"),
        admin_chat_id=int(_require("ADMIN_CHAT_ID")),
        publish_channel_id=int(_require("PUBLISH_CHANNEL_ID")),
        admin_user_ids=_parse_admin_ids(_require("ADMIN_USER_IDS")),
        post_signature=os.getenv("POST_SIGNATURE", "").replace("\\n", "\n"),
        chad_api_key=_require("CHAD_API_KEY"),
        chad_base_url=os.getenv("CHAD_BASE_URL", "https://ask.chadgpt.ru/api/v1").rstrip(
            "/"
        ),
        chad_model=os.getenv("CHAD_MODEL", "gpt-5-nano"),
    )
