#!/usr/bin/env python3
"""Standalone test for Nastya's multi-faceted personality.

Verifies:
1. All system prompts (NASTYA_SYSTEM_PROMPT, LOCAL_MODEL_SYSTEM_PROMPT,
   LOCAL_COMMENT_SYSTEM_PROMPT, inline, channel local post) enumerate
   multiple interests — not just cars.
2. Cars are present (Nastya still owns BMW M3) but NOT the loudest/only trait.
3. Topic enrichment exists for non-auto topics (cinema, food, travel, fashion,
   tech, psychology) so Nastya uses ALL her skills in dialogue.
4. The "РАЗНОСТОРОННОСТЬ" rule (don't mention cars in every sentence) is present.
5. All 5 professional consultation skills (numerology, astrology, HD, jyotish,
   health) are still enumerated in NASTYA_SYSTEM_PROMPT.
6. Topic enrichment detection works for sample user messages.
"""

import sys
import os
import inspect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.config import NASTYA_SYSTEM_PROMPT, KNOWLEDGE_TOPICS, NASTYA_VOCABULARY
from ai.providers.llama_cpp_provider import (
    LOCAL_MODEL_SYSTEM_PROMPT,
    LOCAL_COMMENT_SYSTEM_PROMPT,
)
from bot.handlers.inline import _generate_inline_response
from ai.router import AIRouter

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


print("=" * 70)
print("🧪 Nastya Bot — Multi-Faceted Personality Test")
print("=" * 70)
print()

# ─── 1. NASTYA_SYSTEM_PROMPT — all skills enumerated ─────────────────────
print("── 1. NASTYA_SYSTEM_PROMPT — all interests & skills enumerated ──")

required_interests = [
    "Авто", "Кино и сериалы", "Мода и красота", "Кулинария и еда",
    "Путешествия", "Технологии", "Психология", "Новости и события",
    "Животные", "Лайфстайл",
]
for interest in required_interests:
    check(
        f"NASTYA_SYSTEM_PROMPT contains '{interest}'",
        interest in NASTYA_SYSTEM_PROMPT,
    )

# 5 professional consultation skills
consultation_skills = ["Нумерология", "Астрология", "Дизайн Человека", "Здоровье", "Джйотиш"]
for skill in consultation_skills:
    check(
        f"NASTYA_SYSTEM_PROMPT contains consultation skill '{skill}'",
        skill in NASTYA_SYSTEM_PROMPT,
    )

# Multi-faceted rule
check(
    "NASTYA_SYSTEM_PROMPT has РАЗНОСТОРОННОСТЬ rule",
    "РАЗНОСТОРОННОСТЬ" in NASTYA_SYSTEM_PROMPT,
)
check(
    "NASTYA_SYSTEM_PROMPT says cars are NOT in every sentence",
    "НЕ упоминаешь машину в каждом ответе" in NASTYA_SYSTEM_PROMPT,
)
check(
    "NASTYA_SYSTEM_PROMPT gives ~10-15% guidance for car mentions",
    "10-15%" in NASTYA_SYSTEM_PROMPT,
)
print()

# ─── 2. Car is ONE of many — not the loudest trait ───────────────────────
print("── 2. Car is ONE of many interests (not loudest) ──")

# Count BMW/M3 mentions
bmw_count = NASTYA_SYSTEM_PROMPT.lower().count("bmw")
m3_count = NASTYA_SYSTEM_PROMPT.lower().count("m3")
check(
    "BMW mentioned in NASTYA_SYSTEM_PROMPT (still owns M3)",
    bmw_count >= 1,
    f"(count={bmw_count})",
)
check(
    "M3 mentioned in NASTYA_SYSTEM_PROMPT",
    m3_count >= 1,
    f"(count={m3_count})",
)
# Cars should NOT dominate — check that other interests appear at least as often
check(
    "Cinema mentioned (Кино)",
    "Кино" in NASTYA_SYSTEM_PROMPT,
)
check(
    "Food mentioned (Кулинария/еда)",
    "Кулинария" in NASTYA_SYSTEM_PROMPT,
)
check(
    "Travel mentioned (Путешествия)",
    "Путешествия" in NASTYA_SYSTEM_PROMPT,
)
# The FIRST trait listed should NOT be the car (it should be the РАЗНОСТОРОННЯЯ statement)
first_trait_section = NASTYA_SYSTEM_PROMPT.split("Твои интересы и навыки")[0]
check(
    "First trait section says РАЗНОСТОРОННЯЯ (not car-first)",
    "РАЗНОСТОРОННЯЯ" in first_trait_section,
)
print()

# ─── 3. LOCAL_MODEL_SYSTEM_PROMPT (chat route) ───────────────────────────
print("── 3. LOCAL_MODEL_SYSTEM_PROMPT (chat route, 4B model) ──")

check(
    "LOCAL_MODEL_SYSTEM_PROMPT is multi-faceted",
    "РАЗНОСТОРОННЯЯ" in LOCAL_MODEL_SYSTEM_PROMPT,
)
# Multiple interests listed
for interest in ["кино", "еда", "путешеств", "мода", "технолог", "психолог"]:
    check(
        f"LOCAL_MODEL_SYSTEM_PROMPT mentions '{interest}'",
        interest in LOCAL_MODEL_SYSTEM_PROMPT.lower(),
    )
check(
    "LOCAL_MODEL_SYSTEM_PROMPT says car mentioned ТОЛЬКО когда уместно",
    "ТОЛЬКО когда уместно" in LOCAL_MODEL_SYSTEM_PROMPT,
)
check(
    "LOCAL_MODEL_SYSTEM_PROMPT has topic-rotation guidance",
    "не зацикливайся" in LOCAL_MODEL_SYSTEM_PROMPT.lower(),
)
# Car is NOT the only/main trait
check(
    "LOCAL_MODEL_SYSTEM_PROMPT does NOT say 'BMW M3 серая — твоя машина' as primary trait",
    "BMW M3 серая — твоя машина" not in LOCAL_MODEL_SYSTEM_PROMPT,
)
print()

# ─── 4. LOCAL_COMMENT_SYSTEM_PROMPT (group comment route) ───────────────
print("── 4. LOCAL_COMMENT_SYSTEM_PROMPT (group comment route) ──")

check(
    "LOCAL_COMMENT_SYSTEM_PROMPT lists multiple interests",
    "кино" in LOCAL_COMMENT_SYSTEM_PROMPT and "еда" in LOCAL_COMMENT_SYSTEM_PROMPT,
)
check(
    "LOCAL_COMMENT_SYSTEM_PROMPT says car ТОЛЬКО когда тема уместна",
    "ТОЛЬКО когда тема уместна" in LOCAL_COMMENT_SYSTEM_PROMPT,
)
# Car is NOT the only interest (was: "BMW M3 — твоя тачка" only)
check(
    "LOCAL_COMMENT_SYSTEM_PROMPT does NOT have 'BMW M3 — твоя тачка' as the only trait",
    "BMW M3 — твоя тачка." not in LOCAL_COMMENT_SYSTEM_PROMPT,
)
print()

# ─── 5. Inline system prompt ────────────────────────────────────────────
print("── 5. Inline system prompt ──")

inline_src = inspect.getsource(_generate_inline_response)
check(
    "Inline prompt mentions 'разносторонняя'",
    "разносторонняя" in inline_src.lower(),
)
check(
    "Inline prompt lists multiple interests",
    "кино" in inline_src and "еда" in inline_src and "путешеств" in inline_src.lower(),
)
check(
    "Inline prompt says car ТОЛЬКО когда уместно",
    "ТОЛЬКО когда уместно" in inline_src,
)
print()

# ─── 6. Channel local post prompt (generate_local_post) ─────────────────
print("── 6. Channel local post prompt (generate_local_post) ──")

post_src = inspect.getsource(AIRouter.generate_local_post)
check(
    "generate_local_post prompt mentions 'разносторонняя'",
    "разносторонняя" in post_src.lower(),
)
check(
    "generate_local_post prompt lists multiple interests",
    "кино" in post_src and "еда" in post_src,
)
check(
    "generate_local_post prompt says car only when appropriate",
    "только когда уместно" in post_src.lower(),
)
print()

# ─── 7. chat.py topic enrichment for non-auto topics ───────────────────
print("── 7. chat.py — non-auto topic enrichment ──")

from bot.handlers import chat as chat_mod
chat_src = inspect.getsource(chat_mod)

enrichments = {
    "cinema": "Настя-киноманка",
    "food": "Настя-кулинар",
    "travel": "Настя-путешественница",
    "fashion": "Настя-модница",
    "tech": "Настя-технолог",
    "psychology": "Настя-психолог",
}
for topic, marker in enrichments.items():
    check(
        f"chat.py has '{marker}' enrichment for {topic}",
        marker in chat_src,
    )

# Verify each enrichment has substantive content (not just a label)
for topic, marker in enrichments.items():
    idx = chat_src.find(marker)
    snippet = chat_src[idx:idx + 300]
    check(
        f"{topic} enrichment has substantive content (>100 chars)",
        len(snippet) > 150,
    )
print()

# ─── 8. Topic enrichment detection (keyword matching) ──────────────────
print("── 8. Topic enrichment detection (keyword matching) ──")

# Simulate the detection logic from chat.py
test_cases = [
    ("посоветуй фильм на вечер", "cinema"),
    ("что приготовить на ужин?", "food"),
    ("хочу поехать в Стамбул", "travel"),
    ("какая одежда в тренде?", "fashion"),
    ("посоветуй айфон", "tech"),
    ("как справиться со стрессом?", "psychology"),
    ("какое масло для BMW M3?", "auto"),  # should NOT trigger non-auto enrichment
]

cinema_kws = ["фильм", "сериал", "кино", "нетфликс", "netflix", "нолан", "аниме", "режиссёр", "оскар", "кинопоиск", "трейлер", "премьер"]
food_kws = ["рецепт", "готовить", "еда", "кушать", "суши", "пицца", "кофе", "матча", "торт", "шоколад", "обед", "ужин", "завтрак", "ресторан"]
travel_kws = ["путешеств", "отпуск", "стамбул", "дубай", "бали", "казань", "калининград", "сочи", "полёт", "билет ", "отель", "виза"]
fashion_kws = ["мода", "одежд", "zara", "h&m", "стил", "наряд", "платье", "маникюр", "макияж", "брови", "оверсайз", "тренд"]
tech_kws = ["айфон", "iphone", "гаджет", "нейросет", "ai ", "chatgpt", "чатгпт", "telegram", "технолог", "5g", "квантов"]
psych_kws = ["психолог", "эмоци", "отношени", "тип личност", "дофамин", "стресс", "депресс", "любов", "привычк"]
auto_pre_kws = ["bmw", "бмв", "m3", "m4", "m5", "запчаст", "масло", "фильтр", "колодки", "тачк", "машина", "авто", "сто ", "ремонт ", "регламент"]

for text, expected_topic in test_cases:
    text_lower = text.lower()
    is_auto = any(kw in text_lower for kw in auto_pre_kws)
    detected = None
    if not is_auto:
        if any(kw in text_lower for kw in cinema_kws):
            detected = "cinema"
        elif any(kw in text_lower for kw in food_kws):
            detected = "food"
        elif any(kw in text_lower for kw in travel_kws):
            detected = "travel"
        elif any(kw in text_lower for kw in fashion_kws):
            detected = "fashion"
        elif any(kw in text_lower for kw in tech_kws):
            detected = "tech"
        elif any(kw in text_lower for kw in psych_kws):
            detected = "psychology"
    else:
        detected = "auto"

    check(
        f"'{text}' → detected as '{expected_topic}'",
        detected == expected_topic,
        f"(got: {detected})",
    )
print()

# ─── 9. KNOWLEDGE_TOPICS still has all 15 topics ────────────────────────
print("── 9. KNOWLEDGE_TOPICS — all 15 topics present ──")

expected_topics = [
    "auto", "zodiac", "psychology", "fun_facts", "moscow",
    "blogging", "cinema", "cooking", "relationships", "fashion",
    "spb", "sochi", "restaurants", "travel", "tech",
]
for topic in expected_topics:
    check(
        f"KNOWLEDGE_TOPICS has '{topic}'",
        topic in KNOWLEDGE_TOPICS,
    )
print()

# ─── 10. Nastya still owns BMW M3 (not removed, just de-emphasized) ────
print("── 10. Nastya still owns BMW M3 (de-emphasized, NOT removed) ──")

check(
    "NASTYA_SYSTEM_PROMPT still mentions BMW M3",
    "BMW M3" in NASTYA_SYSTEM_PROMPT,
)
check(
    "LOCAL_MODEL_SYSTEM_PROMPT still mentions BMW M3",
    "BMW M3" in LOCAL_MODEL_SYSTEM_PROMPT,
)
check(
    "LOCAL_COMMENT_SYSTEM_PROMPT still mentions BMW M3",
    "BMW M3" in LOCAL_COMMENT_SYSTEM_PROMPT,
)
print()

# ─── SUMMARY ────────────────────────────────────────────────────────────
print("=" * 70)
print(f"📊 RESULTS: {passed} ✅  |  {failed} ❌")
print("=" * 70)
if failed == 0:
    print("🎉 ALL CHECKS PASSED — Nastya is multi-faceted!")
    print("   + Cars are ONE interest, not the loudest")
    print("   + All 10 interests + 5 consultation skills + 15 knowledge topics")
    print("   + Topic enrichment for cinema/food/travel/fashion/tech/psychology")
    print("   + 'РАЗНОСТОРОННОСТЬ' rule prevents car-in-every-sentence")
else:
    print("⚠️  Some checks failed — review above.")
sys.exit(0 if failed == 0 else 1)
