from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiogram.types import InputMediaPhoto, InputMediaVideo

from services.case_store import CaseRecord, CaseStore
from services.media_bridge import MediaBridge, build_album_media
from services.message_content import (
    MediaItem,
    extract_media_item,
    extract_media_items,
    replace_media_item_for_message,
)


class MediaExtractTests(unittest.TestCase):
    def test_extract_photo_uses_largest_size(self) -> None:
        message = SimpleNamespace(
            photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")],
            video=None,
            animation=None,
            document=None,
            audio=None,
        )
        self.assertEqual(
            extract_media_item(message),
            MediaItem(kind="photo", file_id="big"),
        )

    def test_extract_media_items_skips_text(self) -> None:
        photo = SimpleNamespace(
            photo=[SimpleNamespace(file_id="p1")],
            video=None,
            animation=None,
            document=None,
            audio=None,
        )
        text = SimpleNamespace(
            photo=None,
            video=None,
            animation=None,
            document=None,
            audio=None,
        )
        self.assertEqual(
            extract_media_items([photo, text]),
            [MediaItem(kind="photo", file_id="p1")],
        )

    def test_replace_media_item_updates_matching_source_id(self) -> None:
        items = [
            MediaItem(kind="photo", file_id="old-a"),
            MediaItem(kind="photo", file_id="old-b"),
        ]
        updated = replace_media_item_for_message(
            items,
            [101, 102],
            102,
            MediaItem(kind="photo", file_id="new-b"),
        )
        self.assertEqual(updated[0].file_id, "old-a")
        self.assertEqual(updated[1].file_id, "new-b")
        self.assertEqual(items[1].file_id, "old-b")

    def test_replace_media_item_ignores_unknown_message(self) -> None:
        items = [MediaItem(kind="photo", file_id="a")]
        self.assertEqual(
            replace_media_item_for_message(
                items,
                [101],
                999,
                MediaItem(kind="photo", file_id="x"),
            ),
            items,
        )


class AlbumMediaBuildTests(unittest.TestCase):
    def test_caption_only_on_first_item(self) -> None:
        media = build_album_media(
            [
                MediaItem(kind="photo", file_id="p1"),
                MediaItem(kind="photo", file_id="p2"),
                MediaItem(kind="video", file_id="v1"),
            ],
            caption="Прислано через @backstage_staff_bot",
        )
        self.assertEqual(len(media), 3)
        self.assertIsInstance(media[0], InputMediaPhoto)
        self.assertEqual(media[0].caption, "Прислано через @backstage_staff_bot")
        self.assertIsNone(media[1].caption)
        self.assertIsInstance(media[2], InputMediaVideo)
        self.assertIsNone(media[2].caption)

    def test_skips_unknown_kind(self) -> None:
        media = build_album_media(
            [
                MediaItem(kind="sticker", file_id="s1"),
                MediaItem(kind="photo", file_id="p1"),
                MediaItem(kind="photo", file_id="p2"),
            ],
            caption="footer",
        )
        self.assertEqual(len(media), 2)
        self.assertEqual(media[0].caption, "footer")
        self.assertEqual(media[0].media, "p1")


class SendAlbumTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_album_does_not_call_bot_for_single_item(self) -> None:
        bot = Mock()
        bot.send_media_group = AsyncMock()
        result = await MediaBridge().send_album(
            bot=bot,
            chat_id=-100,
            items=[MediaItem(kind="photo", file_id="p1")],
            caption="x",
        )
        self.assertEqual(result, [])
        bot.send_media_group.assert_not_called()

    async def test_send_album_forwards_built_media(self) -> None:
        bot = Mock()
        sent = [SimpleNamespace(message_id=1), SimpleNamespace(message_id=2)]
        bot.send_media_group = AsyncMock(return_value=sent)
        result = await MediaBridge().send_album(
            bot=bot,
            chat_id=-100,
            items=[
                MediaItem(kind="photo", file_id="p1"),
                MediaItem(kind="photo", file_id="p2"),
            ],
            caption="caption",
        )
        self.assertEqual(result, sent)
        bot.send_media_group.assert_awaited_once()
        kwargs = bot.send_media_group.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], -100)
        self.assertEqual(kwargs["media"][0].caption, "caption")
        self.assertEqual(len(kwargs["media"]), 2)


class CaseStoreMediaTests(unittest.TestCase):
    def test_media_items_survive_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "open_cases.json"
            store = CaseStore(path)
            store.add_case(
                CaseRecord(
                    case_id="aa11bb22",
                    user_chat_id=7,
                    source_message_ids=[1, 2],
                    is_media_group=True,
                    media_items=[
                        MediaItem(kind="photo", file_id="file-a"),
                        MediaItem(kind="photo", file_id="file-b"),
                    ],
                )
            )
            reloaded = CaseStore(path)
            case = reloaded.get_case("aa11bb22")
            self.assertIsNotNone(case)
            assert case is not None
            self.assertEqual(
                [(item.kind, item.file_id) for item in case.media_items],
                [("photo", "file-a"), ("photo", "file-b")],
            )
            self.assertIsNone(case.user_chat_id)

    def test_legacy_payload_without_media_items_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "open_cases.json"
            path.write_text(
                (
                    '{"aa11bb22": {"source_message_ids": [1], '
                    '"is_media_group": false, "status": "open"}}'
                ),
                encoding="utf-8",
            )
            store = CaseStore(path)
            case = store.get_case("aa11bb22")
            self.assertIsNotNone(case)
            assert case is not None
            self.assertEqual(case.media_items, [])
