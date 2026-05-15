from __future__ import annotations

"""Точка входа приложения.

Здесь мы:
- создаем все сервисы;
- подключаем роутеры с обработчиками;
- запускаем long polling Telegram-бота.
"""

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher

from config import load_settings
from handlers.admin_actions import create_admin_router
from handlers.user_messages import create_user_router
from services.ban_store import BanStore
from services.case_store import CaseStore
from services.media_bridge import MediaBridge
from services.tagging_service import TaggingService


async def run() -> None:
    """Собирает зависимости и запускает цикл обработки обновлений."""
    # Абсолютные пути, чтобы приложение работало одинаково локально и на хостинге.
    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / "data"

    # Читаем настройки из окружения.
    settings = load_settings()
    # Создаем объект Telegram-бота и диспетчер роутеров.
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    # Внутренние сервисы приложения.
    case_store = CaseStore()
    media_bridge = MediaBridge()
    ban_store = BanStore(data_dir / "bans.json")
    tagging_service = TaggingService(
        api_key=settings.chad_api_key,
        base_url=settings.chad_base_url,
        model=settings.chad_model,
    )

    # Роутер входящих сообщений пользователей.
    dp.include_router(create_user_router(settings, case_store, media_bridge, ban_store))
    # Роутер действий админов (модерация, публикация, баны, теги).
    dp.include_router(
        create_admin_router(
            settings=settings,
            case_store=case_store,
            media_bridge=media_bridge,
            ban_store=ban_store,
            tagging_service=tagging_service,
            reasons_path=str(base_dir / "reject_reasons.json"),
        )
    )

    # Стартуем polling: бот регулярно опрашивает Telegram на новые апдейты.
    await dp.start_polling(bot)


def main() -> None:
    """Синхронная обертка для запуска async-кода."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
