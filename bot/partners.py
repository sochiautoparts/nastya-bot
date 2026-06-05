"""Partner Links System for Nastya Bot v3.0.

Nastya is a lifestyle blogger — she recommends products naturally in conversation.
Partner links are integrated into her responses when relevant topics come up.

v3.0 KEY CHANGES:
- Downloads admitad_ads.json from remote GitHub URL (updateable file!)
- Auto-refreshes every 6 hours
- Uses goto_link EXACTLY as provided — NO subid additions!
- Regional filtering by allowed_regions
- For article searches, modifies ulp parameter in goto_link
- Proper formatting: "Name (category description): goto_link"
"""

import json
import random
import re
import time
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus, urlparse, parse_qs, urlencode, urlunparse

from bot.config import CHANNEL_USERNAME

logger = logging.getLogger("nastya.partners")

# Remote admitad_ads.json URL (updateable file!)
ADMITAD_JSON_URL = "https://raw.githubusercontent.com/creastudioai-beep/pr/main/data/admitad_ads.json"
ADMITAD_LOCAL_CACHE = "data/admitad_ads.json"
ADMITAD_REFRESH_INTERVAL = 6 * 3600  # Refresh every 6 hours

# Default region for partner filtering
DEFAULT_REGION = "RU"


class PartnerProgram:
    """Single partner program from admitad."""

    def __init__(self, data: Dict):
        self.id = str(data.get("id", ""))
        self.name = data.get("name", "")
        self.slug = data.get("slug", "")
        self.image = (
            data.get("image") or
            data.get("image_url") or
            data.get("logo") or
            data.get("brand_logo") or
            ""
        )
        self.description = data.get("description", "")
        self.ad_text = data.get("ad_text", "")
        self.goto_link = data.get("goto_link", "")
        self.site_url = data.get("site_url", "")
        self.category = data.get("category", "")
        self.category_name = data.get("category_name", "")
        self.allowed_regions = data.get("allowed_regions", [])
        self.rating = data.get("rating", "")
        self.raw = data

    def has_region(self, region: str = DEFAULT_REGION) -> bool:
        """Check if program is available in a region."""
        if not self.allowed_regions:
            return True
        region_upper = region.upper()
        if "00" in self.allowed_regions:
            return True
        return region_upper in [r.upper() for r in self.allowed_regions]

    def has_category(self, category: str) -> bool:
        """Check if program belongs to a category."""
        cat_lower = category.lower()
        if cat_lower == self.category.lower():
            return True
        if cat_lower in self.category_name.lower():
            return True
        return False

    def matches_text(self, text: str) -> bool:
        """Check if text contains keywords related to this program."""
        text_lower = text.lower()
        name_words = [w.lower() for w in self.name.split() if len(w) > 3]
        for word in name_words:
            if word in text_lower:
                return True
        cat_words = [w.lower() for w in self.category_name.split() if len(w) > 3]
        for word in cat_words:
            if word in text_lower:
                return True
        if self.site_url:
            domain = urlparse(self.site_url).netloc.replace("www.", "")
            if domain and domain in text_lower:
                return True
        return False

    def get_search_url(self, query: str) -> str:
        """Get a search URL for this partner, preserving the goto_link tracking."""
        if not self.goto_link:
            return ""
        if not query:
            return self.goto_link

        try:
            parsed = urlparse(self.goto_link)
            params = parse_qs(parsed.query)

            if "ulp" in params and params["ulp"]:
                original_ulp = params["ulp"][0]
                search_url = self._build_search_url(original_ulp, query)
                if search_url != original_ulp:
                    new_params = {}
                    for k, v_list in params.items():
                        if k == "ulp":
                            new_params[k] = search_url
                        else:
                            new_params[k] = v_list[0] if len(v_list) == 1 else v_list
                    new_query = urlencode(new_params, doseq=True)
                    return urlunparse(parsed._replace(query=new_query))
        except Exception as e:
            logger.debug(f"Error modifying goto_link for search: {e}")

        return self.goto_link

    def _build_search_url(self, original_ulp: str, query: str) -> str:
        """Build a search URL by modifying the original redirect URL."""
        site_url = self.site_url.rstrip("/")
        query_encoded = quote_plus(query)

        search_patterns = {
            "rossko.ru": f"{site_url}/search?text={query_encoded}",
            "autopiter.ru": f"{site_url}/search?querystr={query_encoded}",
            "autopiter.kz": f"{site_url}/search?querystr={query_encoded}",
            "aliexpress.ru": f"{site_url}/wholesale?SearchText={query_encoded}",
            "aliexpress.com": f"{site_url}/wholesale?SearchText={query_encoded}",
        }

        for domain, pattern in search_patterns.items():
            if domain in self.site_url:
                return quote_plus(pattern)

        return original_ulp

    def format_link(self, with_description: bool = True) -> str:
        """Format this partner's link for display. goto_link used as-is!"""
        if not self.goto_link:
            return ""
        if with_description and self.category_name:
            return f"{self.name} ({self.category_name}): {self.goto_link}"
        return f"{self.name}: {self.goto_link}"

    def format_link_with_search(self, query: str) -> str:
        """Format partner link with search query."""
        search_url = self.get_search_url(query)
        if not search_url:
            return ""
        desc = self._get_category_description()
        if desc:
            return f"{self.name} ({desc}): {search_url}"
        return f"{self.name}: {search_url}"

    def _get_category_description(self) -> str:
        """Get a user-friendly description for this partner's category."""
        descriptions = {
            "autoparts": "профессиональный подбор запчастей",
            "tires": "шины и диски",
            "tools": "автоинструменты",
            "autoinsurance": "автострахование",
            "checkauto": "проверка авто",
            "autorent": "аренда авто",
            "coupons": "скидки и промокоды",
            "other": "рекомендую",
        }
        return descriptions.get(self.category, self.category_name or "рекомендую")


class NastyaPartnerManager:
    """Manages partner links for Nastya Bot v3.0.

    Downloads admitad_ads.json from remote URL.
    Uses goto_link EXACTLY as-is — NO subid additions!
    Nastya weaves partner links into her conversation style naturally.
    """

    def __init__(self):
        self._admitad_programs: List[PartnerProgram] = []
        self._site_map: Dict[str, PartnerProgram] = {}
        self._loaded = False
        self._last_load_time: float = 0

    async def load_admitad_async(self) -> int:
        """Load admitad programs — try remote first, then local cache."""
        count = await self._load_from_remote()
        if count > 0:
            return count
        return self._load_from_local()

    async def _load_from_remote(self) -> int:
        """Download admitad_ads.json from GitHub."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(ADMITAD_JSON_URL)
                if response.status_code == 200:
                    data = response.json()
                    count = self._parse_programs(data)
                    if count > 0:
                        self._save_cache(data)
                        self._loaded = True
                        self._last_load_time = time.time()
                        logger.info(f"Loaded {count} admitad partner programs from remote URL")
                        return count
        except Exception as e:
            logger.warning(f"Failed to load admitad_ads.json from remote: {e}")
        return 0

    def _load_from_local(self) -> int:
        """Load from local cache."""
        for filepath in [ADMITAD_LOCAL_CACHE, "admitad_ads.json"]:
            path = Path(filepath)
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    count = self._parse_programs(data)
                    self._loaded = True
                    self._last_load_time = time.time()
                    logger.info(f"Loaded {count} admitad partner programs from local: {filepath}")
                    return count
                except Exception as e:
                    logger.error(f"Error loading local admitad cache: {e}")
        logger.info("No admitad_ads.json found — using direct shop links only")
        self._loaded = True
        return 0

    def load_admitad(self, filepath: str = "admitad_ads.json") -> int:
        """Synchronous load from local file."""
        path = Path(filepath)
        if not path.exists():
            path = Path(ADMITAD_LOCAL_CACHE)
        if not path.exists():
            path = Path("admitad_ads.json")

        if not path.exists():
            logger.info(f"No admitad_ads.json found — using direct shop links only")
            self._loaded = True
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = self._parse_programs(data)
            self._loaded = True
            self._last_load_time = time.time()
            logger.info(f"Loaded {count} admitad partner programs for Nastya")
            return count
        except Exception as e:
            logger.error(f"Error loading admitad programs: {e}")
            self._loaded = True
            return 0

    def _parse_programs(self, data) -> int:
        """Parse programs from JSON data."""
        self._admitad_programs = []
        self._site_map = {}

        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("programs", data.get("items", data.get("results", [])))
            if not isinstance(items, list):
                items = []

        for item in items:
            prog = PartnerProgram(item)
            if prog.goto_link:
                self._admitad_programs.append(prog)
                if prog.site_url:
                    domain = urlparse(prog.site_url).netloc.replace("www.", "")
                    self._site_map[domain] = prog

        return len(self._admitad_programs)

    def _save_cache(self, data) -> None:
        """Save data to local cache."""
        try:
            cache_path = Path(ADMITAD_LOCAL_CACHE)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save admitad cache: {e}")

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_admitad()

    async def maybe_refresh(self) -> None:
        """Refresh from remote if enough time has passed."""
        if not self._loaded or (time.time() - self._last_load_time > ADMITAD_REFRESH_INTERVAL):
            await self.load_admitad_async()

    def detect_categories(self, text: str) -> List[str]:
        """Detect which partner categories match the user's message."""
        text_lower = text.lower()
        matched = []

        category_keywords = {
            "autoparts": [
                "запчаст", "деталь", "артикул", "купить запчас", "купить детал",
                "оригинал", "аналог", "замена", "подбор", "номер детал",
                "oem", "оригинальн", "поиск запчас", "найти запчас",
                "фильтр", "колодки", "свечи", "ремень", "прокладк",
                "сальник", "подшипник", "амортизатор", "реле", "датчик",
                "масло", "антифриз", "тормозн", "двигател", "bmw", "бмв",
                "сто", "ремонт", "обслуживание", "то ", "регламент",
                "росско", "rossko", "автопитер", "autopiter",
            ],
            "shopping": [
                "купить", "заказать", "цена", "стоимость", "подешевле",
                "где купить", "найти", "поищи", "нужен", "хочу",
                "вариант", "выбрать", "подобрать", "лучший", "топ",
                "скидк", "акци", "промокод", "aliexpress", "алиэкспресс",
            ],
            "fashion": [
                "одежд", "платье", "сумоч", "обувь", "кроссов", "куртк",
                "пальто", "брюк", "джинс", "футбол", "свитер",
                "маникюр", "косметик", "макияж", "парфюм", "духи",
            ],
            "electronics": [
                "айфон", "iphone", "телефон", "смартфон", "ноутбук",
                "планшет", "наушник", "часы", "apple watch", "гаджет",
                "техник", "компьютер", "монитор",
            ],
            "travel": [
                "путешеств", "тур", "авиа", "билет", "самолёт",
                "отель", "гостиниц", "авиасейлс", "aviasales",
                "аренда авто", "прокат авто", "discovercars",
            ],
        }

        for cat_key, keywords in category_keywords.items():
            for kw in keywords:
                if kw in text_lower:
                    matched.append(cat_key)
                    break
        return matched

    def find_matching_programs(self, text: str, region: str = DEFAULT_REGION) -> List[PartnerProgram]:
        """Find programs matching the text."""
        self.ensure_loaded()
        matches = []
        for p in self._admitad_programs:
            if p.has_region(region) and p.matches_text(text):
                matches.append(p)
        return matches

    def get_programs_for_categories(self, categories: List[str], region: str = DEFAULT_REGION) -> List[PartnerProgram]:
        """Get programs matching given categories."""
        self.ensure_loaded()
        results = []
        seen = set()
        for cat in categories:
            for p in self._admitad_programs:
                if p.has_region(region) and p.has_category(cat) and p.id not in seen:
                    results.append(p)
                    seen.add(p.id)
        return results

    def generate_partner_context(self, text: str, max_programs: int = 4, region: str = DEFAULT_REGION) -> str:
        """Generate partner context for AI system prompt.

        v3: Uses goto_link from admitad_ads.json EXACTLY as-is!
        No subid additions — the link is ready to use!
        """
        self.ensure_loaded()

        categories = self.detect_categories(text)

        # Find matching programs
        matches = self.find_matching_programs(text, region)
        if not matches and categories:
            matches = self.get_programs_for_categories(categories, region)

        if not matches:
            return ""

        # Check for article number
        article_match = re.search(r'\b([A-Z0-9]{4,}[-/]?[A-Z0-9]*)\b', text.upper())
        article = article_match.group(1) if article_match else ""

        # Build context lines
        cat_labels = [c for c in categories] if categories else ["разное"]
        lines = [
            f"Партнёрские ссылки для темы: {', '.join(cat_labels)}",
            "Вставь эти ссылки ЕСТЕСТВЕННО в ответ — как личная рекомендация! Не как рекламу!",
        ]

        for p in matches[:max_programs]:
            if article:
                link = p.format_link_with_search(article)
            else:
                link = p.format_link(with_description=True)
            if link:
                lines.append(f"- {link}")

        if not lines:
            return ""

        lines.append("")
        lines.append("ВАЖНО: Ссылки выше — ПАРТНЁРСКИЕ (goto_link из admitad_ads.json). Используй их КАК ЕСТЬ, ничего не добавляй и не меняй!")

        return "\n".join(lines)

    def get_auto_parts_links(self, query: str, region: str = DEFAULT_REGION) -> str:
        """Get auto parts shop links specifically (for BMW-related queries)."""
        self.ensure_loaded()

        parts_programs = self.get_by_category("autoparts", region)
        if not parts_programs:
            parts_programs = self.find_matching_programs(query, region)

        article_match = re.search(r'\b([A-Z0-9]{4,}[-/]?[A-Z0-9]*)\b', query.upper())
        article = article_match.group(1) if article_match else query.strip()

        if not parts_programs:
            return ""

        lines = ["Ссылки на магазины автозапчастей (вставь естественно в ответ с описанием!):"]
        for p in parts_programs[:4]:
            link = p.format_link_with_search(article)
            if link:
                lines.append(f"- {link}")

        lines.append("Настя водит M3 и знает где покупать запчасти! Упомяни это естественно.")
        lines.append("ВАЖНО: Ссылки — ПАРТНЁРСКИЕ (goto_link из admitad_ads.json). Используй КАК ЕСТЬ!")
        return "\n".join(lines)

    def get_by_category(self, category: str, region: str = DEFAULT_REGION) -> List[PartnerProgram]:
        """Get programs in a specific category and region."""
        self.ensure_loaded()
        return [p for p in self._admitad_programs if p.has_category(category) and p.has_region(region)]

    def get_by_site(self, site_url: str) -> Optional[PartnerProgram]:
        """Find a partner program by its site URL."""
        self.ensure_loaded()
        domain = urlparse(site_url).netloc.replace("www.", "") if site_url else ""
        return self._site_map.get(domain)


# ── Global instance ────────────────────────────────────────────────────────────

nastya_partner_manager = NastyaPartnerManager()
