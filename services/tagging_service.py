from __future__ import annotations

"""Сервис автоматической генерации тегов через Chad AI (OpenAI-compatible API).

Логика:
1) отправляем текст поста в модель;
2) просим вернуть строгий JSON с тегами и оценками;
3) валидируем и нормализуем ответ;
4) применяем внутренние эвристики проекта.
"""

import json
import re
from dataclasses import dataclass

import aiohttp


# Разрешенный каталог тегов, который знает и кнопочный интерфейс, и LLM-промпт.
TAG_CATALOG: list[str] = [
    "#телеграф",
    "#тейк",
    "#вопросы",
    "#фест",
    "#фотосет",
    "#помощь",
    "#срочно",
    "#дропбазы",
    "#пошив",
    "#крафт",
    "#вигмейкинг",
    "#мемы",
    "#отзыв",
    "#щитпост",
]

# Человеческие объяснения тегов — они подставляются в prompt для повышения точности.
TAG_DESCRIPTIONS: dict[str, str] = {
    "#телеграф": (
        "длинная аналитика или структурный разбор, обычно с ссылкой на Telegraph/Telegra.ph; "
        "если это короткий тезисный пост без лонгрида, скорее #дропбазы"
    ),
    "#тейк": (
        "обычное мнение/вброс/сплетня, эмоциональная реакция, личная позиция без системного разбора; "
        "если текст 'по делу' и тезисный, может быть #дропбазы"
    ),
    "#вопросы": (
        "явный вопрос: 'как', 'где', 'кого посоветуете', 'кто знает'; поиск людей, мест, инструкций, ресурсов"
    ),
    "#фест": (
        "все про фестивали и ивенты: анонсы, даты, билеты, расписание, отзывы о фестивале, обсуждение оргвопросов"
    ),
    "#фотосет": (
        "темы фотосессий: студии, локации, аренда, условия съемки, оценка мест и форматов фотосета"
    ),
    "#помощь": (
        "прямая просьба о помощи, особенно с формулировками 'помогите', 'нужна помощь', 'выручите'"
    ),
    "#срочно": (
        "критично срочные кейсы с немедленным действием: поиск человека сейчас, срочный дедлайн/инцидент"
    ),
    "#дропбазы": (
        "качественный информативный тезисный пост/разбор по сути, который 'база', "
        "но слишком короткий для телеграфа; может быть жестким и контроверсивным, "
        "если есть аргументация и выводы"
    ),
    "#пошив": (
        "все про пошив: выкройки, ткани, швейные техники, посадка костюма, изготовление одежды"
    ),
    "#крафт": (
        "крафт и пропсы: изготовление деталей, печать/3D-печать, обработка материалов, покраска, сборка"
    ),
    "#вигмейкинг": (
        "парики: подбор, укладка, стрижка, прошивка, уход, изготовление и доработка"
    ),
    "#мемы": (
        "контент с акцентом на юмор/иронию/мемность: шутки, смешные картинки, комедийная подача"
    ),
    "#отзыв": (
        "оценка опыта взаимодействия с конкретным объектом: мастер, фестиваль, студия, сервис; "
        "личный опыт, плюсы/минусы, вывод"
    ),
    "#щитпост": (
        "оффтоп или почти оффтоп для темы канала, но смешной/развлекательный пост"
    ),
}


@dataclass(slots=True)
class TagScore:
    """Оценка релевантности конкретного тега (0..10)."""

    tag: str
    score: float


class TaggingService:
    """Клиент Chad API + правила постобработки тегов."""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._catalog_map = {tag.lower(): tag for tag in TAG_CATALOG}

    def _extract_json(self, content: str) -> str:
        """Извлекает JSON-часть из ответа модели (в т.ч. если она завернула в ```json блок)."""
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped, count=1)
            stripped = re.sub(r"\s*```$", "", stripped, count=1)
        return stripped.strip()

    def _normalize_tag(self, raw: str) -> str | None:
        """Приводит тег к каноническому виду из каталога (или возвращает None)."""
        candidate = raw.strip().lower()
        if not candidate.startswith("#"):
            candidate = f"#{candidate.lstrip('#')}"
        return self._catalog_map.get(candidate)

    def _apply_heuristics(
        self,
        scored: list[TagScore],
        content_text: str,
    ) -> list[TagScore]:
        """Применяет бизнес-эвристики проекта к оценкам модели.

        Нужны, чтобы улучшить качество на спорных случаях (например, #тейк vs #дропбазы).
        """
        score_map: dict[str, float] = {item.tag.lower(): item.score for item in scored}

        text = content_text.lower()
        word_count = len(re.findall(r"\w+", text, flags=re.UNICODE))
        paragraph_count = len([part for part in re.split(r"\n\s*\n", content_text) if part.strip()])

        argument_markers = [
            "во первых",
            "во-вторых",
            "в третьих",
            "проблема",
            "по фактам",
            "по делу",
            "база",
            "деградац",
            "маргинал",
            "уровень",
            "аргументац",
            "вывод",
        ]
        marker_hits = sum(1 for marker in argument_markers if marker in text)

        # Усиливаем #дропбазы для длинных структурных разборов с аргументацией.
        if word_count >= 180 and paragraph_count >= 4 and marker_hits >= 3:
            current = score_map.get("#дропбазы", 0.0)
            score_map["#дропбазы"] = max(current, 8.6)

        # Для коротких opinion-постов поддерживаем конкурентный вес #тейк.
        if word_count <= 140 and marker_hits <= 1:
            current_take = score_map.get("#тейк", 0.0)
            score_map["#тейк"] = max(current_take, 7.8)

        merged = [TagScore(tag=tag, score=score) for tag, score in score_map.items()]
        merged.sort(key=lambda item: item.score, reverse=True)
        return merged

    def _parse_scores(self, content: str) -> list[TagScore]:
        """Разбирает JSON-ответ модели и оставляет валидные теги с оценками."""
        payload = json.loads(self._extract_json(content))
        entries = payload.get("tags") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            raise RuntimeError("LLM response must contain `tags` list.")

        best: dict[str, float] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            tag_value = item.get("tag")
            score_value = item.get("score")
            if tag_value is None or score_value is None:
                continue
            normalized = self._normalize_tag(str(tag_value))
            if not normalized:
                continue
            try:
                score = float(score_value)
            except (TypeError, ValueError):
                continue
            score = max(0.0, min(10.0, score))
            prev = best.get(normalized)
            if prev is None or score > prev:
                best[normalized] = score

        if not best:
            raise RuntimeError("No valid tags returned by LLM.")

        ranked = [TagScore(tag=tag, score=score) for tag, score in best.items()]
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked

    async def score_tags(self, content_text: str) -> list[TagScore]:
        """Основной вызов модели: возвращает список тегов с оценками."""
        # В LLM отправляем только контент кейса (текст/описание медиа) без user_chat_id,
        # user_id и других идентификаторов автора. Это сохраняет анонимность источника.
        tag_guide_lines = [
            f"{tag} - {TAG_DESCRIPTIONS[tag]}"
            for tag in TAG_CATALOG
            if tag in TAG_DESCRIPTIONS
        ]
        prompt = (
            "Проанализируй сообщение и верни только JSON формата "
            '{"tags":[{"tag":"#тег","score":0..10}]}. '
            "Никакого markdown и пояснений.\n"
            "Используй только теги из списка ниже и учитывай их значения:\n"
            + "\n".join(tag_guide_lines)
            + "\n"
            "Правила выставления баллов:\n"
            "- Для каждого тега оцени релевантность по шкале 0..10.\n"
            "- Верни оценки для максимального числа релевантных тегов, а не только один.\n"
            "- Не смешивай #тейк и #дропбазы автоматически: "
            "#дропбазы — это короткий, но сильный тезисный разбор/база, "
            "#тейк — обычное мнение или вброс.\n"
            "- ВАЖНО: #дропбазы подходит и для жесткого критического разбора сообщества/ситуации, "
            "если есть аргументы, структура и выводы, даже при токсичном тоне.\n"
            "- #телеграф ставь только когда есть признаки лонгрида/большой аналитики (часто ссылка на Telegraph).\n"
            "- #отзыв требует именно опыта/оценки объекта, а не просто реплики.\n"
            "- #помощь и #срочно могут стоять вместе, если это срочная просьба.\n"
            "Оцени релевантность каждого выбранного тега по шкале от 0 до 10.\n"
            f"Сообщение:\n{content_text}"
        )
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": "Ты классификатор постов. Возвращай строго JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_completion_tokens": 100000,
        }

        # Таймаут, чтобы не зависать бесконечно при сетевых проблемах.
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=body) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise RuntimeError(f"Chad API error {response.status}: {text[:300]}")
                data = await response.json()

        choices = data.get("choices")
        if not choices:
            raise RuntimeError("Chad API returned empty choices.")
        message = choices[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Chad API returned empty content.")
        parsed = self._parse_scores(content)
        return self._apply_heuristics(parsed, content_text)

    async def generate_top_tags(self, content_text: str, min_score: float = 7.0) -> list[str]:
        """Упрощенный режим: вернуть только теги, прошедшие порог минимального score."""
        scored = await self.score_tags(content_text)
        return [item.tag for item in scored if item.score > min_score]
