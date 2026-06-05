"""Partner Links System for Nastya Bot.

Nastya is a lifestyle blogger — she recommends products naturally in conversation.
Partner links are integrated into her responses when relevant topics come up.
Not limited to just partners — Nastya gives both partner AND general links,
but when a partner option exists, it's included naturally.

Categories:
- Auto parts (Rossko, Autopiter, Exist) — Nastya drives BMW M3!
- Shopping (Ozon, Wildberries, Yandex.Market, Lamoda)
- Beauty & Health
- Electronics
- Food & Delivery
- Travel & Events
"""

import json
import random
import re
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus

from bot.config import CHANNEL_USERNAME

logger = logging.getLogger("nastya.partners")


# ── Direct shop search URL templates ──────────────────────────────────────────
# When Nastya mentions a product, she can give DIRECT links to shops.
# These are always available (no API calls needed).

SHOP_SEARCH_URLS = {
    # Auto parts — Nastya knows BMW!
    "rossko": "https://rossko.ru/search?text={article}&subid=nastya_bot",
    "autopiter": "https://www.autopiter.ru/search?querystr={article}",
    "exist": "https://exist.ru/Price/?p={article}",
    "emex": "https://emex.ru/products?search={article}",
    "autodoc": "https://autodoc.ru/search?keyword={article}",
    "zzap": "https://zzap.ru/search/?q={article}",
    # General shopping
    "ozon": "https://www.ozon.ru/search/?text={query}",
    "wildberries": "https://www.wildberries.ru/catalog/0/search.aspx?search={query}",
    "yandex_market": "https://market.yandex.ru/search?text={query}",
    "lamoda": "https://www.lamoda.ru/c/{query}/",
    "aliexpress": "https://aliexpress.ru/wholesale?SearchText={query}",
    # Electronics
    "dns": "https://www.dns-shop.ru/search/?q={query}",
    "mvideo": "https://www.mvideo.ru/product-list?q={query}",
    "citilink": "https://www.citilink.ru/search/?q={query}",
    # Food & Delivery
    "edamail": "https://edamail.ru/search?q={query}",
    # Sports
    "sportsmaster": "https://www.sportmaster.ru/search/{query}/",
    "decathlon": "https://www.decathlon.ru/search?query={query}",
}

# Partner categories for Nastya — lifestyle blogger context
PARTNER_CATEGORIES = {
    "autoparts": {
        "label": "Автозапчасти",
        "keywords": [
            "запчаст", "деталь", "артикул", "купить запчас", "купить детал",
            "оригинал", "аналог", "замена", "подбор", "номер детал",
            "oem", "оригинальн", "поиск запчас", "найти запчас",
            "фильтр", "колодки", "свечи", "ремень", "прокладк",
            "сальник", "подшипник", "амортизатор", "реле", "датчик",
            "масло", "антифриз", "тормозн", "двигател", "bmw", "бмв",
            "сто", "ремонт", "обслуживание", "то ", "регламент",
        ],
        "shops": ["rossko", "autopiter", "exist", "emex", "zzap", "autodoc"],
        "context": "Настя водит BMW M3 и разбирается в запчастях! Упомяни где купить — дай ссылки на магазины автозапчастей.",
    },
    "shopping": {
        "label": "Покупки",
        "keywords": [
            "купить", "заказать", "цена", "стоимость", "подешевле",
            "где купить", "найти", "поищи", "нужен", "хочу",
            "вариант", "выбрать", "подобрать", "лучший", "топ",
            "скидк", "акци", "промокод",
        ],
        "shops": ["ozon", "wildberries", "yandex_market", "aliexpress"],
        "context": "Настя любит шопинг! Предложи конкретные магазины и дай ссылки на поиск товара.",
    },
    "fashion": {
        "label": "Мода и красота",
        "keywords": [
            "одежд", "платье", "сумоч", "обувь", "кроссов", "куртк",
            "пальто", "брюк", "джинс", "футбол", "свитер",
            "маникюр", "косметик", "макияж", "парфюм", "духи",
            "украшен", "серьг", "кольц", "браслет",
            "lamoda", "зара", "zara", "h&m", "hm",
        ],
        "shops": ["lamoda", "wildberries", "ozon", "yandex_market"],
        "context": "Настя разбирается в моде! Предложи магазины одежды и красоты, дай ссылки.",
    },
    "electronics": {
        "label": "Электроника и гаджеты",
        "keywords": [
            "айфон", "iphone", "телефон", "смартфон", "ноутбук",
            "планшет", "наушник", "часы", "apple watch", "гаджет",
            "техник", "компьютер", "монитор", "клавиатур",
            "роутер", "колонк", "блютуз",
        ],
        "shops": ["dns", "mvideo", "citilink", "ozon", "yandex_market"],
        "context": "Настя в курсе новинок техники! Дай ссылки на магазины электроники.",
    },
    "food": {
        "label": "Еда и доставка",
        "keywords": [
            "доставк", "еды", "суши", "пицц", "бургер",
            "ресторан", "кафе", "рецепт", "продукт",
            "яндекс еда", "delivery", "самокат",
        ],
        "shops": ["yandex_market", "ozon"],
        "context": "Настя любит вкусную еду! Если просят доставку или продукты — предложи варианты.",
    },
    "sports": {
        "label": "Спорт и фитнес",
        "keywords": [
            "спорт", "фитнес", "зал", "тренажёр", "бегов",
            "гантел", "турник", "коврик", "кроссовки для бега",
            "велотренажёр", "эллипс", "футбол", "плаван",
        ],
        "shops": ["sportsmaster", "decathlon", "ozon", "wildberries"],
        "context": "Настя иногда ходит в зал! Дай ссылки на спортивные магазины.",
    },
}


class NastyaPartnerManager:
    """Manages partner links for Nastya Bot.
    
    Unlike Asya (auto-expert), Nastya is a lifestyle blogger.
    She recommends products naturally — not as ads, but as personal suggestions.
    Partner links are woven into her conversation style.
    """

    def __init__(self):
        self._admitad_programs: List[Dict] = []
        self._loaded = False

    def load_admitad(self, filepath: str = "admitad_ads.json") -> int:
        """Load admitad partner programs from JSON file."""
        path = Path(filepath)
        if not path.exists():
            logger.info(f"No admitad_ads.json found at {filepath} — using direct shop links only")
            self._loaded = True
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data if isinstance(data, list) else data.get("programs", data.get("items", []))
            self._admitad_programs = [p for p in items if p.get("goto_link")]
            self._loaded = True
            logger.info(f"Loaded {len(self._admitad_programs)} admitad partner programs for Nastya")
            return len(self._admitad_programs)
        except Exception as e:
            logger.error(f"Error loading admitad programs: {e}")
            self._loaded = True
            return 0

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_admitad()

    def detect_categories(self, text: str) -> List[str]:
        """Detect which partner categories match the user's message."""
        text_lower = text.lower()
        matched = []
        for cat_key, cat_data in PARTNER_CATEGORIES.items():
            for kw in cat_data["keywords"]:
                if kw in text_lower:
                    matched.append(cat_key)
                    break
        return matched

    def get_shop_links(self, query: str, categories: Optional[List[str]] = None) -> List[Dict[str, str]]:
        """Get direct shop search links for a query.
        
        Returns list of dicts: {name, url, description}
        """
        links = []
        query_clean = query.strip()
        article_match = re.search(r'\b([A-Z0-9]{4,}[-/]?[A-Z0-9]*)\b', query.upper())
        is_article = bool(article_match)
        
        # If specific categories, use their shops
        if categories:
            seen_shops = set()
            for cat_key in categories:
                cat_data = PARTNER_CATEGORIES[cat_key]
                for shop_key in cat_data["shops"]:
                    if shop_key in seen_shops:
                        continue
                    seen_shops.add(shop_key)
                    if shop_key not in SHOP_SEARCH_URLS:
                        continue
                    url_template = SHOP_SEARCH_URLS[shop_key]
                    # Auto parts shops use article-based search
                    if shop_key in ("rossko", "autopiter", "exist", "emex", "autodoc", "zzap"):
                        search_term = article_match.group(1) if article_match else quote_plus(query_clean)
                        url = url_template.format(article=search_term, query=quote_plus(query_clean))
                    else:
                        url = url_template.format(query=quote_plus(query_clean), article=quote_plus(query_clean))
                    links.append({
                        "name": shop_key.capitalize(),
                        "url": url,
                        "description": f"Поиск '{query_clean}' на {shop_key.capitalize()}",
                    })
        else:
            # General search — add most common shops
            for shop_key in ["ozon", "wildberries", "yandex_market"]:
                if shop_key in SHOP_SEARCH_URLS:
                    url = SHOP_SEARCH_URLS[shop_key].format(
                        query=quote_plus(query_clean), article=quote_plus(query_clean)
                    )
                    links.append({
                        "name": shop_key.capitalize(),
                        "url": url,
                        "description": f"Поиск '{query_clean}'",
                    })

        return links

    def generate_partner_context(self, text: str, max_shops: int = 4) -> str:
        """Generate partner context for AI system prompt.
        
        Nastya should naturally mention these links in her response.
        She gives both partner AND general links — not limited to partners.
        """
        categories = self.detect_categories(text)
        if not categories:
            return ""

        query = text.strip()
        # Extract cleaner search query
        for prefix in ["найди", "поищи", "ищу", "дай", "скинь", "где ", "какой ",
                       "подскажи", "посоветуй", "рекомендуй", "выбери", "какие "]:
            if query.lower().startswith(prefix):
                query = query[len(prefix):].strip()
                break

        links = self.get_shop_links(query, categories)[:max_shops]
        if not links:
            return ""

        cat_labels = [PARTNER_CATEGORIES[c]["label"] for c in categories]
        lines = [
            f"Партнёрские ссылки для темы: {', '.join(cat_labels)}",
            "Вставь эти ссылки ЕСТЕСТВЕННО в ответ — как личную рекомендация! Не как рекламу!",
        ]
        for link in links:
            lines.append(f"- {link['name']}: {link['url']}")

        # Add category-specific context hints
        for cat_key in categories:
            cat_data = PARTNER_CATEGORIES[cat_key]
            lines.append(cat_data["context"])

        # Add admitad programs if available and relevant
        self.ensure_loaded()
        if self._admitad_programs:
            text_lower = text.lower()
            for prog in self._admitad_programs[:2]:
                name = prog.get("name", "")
                goto = prog.get("goto_link", "")
                desc = prog.get("description", "")[:100]
                if goto and any(kw in text_lower for kw in name.lower().split()):
                    url = goto + ("&subid=nastya_bot" if "?" in goto else "?subid=nastya_bot")
                    lines.append(f"- {name}: {url}")
                    if desc:
                        lines.append(f"  {desc}")

        return "\n".join(lines)

    def get_auto_parts_links(self, query: str) -> str:
        """Get auto parts shop links specifically (for BMW-related queries)."""
        links = self.get_shop_links(query, ["autoparts"])
        if not links:
            return ""
        lines = ["Ссылки на магазины автозапчастей (вставь естественно в ответ):"]
        for link in links:
            lines.append(f"- {link['name']}: {link['url']}")
        lines.append("Настя водит M3 и знает где покупать запчасти! Упомяни это естественно.")
        return "\n".join(lines)


# ── Global instance ────────────────────────────────────────────────────────────

nastya_partner_manager = NastyaPartnerManager()
