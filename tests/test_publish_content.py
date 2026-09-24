from __future__ import annotations

import unittest
from types import SimpleNamespace

from services.case_store import CaseRecord
from services.publish_content import (
    SENT_VIA,
    compose_single_text_with_tags,
    message_has_sent_via,
    tags_block,
)


def _case(**kwargs) -> CaseRecord:
    payload = {
        "case_id": "abcd1234",
        "user_chat_id": 1,
        "source_message_ids": [10, 11],
        "is_media_group": True,
        "single_content_text": "Мастерская",
        "selected_tags": ["#костюмы", "#фест"],
    }
    payload.update(kwargs)
    return CaseRecord(**payload)


class PublishContentTests(unittest.TestCase):
    def test_tags_block_joins_selected_tags(self) -> None:
        self.assertEqual(tags_block(_case()), "#костюмы\n#фест")

    def test_compose_always_includes_sent_via_and_tags(self) -> None:
        text = compose_single_text_with_tags(_case())
        self.assertEqual(
            text,
            f"Мастерская\n\n{SENT_VIA}\n\n#костюмы\n#фест",
        )

    def test_compose_without_base_still_has_footer(self) -> None:
        text = compose_single_text_with_tags(
            _case(single_content_text="", selected_tags=["#тейк"])
        )
        self.assertEqual(text, f"{SENT_VIA}\n\n#тейк")

    def test_message_has_sent_via_reads_caption(self) -> None:
        message = SimpleNamespace(text=None, caption=f"hello\n\n{SENT_VIA}")
        self.assertTrue(message_has_sent_via(message))
        self.assertFalse(
            message_has_sent_via(SimpleNamespace(text="hello", caption=None))
        )
        self.assertFalse(message_has_sent_via(None))
