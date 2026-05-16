from __future__ import annotations

"""Мини-хранилище банов в JSON-файле.

Нужно для того, чтобы баны сохранялись между перезапусками бота.
"""

import json
import time
from pathlib import Path
from typing import Any


class BanStore:
    """Работает с файлом банов: загрузка, проверка, запись, снятие.

    Это единственный персистентный модуль, где хранятся user id/chat id осознанно:
    без этого невозможно технически блокировать повторные сообщения от нарушителя.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._bans: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """Читает JSON с банами, а если файла нет — создает пустой."""
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("{}", encoding="utf-8")
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            self._bans = raw
        else:
            self._bans = {}

    def _save(self) -> None:
        """Сохраняет текущее состояние банов в файл."""
        self._path.write_text(
            json.dumps(self._bans, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_ban_until(self, user_id: int) -> float | None:
        """Возвращает timestamp окончания бана, либо None (если бан вечный или его нет)."""
        entry = self._bans.get(str(user_id))
        if not entry:
            return None
        until_ts = entry.get("until_ts")
        if until_ts is None:
            return None
        try:
            return float(until_ts)
        except (TypeError, ValueError):
            return None

    def is_banned(self, user_id: int) -> bool:
        """Проверяет, есть ли активный бан.

        Если бан истек или поврежден — автоматически чистит запись.
        """
        key = str(user_id)
        entry = self._bans.get(key)
        if not entry:
            return False

        until_ts = entry.get("until_ts")
        if until_ts is None:
            return True

        try:
            until_ts_value = float(until_ts)
        except (TypeError, ValueError):
            self._bans.pop(key, None)
            self._save()
            return False

        if until_ts_value > time.time():
            return True

        self._bans.pop(key, None)
        self._save()
        return False

    def ban_user(self, user_id: int, until_ts: float | None) -> None:
        """Выставляет бан: until_ts=None означает бессрочный бан."""
        self._bans[str(user_id)] = {"until_ts": until_ts}
        self._save()

    def unban_user(self, user_id: int) -> bool:
        """Снимает бан. Возвращает True, если запись реально существовала."""
        removed = self._bans.pop(str(user_id), None)
        if removed is not None:
            self._save()
            return True
        return False
