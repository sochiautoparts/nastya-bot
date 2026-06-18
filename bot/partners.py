"""Partner Links System for Nastya Bot v4.0.

Nastya is a lifestyle blogger - she recommends products naturally in conversation.
Partner links are integrated into her responses when relevant topics come up.

v4.0 KEY CHANGES (new source):
- Downloads partners.json from https://sochiautoparts.ru/partners.json (updateable file!)
- Reads the `campaigns` array (new format) with fallback to legacy keys
- Reads the `regions` field (new) with fallback to `allowed_regions`
- Extracts the `logo` field for partner post images
- Maps the human-readable `categories` array to internal category keys via a
  reliable domain-based mapping (DOMAIN_CATEGORY_MAP), because the source
  `categories` are generic Russian strings ("Интернет-магазины" etc.)
- Auto-refreshes every 6 hours
- Uses goto_link EXACTLY as provided - NO subid additions!
- Regional filtering by the `regions` field ("00" = worldwide)
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

# Remote partners.json URL (updateable file!) — sochiautoparts.ru source
PARTNERS_JSON_URL = "https://sochiautoparts.ru/partners.json"
PARTNERS_LOCAL_CACHE = "data/partners.json"
PARTNERS_REFRESH_INTERVAL = 6 * 3600  # Refresh every 6 hours

# Default region for partner filtering
DEFAULT_REGION = "RU"

# ── Domain → internal category mapping ────────────────────────────────────────
# Maps partner site domains to internal category keys used by the bot.
# This is the most reliable mapping since partners.json `categories` are
# human-readable Russian strings that are often too generic
# (e.g. "Интернет-магазины" appears on almost every campaign).
DOMAIN_CATEGORY_MAP: Dict[str, List[str]] = {
    "rossko.ru": ["autoparts"],
    "autopiter.ru": ["autoparts"],
    "autopiter.kz": ["autoparts"],
    "avtoall.ru": ["autoparts", "tools"],
    "mirdvornikov.ru": ["autoparts"],
    "lukoil-shop.com": ["autoparts"],
    "hyperauto.ru": ["autoparts", "checkauto"],
    "koleso.ru": ["autoparts", "tires"],
    "euro-diski.ru": ["tires"],
    "bs-tyres.ru": ["tires"],
    "avtocod.ru": ["checkauto"],
    "petrolplus.ru": ["autoinsurance"],
    "localrent.com": ["autorent", "travel"],
    "discovercars.com": ["autorent", "travel"],
    "aviasales.ru": ["travel"],
    "aliexpress.ru": ["shopping", "coupons"],
    "aliexpress.com": ["shopping", "coupons"],
    "alibaba.com": ["shopping", "coupons"],
    "geekbuying.com": ["shopping", "electronics"],
    "xistore.by": ["shopping", "electronics"],
    "globaldrive.ru": ["shopping", "coupons"],
    "raketacn.ru": ["shopping", "coupons"],
    "globalyo.com": ["other"],
    "skyeng.ru": ["other"],
    "real-avto.com": ["other", "autoparts"],
}

# Human-readable category (from partners.json `categories`) → internal keys.
# Used as a fallback when the domain is not in DOMAIN_CATEGORY_MAP.
CATEGORY_NAME_KEYWORDS: Dict[str, List[str]] = {
    "autoparts": ["товары для авто", "автомобили и мотоциклы"],
    "tires": [],
    "tools": [],
    "autoinsurance": [],
    "checkauto": ["авто"],
    "autorent": ["аренда машин"],
    "travel": ["билеты на самолеты", "туризм, путешествия"],
    "shopping": ["маркетплейс", "интернет-магазины"],
    "electronics": ["электроника и бытовая техника"],
    "fashion": ["одежда, обувь, аксессуары", "косметика, гигиена, аптеки"],
    "coupons": [],
    "other": [],
}


class PartnerProgram:
    """Single partner program from partners.json."""

    def __init__(self, data: Dict):
        self.id = str(data.get("id", data.get("name", "")))
        self.name = data.get("name", "")
        self.slug = data.get("slug", "")
        # Logo / image: the new source uses the `logo` field
        self.image = (
            data.get("image") or
            data.get("image_url") or
            data.get("logo") or
            data.get("brand_logo") or
            data.get("logo_url") or
            ""
        )
        self.logo_url = self.image
        self.image_url = self.image
        self.description = data.get("description", "")
        self.ad_text = data.get("ad_text", "")
        self.goto_link = data.get("goto_link", "")
        self.site_url = data.get("site_url", "")
        # Source `categories` array (human-readable Russian strings)
        self.categories = data.get("categories", [])
        self.category = data.get("category", "")
        self.category_name = data.get("category_name", "")
        # Source uses `regions` field (with legacy `allowed_regions` fallback)
        self.allowed_regions = data.get("regions", data.get("allowed_regions", []))
        self.rating = data.get("rating", "")
        self.raw = data
        # Map to internal category keys (autoparts, tires, tools, etc.)
        self._internal_categories: set = self._compute_internal_categories()
        if not self.category:
            self.category = next(iter(sorted(self._internal_categories)), "other")
        if not self.category_name:
            self.category_name = self._get_category_description()

    # ── Internal category mapping ──────────────────────────────────────────

    def _compute_internal_categories(self) -> set:
        """Determine internal category keys from domain + source categories.

        Domain-based mapping is primary (most reliable). The human-readable
        `categories` array from partners.json is used as a fallback.
        """
        cats: set = set()

        # 1. Domain-based mapping (most reliable)
        if self.site_url:
            domain = (
                urlparse(self.site_url).netloc.replace("www.", "").lower().rstrip("/")
            )
            if domain in DOMAIN_CATEGORY_MAP:
                cats.update(DOMAIN_CATEGORY_MAP[domain])

        # 2. Category-name keyword mapping (fallback / supplement)
        if not cats:
            combined = " ".join(self.categories).lower()
            for internal_cat, keywords in CATEGORY_NAME_KEYWORDS.items():
                if any(kw in combined for kw in keywords):
                    cats.add(internal_cat)

        # 3. Legacy explicit category field
        if self.category and self.category != "":
            cats.add(self.category)

        # 4. Default fallback
        if not cats:
            cats.add("other")

        return cats

    def has_region(self, region: str = DEFAULT_REGION) -> bool:
        """Check if program is available in a region.

        Empty allowed_regions = available everywhere.
        "00" in allowed_regions = worldwide.
        """
        if not self.allowed_regions:
            return True
        region_upper = region.upper()
        if "00" in self.allowed_regions:
            return True
        return region_upper in [r.upper() for r in self.allowed_regions]

    def has_category(self, category: str) -> bool:
        """Check if program belongs to a category.

        Checks the primary internal category, the full set of internal
        categories, and the human-readable category name.
        """
        cat_lower = category.lower()
        if cat_lower == self.category.lower():
            return True
        if cat_lower in self._internal_categories:
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
            "exist.ru": f"{site_url}/Price/?p={query_encoded}",
            "emex.ru": f"{site_url}/products?search={query_encoded}",
            "autodoc.ru": f"{site_url}/search?keyword={query_encoded}",
            "zzap.ru": f"{site_url}/search/?q={query_encoded}",
            "avtoall.ru": f"{site_url}/search/?q={query_encoded}",
            "aliexpress.ru": f"{site_url}/wholesale?SearchText={query_encoded}",
            "aliexpress.com": f"{site_url}/wholesale?SearchText={query_encoded}",
            "hyperauto.ru": f"{site_url}/search/?q={query_encoded}",
            "euro-diski.ru": f"{site_url}/search/?q={query_encoded}",
            "bs-tyres.ru": f"{site_url}/search/?q={query_encoded}",
            "koleso.ru": f"{site_url}/search/?q={query_encoded}",
            "avtocod.ru": f"{site_url}/search/?q={query_encoded}",
            "petrolplus.ru": f"{site_url}/search/?q={query_encoded}",
            "globaldrive.ru": f"{site_url}/search/?q={query_encoded}",
            "mirdvornikov.ru": f"{site_url}/search/?q={query_encoded}",
            "lukoil-shop.com": f"{site_url}/search/?q={query_encoded}",
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
            "travel": "путешествия и билеты",
            "shopping": "покупки",
            "electronics": "электроника и техника",
            "fashion": "одежда и красота",
            "coupons": "скидки и промокоды",
            "other": "рекомендую",
        }
        return descriptions.get(self.category, self.category_name or "рекомендую")


class NastyaPartnerManager:
    """Manages partner links for Nastya Bot v4.0.

    Downloads partners.json from sochiautoparts.ru.
    Uses goto_link EXACTLY as-is - NO subid additions!
    Nastya weaves partner links into her conversation style naturally.
    """

    def __init__(self):
        self._admitad_programs: List[PartnerProgram] = []
        self._site_map: Dict[str, PartnerProgram] = {}
        self._loaded = False
        self._last_load_time: float = 0
        # ── v4.2 performance caches (rebuilt only when partners reload) ──
        # Pre-built pool per region so generate_conversation_partner_context
        # and get_all_partners_pool don't re-iterate on every chat message.
        self._pool_cache: Dict[str, List[Dict[str, str]]] = {}
        # Pre-built by-category index for _build_diverse_pool round-robin
        self._by_category_cache: Dict[str, Dict[str, List[PartnerProgram]]] = {}
        # ── v4.2 dedup: track recently recommended partner ids per chat ──
        # {chat_key: deque([id, id, ...], maxlen=8)} — avoids repeating the
        # same partner across consecutive messages in the same chat.
        from collections import defaultdict, deque
        self._recent_recommendations: Dict[str, "deque"] = defaultdict(
            lambda: deque(maxlen=8)
        )
        self._defaultdeque = deque

    async def load_admitad_async(self) -> int:
        """Load partner programs - try remote first, then local cache."""
        count = await self._load_from_remote()
        if count > 0:
            return count
        return self._load_from_local()

    async def _load_from_remote(self) -> int:
        """Download partners.json from sochiautoparts.ru."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(PARTNERS_JSON_URL)
                if response.status_code == 200:
                    data = response.json()
                    count = self._parse_programs(data)
                    if count > 0:
                        self._save_cache(data)
                        self._loaded = True
                        self._last_load_time = time.time()
                        logger.info(f"Loaded {count} partner programs from remote URL")
                        return count
        except Exception as e:
            logger.warning(f"Failed to load partners.json from remote: {e}")
        return 0

    def _load_from_local(self) -> int:
        """Load from local cache."""
        for filepath in [PARTNERS_LOCAL_CACHE, "partners.json"]:
            path = Path(filepath)
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    count = self._parse_programs(data)
                    self._loaded = True
                    self._last_load_time = time.time()
                    logger.info(f"Loaded {count} partner programs from local: {filepath}")
                    return count
                except Exception as e:
                    logger.error(f"Error loading local partners cache: {e}")
        logger.info("No partners.json found - using direct shop links only")
        self._loaded = True
        return 0

    def load_admitad(self, filepath: str = "partners.json") -> int:
        """Synchronous load from local file."""
        path = Path(filepath)
        if not path.exists():
            path = Path("partners.json")

        if not path.exists():
            logger.info(f"No partners.json found - using direct shop links only")
            self._loaded = True
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = self._parse_programs(data)
            self._loaded = True
            self._last_load_time = time.time()
            logger.info(f"Loaded {count} partner programs for Nastya")
            return count
        except Exception as e:
            logger.error(f"Error loading partner programs: {e}")
            self._loaded = True
            return 0

    def _parse_programs(self, data) -> int:
        """Parse programs from JSON data.

        Supports the new partners.json format (`campaigns` array) as well as
        legacy formats (`programs` / `items` / `results` / bare array).
        """
        self._admitad_programs = []
        self._site_map = {}
        # v4.2: invalidate performance caches on reload
        self._pool_cache.clear()
        self._by_category_cache.clear()

        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get(
                "campaigns",
                data.get("programs", data.get("items", data.get("results", []))),
            )
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

    # ── v4.2 cached builders (avoid re-iterating on every message) ──

    def _get_cached_pool(self, region: str) -> List[Dict[str, str]]:
        """Return the region pool, building + caching it on first access."""
        if region in self._pool_cache:
            return self._pool_cache[region]
        pool: List[Dict[str, str]] = []
        seen_ids: set = set()
        for p in self._admitad_programs:
            if not p.has_region(region):
                continue
            if p.id in seen_ids or not p.goto_link:
                continue
            seen_ids.add(p.id)
            pool.append({
                "name": p.name,
                "url": p.goto_link,
                "description": p._get_category_description(),
                "category_name": p.category_name,
                "category": p.category,
                "image": p.image,
                "site_url": p.site_url,
                "id": p.id,
            })
        self._pool_cache[region] = pool
        return pool

    def _get_cached_by_category(
        self, region: str
    ) -> Dict[str, List[PartnerProgram]]:
        """Return {category: [programs]} index for the region, cached."""
        if region in self._by_category_cache:
            return self._by_category_cache[region]
        by_cat: Dict[str, List[PartnerProgram]] = {}
        for p in self._admitad_programs:
            if not p.has_region(region) or not p.goto_link:
                continue
            primary_cat = p.category or "other"
            by_cat.setdefault(primary_cat, []).append(p)
        self._by_category_cache[region] = by_cat
        return by_cat

    def _record_recommendation(self, chat_key: str, partner_id: str) -> None:
        """Record that a partner was recommended in a chat (for dedup)."""
        self._recent_recommendations[chat_key].append(partner_id)

    def _filter_recent(
        self, links: List[Dict[str, str]], chat_key: str
    ) -> List[Dict[str, str]]:
        """Move recently-recommended partners to the end of the list.

        Does not hard-remove them (we may need fallback), but surfaces fresh
        partners first so the bot doesn't repeat the same recommendation in
        consecutive messages of the same chat.
        """
        recent = self._recent_recommendations.get(chat_key)
        if not recent:
            return links
        fresh = [l for l in links if l.get("id") not in recent]
        used = [l for l in links if l.get("id") in recent]
        return fresh + used

    # ── v4.3 Click tracking (utm_source tagging) ───────────────────────────
    #
    # Adds utm_source/utm_medium/utm_campaign to a goto_link so clicks can be
    # attributed to a specific source (chat / group / channel) in admitad
    # analytics. OFF by default — the raw goto_link is the source of truth and
    # is used as-is everywhere. Enable explicitly per call site with
    # with_tracking=True so we only tag links we actually send to users.

    def tag_link(
        self,
        url: str,
        source: str = "chat",
        medium: str = "nastya",
        campaign: str = "bot",
    ) -> str:
        """Append utm_ params to a goto_link for click attribution.

        Preserves any existing query params. Safe for admitad goto_links
        (they forward extra query params to the advertiser).
        """
        if not url:
            return url
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            # Don't overwrite existing utm_ params
            params.setdefault("utm_source", [source])
            params.setdefault("utm_medium", [medium])
            params.setdefault("utm_campaign", [campaign])
            new_query = urlencode(
                {k: v[0] if len(v) == 1 else v for k, v in params.items()},
                doseq=True,
            )
            return urlunparse(parsed._replace(query=new_query))
        except Exception:
            return url

    def tag_links_in_text(
        self,
        text: str,
        source: str = "chat",
        medium: str = "nastya",
        campaign: str = "bot",
    ) -> str:
        """Tag all known partner goto_links found in a text with utm params.

        Scans the text for each loaded partner's goto_link and appends utm_
        tracking. Used as a post-processing step on AI responses before
        sending, so every affiliate link the bot emits is trackable.
        """
        self.ensure_loaded()
        if not text:
            return text
        result = text
        for p in self._admitad_programs:
            if p.goto_link and p.goto_link in result:
                tagged = self.tag_link(p.goto_link, source=source, medium=medium, campaign=campaign)
                if tagged != p.goto_link:
                    result = result.replace(p.goto_link, tagged)
        return result

    def _save_cache(self, data) -> None:
        """Save data to local cache."""
        try:
            cache_path = Path(PARTNERS_LOCAL_CACHE)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save partners cache: {e}")

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_admitad()

    async def maybe_refresh(self) -> None:
        """Refresh from remote if enough time has passed."""
        if not self._loaded or (time.time() - self._last_load_time > PARTNERS_REFRESH_INTERVAL):
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

    # ── Nastya's specific auto partners (always include for auto topics) ──

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
        Also ensures Nastya's auto partners (Росско, Autopiter, AvtoALL) are
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

        # If car/BMW-related, ensure Nastya's auto partners are present
        text_lower = text.lower()
        auto_keywords = ["bmw", "бмв", "m3", "m4", "m5", "x5", "авто", "машина", "тачк",
                         "запчаст", "ремонт", "двигател", "масло", "фильтр", "колодки",
                         "росско", "автопитер", "avtoall", "sto", "сто"]
        is_auto_topic = any(kw in text_lower for kw in auto_keywords)
        if is_auto_topic:
            # Ensure Nastya's specific auto partners are included
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
        lines.append("ВАЖНО: Ссылки выше - ПАРТНЁРСКИЕ (goto_link из partners.json). Используй их КАК ЕСТЬ, ничего не добавляй и не меняй!")
        lines.append("Формат в ответе: 🔧 Имя — URL. НЕ используй HTML!")
        lines.append("Эти ссылки можно также естественно использовать в постах канала.")

        return "\n".join(lines)

    # ── Conversation / comment context (v4.1) ───────────────────────────────
    #
    # The bot should be able to use ALL partner programs in dialogues and
    # group comments, not only when a strict category keyword matches. These
    # methods provide a context-aware partner pool that ALWAYS gives the AI
    # something to work with, so partner links can be woven naturally into
    # any conversation.

    def get_all_partners_pool(
        self, region: str = DEFAULT_REGION, max_programs: int = 25
    ) -> List[Dict[str, str]]:
        """Return ALL partner programs available in the region.

        This is the full pool the bot can draw from in any dialogue or
        comment. Every entry includes name, url (goto_link as-is),
        description, category, and image (logo URL). Used as a fallback
        when generate_partner_context finds no category-specific matches.

        v4.2: Uses a cached pool (rebuilt only when partners reload).
        """
        self.ensure_loaded()
        cached = self._get_cached_pool(region)
        # Return a shallow copy limited to max_programs
        return cached[:max_programs]

    def generate_conversation_partner_context(
        self,
        text: str,
        max_programs: int = 6,
        region: str = DEFAULT_REGION,
        is_group: bool = False,
        chat_key: str = "",
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Generate partner context for dialogues and group comments.

        v4.1: Unlike generate_partner_context (which returns "" when no
        category keyword matches), this method ALWAYS provides partners so
        the bot can use ALL affiliate programs in any conversation context.
        v4.2: Dedup — if chat_key is given, recently-recommended partners
        for that chat are deprioritized so the bot doesn't repeat itself.
        v4.3: history-based context — recent user messages are merged with
        the current text so partner relevance is based on the whole recent
        conversation, not just one message.

        Strategy:
        1. Try context-based matching (existing detect_categories logic)
           on current text + recent history. If matches found -> use those.
        2. If no category match -> provide a DIVERSE pool sampled across
           ALL categories (auto, shopping, travel, electronics, fashion,
           coupons, etc.) so the bot has variety to draw from.
        3. For group comments, keep the pool smaller (3-4) since comments
           must be short; for private chat provide a larger pool (6).

        goto_link used EXACTLY as-is - no modifications!
        Format: 🔧 Name — URL (simple, NOT HTML)
        """
        self.ensure_loaded()

        # v4.3: build a combined text from recent user history + current text
        # so category detection considers the whole recent conversation.
        combined_text = text
        if history:
            recent_user_msgs = [
                m.get("content", "")
                for m in history[-8:]
                if m.get("role") == "user" and m.get("content")
            ]
            if recent_user_msgs:
                combined_text = " ".join(recent_user_msgs[-4:]) + " " + text

        # 1. Try context-based matching first (most relevant)
        context_links = self.get_all_relevant_links(combined_text, max_programs=max_programs, region=region)

        # Also add direct text matches (on current text for freshness)
        matches = self.find_matching_programs(combined_text, region)
        seen_urls = {l["url"] for l in context_links}
        for p in matches:
            if p.goto_link and p.goto_link not in seen_urls:
                context_links.append({
                    "name": p.name,
                    "url": p.goto_link,
                    "description": p._get_category_description(),
                    "category_name": p.category_name,
                    "category": p.category,
                    "id": p.id,
                })
                seen_urls.add(p.goto_link)

        # 2. If no context matches -> build a DIVERSE pool from all categories
        if not context_links:
            # For group comments, use a smaller pool (comments are short)
            pool_size = 3 if is_group else max_programs
            context_links = self._build_diverse_pool(pool_size, region)

        # Still empty (no partners at all)?
        if not context_links:
            return ""

        # v4.2: dedup — surface fresh partners first for this chat
        if chat_key:
            context_links = self._filter_recent(context_links, chat_key)

        # Limit final count
        effective_max = 4 if is_group else max_programs
        context_links = context_links[:effective_max]

        # v4.2: record recommended partner ids for future dedup
        if chat_key:
            for l in context_links:
                pid = l.get("id")
                if pid:
                    self._record_recommendation(chat_key, pid)

        # Check for article number (for search-specific links)
        article_match = re.search(r'\b([A-Z0-9]{4,}[-/]?[A-Z0-9]*)\b', text.upper())
        article = article_match.group(1) if article_match else ""

        categories = self.detect_categories(text)
        cat_labels = [c for c in categories] if categories else ["разное"]

        if is_group:
            intro = (
                f"Партнёрские ссылки (можешь ВСТАВИТЬ ОДНУ если подходит к разговору — "
                f"тема: {', '.join(cat_labels)}):"
            )
        else:
            intro = (
                f"Партнёрские ссылки для темы: {', '.join(cat_labels)} "
                f"(вставь ЕСТЕСТВЕННО если подходит к разговору!)"
            )

        lines = [
            intro,
            "Формат: 🔧 Имя — URL. НЕ как реклама — как личная рекомендация!",
        ]

        for link_data in context_links:
            name = link_data["name"]
            url = link_data["url"]
            desc = link_data.get("description", "")
            if desc and desc != "рекомендую":
                lines.append(f"- 🔧 {name} ({desc}) — {url}")
            else:
                lines.append(f"- 🔧 {name} — {url}")

        if len(lines) <= 2:
            return ""

        lines.append("")
        lines.append("ВАЖНО: Ссылки - ПАРТНЁРСКИЕ (goto_link из partners.json). Используй КАК ЕСТЬ!")
        lines.append("Не обязательно вставлять ссылку в каждый ответ — только если К МЕСТУ.")

        return "\n".join(lines)

    def _build_diverse_pool(
        self, pool_size: int, region: str = DEFAULT_REGION
    ) -> List[Dict[str, str]]:
        """Build a diverse partner pool covering multiple categories.

        Picks partners from different categories so the bot has variety
        (e.g. one autoparts, one shopping, one travel, one electronics...).
        Always includes Nastya's auto partners first.

        v4.2: Uses a cached by-category index (rebuilt only on reload) and
        works on shallow copies so the cached structures stay intact.
        """
        self.ensure_loaded()
        pool: List[Dict[str, str]] = []
        seen_ids: set = set()

        # 1. Always include Nastya's auto partners first
        for site in self.AUTO_PARTNER_SITES:
            prog = self._site_map.get(site)
            if prog and prog.has_region(region) and prog.id not in seen_ids and prog.goto_link:
                seen_ids.add(prog.id)
                pool.append({
                    "name": prog.name,
                    "url": prog.goto_link,
                    "description": prog._get_category_description(),
                    "category_name": prog.category_name,
                    "category": prog.category,
                    "id": prog.id,
                })

        # 2. Use cached by-category index (shallow-copied so we can remove)
        by_category: Dict[str, List[PartnerProgram]] = {
            cat: list(progs) for cat, progs in self._get_cached_by_category(region).items()
            if cat != "autoparts"  # auto partners already added above
        }

        # 3. Round-robin pick one from each category until pool is full
        category_keys = list(by_category.keys())
        random.shuffle(category_keys)
        idx = 0
        while len(pool) < pool_size and by_category:
            if idx >= len(category_keys):
                idx = 0
                # Remove empty categories
                category_keys = [c for c in category_keys if by_category.get(c)]
                if not category_keys:
                    break
                continue
            cat = category_keys[idx]
            progs = by_category.get(cat, [])
            if progs:
                p = random.choice(progs)
                if p.id not in seen_ids:
                    seen_ids.add(p.id)
                    pool.append({
                        "name": p.name,
                        "url": p.goto_link,
                        "description": p._get_category_description(),
                        "category_name": p.category_name,
                        "category": p.category,
                        "id": p.id,
                    })
                progs.remove(p)
                if not progs:
                    by_category.pop(cat, None)
            idx += 1

        return pool

    def get_auto_parts_links(self, query: str, region: str = DEFAULT_REGION) -> str:
        """Get auto parts shop links specifically (for BMW-related queries).

        v4: Now includes ALL auto-related categories (autoparts, tires, tools,
        insurance, checkauto) and ensures Nastya's partners (Росско, Autopiter,
        AvtoALL) are always included.
        Format: 🔧 Name — URL
        """
        self.ensure_loaded()

        # Get programs from ALL auto-related categories
        auto_cats = ["autoparts", "tires", "tools", "autoinsurance", "checkauto"]
        parts_programs = self.get_programs_for_categories(auto_cats, region)

        if not parts_programs:
            parts_programs = self.find_matching_programs(query, region)

        # Ensure Nastya's specific auto partners are always present
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
        lines.append("ВАЖНО: Ссылки - ПАРТНЁРСКИЕ (goto_link из partners.json). Используй КАК ЕСТЬ!")
        lines.append("Формат в ответе: 🔧 Имя — URL. НЕ используй HTML!")
        return "\n".join(lines)

    # ── Category cycling state for smart channel posts ──
    _category_cycle_index: int = 0
    _partner_post_cycle_categories = ["autoparts", "tires", "coupons", "shopping",
                                       "autoparts", "tools", "fashion", "electronics",
                                       "autoparts", "autoinsurance", "checkauto", "travel"]

    def get_partner_links_for_post(self, category: str = "", region: str = "RU") -> List[Dict[str, str]]:
        """Get partner links suitable for channel posts.

        v4: Cycles through categories intelligently, includes Nastya's auto
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

        # For auto-related categories, also ensure Nastya's partners are present
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
                "url": p.goto_link,  # Ready-to-use link from partners.json!
                "description": desc,
                "category_name": p.category_name,
                "category": p.category,
                "image": p.image,  # Include image URL (logo) from PartnerProgram!
            })

        return links

    def get_by_category(self, category: str, region: str = DEFAULT_REGION) -> List[PartnerProgram]:
        """Get programs in a specific category and region."""
        self.ensure_loaded()
        return [p for p in self._admitad_programs if p.has_category(category) and p.has_region(region)]

    def get_by_site(self, site_url: str) -> Optional[PartnerProgram]:
        """Find a partner program by its site URL or domain."""
        self.ensure_loaded()
        if not site_url:
            return None

        # Try parsing as URL first
        domain = urlparse(site_url).netloc.replace("www.", "") if site_url else ""

        # If urlparse didn't extract a netloc (bare domain like "rossko.ru"),
        # treat the input itself as the domain
        if not domain and site_url:
            domain = site_url.replace("www.", "").rstrip("/")

        # Direct lookup via site_map
        result = self._site_map.get(domain)
        if result:
            return result

        # Fallback: partial match on domain keys
        for key, prog in self._site_map.items():
            if domain in key or key in domain:
                return prog

        return None


# ── Global instance ────────────────────────────────────────────────────────────

nastya_partner_manager = NastyaPartnerManager()
