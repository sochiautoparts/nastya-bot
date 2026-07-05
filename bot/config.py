"""Настя Bot Configuration — loaded from environment variables."""
import os
from dataclasses import dataclass, field
from typing import List

def _env(name, default=""):
    v = os.getenv(name, default).strip()
    if v.lower() in ("not_configured", "none", "null"): return ""
    return v

@dataclass
class BotConfig:
    BOT_TOKEN: str = field(default_factory=lambda: _env("BOT_TOKEN"))
    BOT_USERNAME: str = field(default_factory=lambda: _env("BOT_USERNAME", "asnastya_bot"))
    BOT_ID: int = field(default_factory=lambda: int(_env("BOT_ID", "0") or 0))
    OWNER_ID: int = field(default_factory=lambda: int(_env("OWNER_ID", "0") or 0))
    ADMIN_IDS: List[int] = field(default_factory=lambda: [int(x) for x in _env("ADMIN_IDS").replace(","," ").split() if x.isdigit()])

    GH_PAT_TOKEN: str = field(default_factory=lambda: _env("GH_PAT_TOKEN"))
    GH_REPO: str = field(default_factory=lambda: _env("GH_REPO", "sochiautoparts/nastya-bot"))

    OPENCLAW_PORT: int = field(default_factory=lambda: int(_env("OPENCLAW_PORT", "18789")))
    OPENCLAW_BIN: str = field(default_factory=lambda: _env("OPENCLAW_BIN", "openclaw"))

    @property
    def OPENCLAW_URL(self): return f"http://127.0.0.1:{self.OPENCLAW_PORT}"

    GROQ_API_KEY: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    GEMINI_API_KEY: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    OPENROUTER_API_KEY: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    HF_TOKEN: str = field(default_factory=lambda: _env("HF_TOKEN"))
    CEREBRAS_API_KEY: str = field(default_factory=lambda: _env("CEREBRAS_API_KEY"))
    SAMBANOVA_API_KEY: str = field(default_factory=lambda: _env("SAMBANOVA_API_KEY"))
    MISTRAL_API_KEY: str = field(default_factory=lambda: _env("MISTRAL_API_KEY"))
    OPENAI_API_KEY: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    ANTHROPIC_API_KEY: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    POLLINATIONS_API_KEY: str = field(default_factory=lambda: _env("POLLINATIONS_API_KEY"))
    CF_ACCOUNT_ID_1: str = field(default_factory=lambda: _env("CF_ACCOUNT_ID_1"))
    CF_API_TOKEN_1: str = field(default_factory=lambda: _env("CF_API_TOKEN_1"))

    DB_PATH: str = field(default_factory=lambda: _env("DB_PATH", "data/nastya.db"))

    PARTNERS_URL: str = field(default_factory=lambda: _env("PARTNERS_URL", "https://sochiautoparts.ru/partners.json"))

    # Channel
    CHANNEL_ID: str = field(default_factory=lambda: _env("CHANNEL_ID"))
    CHANNEL_USERNAME: str = field(default_factory=lambda: _env("CHANNEL_USERNAME", "chasnastya"))

    GROUP_PROACTIVE_PROB: float = field(default_factory=lambda: float(_env("GROUP_PROACTIVE_PROB", "0.65")))
    GROUP_MAX_PER_MINUTE: int = field(default_factory=lambda: int(_env("GROUP_MAX_PER_MINUTE", "15")))
    GROUP_MEMORY_SIZE: int = field(default_factory=lambda: int(_env("GROUP_MEMORY_SIZE", "30")))
    CHANNEL_REACTION_PROB: float = field(default_factory=lambda: float(_env("CHANNEL_REACTION_PROB", "0.70")))
    REACTION_PROB: float = field(default_factory=lambda: float(_env("REACTION_PROB", "0.45")))
    WEB_VERIFY_PROB: float = field(default_factory=lambda: float(_env("WEB_VERIFY_PROB", "1.0")))
    SEARCH_TIMEOUT_SECONDS: int = field(default_factory=lambda: int(_env("SEARCH_TIMEOUT_SECONDS", "8")))

    CHAT_MAX_CHARS: int = 1200
    COMMENT_MAX_CHARS: int = 500
    GROUP_MAX_CHARS: int = 700

    LOG_LEVEL: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    @property
    def BOT_HANDLE(self): return self.BOT_USERNAME.lstrip("@")

    def active_providers(self):
        p = []
        if self.GROQ_API_KEY: p.append("groq")
        if self.GEMINI_API_KEY: p.append("gemini")
        if self.CEREBRAS_API_KEY: p.append("cerebras")
        if self.OPENROUTER_API_KEY: p.append("openrouter")
        if self.HF_TOKEN: p.append("huggingface")
        if self.SAMBANOVA_API_KEY: p.append("sambanova")
        if self.MISTRAL_API_KEY: p.append("mistral")
        if self.CF_API_TOKEN_1 and self.CF_ACCOUNT_ID_1: p.append("cloudflare")
        if self.OPENAI_API_KEY: p.append("openai")
        if self.ANTHROPIC_API_KEY: p.append("anthropic")
        p.append("pollinations")
        return p

    def providers_status(self): return f"active={self.active_providers()}"

config = BotConfig()

# RSS News Sources for channel posting (restored from pre-OpenClaw)
NEWS_SOURCES = [
    {"name": "СочиАвтоЗапчасти", "url": "https://sochiautoparts.ru/rss.xml", "category": "auto"},
    {"name": "Колёса.ру", "url": "https://kolesa.ru/rss", "category": "auto"},
    {"name": "Авто.Mail.ru", "url": "https://auto.mail.ru/rss/", "category": "auto"},
    {"name": "Хабр", "url": "https://habr.com/ru/rss/articles/top/", "category": "tech"},
    {"name": "iXBT", "url": "https://www.ixbt.com/export/news.rss", "category": "tech"},
    {"name": "VC.ru", "url": "https://vc.ru/rss", "category": "tech"},
    {"name": "N+1", "url": "https://nplus1.ru/rss", "category": "science"},
    {"name": "Naked Science", "url": "https://naked-science.ru/feed", "category": "science"},
    {"name": "DTF", "url": "https://dtf.ru/rss", "category": "gaming"},
    {"name": "ТАСС", "url": "https://tass.ru/rss/v2.xml", "category": "general"},
    {"name": "РИА Новости", "url": "https://ria.ru/export/rss2/archive/index.xml", "category": "general"},
    {"name": "Лента.ру", "url": "https://lenta.ru/rss", "category": "general"},
    {"name": "РБК", "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "category": "general"},
    {"name": "Известия", "url": "https://iz.ru/rss", "category": "general"},
    {"name": "Повар.ру", "url": "https://povar.ru/rss/", "category": "food"},
    {"name": "Гастрономъ", "url": "https://www.gastronom.ru/rss", "category": "food"},
    {"name": "Евро-Футбол", "url": "https://euro-football.ru/rss/", "category": "sports"},
    {"name": "Woman.ru", "url": "https://www.woman.ru/rss/", "category": "lifestyle"},
]

# Political filter keywords (restored from pre-OpenClaw)
POLITICAL_KEYWORDS = [
    "путин", "кремл", "госдум", "санкци", "мобилиз", "война", "сво ",
    "зеленск", "байден", "трамп", "выборы", "парламент", "ракетн", "обстрел",
    "минобор", "днр", "лнр", "ввп", "спецопераци", "вооружен",
    "армия россии", "мо рф", "боевые действия", "конфликт",
    "нацизм", "фашизм", "террор", "взрыв", "погибш",
    "ранен", "жертвы", "беженц", "оккупаци",
]
