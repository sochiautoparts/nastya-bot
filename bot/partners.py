"""Partner Links System for Nastya Bot v3.0.

Nastya is a lifestyle blogger - she recommends products naturally in conversation.
Partner links are integrated into her responses when relevant topics come up.

v3.0 KEY CHANGES:
- Downloads admitad_ads.json from remote GitHub URL (updateable file!)
- Auto-refreshes every 6 hours
- Uses goto_link EXACTLY as provided - NO subid additions!
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
            return f"🔧 {self.name} ({self.category_name}) — {self.goto_link}"
        return f"🔧 {self.name} — {self.goto_link}"

    def format_link_with_search(self, query: str) -> str:
        """Format partner link with search query."""
        search_url = self.get_search_url(query)
        if not search_url:
            return ""
        desc = self._get_category_description()
        if desc:
            return f"🔧 {self.name} ({desc}) — {search_url}"
        return f"🔧 {self.name} — {search_url}"

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
    Uses goto_link EXACTLY as-is - NO subid additions!
    Nastya weaves partner links into her conversation style naturally.
    """

    def __init__(self):
        self._admitad_programs: List[PartnerProgram] = []
        self._site_map: Dict[str, PartnerProgram] = {}
        self._loaded = False
        self._last_load_time: float = 0

    async def load_admitad_async(self) -> int:
        """Load admitad programs - try remote first, then local cache."""
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
        logger.info("No admitad_ads.json found - using direct shop links only")
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
            logger.info(f"No admitad_ads.json found - using direct shop links only")
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

    # ── Category keyword definitions (used by detect_categories and get_all_relevant_links) ──

    CATEGORY_KEYWORDS = {
        "autoparts": [
            "запчаст", "деталь", "артикул", "купить запчас", "купить детал",
            "оригинал", "аналог", "замена", "подбор", "номер детал",
            "oem", "оригинальн", "поиск запчас", "найти запчас",
            "фильтр", "колодки", "свечи", "ремень", "прокладк",
            "сальник", "подшипник", "амортизатор", "реле", "датчик",
            "масло", "антифриз", "тормозн", "двигател", "bmw", "бмв",
            "сто", "ремонт", "обслуживание", "то ", "регламент",
            "росско", "rossko", "автопитер", "autopiter", "avtoall", "автоолл",
            "m3", "m4", "m5", "x5", "x3", "x6",  # BMW models
            "автозапч", "автомагазин", "автотовар",
            "турбо", "кузов", "ходов", "рулев", "сцеплен", "коробк",
            "авто", "машина", "тачк", "движок", "ходовая",
        ],
        "tires": [
            "шины", "шина", "диски", "колёс", "колеса", "резин",
            "зимн", "летн", "всесезон", "покрышк", "r16", "r17", "r18", "r19",
        ],
        "tools": [
            "инструмент", "ключ", "набор инструмент", "домкрат",
            "балонник", "съёмник", "ключ головк", "torx",
        ],
        "autoinsurance": [
            "осаго", "каско", "страхов", "полис", "договор страх",
        ],
        "checkauto": [
            "проверк", "вин", "vin", "история авто", "проверить авто",
            "пробег", "аварий", "залог", "ограничен",
        ],
        "autorent": [
            "аренда авто", "прокат авто", "арендовать авто", "rentcar",
            "discovercars", "автопрокат", "взять машину",
        ],
        "shopping": [
            "купить", "заказать", "цена", "стоимость", "подешевле",
            "где купить", "найти", "поищи", "нужен", "хочу",
            "вариант", "выбрать", "подобрать", "лучший", "топ",
            "скидк", "акци", "промокод", "aliexpress", "алиэкспресс",
        ],
        "coupons": [
            "промокод", "купон", "скидк", "акци", "кэшбэк", "cashback",
            "бонус",
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
        ],
    }

    # ── Category groups: related categories to include together ──

    CATEGORY_GROUPS = {
        "auto": ["autoparts", "tires", "tools", "autoinsurance", "checkauto"],
        "shopping": ["shopping", "coupons"],
        "travel": ["travel", "autorent"],
    }

    # ── Ася's specific auto partners (always include for auto topics) ──

    AUTO_PARTNER_SITES = ["rossko.ru", "autopiter.ru", "avtoall.ru"]

    def detect_categories(self, text: str) -> List[str]:
        """Detect which partner categories match the user's message."""
        text_lower = text.lower()
        matched = []

        for cat_key, keywords in self.CATEGORY_KEYWORDS.items():
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

    def get_all_relevant_links(self, text: str, max_programs: int = 5, region: str = DEFAULT_REGION) -> List[Dict[str, str]]:
        """Get ALL relevant partner links for a given text, expanding categories.

        Detects ALL relevant categories from the text and expands them
        using CATEGORY_GROUPS. For auto-related queries, includes autoparts +
        tires + tools + insurance + checkauto. For shopping, includes coupons.
        For travel, includes autorent.
        Also ensures Ася's auto partners (Росско, Autopiter, AvtoALL) are
        included for car/BMW-related queries.

        Returns a list of dicts: {name, url, description, category_name}
        """
        self.ensure_loaded()

        detected = self.detect_categories(text)
        if not detected:
            return []

        # Expand detected categories via CATEGORY_GROUPS
        expanded_categories = set(detected)
        for cat in detected:
            for group_name, group_cats in self.CATEGORY_GROUPS.items():
                if cat in group_cats:
                    expanded_categories.update(group_cats)
                # Also: if any keyword from a group category matched, include the whole group
                # E.g. if "autoparts" was detected, include the full "auto" group

        # Special case: if any auto-related keyword is present, include all auto categories
        auto_cats = {"autoparts", "tires", "tools", "autoinsurance", "checkauto", "autorent"}
        if expanded_categories & auto_cats:
            expanded_categories.update(auto_cats)

        # Get programs for all expanded categories
        programs = self.get_programs_for_categories(list(expanded_categories), region)

        # If car/BMW-related, ensure Ася's auto partners are present
        text_lower = text.lower()
        auto_keywords = ["bmw", "бмв", "m3", "m4", "m5", "x5", "авто", "машина", "тачк",
                         "запчаст", "ремонт", "двигател", "масло", "фильтр", "колодки",
                         "росско", "автопитер", "avtoall", "sto", "сто"]
        is_auto_topic = any(kw in text_lower for kw in auto_keywords)
        if is_auto_topic:
            # Ensure Ася's specific auto partners are included
            seen_ids = {p.id for p in programs}
            for site in self.AUTO_PARTNER_SITES:
                prog = self._site_map.get(site)
                if prog and prog.id not in seen_ids and prog.has_region(region):
                    programs.append(prog)
                    seen_ids.add(prog.id)

        # Deduplicate and shuffle for variety
        random.shuffle(programs)

        # Build result list
        results = []
        seen_names = set()
        for p in programs[:max_programs * 2]:  # Over-select to allow dedup
            if p.name not in seen_names and p.goto_link:
                desc = p._get_category_description()
                results.append({
                    "name": p.name,
                    "url": p.goto_link,  # Use EXACTLY as-is!
                    "description": desc,
                    "category_name": p.category_name,
                    "category": p.category,
                })
                seen_names.add(p.name)
            if len(results) >= max_programs:
                break

        return results

    def generate_partner_context(self, text: str, max_programs: int = 4, region: str = DEFAULT_REGION) -> str:
        """Generate partner context for AI system prompt.

        v4: Uses get_all_relevant_links for cross-category partner links.
        goto_link used EXACTLY as-is - no modifications!
        Format: 🔧 Name — URL (simple, NOT HTML)
        """
        self.ensure_loaded()

        categories = self.detect_categories(text)

        # Use get_all_relevant_links for expanded category coverage
        all_links = self.get_all_relevant_links(text, max_programs=max_programs, region=region)

        # Also check for direct text matches (existing behavior)
        matches = self.find_matching_programs(text, region)

        # Merge: prefer all_links, supplement with direct matches
        seen_urls = {l["url"] for l in all_links}
        for p in matches:
            if p.goto_link and p.goto_link not in seen_urls:
                all_links.append({
                    "name": p.name,
                    "url": p.goto_link,
                    "description": p._get_category_description(),
                    "category_name": p.category_name,
                    "category": p.category,
                })
                seen_urls.add(p.goto_link)

        if not all_links:
            # Fallback to category-based programs
            if categories:
                cat_programs = self.get_programs_for_categories(categories, region)
                for p in cat_programs[:max_programs]:
                    if p.goto_link:
                        all_links.append({
                            "name": p.name,
                            "url": p.goto_link,
                            "description": p._get_category_description(),
                            "category_name": p.category_name,
                            "category": p.category,
                        })

        if not all_links:
            return ""

        # Check for article number
        article_match = re.search(r'\b([A-Z0-9]{4,}[-/]?[A-Z0-9]*)\b', text.upper())
        article = article_match.group(1) if article_match else ""

        # Build context lines
        cat_labels = [c for c in categories] if categories else ["разное"]
        lines = [
            f"Партнёрские ссылки для темы: {', '.join(cat_labels)}",
            "Вставь эти ссылки ЕСТЕСТВЕННО в ответ - как личная рекомендация! Не как рекламу!",
            "Формат ссылок: 🔧 Имя — URL",
        ]

        for link_data in all_links[:max_programs]:
            name = link_data["name"]
            url = link_data["url"]
            desc = link_data.get("description", "")
            if desc and desc != "рекомендую":
                lines.append(f"- 🔧 {name} ({desc}) — {url}")
            else:
                lines.append(f"- 🔧 {name} — {url}")

        if len(lines) <= 3:
            return ""

        lines.append("")
        lines.append("ВАЖНО: Ссылки выше - ПАРТНЁРСКИЕ (goto_link из admitad_ads.json). Используй их КАК ЕСТЬ, ничего не добавляй и не меняй!")
        lines.append("Формат в ответе: 🔧 Имя — URL. НЕ используй HTML!")
        lines.append("Эти ссылки можно также естественно использовать в постах канала.")

        return "\n".join(lines)

    def get_auto_parts_links(self, query: str, region: str = DEFAULT_REGION) -> str:
        """Get auto parts shop links specifically (for BMW-related queries).

        v4: Now includes ALL auto-related categories (autoparts, tires, tools,
        insurance, checkauto) and ensures Ася's partners (Росско, Autopiter,
        AvtoALL) are always included.
        Format: 🔧 Name — URL
        """
        self.ensure_loaded()

        # Get programs from ALL auto-related categories
        auto_cats = ["autoparts", "tires", "tools", "autoinsurance", "checkauto"]
        parts_programs = self.get_programs_for_categories(auto_cats, region)

        if not parts_programs:
            parts_programs = self.find_matching_programs(query, region)

        # Ensure Ася's specific auto partners are always present
        seen_ids = {p.id for p in parts_programs}
        for site in self.AUTO_PARTNER_SITES:
            prog = self._site_map.get(site)
            if prog and prog.id not in seen_ids and prog.has_region(region):
                parts_programs.append(prog)
                seen_ids.add(prog.id)

        article_match = re.search(r'\b([A-Z0-9]{4,}[-/]?[A-Z0-9]*)\b', query.upper())
        article = article_match.group(1) if article_match else query.strip()

        if not parts_programs:
            return ""

        lines = ["Ссылки на магазины автозапчастей и автосервисы (вставь естественно в ответ!):"]
        lines.append("Формат: 🔧 Имя — URL")
        for p in parts_programs[:5]:
            link = p.format_link_with_search(article)
            if link:
                lines.append(f"- {link}")

        lines.append("Настя водит M3 и знает где покупать запчасти! Упомяни это естественно.")
        lines.append("ВАЖНО: Ссылки - ПАРТНЁРСКИЕ (goto_link из admitad_ads.json). Используй КАК ЕСТЬ!")
        lines.append("Формат в ответе: 🔧 Имя — URL. НЕ используй HTML!")
        return "\n".join(lines)

    # ── Category cycling state for smart channel posts ──
    _category_cycle_index: int = 0
    _partner_post_cycle_categories = ["autoparts", "tires", "coupons", "shopping",
                                       "autoparts", "tools", "fashion", "electronics",
                                       "autoparts", "autoinsurance", "checkauto", "travel"]

    def get_partner_links_for_post(self, category: str = "", region: str = "RU") -> List[Dict[str, str]]:
        """Get partner links suitable for channel posts.

        v4: Cycles through categories intelligently, includes Ася's auto
        partners, uses image field directly. goto_link used EXACTLY as-is!

        Returns 1-2 links that Настя can naturally include in her posts.
        """
        self.ensure_loaded()
        links = []

        # If no specific category, cycle through categories for variety
        if not category:
            category = self._partner_post_cycle_categories[
                self._category_cycle_index % len(self._partner_post_cycle_categories)
            ]
            self._category_cycle_index += 1

        # Get programs matching category
        programs = self.get_by_category(category, region)

        # For auto-related categories, also ensure Ася's partners are present
        auto_cats = {"autoparts", "tires", "tools", "autoinsurance", "checkauto"}
        if category in auto_cats or not programs:
            seen_ids = {p.id for p in programs}
            for site in self.AUTO_PARTNER_SITES:
                prog = self._site_map.get(site)
                if prog and prog.id not in seen_ids and prog.has_region(region):
                    programs.append(prog)
                    seen_ids.add(prog.id)

        if not programs:
            # Fallback: get programs from all auto categories + coupons
            auto_cats_list = ["autoparts", "tires", "coupons"]
            for cat in auto_cats_list:
                progs = self.get_by_category(cat, region)
                programs.extend(progs)

        if not programs:
            # Just get any programs for the region
            programs = [p for p in self._admitad_programs if p.has_region(region)]

        # Pick 1-2 relevant programs
        selected = random.sample(programs, min(2, len(programs))) if programs else []

        for p in selected:
            desc = p._get_category_description()
            links.append({
                "name": p.name,
                "url": p.goto_link,  # Ready-to-use link from admitad!
                "description": desc,
                "category_name": p.category_name,
                "category": p.category,
                "image": p.image,  # Include image URL from PartnerProgram!
            })

        return links

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
