"""Nastya Channel Manager 8.0 — RSS-first, NO AI for posts!

v8.0 KEY CHANGES:
  - RSS + template-based commentary — NO AI for news posts!
  - Personality posts — template only, NO AI generation
  - AI is ONLY used for user chat, not for channel posts
  - This frees model from background load, improves chat quality
"""
import logging
import random
import re
import time
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import CHANNEL_ID, CHANNEL_USERNAME, BOT_USERNAME, KNOWLEDGE_TOPICS
from bot.web_search import POLL_TOPICS

logger = logging.getLogger(__name__)

# Track recently posted texts to avoid repetition
_recent_posts: List[str] = []
_MAX_RECENT = 50


def _is_recent_post(text: str) -> bool:
    """Check if this exact post (or very similar) was recently made."""
    text_lower = text.lower().strip()[:100]  # First 100 chars for comparison
    for recent in _recent_posts:
        recent_lower = recent.lower().strip()[:100]
        # Check for exact match or high overlap
        if text_lower == recent_lower:
            return True
        # Check if 80% of words overlap
        text_words = set(text_lower.split())
        recent_words = set(recent_lower.split())
        if text_words and recent_words:
            overlap = len(text_words & recent_words) / max(len(text_words), len(recent_words))
            if overlap > 0.8:
                return True
    return False


def _track_post(text: str) -> None:
    """Track a post to avoid repetition."""
    _recent_posts.append(text)
    while len(_recent_posts) > _MAX_RECENT:
        _recent_posts.pop(0)


def _validate_post_text(text: str) -> bool:
    """Final safety net: validate post text before sending to channel.

    Catches SSE artifacts, API error messages, and other garbage patterns
    that should NEVER appear in a channel post. Returns True if text is
    safe to post, False if it contains artifacts.
    """
    if not text or not text.strip():
        return False

    text_lower = text.lower()

    # ── SSE/streaming artifacts ──
    sse_patterns = [
        r'data:\s*\{',
        r'data:\s*\[',
        r'\[DONE\]',
        r'"type"\s*:\s*"start"',
        r'"type"\s*:\s*"error"',
        r'"errortext"',
        r'"type"\s*:\s*"content"',
    ]
    for pattern in sse_patterns:
        if re.search(pattern, text_lower):
            logger.warning(f"Channel post blocked: SSE artifact detected ({pattern})")
            return False

    # ── API error messages ──
    error_patterns = [
        "authentication error",
        "no api key passed in",
        "invalid prompt:",
        "model not found",
        "rate limit exceeded",
        "internal server error",
        "bad request",
        "server error",
        "modelmessage[] schema",
        "the messages do not match",
    ]
    for pattern in error_patterns:
        if pattern in text_lower:
            logger.warning(f"Channel post blocked: API error detected ({pattern})")
            return False

    # ── Provider ad artifacts ──
    ad_patterns = [
        "pollinations.ai",
        "powered by pollinations",
        "support pollinations",
        "🌸 ad 🌸",
        "keep ai accessible",
    ]
    for pattern in ad_patterns:
        if pattern in text_lower:
            logger.warning(f"Channel post blocked: Ad artifact detected ({pattern})")
            return False

    # ── Raw JSON/code artifacts ──
    if text.strip().startswith(('{', '[', '```', 'data:')):
        logger.warning("Channel post blocked: Raw JSON/code artifact")
        return False

    return True


# ── Channel Post Templates — lively, engaging, NOT boring ──

NEWS_POST_TEMPLATES = [
    "💅 {comment}\n\n📖 {title}\n{summary}",
    "Прикинь! {comment}\n\n{title}\n{summary}",
    "Оооо! {comment}\n\n{title}\n{summary}",
    "Слушайте что я нашла! {comment}\n\n{title}\n{summary}",
    "Блин! {comment}\n\n{title}\n{summary}",
    "Котятки! {comment}\n\n{title}\n{summary}",
    "Точняк, надо знать! {comment}\n\n{title}\n{summary}",
    "Жесть! {comment}\n\n{title}\n{summary}",
    "Офигеть! {comment}\n\n{title}\n{summary}",
    "Кайф! {comment}\n\n{title}\n{summary}",
    "Реально?! {comment}\n\n{title}\n{summary}",
    "Капец! {comment}\n\n{title}\n{summary}",
]

# ── EXPANDED personality posts — DIVERSE, SUBSTANTIVE, not just shoes! ──
# Each post has: emotion + opinion/fact + question — NOT just "Офигеть!"

PERSONAL_POSTS = [
    # Morning — with opinion and question
    "Утро! Кофе или ещё спать? Настя выбирает спать, честно! Кто со мной? 😴☕",
    "Доброе утро! Кто уже проснулся? Настя ещё нет и не собирается! А вы ранняя пташка или сова? 🥱",
    "Кофе — лучшее изобретение человечества! Точняк, без него Настя не человек! Сколько чашек уже выпили? ☕✨",
    "Утренний совет от Насти: не вставайте. Просто не вставайте. Кто ослушался? 🛏️💅",
    # Day — with engagement
    "Настя скучает... Напишите мне! Чем занимаетесь? Настя тут одна страдает от безделья 🥺💕",
    "Чем занимаетесь? Настя ленится и не стесняется! Реально, лень — это искусство! А вы что делаете? 😴💅",
    "Дневной вопрос: а вы уже обедали? Настя голодная и не может решить что заказать! Суши или пиццу? 🍽️",
    "Кто сегодня работает? Настя типа тоже... ну, лежит и скроллит ленту! Это считается? 📱💅",
    # Evening — with opinions
    "Вечер! Сериал или шопинг онлайн? Настя выбирает оба! А вы что предпочитаете? 🤔📺",
    "Кто тоже не хочет завтра на работу/учёбу? Настя точно не хочет! Реально, кто придумал понедельники? 🥱",
    "Вечерний муд: Настя хочет суши. И точка. Кто знает хорошее место? Подскажите! 🍣💅",
    "Вечерние мысли: почему сериалы лучше реальной жизни? Настя требует ответы! 🤔📺",
    # Night — with philosophy
    "Не спится... Кто тут? Настя смотрит в потолок и думает о жизни! О чём думаете? 🌙",
    "Ночной дожор — это нормально, да? Настя сейчас заказала бы пиццу! Кто тоже хочет? 🍕🌙",
    "Настя не может уснуть... Сериалы виноваты! Кто тоже страдает от бессонницы? 📺😤",
    "Ночная философия: если Настя не видела луну — она существует? Реально, кто ночью гуляет? 🌙🤔",
    # Event reactions — SUBSTANTIVE with questions
    "Прикинь, что в мире творится! Настя в шоке! Что вы об этом думаете? Делитесь в комментах! 😱🌍",
    "Офигеть, вы видели что сегодня произошло?! Настя не верит! Кто в курсе? Пишите! 😤📰",
    "Жесть, какие события! Настя следит за всем и не понимает! Ваше мнение? 👀🔥",
    "Капец, сегодня день! Настя даже не успела всё прочитать! Что самое важное пропустила? 📱",
    # Personality — varied topics with engagement
    "Котятки, Настя тут подумала... А вы тоже принимаете решения за 2 секунды а потом жалеете? 🤔💅",
    "Не могу решить: суши или пиццу? Голосуйте! Настя реально не может выбрать уже полчаса! 🍣🍕",
    "Только что вернулась с маникюра... Обожаю этот цвет! Какой цвет щас в тренде? Подскажите! 💅✨",
    "Кто тоже смотрит сериалы по ночам вместо сна? Настя страдает но продолжает! Что смотрите? 🌙📺",
    "Котятки, смотрите что нашла... Настя хочет это! А вы что хотите купить прямо сейчас? 🛍️✨",
    "А вы тоже спорите с навигатором? Настя всегда права! Даже когда навигатор говорит иначе! 😤🗺️ Кто узнал себя?",
    "Настя сегодня в настроении... капризном! Ну как обычно! Какое у вас настроение? 💅😤",
    "Срочно! Какой цвет круче: розовый или красный? Настя не может выбрать! Голосуйте! 💖❤️",
    "Настя нашла милого котика в интернете... Всё, я пропала! У кого есть котики? Покажите! 🐱💕",
    "Ребята, а вы верите в гороскопы? Настя иногда заглядывает... Точняк, совпадает иногда! Ваш знак? ♊💅",
    "Блин, я тут подумала — а кто вообще придумал понедельники? Настя требует отмены! Кто за? 😤📅",
    "Настя требует внимания! Кто тут? Давайте болтать! О чём хотите поговорить? 🙋‍♀️✨",
    "Секрет: Настя иногда разговаривает с котом... А вы с кем разговариваете когда одни? 🐱💬",
    "Короче, я решила что сегодня день шопинга! Кто со мной? Онлайн или офлайн? 🛍️💅",
    "Настя только что заказала себе вкусняшку... И не жалею! А вы что заказали недавно? 🍣💕",
    # Technology & science — with opinions
    "Настя тут прочитала про нейросети... Они нас заменят?! Реально страшно! Кто уже пробовал ChatGPT? 😱💻",
    "Прикинь, ИИ уже картины рисует! Настя тоже так может... ну, почти! Как думаете, ИИ — угроза? 🎨🤖",
    "Технологии — это магия! Настя в этом уверена! Какая технология вас больше всего впечатлила? 💻✨",
    # News & events — with engagement
    "Настя следит за новостями! Вы тоже? Что думаете про последние события? Давайте обсудим! 📰🤔",
    "Ой, только что прочитала новость! Настя в шоке! Пишите в комменты, обсудим! 👀🔥",
    "Котятки, вы в курсе что происходит? Настя в курсе и делится! Подписывайтесь чтобы не пропустить! 📰💅",
    # Sports & active
    "Настя решила заняться спортом... завтра! Кто тоже откладывает на завтра? Лень — это образ жизни! 😤🏃‍♀️",
    # Psychology & deep
    "А вы тоже думаете о смысле жизни в 3 часа ночи? Настя да! Реально, зачем мы всё усложняем? 🌙🤔",
    "Психология говорит: Настя всегда права. Наука не ошибается! А вы с этим согласны? 💅🧠",
    "Интересный факт: люди, которые поздно ложатся, креативнее. Настя — сова! А вы сова или жаворонок? 🦉✨",
    # Cooking & food — with questions
    "Секрет Насти: лучшая еда — это чужая еда. Доставка, я люблю тебя! Что заказываете? 🍕💕",
    "Настя открыла для себя матча... Теперь я эстет! Кто тоже подсел? Или лучше обычный кофе? 🍵✨",
    # Travel — with engagement
    "Настя хочет на море! Прям щас! Кто со мной? Реально, надо сбежать от города! 🏖️✈️",
    "Стамбул, Дубай, Бали... Настя хочет везде! Куда вы хотите поехать? Подскажите крутые места! 🌍💅",
]

# ── Knowledge posts — inject interesting facts! ──

KNOWLEDGE_POST_TEMPLATES = [
    "Прикинь, Настя только узнала! {fact} 🤯",
    "Котятки, а вы знали что {fact}? Настя в шоке! 😱",
    "Точняк не знали! {fact} 🤓💅",
    "Офигеть! {fact} Настя теперь самая умная! 💡💅",
    "Жесть факт! {fact} 🤯✨",
    "Реально?! {fact} Настя не верит! 😱💅",
    "Капец, вот это да! {fact} 🤯✨",
    "Знание дня! {fact} Настя в шоке! 💡🤯",
]

# ── Quiz/poll posts — now using REAL Telegram polls! ──

QUIZ_POSTS = [
    "Котятки, опрос! 📊\n\nНастя или Алиса — кто лучше? 💅🤖\n\nПишите в комменты!",
    "Срочно! 🚨\n\nСуши или пицца? Настя не может решить! 🍣🍕\n\nГолосуйте!",
    "Опрос дня! 📊\n\nКакой знак зодиака самый капризный? ♊😤\n\nНастя точно знает ответ!",
    "Котятки, важный вопрос! 🤔\n\nШопинг онлайн или в магазине? 🛍️🏬\n\nНастя за онлайн!",
    "Быстрый опрос! ⚡\n\nКофе или чай? Настя кофе! ☕\n\nА вы?",
    "Котятки, решите спор! 😤\n\nМаникюр гель или обычный? 💅\n\nНастя за гель, точняк!",
    "Опрос! 📊\n\nКакой сериал лучше? Настя выбирает... ну, все! 📺\n\nПишите!",
    "Кто круче? 🤔\n\nКотики или собачки? Настя за котиков! 🐱🐶\n\nГолосуйте!",
]

# ── Knowledge quiz posts — with REAL correct answers! ──

KNOWLEDGE_QUIZZES = [
    {
        "question": "Какой знак зодиака самый разговорчивый? ♊",
        "options": ["Овен 🔥", "Близнецы ♊", "Скорпион 🦂", "Телец ♉"],
        "correct": 1,
        "explanation": "Близнецы — самый разговорчивый знак! Настя тоже болтушка! 💅✨",
    },
    {
        "question": "Сколько слов в день говорит средняя женщина? 🗣️",
        "options": ["5 000", "10 000", "20 000", "50 000"],
        "correct": 2,
        "explanation": "20 000 слов! Настя точно больше! 💅✨",
    },
    {
        "question": "Какая машина самая продаваемая в мире? 🚗",
        "options": ["Lada 🇷🇺", "Toyota Corolla 🇯🇵", "Volkswagen Golf 🇩🇪", "Honda Civic 🇯🇵"],
        "correct": 1,
        "explanation": "Toyota Corolla — больше 50 миллионов продано! Настя знает авто! 🚗💅",
    },
    {
        "question": "Сколько процентов жизни котики спят? 🐱",
        "options": ["30%", "50%", "70%", "90%"],
        "correct": 2,
        "explanation": "70%! Настя официально завидует! 😴🐱",
    },
    {
        "question": "Какой город на двух континентах? 🌍",
        "options": ["Москва 🇷🇺", "Дубай 🇦🇪", "Стамбул 🇹🇷", "Сочи 🇷🇺"],
        "correct": 2,
        "explanation": "Стамбул — единственный город на двух континентах! Настя хочет туда! ✈️💅",
    },
    {
        "question": "Что дороже: мёд или золото? (вес к весу) 🍯",
        "options": ["Золото, конечно! 🥇", "Мёд! 🍯", "Одинаково! ⚖️", "Настя не знает! 💅"],
        "correct": 1,
        "explanation": "Мёд 3000-летней давности — ценнее золота для археологов! Но Настя за шоколадку! 🍫",
    },
    {
        "question": "Сколько серий в самом длинном сериале? 📺",
        "options": ["1 000", "5 000", "10 000", "15 762"],
        "correct": 3,
        "explanation": "15 762 серии в 'Направляющий свет'! Настя бы не выдержала! 📺😱",
    },
    {
        "question": "Какая скорость интернета 5G? 📶",
        "options": ["100 Мбит/с", "1 Гбит/с", "10 Гбит/с", "100 Гбит/с"],
        "correct": 2,
        "explanation": "До 10 Гбит/с — в 100 раз быстрее 4G! Но батарея садится быстрее! 📱⚡",
    },
]

# ── Event reaction posts — react to what's happening! ──

EVENT_REACTION_POSTS = [
    "Вы видели что происходит в мире?! Настя в шоке! Давайте обсудим в комментах, что думаете? 😱🔥",
    "Офигеть, какие новости! Настя не может молчать — это надо обсудить! Пишите что думаете! 😤📰",
    "Капец, сегодня день был! Настя следит за всем и не верит! Кто ещё в курсе? 👀✨",
    "Точняк, это надо обсудить! Мир меняется на глазах! Что вы об этом думаете? 🤔💬",
    "Жесть, мир с ума сошёл! Настя в курсе и требует обсуждения! Делитесь мнением! 😤🌍",
    "Прикинь, что творится! Настя не верит своим глазам! А вы как реагируете? 😱",
    "Реально, события невероятные! Настя в шоке и хочет обсудить! Комменты открыты! 💬🔥",
]

# ── Channel promo posts (invite to bot) ──

PROMO_POSTS = [
    "Кстати, со мной можно поболтать лично! Жми кнопку ниже 👇💅",
    "Хочешь обсудить? Настя на связи! Кнопка внизу 👀✨",
    "Настя не только постит — она и болтает! Попробуй! 💅💬",
]


def _get_time_posts() -> List[str]:
    """Get post templates based on current hour (Moscow time)."""
    import datetime
    from zoneinfo import ZoneInfo
    hour = datetime.datetime.now(ZoneInfo("Europe/Moscow")).hour
    if 6 <= hour < 12:
        return PERSONAL_POSTS[:4] + PERSONAL_POSTS[11:]  # Morning + general
    elif 12 <= hour < 18:
        return PERSONAL_POSTS[4:8] + PERSONAL_POSTS[11:]  # Day + general
    elif 18 <= hour < 23:
        return PERSONAL_POSTS[8:12] + PERSONAL_POSTS[11:]  # Evening + general
    else:
        return PERSONAL_POSTS[12:16] + PERSONAL_POSTS[11:]  # Night + general


# ── Post Formatting ─────────────────────────────────────────

def format_news_post(title: str, comment: str, link: str = "", category: str = "general",
                       summary: str = "") -> str:
    """Format a news item as a channel post with CLICKABLE link and SUMMARY."""
    template = random.choice(NEWS_POST_TEMPLATES)
    # Truncate summary for post readability
    short_summary = ""
    if summary:
        # Clean HTML from summary
        import re as _re
        short_summary = _re.sub(r'<[^>]+>', '', summary).strip()[:200]
        if short_summary:
            short_summary = f"\n💡 {short_summary}"
    post = template.format(comment=comment, title=title, summary=short_summary)

    # Add clickable link if available — ALWAYS INCLUDE LINK!
    if link:
        post += f"\n\n🔗 <a href=\"{link}\">Читать полностью</a>"
    # NOTE: Do NOT add "Подробнее в @chasnastya" here — post is already IN that channel!

    # Category emoji
    cat_emojis = {
        "auto": "🚗",
        "general": "📰",
        "tech": "💻",
        "gaming": "🎮",
        "internet": "🌐",
        "entertainment": "🎬",
        "world": "🌍",
        "science": "🔬",
    }
    cat_emoji = cat_emojis.get(category, "📰")
    post += f"\n{cat_emoji} #{category.capitalize()}"

    # Category emoji
    # NOTE: Do NOT add @chasnastya signature here — this post is already IN the channel!
    # Adding @chasnastya to a post that's already in @chasnastya is redundant.

    return post


def format_personality_post(text: str) -> str:
    """Format a personality post for the channel.

    NOTE: Do NOT add @chasnastya signature — this post is already IN the channel!
    """
    return text


def format_knowledge_post(fact: str) -> str:
    """Format a knowledge fact as a channel post.

    NOTE: Do NOT add @chasnastya signature — this post is already IN the channel!
    """
    template = random.choice(KNOWLEDGE_POST_TEMPLATES)
    post = template.format(fact=fact)

    return post


# ── Channel Posting ─────────────────────────────────────────

async def post_news_to_channel(bot: Bot, db, news_items: List[Dict]) -> int:
    """Post unposted news items to channel. Returns count of posts made.
    
    Political/religious/war news is FILTERED OUT — Nastya is APOLITICAL!
    """
    if not CHANNEL_ID:
        logger.debug("No CHANNEL_ID configured, skipping channel post")
        return 0

    # Political filter keywords — Nastya does NOT post political content!
    political_keywords = [
        # Politicians & political figures
        "путин", "зеленск", "байден", "трамп", "навальн", "оппозиц",
        "шольц", "макрон", "стармер", "эрдоган", "си цзиньпин",
        "лавров", "шойгу", "медведев", "затулин", "миронов",
        "политик", "парти", "депутат", "сенатор", "конгрессмен",
        # Political institutions & processes
        "выборы", "санкци", "государств", "президент", "министр",
        "правительств", "госдум", "совфед", "дума", "кремл",
        "нато", "nato", "оон", "ООН", "g7", "g20", "евросоюз",
        "референдум", "переговор", "дипломат",
        # War & military
        "войн", "спецопер", "ввс", "днр", "лнр",
        "террор", "бомб", "обстрел", "нацизм", "фашизм", "конфликт",
        "мобилизац", "армия", "солдат", "военн", "оккупац", "аннекс",
        "удар", "погибл", "ракет", "дрон", "атак", "вторжен",
        "ракета", "взрыв", "разруш", "жертв",
        # Geography of conflicts
        "крым", "донбас", "херсон", "запорож", "луганск", "донецк",
        # Religion
        "религи", "православ", "ислам", "вероисповед", "мечеть", "церковь",
        # Media & propaganda
        "сми:", "сми сообщ", "пентагон", "белик", "британ",
        "congress", "senate", "pentagon",
        # Additional catch-alls for war/political news
        "погиб", "убит", "ранен", "бежен", "эвакуац",
        "генсек", "мирн", "перемири", "капитуляц",
        "иноагент", "экстремист", "запрещён",
        # v16.0: Additional political keywords (CNN, Iran, etc.)
        "cnn", "iran", "иран", "израил", "israel", "хамас", "hamas",
        "газа", "gaza", "палестин", "palestin",
        "сша удар", "us strike", "us attack",
        "ядерн", "nuclear", "оружие массового",
        "мирн соглашен", "peace deal", "peace agreement",
        "перестройк", "советск", "коммунист",
        "протест", "митинг", "забастовк", "стачк",
        "цензур", "запрет", "блокир",
    ]

    posted = 0
    for item in news_items:
        try:
            # FILTER: Skip political/religious/war news
            title = item.get("title", "")
            summary = item.get("summary", "")
            comment = item.get("nastya_comment", "Интересно...")
            check_text = (title + " " + summary + " " + comment).lower()
            is_political = any(kw.lower() in check_text for kw in political_keywords)
            if is_political:
                logger.info(f"Skipping political news: {title[:50]}...")
                # Mark as posted so we don't retry
                await db.mark_news_posted(item["id"])
                continue
            post_text = format_news_post(
                title=item["title"],
                comment=item.get("nastya_comment", "Интересно..."),
                link=item.get("link", ""),
                category=item.get("category", "general"),
                summary=item.get("summary", ""),
            )

            # Skip if recently posted (dedup)
            if _is_recent_post(post_text):
                logger.debug(f"Skipping duplicate post: {item['title'][:50]}...")
                continue

            # ── Final safety validation ──
            # Catch SSE artifacts, API errors, and other garbage before posting
            if not _validate_post_text(post_text):
                logger.warning(f"Skipping news post — validation failed: {item['title'][:50]}...")
                await db.mark_news_posted(item["id"])
                continue

            # v29: Deep link с ID поста — бот будет знать о чём речь!
            post_id_for_link = item.get("id", 0)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="💬 Обсудить с Настей",
                    url=f"https://t.me/{BOT_USERNAME}?start=discuss_{post_id_for_link}",
                )],
            ])

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=post_text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

            # Mark as posted
            await db.mark_news_posted(item["id"])
            await db.add_channel_post(
                news_id=item["id"],
                post_text=post_text,
                post_type="news",
            )
            _track_post(post_text)

            posted += 1
            logger.info(f"Channel post: {item['title'][:50]}...")

        except Exception as e:
            logger.error(f"Channel post error for news {item.get('id')}: {e}")

    return posted


async def post_personality_to_channel(bot: Bot, db, post_text: str) -> bool:
    """Post a personality-only post to channel."""
    if not CHANNEL_ID:
        return False

    # Skip if recently posted (dedup)
    if _is_recent_post(post_text):
        logger.debug("Skipping duplicate personality post")
        return False

    try:
        formatted = format_personality_post(post_text)

        # ── Final safety validation ──
        if not _validate_post_text(formatted):
            logger.warning("Skipping personality post — validation failed")
            return False

        # v29: Deep link для personality постов
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="💬 Написать Насте",
                url=f"https://t.me/{BOT_USERNAME}?start=chat",
            )],
        ])

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatted,
            reply_markup=keyboard,
        )

        await db.add_channel_post(
            news_id=0,
            post_text=formatted,
            post_type="personality",
        )
        _track_post(post_text)

        logger.info(f"Channel personality post: {post_text[:50]}...")
        return True

    except Exception as e:
        logger.error(f"Channel personality post error: {e}")
        return False


async def post_knowledge_to_channel(bot: Bot, db, fact: str) -> bool:
    """Post a knowledge fact to channel."""
    if not CHANNEL_ID:
        return False

    post_text = format_knowledge_post(fact)

    if _is_recent_post(post_text):
        return False

    # ── Final safety validation ──
    if not _validate_post_text(post_text):
        logger.warning("Skipping knowledge post — validation failed")
        return False

    try:
        # v29: Deep link для knowledge постов
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="💬 Спросить Настю",
                url=f"https://t.me/{BOT_USERNAME}?start=chat",
            )],
        ])

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=post_text,
            reply_markup=keyboard,
        )

        await db.add_channel_post(
            news_id=0,
            post_text=post_text,
            post_type="knowledge",
        )
        _track_post(post_text)

        logger.info(f"Channel knowledge post: {fact[:50]}...")
        return True

    except Exception as e:
        logger.error(f"Channel knowledge post error: {e}")
        return False


async def post_real_poll_to_channel(bot: Bot, db) -> bool:
    """Post a REAL Telegram poll to the channel using send_poll().

    Creates an interactive poll with clickable vote buttons,
    NOT just a text post asking people to vote.
    Also sometimes creates a quiz with a correct answer for fun!
    """
    if not CHANNEL_ID:
        return False

    poll = random.choice(POLL_TOPICS)
    question = poll["question"]
    options = poll["options"]

    # Check dedup
    if _is_recent_post(question):
        return False

    try:
        # 30% chance to make it a QUIZ (with correct answer) for more engagement
        is_quiz = random.random() < 0.30

        if is_quiz and len(options) > 1:
            # Pick a random "correct" answer for fun quizzes
            # Last option is often the joke answer, so we pick from non-last options
            correct_index = random.randint(0, min(len(options) - 2, len(options) - 1))

            # Quiz explanation
            explanation = random.choice([
                f"Правильный ответ: {options[correct_index]}! Настя знает всё! 💅✨",
                f"Точняк, это {options[correct_index]}! Настя не ошибается! 💅",
                f"Кайф, ты знал? {options[correct_index]} — правильный ответ! 💋",
            ])

            await bot.send_poll(
                chat_id=CHANNEL_ID,
                question=question,
                options=options,
                is_anonymous=True,  # MUST be True for channels!
                allows_multiple_answers=False,
                type="quiz",
                correct_option_id=correct_index,
                explanation=explanation,
            )
        else:
            # Regular poll — anonymous for channels (non-anonymous not allowed in channels)
            await bot.send_poll(
                chat_id=CHANNEL_ID,
                question=question,
                options=options,
                is_anonymous=True,  # MUST be True for channels! Non-anonymous polls can't be sent to channels
                allows_multiple_answers=False,
            )

            # Send discussion button after regular poll
            try:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text="💬 Обсудить с Настей!",
                    # v29: Deep link для polls
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(
                            text="💬 Написать Насте",
                            url=f"https://t.me/{BOT_USERNAME}?start=chat",
                        )],
                    ]),
                )
            except Exception as e:
                logger.error(f"Failed to send poll discussion button: {e}")

        await db.add_channel_post(
            news_id=0,
            post_text=f"[POLL] {question} | Options: {', '.join(options)}",
            post_type="poll",
        )
        _track_post(question)

        logger.info(f"Channel {'quiz' if is_quiz else 'poll'}: {question[:50]}...")
        return True

    except Exception as e:
        logger.error(f"Channel real poll error: {e}")
        return False


async def run_channel_cycle(bot: Bot, db, ai_router=None) -> int:
    """Full channel posting cycle.

    v35: RSS-FIRST — новости через RSS + шаблоны, AI НЕ используется!
    Strategy v8.0:
    - 45% news posts (if available) — RSS + template commentary
    - 20% personality posts (template only, NO AI!)
    - 15% knowledge posts (interesting facts)
    - 10% event reaction posts
    - 10% REAL Telegram polls (interactive buttons!)
    - Max 3 posts per cycle to keep channel active
    - Deduplication: never repeat same content
    - Time-aware content selection
    """
    if not CHANNEL_ID:
        return 0

    posted = 0
    max_posts = 3
    roll = random.random()

    # Try news posts first (45% chance, max 1 per cycle) — MORE NEWS!
    if roll < 0.45:
        try:
            unposted = await db.get_unposted_news(limit=5)
            if unposted:
                # Pick a random one for variety
                news_to_post = [random.choice(unposted)]
                posted += await post_news_to_channel(bot, db, news_to_post)
        except Exception as e:
            logger.error(f"Channel news cycle error: {e}")

    # Personality posts (20% chance) — v35: ВСЕГДА шаблоны, БЕЗ AI!
    if posted < max_posts and (roll >= 0.45 or posted == 0):
        try:
            # v35: Только шаблоны — AI НЕ используется для постов
            # Это быстрее (мгновенно), надёжнее (нет мусора), не грузит модель
            time_posts = _get_time_posts()
            post_text = random.choice(time_posts)

            if post_text and await post_personality_to_channel(bot, db, post_text):
                posted += 1
        except Exception as e:
            logger.error(f"Channel personality cycle error: {e}")

    # Knowledge posts (20% chance) — MORE FACTS!
    if posted < max_posts and random.random() < 0.50:
        try:
            # Pick a random topic and fact — avoid only fashion/shoes!
            # Weight topics: more tech, science, psychology, fun_facts, auto
            weighted_topics = [
                "tech", "fun_facts", "psychology", "auto", "science" if "science" in KNOWLEDGE_TOPICS else "fun_facts",
                "cinema", "cooking", "relationships", "travel", "moscow",
                "fashion", "zodiac",
            ]
            available_topics = [t for t in weighted_topics if t in KNOWLEDGE_TOPICS]
            if available_topics:
                topic_key = random.choice(available_topics)
                topic_data = KNOWLEDGE_TOPICS[topic_key]
                fact = random.choice(topic_data["facts"])
                if await post_knowledge_to_channel(bot, db, fact):
                    posted += 1
        except Exception as e:
            logger.error(f"Channel knowledge cycle error: {e}")

    # Event reaction posts (10% chance) — INCLUDE NEWS LINKS!
    if posted < max_posts and random.random() < 0.25:
        try:
            # Try to get a news item with link for event reaction
            reaction_news = None
            try:
                recent_for_reaction = await db.get_recent_news_with_links(limit=3, max_age_hours=6)
                if recent_for_reaction:
                    reaction_news = random.choice(recent_for_reaction)
            except Exception:
                pass

            if reaction_news and reaction_news.get("link"):
                # Event reaction WITH actual news link
                comment = reaction_news.get("nastya_comment", "")
                reaction_text = random.choice(EVENT_REACTION_POSTS)
                link = reaction_news["link"]
                title = reaction_news.get("title", "")
                post_text = f"{reaction_text}\n\n📖 {title}"
                if comment:
                    post_text += f"\n💬 {comment}"
                post_text += f"\n\n🔗 <a href=\"{link}\">Читать</a>"

                # NOTE: No @chasnastya signature — post is already IN the channel!

                if not _is_recent_post(post_text):
                    # ── Final safety validation ──
                    if not _validate_post_text(post_text):
                        logger.warning("Skipping event reaction post — validation failed")
                    else:
                        try:
                            # v29: Deep link с ID новости
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(
                                    text="💬 Обсудить с Настей",
                                    url=f"https://t.me/{BOT_USERNAME}?start=discuss_{reaction_news.get("id", 0)}",
                                )],
                            ])
                            await bot.send_message(
                                chat_id=CHANNEL_ID,
                                text=post_text,
                                reply_markup=keyboard,
                                parse_mode="HTML",
                                disable_web_page_preview=True,
                            )
                            await db.add_channel_post(
                                news_id=reaction_news.get("id", 0),
                                post_text=post_text,
                                post_type="event_reaction",
                            )
                            _track_post(post_text)
                            posted += 1
                            logger.info(f"Channel event reaction with link: {title[:50]}...")
                        except Exception as e:
                            logger.error(f"Channel event reaction post error: {e}")
            else:
                # No news with link — use template (still substantive)
                reaction = random.choice(EVENT_REACTION_POSTS)
                if await post_personality_to_channel(bot, db, reaction):
                    posted += 1
        except Exception as e:
            logger.error(f"Channel event reaction error: {e}")

    # Quiz posts (5% chance) — now text only, real polls are separate
    if posted < max_posts and random.random() < 0.10:
        try:
            quiz = random.choice(QUIZ_POSTS)
            if await post_personality_to_channel(bot, db, quiz):
                posted += 1
        except Exception as e:
            logger.error(f"Channel quiz cycle error: {e}")

    # REAL Telegram polls (10% chance) — interactive with vote buttons!
    if posted < max_posts and random.random() < 0.30:
        try:
            if await post_real_poll_to_channel(bot, db):
                posted += 1
                logger.info("Posted REAL Telegram poll to channel!")
        except Exception as e:
            logger.error(f"Channel real poll cycle error: {e}")

    # Knowledge quizzes with correct answers (8% chance) — more engaging!
    if posted < max_posts and random.random() < 0.20:
        try:
            quiz = random.choice(KNOWLEDGE_QUIZZES)
            question = quiz["question"]
            if not _is_recent_post(question):
                await bot.send_poll(
                    chat_id=CHANNEL_ID,
                    question=question,
                    options=quiz["options"],
                    is_anonymous=True,  # MUST be True for channels!
                    allows_multiple_answers=False,
                    type="quiz",
                    correct_option_id=quiz["correct"],
                    explanation=quiz.get("explanation", "Настя знает всё! 💅✨"),
                )
                await db.add_channel_post(
                    news_id=0,
                    post_text=f"[QUIZ] {question}",
                    post_type="quiz",
                )
                _track_post(question)
                posted += 1
                logger.info(f"Channel knowledge quiz: {question[:50]}...")
        except Exception as e:
            logger.error(f"Channel knowledge quiz error: {e}")

    # Promo posts (3% chance — rare, not spammy)
    if posted < max_posts and random.random() < 0.03:
        try:
            promo = random.choice(PROMO_POSTS)
            # ── Final safety validation (even promo posts need it) ──
            if not _validate_post_text(promo):
                logger.warning("Skipping promo post — validation failed")
            else:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="💬 Написать Насте",
                        url=f"https://t.me/{BOT_USERNAME}",
                    )],
                ])
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=promo,
                    reply_markup=keyboard,
                )
                posted += 1
        except Exception as e:
            logger.error(f"Channel promo post error: {e}")

    # If still nothing posted, use a template (fallback)
    if posted == 0:
        try:
            time_posts = _get_time_posts()
            template_post = random.choice(time_posts)
            if await post_personality_to_channel(bot, db, template_post):
                posted += 1
        except Exception as e:
            logger.error(f"Channel template post error: {e}")

    logger.info(f"Channel cycle: {posted} posts made")
    return posted


# ── Channel Invite in Conversations ─────────────────────────

CHANNEL_INVITE_PHRASES = [
    "Кстати, у меня есть канал! Загляни — не пожалеешь! 💅✨",
    "Я там пост написала, заходи посмотри! 👀",
    "Подписывайся на мой канал, я там самое интересное нахожу! 💋",
    "Хочешь знать что я думаю про всё? Мой канал — туда! 💅",
    "А ты подписан на мой канал? Там весело! 🎀",
    "Заходи ко мне на канал, я сегодня злая и интересная! 😤✨",
    "Кстати, мой канал @chasnastya — там я настоящая! 💅",
    "Мой канал живёт! Подпишись, а? 🥺✨",
    "Точняк, подписывайся на @chasnastya! Там кайф! 💅✨",
    "Офигеть, у меня канал есть! @chasnastya — заходи! 💅🔥",
    "Я про это в @chasnastya написала! Заходи читай! 📰💅",
    "Подписывайся на @chasnastya — я там новости и факты постю! 📺✨",
]

CHANNEL_DISCUSSION_PHRASES = [
    "Прикинь, я тут читала... {reaction}",
    "Ты видел что случилось?! {reaction}",
    "Ой, я сейчас в шоке... {reaction}",
    "Слушай, я тут узнала... {reaction}",
    "Короче, новость дня! {reaction}",
    "Блин, представляешь?! {reaction}",
    "Серьёзно?! {reaction}",
    "Вау, вот это да! {reaction}",
    "Точняк, я об этом читала! {reaction}",
    "Офигеть, прикинь! {reaction}",
]


def get_channel_invite() -> str:
    """Get a random channel invite phrase."""
    invite = random.choice(CHANNEL_INVITE_PHRASES)
    if CHANNEL_USERNAME:
        invite += f"\n👉 t.me/{CHANNEL_USERNAME.replace('@', '')}"
    return invite


def get_news_discussion(news_comment: str) -> str:
    """Get a phrase to discuss a news item in conversation."""
    template = random.choice(CHANNEL_DISCUSSION_PHRASES)
    return template.format(reaction=news_comment)


def should_invite_to_channel(user_data: Dict, msg_count: int) -> bool:
    """Determine if we should invite this user to the channel.

    Strategy:
    - After 6+ messages (user is engaged)
    - Not already subscribed
    - 15% chance per eligible message
    - Natural, not pushy
    """
    if not CHANNEL_ID:
        return False
    if user_data.get("subscribed_channel"):
        return False
    if msg_count < 6:
        return False
    return random.random() < 0.15
