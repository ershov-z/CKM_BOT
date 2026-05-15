from __future__ import annotations

from dataclasses import dataclass, field

from aiogram.types import MessageEntity


@dataclass(slots=True)
class CaseRecord:
    case_id: str
    user_chat_id: int
    source_message_ids: list[int]
    is_media_group: bool
    admin_message_ids: list[int] = field(default_factory=list)
    control_message_id: int | None = None
    content_for_tagging: str = ""
    single_content_text: str = ""
    single_content_entities: list[MessageEntity] = field(default_factory=list)
    single_content_type: str = ""
    selected_tags: list[str] = field(default_factory=list)
    is_waiting_tag_edit: bool = False
    status: str = "open"


class CaseStore:
    def __init__(self) -> None:
        self._cases: dict[str, CaseRecord] = {}
        self._pending_reply_by_admin: dict[int, str] = {}
        self._pending_tag_edit_by_admin: dict[int, str] = {}

    def add_case(self, case: CaseRecord) -> None:
        self._cases[case.case_id] = case

    def get_case(self, case_id: str) -> CaseRecord | None:
        return self._cases.get(case_id)

    def mark_done(self, case_id: str, status: str) -> None:
        case = self._cases.get(case_id)
        if case:
            case.status = status

    def is_open(self, case_id: str) -> bool:
        case = self.get_case(case_id)
        return bool(case and case.status == "open")

    def set_pending_reply(self, admin_user_id: int, case_id: str) -> None:
        self._pending_reply_by_admin[admin_user_id] = case_id

    def pop_pending_reply(self, admin_user_id: int) -> str | None:
        return self._pending_reply_by_admin.pop(admin_user_id, None)

    def peek_pending_reply(self, admin_user_id: int) -> str | None:
        return self._pending_reply_by_admin.get(admin_user_id)

    def set_pending_tag_edit(self, admin_user_id: int, case_id: str) -> None:
        self._pending_tag_edit_by_admin[admin_user_id] = case_id

    def pop_pending_tag_edit(self, admin_user_id: int) -> str | None:
        return self._pending_tag_edit_by_admin.pop(admin_user_id, None)

    def peek_pending_tag_edit(self, admin_user_id: int) -> str | None:
        return self._pending_tag_edit_by_admin.get(admin_user_id)
