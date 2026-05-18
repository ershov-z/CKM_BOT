from __future__ import annotations

"""In-memory реестр кейсов модерации.

Кейс = одна пользовательская анонимка + служебные данные по её обработке.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from aiogram.types import MessageEntity


@dataclass(slots=True)
class CaseRecord:
    """Структура одного кейса модерации."""

    # Короткий ID кейса для кнопок и сервисных сообщений.
    case_id: str
    # chat_id автора в личке с ботом (держим в памяти; может отсутствовать после рестарта).
    user_chat_id: int | None
    # ID исходных сообщений пользователя (одно или несколько).
    source_message_ids: list[int]
    # True, если кейс собран из нескольких сообщений/альбома.
    is_media_group: bool
    # True, если кейс должен публиковаться как "тейк из нескольких постов" с маркерами.
    is_composed_multi_post: bool = False
    # ID сообщений, отправленных ботом в админ-чат по этому кейсу.
    admin_message_ids: list[int] = field(default_factory=list)
    # Только контентные сообщения кейса в админ-чате (без служебных маркеров/кнопок).
    admin_content_message_ids: list[int] = field(default_factory=list)
    # ID основного служебного сообщения с кнопками.
    control_message_id: int | None = None
    # Текстовая “выжимка” для тегирования через LLM.
    content_for_tagging: str = ""
    # Оригинальный текст/подпись одиночного сообщения (для корректной публикации).
    single_content_text: str = ""
    # Telegram entities (жирный, цитаты и т.п.) для сохранения форматирования.
    single_content_entities: list[MessageEntity] = field(default_factory=list)
    # Тип одиночного контента (text/photo/audio/voice/video_note/...).
    single_content_type: str = ""
    # Текущий набор тегов, выбранный/сгенерированный для публикации.
    selected_tags: list[str] = field(default_factory=list)
    # Флаг, что админ сейчас редактирует теги.
    is_waiting_tag_edit: bool = False
    # Статус кейса (open/published/rejected/replied/banned...).
    status: str = "open"


class CaseStore:
    """Операции с кейсами и краткоживущими режимами админов."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._storage_path = Path(storage_path) if storage_path else None
        self._cases: dict[str, CaseRecord] = {}
        # Хранит состояние “следующее сообщение админа = ответ пользователю”.
        self._pending_reply_by_admin: dict[int, str] = {}
        # Хранит состояние “админ сейчас редактирует теги кейса”.
        self._pending_tag_edit_by_admin: dict[int, str] = {}
        self._load_cases()

    def _serialize_case(self, case: CaseRecord) -> dict:
        # Сохраняем только то, что нужно для восстановления публикации после рестарта.
        # ВАЖНО: user_chat_id намеренно не пишем в JSON ради анонимности.
        return {
            "case_id": case.case_id,
            "source_message_ids": case.source_message_ids,
            "is_media_group": case.is_media_group,
            "is_composed_multi_post": case.is_composed_multi_post,
            "admin_message_ids": case.admin_message_ids,
            "admin_content_message_ids": case.admin_content_message_ids,
            "control_message_id": case.control_message_id,
            "content_for_tagging": case.content_for_tagging,
            "single_content_text": case.single_content_text,
            "single_content_type": case.single_content_type,
            "selected_tags": case.selected_tags,
            "is_waiting_tag_edit": case.is_waiting_tag_edit,
            "status": case.status,
        }

    def _deserialize_case(self, payload: dict) -> CaseRecord | None:
        try:
            return CaseRecord(
                case_id=str(payload["case_id"]),
                # После рестарта chat_id автора обычно недоступен (None).
                # Это нормальный режим: publish работает, reply/ban/reject нет.
                user_chat_id=(
                    int(payload["user_chat_id"])
                    if payload.get("user_chat_id") is not None
                    else None
                ),
                source_message_ids=[int(item) for item in payload["source_message_ids"]],
                is_media_group=bool(payload["is_media_group"]),
                is_composed_multi_post=bool(
                    payload.get("is_composed_multi_post", bool(payload["is_media_group"]))
                ),
                admin_message_ids=[int(item) for item in payload.get("admin_message_ids", [])],
                admin_content_message_ids=[
                    int(item) for item in payload.get("admin_content_message_ids", [])
                ],
                control_message_id=(
                    int(payload["control_message_id"])
                    if payload.get("control_message_id") is not None
                    else None
                ),
                content_for_tagging=str(payload.get("content_for_tagging", "")),
                single_content_text=str(payload.get("single_content_text", "")),
                single_content_entities=[],
                single_content_type=str(payload.get("single_content_type", "")),
                selected_tags=[str(item) for item in payload.get("selected_tags", [])],
                is_waiting_tag_edit=bool(payload.get("is_waiting_tag_edit", False)),
                status=str(payload.get("status", "open")),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _load_cases(self) -> None:
        # Загружаем только "рабочее" состояние публикации. Даже если файл будет
        # скомпрометирован, в нём нет user_chat_id для большинства кейсов.
        if not self._storage_path:
            return
        if not self._storage_path.exists():
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._storage_path.write_text("{}", encoding="utf-8")
            return
        try:
            raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        for case_id, payload in raw.items():
            if not isinstance(payload, dict):
                continue
            case = self._deserialize_case({"case_id": case_id, **payload})
            if case:
                self._cases[case.case_id] = case

    def _save_cases(self) -> None:
        # Персистим только открытые кейсы: закрытые (published/rejected/...) удаляются
        # из JSON, чтобы не накапливать историю и не увеличивать риски deanonymization.
        if not self._storage_path:
            return
        data = {
            case_id: self._serialize_case(case)
            for case_id, case in self._cases.items()
            if case.status == "open"
        }
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_case(self, case: CaseRecord) -> None:
        """Регистрирует новый кейс."""
        self._cases[case.case_id] = case
        self._save_cases()

    def get_case(self, case_id: str) -> CaseRecord | None:
        """Возвращает кейс по ID или None."""
        return self._cases.get(case_id)

    def mark_done(self, case_id: str, status: str) -> None:
        """Переводит кейс в финальный статус."""
        case = self._cases.get(case_id)
        if case:
            case.status = status
            self._save_cases()

    def is_open(self, case_id: str) -> bool:
        """Проверка, что кейс еще не закрыт."""
        case = self.get_case(case_id)
        return bool(case and case.status == "open")

    def find_open_case_by_user_message(
        self,
        user_chat_id: int,
        message_id: int,
    ) -> CaseRecord | None:
        """Ищет открытый кейс по исходному сообщению пользователя.

        В privacy-режиме опираемся на chat_id только в памяти процесса:
        после рестарта часть кейсов может не матчиться (и это ожидаемо).
        """
        for case in self._cases.values():
            if case.status != "open":
                continue
            if case.user_chat_id != user_chat_id:
                continue
            if message_id in case.source_message_ids:
                return case
        return None

    def set_pending_reply(self, admin_user_id: int, case_id: str) -> None:
        """Включает режим ожидания ответа от конкретного админа."""
        self._pending_reply_by_admin[admin_user_id] = case_id

    def pop_pending_reply(self, admin_user_id: int) -> str | None:
        """Снимает и возвращает режим ответа админа."""
        return self._pending_reply_by_admin.pop(admin_user_id, None)

    def peek_pending_reply(self, admin_user_id: int) -> str | None:
        """Смотрит режим ответа без удаления."""
        return self._pending_reply_by_admin.get(admin_user_id)

    def set_pending_tag_edit(self, admin_user_id: int, case_id: str) -> None:
        """Включает режим редактирования тегов для админа."""
        self._pending_tag_edit_by_admin[admin_user_id] = case_id

    def pop_pending_tag_edit(self, admin_user_id: int) -> str | None:
        """Снимает и возвращает режим редактирования тегов."""
        return self._pending_tag_edit_by_admin.pop(admin_user_id, None)

    def peek_pending_tag_edit(self, admin_user_id: int) -> str | None:
        """Смотрит режим редактирования тегов без удаления."""
        return self._pending_tag_edit_by_admin.get(admin_user_id)
