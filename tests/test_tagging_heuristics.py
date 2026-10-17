from __future__ import annotations

import unittest

from services.tagging_service import TAG_CATALOG, TagScore, TaggingService


def _service() -> TaggingService:
    return TaggingService(api_key="test", base_url="https://example.test", model="test")


def _score(service: TaggingService, text: str) -> dict[str, float]:
    ranked = service._apply_heuristics([TagScore(tag="#тейк", score=6.0)], text)
    return {item.tag: item.score for item in ranked}


class TaggingHeuristicsTests(unittest.TestCase):
    def test_imho_is_in_catalog(self) -> None:
        self.assertIn("#имхо", TAG_CATALOG)

    def test_personal_opinion_without_proof_gets_imho(self) -> None:
        scores = _score(
            _service(),
            "Имхо, этот фестиваль стал хуже. Мне кажется, организация развалилась.",
        )
        self.assertGreater(scores.get("#имхо", 0.0), 7.0)

    def test_opinion_with_proof_does_not_force_imho(self) -> None:
        scores = _score(
            _service(),
            "Мне кажется, организация развалилась. Пруф: https://t.me/example/1",
        )
        self.assertLess(scores.get("#имхо", 0.0), 7.0)

    def test_sale_with_opinion_phrase_does_not_force_imho(self) -> None:
        scores = _score(
            _service(),
            "Продам парик. Мне кажется, цена нормальная, самовывоз.",
        )
        self.assertLess(scores.get("#имхо", 0.0), 7.0)


if __name__ == "__main__":
    unittest.main()
