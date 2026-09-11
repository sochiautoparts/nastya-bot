"""
Post Quality Engine v2 — редакторское качество постов для каналов.

Возможности:
- Структурная генерация: ЗАГОЛОВОК / ТЕКСТ / ВОПРОС / ХЭШТЕГИ (детерминированный парсинг)
- Анти-галлюцинации: жёсткое правило «факты только из новости + общеизвестное»
- Сборка Telegram HTML: жирный заголовок, жирные цифры (л.с./Н·м/км/ч), курсивный вопрос
- Детерминированный quality gate (длина/структура/вопрос) → ретрай с критикой
- Антиповторы начал постов (hooks-память в БД через bot.database)
- Freshness-фильтр новостей (свежее — вперёд, старьё — в конец)
- Prime-time планировщик: ночью реже, в пиковые часы чаще
- Уведомления владельцу при сбоях (rate-limited, не спамит)
- Отправка с HTML → автофоллбэк на plain text при ошибке парсинга Telegram
"""

import html
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("post_quality")

_MSK = timezone(timedelta(hours=3))

# ─── Промпт-блоки ───────────────────────────────────────────────────────────

STRUCTURED_POST_RULES = """СТРУКТУРА ПОСТА (верни ответ СТРОГО в этом формате, без markdown):
ЗАГОЛОВОК: <свой цепляющий заголовок 5-9 слов, НЕ копия заголовка новости>
ТЕКСТ: <основной текст 450-800 символов: крючок → факты из новости → экспертный разбор → вывод>
ВОПРОС: <короткий вопрос подписчикам для комментариев (заканчивай знаком ?)>
ХЭШТЕГИ: <2-3 тематических хештега через пробел, латиницей, например #bmw #m5>

Правила ТЕКСТА:
- Первый абзац — крючок: интрига, цифра или провокация (но честная)
- Дальше — суть новости с конкретикой (модель, цифры, сроки)
- Затем — экспертный разбор: что это значит для рынка/владельцев
- Финал — короткий вывод позицией редакции
- Пиши абзацами, разделяй пустой строкой
- Пиши только кириллицей (и латиницей для названий брендов) — никаких иероглифов и слов из других алфавитов
- Не смешивай латиницу внутри русских слов
- Без ссылок и без слова «Источник»"""

ANTI_HALLUCINATION_RULES = """ФАКТЫ И ТОЧНОСТЬ (критично):
- Опирайся ТОЛЬКО на факты из новости ниже + общеизвестные сведения о модели/бренде
- НЕ выдумывай цифры, даты, имена, цены которых нет в новости (общие характеристики модели — можно)
- Если каких-то данных нет — рассуждай о значимости, а не сочиняй детали
- Никаких «по слухам, которые мне стали известны» — пиши как редакция, которая читала новость"""

RETRY_CRITIQUE_TMPL = """Твой предыдущий вариант поста не прошёл проверку качества.
Причина: {reason}
Предыдущий вариант:
---
{prev}
---
Перепиши пост ЛУЧШЕ и верни СТРОГО в формате:
ЗАГОЛОВОК: ...
ТЕКСТ: ...
ВОПРОС: ...
ХЭШТЕГИ: ..."""

# One-shot пример формата — критично для локальной 7B-модели: small-модели
# надёжно копируют структуру по примеру, а не по описанию. Тема примера
# нейтральная (прогулки/лайфстайл), чтобы исключить перетаскивание темы в пост.
LOCAL_EXAMPLE = """ПРИМЕР ФОРМАТА ОТВЕТА (тему бери из задания ниже, пример показывает только структуру):
ЗАГОЛОВОК: Короткие прогулки работают лучше долгих
ТЕКСТ: Исследования привычек показали неожиданную деталь: три прогулки по 10 минут в день дают больше энергии, чем одна часовую. Причина проста — короткие выходы из помещения сбрасывают усталость несколько раз за день, а не один, плюс мозг успевает отдохнуть между делами.

Для тех, кто не может выкроить час на спортзал, это вообще хорошая новость: десятиминутный круг вокруг дома влезает в любой график. Вывод простой: регулярность бьёт длительность, и это касается не только прогулок.
ВОПРОС: Больше любите одну долгую прогулку или несколько коротких?
ХЭШТЕГИ: #лайфстайл #привычки"""

# Напоминание задачи СТРОГО после примера: для small-моделей последнее,
# что читает модель, должно быть реальной задачей, а не примером — иначе
# small-модель копирует пример (проверено на 1.5B).
LOCAL_TASK_REMINDER = ("ТЕПЕРЬ ТВОЯ ЗАДАЧА: напиши СВОЙ пост по теме из этого задания "
                       "(тема: «{title}»). Пример выше — только шаблон структуры. "
                       "НИКОГДА не копируй текст примера и его тему про прогулки. "
                       "Все факты бери только из задания.")

# ─── Парсинг структурированного ответа ──────────────────────────────────────

def parse_structured_post(raw: str) -> Optional[dict]:
    """Parse ЗАГОЛОВОК/ТЕКСТ/ВОПРОС/ХЭШТЕГИ. Returns dict or None if unusable."""
    if not raw:
        return None
    text = raw.strip()

    m_h = re.search(r"ЗАГОЛОВОК\s*:\s*(.+)", text, re.IGNORECASE)
    m_b = re.search(
        r"ТЕКСТ\s*:\s*(.+?)(?=\n\s*ВОПРОС\s*:|\n\s*ХЭШТЕГИ\s*:|\Z)",
        text, re.IGNORECASE | re.DOTALL)
    m_q = re.search(
        r"ВОПРОС\s*:\s*(.+?)(?=\n\s*ХЭШТЕГИ\s*:|\Z)",
        text, re.IGNORECASE | re.DOTALL)
    m_t = re.search(r"ХЭШТЕГИ\s*:\s*(.+)", text, re.IGNORECASE)

    if not m_b:
        return None

    body = m_b.group(1).strip()
    # Бывает модель пишет «ТЕКСТ:» и пусто — тогда нет body
    if not body:
        return None

    headline = m_h.group(1).strip() if m_h else ""
    # Заголовок — только первая строка, без хвостов
    if headline:
        headline = headline.split("\n")[0].strip().strip('"«»')

    question = (m_q.group(1).strip() if m_q else "")
    question = " ".join(question.split())[:140]

    hashtags = []
    if m_t:
        for tok in m_t.group(1).split():
            tok = tok.strip().strip(",.;")
            if tok.startswith("#") and 2 <= len(tok) <= 24 and hashtags.count(tok) == 0:
                hashtags.append(tok)
    hashtags = hashtags[:3]

    return {
        "headline": headline,
        "body": body,
        "question": question,
        "hashtags": hashtags,
    }


# ─── Quality gate (детерминированные проверки) ─────────────────────────────

def quality_gate(parsed: dict, min_body: int = 280, max_body: int = 1150) -> tuple:
    """Deterministic quality checks. Returns (ok, reason)."""
    if not parsed or not parsed.get("body"):
        return False, "no_body"

    body = parsed["body"]
    if len(body) < min_body:
        return False, f"body_too_short({len(body)}<{min_body})"
    if len(body) > max_body:
        return False, f"body_too_long({len(body)}>{max_body})"

    headline = parsed.get("headline", "")
    if headline:
        words = len(headline.split())
        if words < 3 or words > 14:
            return False, f"headline_words({words})"
    else:
        return False, "no_headline"

    # Защита от копирования one-shot примера (LOCAL_EXAMPLE): example сам
    # проходит gate, а small-модели иногда копируют его вместо генерации
    low = (body or "").lower()
    if "три прогулки по 10 минут" in low or "регулярность бьёт длительность" in low:
        return False, "example_copy"
    q_low = (parsed.get("question") or "").lower()
    if "одну долгую прогулку" in q_low:
        return False, "example_copy"

    q = parsed.get("question", "")
    if not q or "?" not in q:
        return False, "no_question"

    # Мусорные конструкции
    low = body.lower()
    for bad in ("как искусственный интеллект", "я не могу написать", "у меня нет доступа",
                "напиши пост", "заголовок:"):
        if bad in low:
            return False, f"leakage:{bad[:20]}"

    return True, "ok"

# ─── Санитайзер текста (глюки моделей) ──────────────────────────────────────

_CJK_RE = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]+")
_WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z]+")


def sanitize_text(text: str) -> str:
    """Remove CJK glitches, fix mixed-alphabet words (деcentным → decent), tidy spaces.

    Для слов, где смешаны кириллица и латиница, оставляем более длинный фрагмент.
    """
    if not text:
        return ""
    text = _CJK_RE.sub("", text)

    def _fix(m):
        w = m.group(0)
        has_cyr = re.search(r"[А-Яа-яЁё]", w)
        has_lat = re.search(r"[A-Za-z]", w)
        if has_cyr and has_lat:
            cyr_runs = re.findall(r"[А-Яа-яЁё]+", w)
            lat_runs = re.findall(r"[A-Za-z]+", w)
            best_cyr = max(cyr_runs, key=len) if cyr_runs else ""
            best_lat = max(lat_runs, key=len) if lat_runs else ""
            return best_cyr if len(best_cyr) >= len(best_lat) else best_lat
        return w

    text = _WORD_RE.sub(_fix, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.;:!?])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ─── Хештеги ────────────────────────────────────────────────────────────────

def smart_hashtags(text: str, hashtag_map: dict, defaults: list) -> list:
    """Pick up to 3 hashtags by keyword map (keyword(lower) -> #tag), then defaults."""
    t = (text or "").lower()
    found = []
    for kw, tag in hashtag_map.items():
        if kw in t and tag not in found:
            found.append(tag)
        if len(found) >= 3:
            break
    for tag in defaults:
        if len(found) >= 3:
            break
        if tag not in found:
            found.append(tag)
    return found[:3]


# ─── Сборка HTML-поста ──────────────────────────────────────────────────────

_NUM_RE = re.compile(
    r"(\d[\d\s.,]{0,9}?\s?(?:л\.с\.|лс|Н·м|Нм|Нмт|км/ч|км\\?/ч|сек|с до 100|мм|кг|кВт|км))"
)

def bold_numbers(escaped_body: str) -> str:
    """Bold numeric specs like '590 л.с.', '750 Н·м', '3.3 сек' (input already escaped)."""
    def _b(m):
        frag = m.group(1)
        return f"<b>{frag}</b>"
    out = _NUM_RE.sub(_b, escaped_body)
    # Не булдим внутри уже существующих тегов (наш подзаголовок и т.п.) — защита:
    out = out.replace("<b><b>", "<b>").replace("</b></b>", "</b>")
    return out


def _truncate_plain(text: str, limit: int) -> str:
    """Sentence/word-boundary truncate for plain text (caption limits)."""
    if len(text) <= limit:
        return text
    cut = limit - 1
    for i in range(cut, max(cut - 220, 0), -1):
        if i < len(text) and text[i] in ".!?" and (i + 1 >= len(text) or text[i + 1] in " \n\t"):
            return text[:i + 1] + " …"
    for i in range(cut, max(cut - 120, 0), -1):
        if i < len(text) and text[i] == "\n":
            return text[:i].rstrip() + " …"
    for i in range(cut, max(cut - 60, 0), -1):
        if i < len(text) and text[i] == " ":
            return text[:i].rstrip() + " …"
    return text[:cut].rstrip() + " …"


def assemble_html_post(headline: str, body: str, question: str,
                       hashtags: list, footer: str, headline_emoji: str = "🚗",
                       caption_limit: int = 0) -> tuple:
    """Assemble final post. Returns (html_text, plain_text).

    caption_limit>0 → дополнительно усечёт до указанной длины PLAIN-текста
    (лимит Telegram для подписи к фото = 1024).
    """
    esc = html.escape
    headline = (headline or "").strip()
    body = (body or "").strip()
    question = (question or "").strip()

    plain_parts = []
    html_parts = []

    if headline:
        plain_parts.append(f"{headline_emoji} {headline}".strip())
        html_parts.append(f"<b>{esc(headline_emoji)} {esc(headline)}</b>".strip())

    if body:
        plain_parts.append(body)
        html_parts.append(bold_numbers(esc(body)))

    if question:
        plain_parts.append(f"💬 {question}")
        html_parts.append(f"💬 <i>{esc(question)}</i>")

    tags = [t for t in (hashtags or []) if t]
    if tags:
        tags_line = " ".join(tags)
        plain_parts.append(tags_line)
        html_parts.append(esc(tags_line))

    footer = (footer or "").strip()
    if footer:
        plain_parts.append(footer)
        html_parts.append(esc(footer))

    plain = "\n\n".join(p for p in plain_parts if p)
    html_text = "\n\n".join(p for p in html_parts if p)

    if caption_limit and len(plain) > caption_limit:
        # Урезаем BODY в обоих вариантах согласованно
        overflow = len(plain) - caption_limit
        new_body = _truncate_plain(body, max(len(body) - overflow - 1, 200))
        return assemble_html_post(headline, new_body, question, hashtags, footer,
                                  headline_emoji, caption_limit=0)
    return html_text, plain


# ─── Freshness-фильтр новостей ──────────────────────────────────────────────

def published_epoch(item: dict) -> float:
    """Parse item['published'] (ISO 8601) → epoch seconds; 0 if unknown."""
    raw = (item.get("published") or "").strip()
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def freshness_sort(items: list, max_age_days: float = 5.0, min_keep: int = 4) -> list:
    """Sort by freshness (new first), drop stale items unless too few remain.
    Items without date go last. Also lightly prefers items with images.
    """
    now = time.time()
    def _key(it):
        ts = published_epoch(it)
        age = (now - ts) if ts else 1e9
        has_img = 1 if (it.get("image") or it.get("images")) else 0
        return age

    fresh, stale, undated = [], [], []
    for it in items:
        ts = published_epoch(it)
        if not ts:
            undated.append(it)
        elif now - ts <= max_age_days * 86400:
            fresh.append(it)
        else:
            stale.append(it)

    fresh.sort(key=_key)
    stale.sort(key=_key)
    result = fresh + undated
    if len(result) < min_keep:
        result = result + stale
    return result


# ─── Prime-time интервалы ───────────────────────────────────────────────────

def prime_time_interval(day_s: int = 1200, night_s: int = 2400) -> int:
    """Night (01:00-07:59 MSK) → night_s, else day_s. Audience-aware cadence."""
    h = datetime.now(_MSK).hour
    return night_s if 1 <= h < 8 else day_s


# ─── Антиповторы начал (hooks) ──────────────────────────────────────────────

def build_hook_avoid(hooks: list) -> str:
    """Prompt block: forbid repeating recent openings."""
    clean = [h.strip() for h in (hooks or []) if h and h.strip()]
    if not clean:
        return ""
    examples = "; ".join(f"«{h[:42]}»" for h in clean[:8])
    return (
        "РАЗНООБРАЗИЕ: последние посты канала начинались так:\n"
        f"{examples}\n"
        "Начни ЭТОТ пост по-другому — другой крючок, другой ритм, другой первый абзац."
    )


# ─── Отправка с HTML и фоллбэком ────────────────────────────────────────────

_IMG_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")

def _valid_image(content: bytes) -> bool:
    if not content or len(content) < 1024:
        return False
    if content[:3] == b"\xff\xd8\xff":
        return True
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return True
    return False


async def send_channel_post(bot, channel_id: int, html_text: str, plain_text: str,
                            images: list, log=None):
    """Post to channel: 2+ imgs → media group; 1 img → photo; else text.
    HTML parse mode with automatic plain-text fallback on parse errors.
    Returns Message (or list for media group) on success, None on failure.
    """
    import httpx
    from aiogram.types import BufferedInputFile, InputMediaPhoto
    from aiogram.exceptions import TelegramBadRequest

    log = log or logger
    UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def _is_parse_err(e: Exception) -> bool:
        s = str(e).lower()
        return "can't parse entities" in s or "parse" in s and "entities" in s or "utf-8" in s

    async def _download(url: str):
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
            r = await c.get(url, headers=UA)
            if r.status_code == 200 and _valid_image(r.content):
                return r.content
    #   else None

    # Case A: 2+ images → media group
    if len(images) >= 2:
        media, first = [], True
        for url in images[:10]:
            try:
                content = await _download(url)
                if not content:
                    continue
                buf = BufferedInputFile(content, filename="news.jpg")
                if first:
                    media.append(InputMediaPhoto(media=buf, caption=html_text[:1024],
                                                 parse_mode="HTML"))
                    first = False
                else:
                    media.append(InputMediaPhoto(media=buf))
            except Exception as e:
                log.warning(f"media img fetch failed ({url[:60]}): {e}")
        if media:
            try:
                msgs = await bot.send_media_group(channel_id, media)
                return msgs[0] if msgs else True
            except TelegramBadRequest as e:
                if _is_parse_err(e):
                    log.warning(f"HTML media_group parse failed → plain fallback: {e}")
                    media[0] = InputMediaPhoto(media=media[0].media, caption=plain_text[:1024])
                    try:
                        msgs = await bot.send_media_group(channel_id, media)
                        return msgs[0] if msgs else True
                    except Exception as e2:
                        log.warning(f"plain media_group failed: {e2}")
                else:
                    log.warning(f"media_group failed: {e}")

    # Case B: exactly 1 image → photo
    if len(images) == 1:
        try:
            content = await _download(images[0])
            if content:
                photo = BufferedInputFile(content, filename="news.jpg")
                try:
                    return await bot.send_photo(channel_id, photo, caption=html_text[:1024],
                                                parse_mode="HTML")
                except TelegramBadRequest as e:
                    if _is_parse_err(e):
                        log.warning(f"HTML photo parse failed → plain fallback: {e}")
                        return await bot.send_photo(channel_id, photo, caption=plain_text[:1024])
                    log.warning(f"send_photo failed: {e}")
        except Exception as e:
            log.warning(f"single img download/send failed: {e}")

    # Case C: text only
    try:
        return await bot.send_message(channel_id, html_text[:4096], parse_mode="HTML",
                                      disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if _is_parse_err(e):
            log.warning(f"HTML message parse failed → plain fallback: {e}")
            try:
                return await bot.send_message(channel_id, plain_text[:4096],
                                              disable_web_page_preview=True)
            except Exception as e2:
                log.error(f"plain send_message failed: {e2}")
                return None
        log.error(f"send_message failed: {e}")
        return None
    except Exception as e:
        log.error(f"send_message failed: {e}")
        return None


# ─── Уведомления владельцу ──────────────────────────────────────────────────

_last_owner_notify = 0.0

async def notify_owner(bot, text: str, min_gap_s: int = 1800, log=None) -> bool:
    """Send alert DM to OWNER_ID. Rate-limited: max 1 per min_gap_s. Never raises."""
    global _last_owner_notify
    log = log or logger
    now = time.time()
    if now - _last_owner_notify < min_gap_s:
        return False
    try:
        from bot.config import config as _cfg
        owner = int(getattr(_cfg, "OWNER_ID", 0) or 0)
        if not owner:
            return False
        await bot.send_message(owner, f"🤖⚠️ {text[:600]}")
        _last_owner_notify = now
        log.info(f"Owner notified: {text[:80]}")
        return True
    except Exception as e:
        log.debug(f"notify_owner failed: {e}")
        return False


# ─── Стиль канала (конкретного бота) ────────────────────────────────────────

@dataclass
class PostStyle:
    channel: str
    headline_emoji: str
    default_question: str
    hashtag_map: dict = field(default_factory=dict)
    default_hashtags: list = field(default_factory=list)
    footer: str = ""


# Стиль канала Насти — @chasnastya (лайфстайл)
POST_STYLE = PostStyle(
    channel="@chasnastya",
    headline_emoji="🎀",
    default_question="А вы как считаете? Пишите в комментариях 👇",
    hashtag_map={
        "мода": "#мода", "тренд": "#тренды", "шопинг": "#шопинг", "скидк": "#шопинг",
        "астролог": "#астрология", "зодиак": "#астрология", "гороскоп": "#астрология",
        "кино": "#кино", "фильм": "#кино", "netflix": "#кино", "сериал": "#сериалы",
        "bmw": "#BMW", "m3": "#BMW_M3", "тачк": "#авто",
        "красот": "#красота", "косметик": "#красота", "скін": "#красота",
        "кофе": "#кофе", "путешеств": "#путешествия", "стамбул": "#путешествия",
        "технолог": "#технологии", "гаджет": "#технологии", "ai": "#AI", "ии": "#AI",
        "психолог": "#психология", "отношен": "#психология",
    },
    default_hashtags=["#тренды", "#chasnastya"],
    footer="Автор @asnastya_bot | @chasnastya",
)
