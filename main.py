from __future__ import annotations

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
    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / "data"

    settings = load_settings()
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    case_store = CaseStore()
    media_bridge = MediaBridge()
    ban_store = BanStore(data_dir / "bans.json")
    tagging_service = TaggingService(
        api_key=settings.chad_api_key,
        base_url=settings.chad_base_url,
        model=settings.chad_model,
    )

    dp.include_router(create_user_router(settings, case_store, media_bridge, ban_store))
    dp.include_router(
        create_admin_router(
            settings=settings,
            case_store=case_store,
            media_bridge=media_bridge,
            ban_store=ban_store,
            tagging_service=tagging_service,
            reasons_path=str(data_dir / "reject_reasons.json"),
        )
    )

    await dp.start_polling(bot)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
