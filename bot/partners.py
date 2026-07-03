"""
Настя Partners — affiliate program integration (sochiautoparts.ru/partners.json).

Downloads partner campaigns, matches them contextually to conversation topics,
and returns goto_link URLs for natural recommendations in dialogues.
Only uses goto_link (the affiliate link) — never site_url directly.
"""

import asyncio, json, logging, random, time
from typing import List, Dict, Optional
import httpx
from bot.config import config

logger = logging.getLogger("nastya.partners")


# Category mapping: RU category strings -> keywords for matching + human label
CATEGORY_KEYWORDS = {
    "автомобили и мотоциклы": (["авто", "машин", "тачк", "запчаст", "детал", "мотор", "bmw", "бмв", "ремонт", "сервис", "колесо", "шин", "масл", "фильтр"], "Автозапчасти"),
    "товары для авто и мотоциклов": (["авто", "запчаст", "детал", "аксессуар"], "Автотовары"),
    "аренда машин": (["аренд", "прокат", "машин"], "Аренда авто"),
    "туризм, путешествия": (["путешеств", "поездк", "отпуск", "тур", "билет", "самолёт", "авиа", "рейс"], "Путешествия"),
    "интернет-магазины": (["купить", "заказ", "магазин", "товар", "доставк"], "Покупки"),
    "электроника": (["телефон", "смартфон", "ноутбук", "гаджет", "техник", "электрон"], "Электроника"),
    "одежда, обувь, аксессуары": (["одежд", "обувь", "кроссовк", "куртк", "мод"], "Мода"),
    "транспортные услуги": (["доставк", "перевозк", "груз", "карго"], "Доставка"),
    "интернет-услуги": (["сервис", "подписк", "онлайн"], "Услуги"),
    "связь и коммуникации": (["связь", "сим", "мобильн", "интернет", "wi-fi", "esim"], "Связь"),
    "финансы": (["кредит", "займ", "банк", "карт", "перевод"], "Финансы"),
    "красота и здоровье": (["красот", "космет", "здоров", "аптек", "витамин"], "Красота"),
    "спорт": (["спорт", "фитнес", "тренаж", "бег", "йог"], "Спорт"),
    "образование": (["курс", "обучен", "школ", "универ", "учеб"], "Образование"),
    "дом и сад": (["дом", "мебел", "сад", "ремонт дома", "дач"], "Дом"),
}


class PartnerManager:
    def __init__(self):
        self.campaigns: List[Dict] = []
        self._last_load = 0.0

    async def load(self):
        """Download partners.json from sochiautoparts.ru."""
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                resp = await c.get(config.PARTNERS_URL, headers={"User-Agent": "NastyaBot/1.0"})
            if resp.status_code == 200:
                data = resp.json()
                self.campaigns = data.get("campaigns", [])
                self._last_load = time.time()
                logger.info(f"Loaded {len(self.campaigns)} partner campaigns")
        except Exception as e:
            logger.warning(f"partner load failed: {e}")

    def _match_campaign(self, text: str, campaign: Dict) -> Optional[str]:
        """Check if a campaign matches the conversation text. Returns human description if match."""
        cats = campaign.get("categories", []) or []
        name = (campaign.get("name") or "").lower()
        t = (text or "").lower()

        for cat_ru, (keywords, label) in CATEGORY_KEYWORDS.items():
            # Check if campaign has this category
            cat_match = any(cat_ru in c.lower() for c in cats)
            if not cat_match:
                continue
            # Check if any keyword appears in the conversation text
            for kw in keywords:
                if kw in t:
                    # Build a human description from name + categories
                    desc = self._describe_campaign(campaign, label)
                    return desc
        return None

    def _describe_campaign(self, campaign: Dict, label: str) -> str:
        """Build a human-readable description of what the partner does."""
        name = campaign.get("name", "")
        goto = campaign.get("goto_link", "")
        site = campaign.get("site_url", "")
        cats = campaign.get("categories", [])

        # Determine what they do from name + categories
        name_l = name.lower()
        if "авто" in name_l or "запчаст" in name_l or "autopiter" in name_l:
            what = "автозапчасти"
        elif "аренд" in name_l or "rent" in name_l or "localrent" in name_l:
            what = "аренда авто"
        elif "авиа" in name_l or "aviasales" in name_l:
            what = "авиабилеты"
        elif "raket" in name_l or "китай" in name_l or "доставка" in name_l:
            what = "доставка товаров из Китая"
        elif "esim" in name_l or "globalyo" in name_l or "связь" in name_l:
            what = "eSIM и связь для путешествий"
        elif "одежд" in name_l or "обувь" in name_l:
            what = "одежда и обувь"
        elif "электрон" in name_l or "техник" in name_l:
            what = "электроника"
        elif "спорт" in name_l:
            what = "спортивные товары"
        elif "красот" in name_l or "космет" in name_l:
            what = "красота и косметика"
        elif "финанс" in name_l or "банк" in name_l or "кредит" in name_l:
            what = "финансовые услуги"
        else:
            what = label.lower()

        return f"{name} — {what}. Ссылка: {goto}"

    def get_relevant_partners(self, text: str, max_programs: int = 2) -> List[str]:
        """Return descriptions of partners relevant to the conversation text.

        Returns list of strings like:
          "Autopiter KZ — автозапчасти. Ссылка: https://xmknb.com/g/..."
        Only uses goto_link (affiliate link), never site_url.
        """
        if not self.campaigns:
            return []

        t = (text or "").lower()
        matches = []
        for campaign in self.campaigns:
            desc = self._match_campaign(text, campaign)
            if desc:
                matches.append(desc)
            if len(matches) >= max_programs:
                break

        return matches

    async def refresh_if_needed(self):
        """Auto-refresh if stale."""
        if time.time() - self._last_load > 6 * 3600:  # 6 hours
            await self.load()


partner_manager = PartnerManager()
