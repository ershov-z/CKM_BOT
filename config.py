from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    bot_token: str
    admin_chat_id: int
    publish_channel_id: int
    admin_user_ids: set[int]
    post_signature: str
    chad_api_key: str
    chad_base_url: str
    chad_model: str


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable `{name}` is required.")
    return value


def _parse_admin_ids(raw: str) -> set[int]:
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
    load_dotenv()

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
