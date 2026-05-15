from __future__ import annotations

"""In-memory реестр кейсов модерации.

Кейс = одна пользовательская анонимка + служебные данные по её обработке.
"""

from dataclasses import dataclass, field

from aiogram.types import MessageEntity


@dataclass(slots=True)
class CaseRecord:
    """Структура одного кейса модерации."""

    # Короткий ID кейса для кнопок и сервисных сообщений.
    case_id: str
    # chat_id автора в личке с ботом (нужен для ответа, публикации, бана).
    user_chat_id: int
    # ID исходных сообщений пользователя (одно или несколько).
    source_message_ids: list[int]
    # True, если кейс собран из нескольких сообщений/альбома.
    is_media_group: bool
    # ID сообщений, отправленных ботом в админ-чат по этому кейсу.
    admin_message_ids: list[int] = field(default_factory=list)
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

    def __init__(self) -> None:
        self._cases: dict[str, CaseRecord] = {}
        # Хранит состояние “следующее сообщение админа = ответ пользователю”.
        self._pending_reply_by_admin: dict[int, str] = {}
        # Хранит состояние “админ сейчас редактирует теги кейса”.
        self._pending_tag_edit_by_admin: dict[int, str] = {}

    def add_case(self, case: CaseRecord) -> None:
        """Регистрирует новый кейс."""
        self._cases[case.case_id] = case

    def get_case(self, case_id: str) -> CaseRecord | None:
        """Возвращает кейс по ID или None."""
        return self._cases.get(case_id)

    def mark_done(self, case_id: str, status: str) -> None:
        """Переводит кейс в финальный статус."""
        case = self._cases.get(case_id)
        if case:
            case.status = status

    def is_open(self, case_id: str) -> bool:
        """Проверка, что кейс еще не закрыт."""
        case = self.get_case(case_id)
        return bool(case and case.status == "open")

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
