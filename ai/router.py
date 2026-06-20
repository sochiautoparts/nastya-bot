"""AI Router v71.0 - RUADAPT QWEN3-4B-INSTRUCT + CHAT/COMMENT FIXES!

АРХИТЕКТУРА v71 - RuadaptQwen3-4B-Instruct (tested & tuned) + CHAT/COMMENT FIXES:
  - Русский токенизатор: 48К дополнительных русских токенов → до 2x быстрее генерация
  - Instruct-версия: отвечает НАПРЯМУЮ без <think> тегов! (подтверждено 20+ тестами)
  - Дообучение на русском корпусе: лучше понимает и генерирует русский
  - LEP: сохраняет качество при смене токенизатора
  - v12: Оптимизированные параметры (temp=0.75, top_k=40, repeat_penalty=1.18)
  - v12: LOCAL_MODEL_SYSTEM_PROMPT — специальный конспективный промпт для 4B модели
  - v12: Антигаллюцинационные инструкции во всех локальных промптах
  - v13: FIX: Дублирование сообщения пользователя — _build_messages() теперь проверяет
  - v13: FIX: Таймаут увеличен (90/150/210s вместо 45/70/100s) — реалистично для 2 vCPU
  - v13: FIX: Локальная модель теряла контекст (дата, настроение, групповой режим)
  - v13: ADDED: LOCAL_COMMENT_SYSTEM_PROMPT — короткий промпт для комментариев (2-4 предложения)
  - v13: INCREASED: max_tokens для чата 512→768, для комментариев 384

LOCAL_ONLY_POSTING=true (default):
  - Канал: Local model directly → cloud as emergency fallback
  - Новостные комментарии: Local model directly → cloud as fallback
  - Чат: Local → Pollinations(key) → Pollinations(free) → Cloudflare → Static
  - Консультации: Pollinations(key) → Pollinations(free) → Cloudflare → Local → Static
  - Комментарии в группах: Local → Pollinations(key) → Pollinations(free) → Cloudflare → Static
  - VISION: Pollinations vision(key) → Pollinations vision(free) → Cloudflare vision → Static

LOCAL_ONLY_POSTING=false (legacy):
  - Канал: Pollinations → Cloudflare → Local(fallback) → LOCAL-ONLY → Static
  - Всё остальное как выше

FAILOVER CHAIN (6 уровней до статического фоллбэка):
  Level 0: Local Model (RuadaptQwen3-4B-Instruct GGUF, CPU) — CHAT & COMMENT маршруты
  Level 1: Pollinations (API key) → KEY1 → KEY2
  Level 2: Pollinations FREE API (text.pollinations.ai, без авторизации)
  Level 3: Cloudflare Workers AI (@cf/mistralai/mistral-small-3.1-24b-instruct)
  Level 3.5: Local Model (fallback для FUNCTION + BACKGROUND маршрутов)
  Last resort: LOCAL-ONLY постинг (упрощённый промпт для 4B модели)
  Absolute last: Статические фоллбэк-ответы

Стратегия маршрутизации (v67.0 — LOCAL-ONLY POSTING + LOCAL-FIRST):
  CHAT route_type (пользовательские чаты) → Local → Pollinations(key) → Pollinations(free) → Cloudflare → Static
  FUNCTION route_type (посты, VIN, диагностика) → Pollinations(key) → Pollinations(free) → Cloudflare → Local(fallback) → Static
  COMMENT route_type (комментарии в группах) → Local → Pollinations(key) → Pollinations(free) → Cloudflare → Static
  VISION (фото) → Pollinations vision(key) → Pollinations vision(free) → Cloudflare vision → Static
  BACKGROUND (новости, канал) → Pollinations(key) → Pollinations(free) → Cloudflare → Local(fallback) → LOCAL-ONLY posting
  LOCAL-ONLY posting → Local model directly with simplified prompt (PRIMARY when LOCAL_ONLY_POSTING=true)
  IMAGE generation → Pollinations(key) → Pollinations(free) → None

Локальная модель ИДЕАЛЬНА для:
  - Постинг в канал (экономит облачный баланс!)
  - Быстрые ответы в чате (экономит облачный баланс!)
  - Комментарии в группах (короткие, быстрые, дешёвые)
  - Простой Q&A о машинах, Москве, астрологии
  - Фоллбэк когда все облачные провайдеры недоступны

Облачные модели ЛУЧШЕ для:
  - VIN декодирование (нужна точность)
  - Консультации (нумерология, астрология, HD — нужен глубокий анализ)
  - Vision задачи (локальная модель не умеет vision)
"""

import logging
import random
import time
import re
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.llama_cpp_provider import LlamaCppProvider, LOCAL_MODEL_SYSTEM_PROMPT, LOCAL_COMMENT_SYSTEM_PROMPT
from ai.providers.pollinations_provider import (
    PollinationsProvider, REASONING_CHAT, REASONING_COMPLEX,
    MODEL_REASONING, MODEL_VISION, CHAT_MODELS,
)
from ai.providers.cloudflare_provider import CloudflareProvider
from ai.voice import transcribe_voice_ogg
from bot.config import (
    POLLINATIONS_API_KEY, POLLINATIONS_API_KEY_2, POLLINATIONS_MAX_TOKENS,
    CF_ACCOUNT_ID_1, CF_TOKEN_1, CF_ACCOUNT_ID_2, CF_TOKEN_2,
    ENABLE_LOCAL_MODEL, MODEL_PATH, MODEL_N_CTX, MODEL_N_THREADS,
    LOCAL_POST_MAX_TOKENS,
)

logger = logging.getLogger(__name__)

FALLBACK_RESPONSES = [
    "Ммм... Настя задумалась. Повтори? 🤔",
    "Ой, Настя отвлеклась... Что ты сказал? 😅",
    "Блин, Настя задумалась о вечном... Ещё раз? 💅",
    "Настя не расслышала... Говори ещё! 😏",
    "Ой, мысли улетели! Повтори для Насти? 💭",
]

# -- Task complexity detection for routing --
# Keywords that indicate a SIMPLE task (fast models sufficient)
_SIMPLE_TASK_KEYWORDS = [
    "привет", "как дела", "пока", "спасибо", "ок", "ладно",
    "что делаешь", "скучно", "доброе утро", "добрый день", "добрый вечер",
    "спокойной ночи", "как настроен", "чем занята", "расскажи о себе",
    "кто ты", "что ты", "сколько тебе", "где ты живёшь",
    "хаха", "ахах", "лол", "прикольно", "круто", "класс",
    "да", "нет", "норм", "супер", "окей", "ага", "угу",
    "обнимаю", "целую", "❤", "💕", "😊", "😂",
]

# Keywords that indicate a COMPLEX task (better models needed)
_COMPLEX_TASK_KEYWORDS = [
    # VIN & auto diagnostics
    "vin", "вин", "расшифруй", "декодир", "пробей",
    "диагност", "ремонт", "не заводит", "стучит", "горит",
    "ошибка", "чек", "check", "код ошибки", "obd",
    # Detailed/complex requests
    "подробно", "расскажи подробно", "объясни подробно",
    "напиши статью", "составь список", "сравни",
    "проанализир", "рассчитай", "посчитай",
    # Search and products
    "найди", "поищи", "ищи", "где купить", "купить",
    "ссылк", "заказать", "цена", "стоимость",
    # Tech & science
    "как работает", "принцип действия", "устройство",
    "почему", "зачем", "из-за чего", "в чём разница",
    # Consultations - MUST use cloud for quality
    "нумеролог", "матриц", "астролог", "натальн", "джйотиш",
    "дизайн челов", "human design", "ayurveda", "аюрвед",
    "психосомат", "доша", "гороскоп", "совместимост",
    # Long messages (>200 chars) are also considered complex
]

# Keywords that MANDATE cloud model (vision, photos, etc.)
_CLOUD_ONLY_KEYWORDS = [
    "фото", "изображен", "снимок", "сканер", "документ",
    "птс", "стс", "картинку", "посмотри",
]


def _classify_task_complexity(prompt: str, messages: Optional[List[Dict]] = None) -> str:
    """Classify task complexity to choose reasoning effort level.

    Returns:
        "simple" - fast models sufficient (LOCAL or REASONING_CHAT)
        "complex" - better models recommended (REASONING_COMPLEX)
        "cloud_only" - must use cloud (vision, etc.) (REASONING_COMPLEX)
    """
    prompt_lower = prompt.lower().strip()

    # Check for cloud-only tasks (vision, photos)
    for kw in _CLOUD_ONLY_KEYWORDS:
        if kw in prompt_lower:
            return "cloud_only"

    # Long messages are complex
    if len(prompt) > 300:
        return "complex"

    # Check for complex task keywords
    for kw in _COMPLEX_TASK_KEYWORDS:
        if kw in prompt_lower:
            return "complex"

    # Check for simple task keywords
    simple_count = sum(1 for kw in _SIMPLE_TASK_KEYWORDS if kw in prompt_lower)
    if simple_count > 0 and len(prompt) < 100:
        return "simple"

    # Default: simple for short messages, complex for longer
    if len(prompt) < 150:
        return "simple"

    return "complex"


class AIRouter:
    """AI Router v71.0 - RUADAPT QWEN3-4B-INSTRUCT + CHAT/COMMENT FIXES!

    Strategy v71: RuadaptQwen3-4B-Instruct — answers DIRECTLY (no <think> tags!),
    Russian tokenizer for faster Russian generation.
    
    v13 FIXES:
    - Fixed duplicate user message in local model context
    - Fixed timeout too short for 2 vCPU (90/150/210s)
    - Fixed local model losing ALL context (date, mood, group mode)
    - Added LOCAL_COMMENT_SYSTEM_PROMPT for shorter group comments
    - Increased chat max_tokens from 512 to 768

    LOCAL_ONLY_POSTING=true (default):
      Channel posts: Local model directly → cloud as emergency fallback
      News commentary: Local model directly → cloud as fallback

    LOCAL_ONLY_POSTING=false (legacy):
      Channel posts: Cloud → local as last-resort fallback

    PROVIDER CHAIN:
      Local(RuadaptQwen3-4B-Instruct) -> Pollinations(KEY1->KEY2->OLD API) -> Cloudflare(Acct1->Acct2) -> static

    Route for CHAT (route_type="chat", default):
        Local -> Pollinations -> Cloudflare -> static fallback

    Route for FUNCTION (route_type="function"):
        Pollinations -> Cloudflare -> Local(fallback) -> static fallback

    Route for COMMENT (route_type="comment"):
        Local -> Pollinations -> Cloudflare -> static fallback

    Route for VISION tasks (photos):
        Pollinations vision -> Cloudflare vision -> fallback message

    Route for BACKGROUND tasks (news, channel):
        Pollinations -> Cloudflare -> Local(fallback) -> skip (not critical)
    """

    def __init__(self, db=None):
        self._local: Optional[LlamaCppProvider] = None
        self._pollinations: Optional[PollinationsProvider] = None
        self._cloudflare: Optional[CloudflareProvider] = None
        self._db = db
        self._total_requests: int = 0
        self._total_fallbacks: int = 0
        self._local_requests: int = 0
        self._pollinations_requests: int = 0
        self._cloudflare_requests: int = 0
        self._vision_requests: int = 0
        self._last_cloud_success: float = 0

    async def init(self) -> None:
        """Initialize all providers: Local + Pollinations + Cloudflare."""
        # ── Local Model - PRIMARY for chat/comments ──
        if ENABLE_LOCAL_MODEL and MODEL_PATH:
            try:
                self._local = LlamaCppProvider(
                    model_path=MODEL_PATH,
                    timeout=90.0,  # v10: Dynamic timeout now used in generate() — this is fallback only
                    model_config={
                        "n_ctx": MODEL_N_CTX,
                        "n_threads": MODEL_N_THREADS,
                    },
                )
                # Pre-load model in executor (non-blocking)
                import asyncio
                await self._local.init()
                logger.info(
                    f"LocalProvider (RuadaptQwen3-4B-Instruct) initialized as PRIMARY for chat/comments "
                    f"(n_ctx={MODEL_N_CTX}, n_threads={MODEL_N_THREADS})"
                )
            except Exception as e:
                logger.warning(f"LocalProvider init failed: {e}. Continuing without local model.")
                self._local = None
        else:
            logger.info("Local model DISABLED (ENABLE_LOCAL_MODEL=false or MODEL_PATH empty)")

        # ── Pollinations - PRIMARY for functions, SECONDARY for chat/comments ──
        try:
            self._pollinations = PollinationsProvider(
                api_key=POLLINATIONS_API_KEY,
                api_key_2=POLLINATIONS_API_KEY_2,
                timeout=45.0,
            )
            await self._pollinations.init()
            model_names = [m[0] for m in CHAT_MODELS]
            logger.info(
                f"PollinationsProvider v20 initialized "
                f"({len(CHAT_MODELS)} models + OLD API fallback)"
            )
        except Exception as e:
            logger.warning(f"PollinationsProvider init failed: {e}")
            self._pollinations = None

        # ── Cloudflare Workers AI - FALLBACK provider ──
        try:
            self._cloudflare = CloudflareProvider(
                account_id_1=CF_ACCOUNT_ID_1,
                token_1=CF_TOKEN_1,
                account_id_2=CF_ACCOUNT_ID_2,
                token_2=CF_TOKEN_2,
                timeout=30.0,
            )
            await self._cloudflare.init()
            logger.info(
                f"CloudflareProvider initialized as FALLBACK "
                f"(@cf/mistralai/mistral-small-3.1-24b-instruct, dual-account)"
            )
        except Exception as e:
            logger.warning(f"CloudflareProvider init failed: {e}")
            self._cloudflare = None

        # Log status
        local_status = "active" if self._local and self._local.is_available() else "unavailable"
        pollinations_status = "active" if self._pollinations and self._pollinations.is_available() else "unavailable"
        cloudflare_status = "active" if self._cloudflare and self._cloudflare.is_available() else "unavailable"

        logger.info(
            f"AI Router v71.0 RUADAPT QWEN3-4B-INSTRUCT initialized: "
            f"local={local_status} (RuadaptQwen3-4B-Instruct, PRIMARY chat/comments), "
            f"pollinations={pollinations_status} ({len(CHAT_MODELS)} models + OLD API, PRIMARY functions), "
            f"cloudflare={cloudflare_status} (mistral-small-3.1, FALLBACK), "
            f"strategy=LOCAL->Pollinations->Cloudflare->fallback, "
            f"max_tokens={POLLINATIONS_MAX_TOKENS}"
        )

    async def close(self) -> None:
        """Close providers."""
        if self._local:
            try:
                await self._local.close()
            except Exception:
                pass
        if self._pollinations:
            try:
                await self._pollinations.close()
            except Exception:
                pass
        if self._cloudflare:
            try:
                await self._cloudflare.close()
            except Exception:
                pass

    async def chat(self, prompt: str, system_prompt: str = "",
                   messages: Optional[List[Dict]] = None, **kwargs) -> AIResponse:
        """Route chat based on route_type - LOCAL-FIRST strategy.

        route_type (kwarg):
            "chat" (default) - Local → Pollinations → Cloudflare → static
            "function" - Pollinations → Cloudflare → Local(fallback) → static
            "comment" - Local → Pollinations → Cloudflare → static
        """
        self._total_requests += 1
        priority = kwargs.get("priority", "high")

        if priority == "high":
            return await self._route_chat(prompt, system_prompt, messages, **kwargs)
        else:
            return await self._route_background(prompt, system_prompt, messages, **kwargs)

    async def vision(self, prompt: str, image_data: bytes,
                     image_format: str = "jpeg", system_prompt: str = "",
                     **kwargs) -> AIResponse:
        """Route vision request: Pollinations vision -> Cloudflare vision -> fallback."""
        self._total_requests += 1
        self._vision_requests += 1

        # -- Pollinations Vision - PRIMARY provider --
        if self._pollinations and self._pollinations.is_available():
            try:
                result = await self._pollinations.generate_vision(
                    prompt=prompt,
                    image_data=image_data,
                    image_format=image_format,
                    system_prompt=system_prompt,
                    max_tokens=600,
                    temperature=0.85,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._pollinations_requests += 1
                        self._last_cloud_success = time.time()
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "vision_primary"},
                        )
            except ProviderError as e:
                logger.warning(f"Pollinations vision error: {e}. Trying Cloudflare vision.")
            except Exception as e:
                logger.warning(f"Pollinations vision unexpected error: {e}. Trying Cloudflare vision.")

        # -- Cloudflare Vision - FALLBACK provider --
        if self._cloudflare and self._cloudflare.is_available():
            try:
                result = await self._cloudflare.generate_vision(
                    prompt=prompt,
                    image_data=image_data,
                    image_format=image_format,
                    system_prompt=system_prompt,
                    max_tokens=600,
                    temperature=0.85,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._cloudflare_requests += 1
                        self._last_cloud_success = time.time()
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "vision_cloudflare"},
                        )
            except ProviderError as e:
                logger.warning(f"Cloudflare vision error: {e}")
            except Exception as e:
                logger.warning(f"Cloudflare vision unexpected error: {e}")

        # -- No fallback for vision - static message --
        self._total_fallbacks += 1
        logger.warning("Vision failed - all providers unavailable")
        return AIResponse(
            text="Ой, Настя не может разглядеть фотку... Попробуй ещё раз? 📸💅",
            provider="fallback",
            model="none",
            tokens_used=0,
            metadata={"role": "vision_fallback"},
        )

    async def _try_local(self, prompt: str, system_prompt: str,
                         messages: Optional[List[Dict]], **kwargs) -> Optional[AIResponse]:
        """Try local model. Returns None if unavailable/failed (NOT ProviderError).
        
        v13: CRITICAL FIXES:
          - Extracts essential context (date/time, mood, group mode) from the original
            system_prompt and appends it to the local model prompt. Before, _try_local()
            threw away ALL context, making the bot unaware of date/time/group mode.
          - Uses LOCAL_COMMENT_SYSTEM_PROMPT for comment routes (shorter, 2-4 sentences).
          - Increased max_tokens from 512 to 768 for chat mode (allows more detailed responses).
          - Fixed duplicate user message in _build_messages() (llama_cpp_provider.py v13).
        """
        if not self._local or not self._local.is_available():
            return None

        try:
            route_type = kwargs.get("route_type", "chat")
            
            # v13: Choose system prompt based on route type
            if route_type == "comment":
                local_system_template = LOCAL_COMMENT_SYSTEM_PROMPT
                default_max_tokens = 384  # Comments are shorter (2-4 sentences)
            else:
                local_system_template = LOCAL_MODEL_SYSTEM_PROMPT
                default_max_tokens = 768  # v13: Was 512 — chat needs more room for detailed answers
            
            # v13: Extract essential context from the original system_prompt
            # The original system_prompt has date/time, mood, group mode, etc.
            # We can't use it directly (too long for 4B model), but we extract key parts.
            context_parts = []
            
            # Extract date/time context
            date_match = re.search(r'Сегодня\s+\S+,\s*\d+\s+\S+\s+\d+\s+года,\s*время\s+\d+:\d+\s+МСК', system_prompt)
            if date_match:
                context_parts.append(f" {date_match.group()}")
            
            # Extract mood
            mood_match = re.search(r'Настроение:\s*(\S+)', system_prompt)
            if mood_match:
                context_parts.append(f" Настроение: {mood_match.group(1)}.")
            
            # Detect group mode
            if "групповом чате" in system_prompt or "групповой чат" in system_prompt:
                context_parts.append(" Мы в групповом чате — отвечай коротко!")
            
            # Extract user birth data if present
            birth_match = re.search(r'Дата рождения:\s*\d{2}\.\d{2}\.\d{4}', system_prompt)
            if birth_match:
                context_parts.append(f" {birth_match.group()}.")
            
            # Extract news context (just first 200 chars)
            news_match = re.search(r'Свежие новости:\s*(.+?)(?:\.|$)', system_prompt)
            if news_match:
                news_text = news_match.group(0)[:200]
                context_parts.append(f" {news_text}")
            
            # Build context string
            context_str = " ".join(context_parts) if context_parts else ""
            
            # Apply context to template
            local_system = local_system_template.format(context=context_str)

            result = await self._local.generate(
                prompt,
                system_prompt=local_system,
                messages=messages,
                max_tokens=min(kwargs.get("max_tokens", default_max_tokens), default_max_tokens),
                temperature=kwargs.get("temperature", 0.75),
            )
            if result and result.text:
                cleaned = self.clean_ai_response(result.text)
                if cleaned:
                    self._local_requests += 1
                    return AIResponse(
                        text=cleaned,
                        provider=result.provider,
                        model=result.model,
                        tokens_used=result.tokens_used,
                        metadata={**result.metadata, "role": "local_primary"},
                    )
        except ProviderError as e:
            logger.warning(f"Local model error: {e}. Falling back to cloud.")
        except Exception as e:
            logger.warning(f"Local model unexpected error: {e}. Falling back to cloud.")

        return None

    async def _try_pollinations(self, prompt: str, system_prompt: str,
                                messages: Optional[List[Dict]], reasoning: str,
                                **kwargs) -> Optional[AIResponse]:
        """Try Pollinations. Returns None if unavailable/failed."""
        if not self._pollinations or not self._pollinations.is_available():
            return None

        try:
            caller_max_tokens = kwargs.get("max_tokens", POLLINATIONS_MAX_TOKENS)
            result = await self._pollinations.generate(
                prompt,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=caller_max_tokens,
                reasoning_effort=reasoning,
            )
            if result and result.text:
                cleaned = self.clean_ai_response(result.text)
                if cleaned:
                    self._pollinations_requests += 1
                    self._last_cloud_success = time.time()
                    return AIResponse(
                        text=cleaned,
                        provider=result.provider,
                        model=result.model,
                        tokens_used=result.tokens_used,
                        metadata={**result.metadata, "role": "pollinations"},
                    )
        except ProviderError as e:
            logger.warning(f"Pollinations error: {e}. Trying next provider.")
        except Exception as e:
            logger.warning(f"Pollinations unexpected error: {e}. Trying next provider.")

        return None

    async def _try_cloudflare(self, prompt: str, system_prompt: str,
                              messages: Optional[List[Dict]], **kwargs) -> Optional[AIResponse]:
        """Try Cloudflare. Returns None if unavailable/failed."""
        if not self._cloudflare or not self._cloudflare.is_available():
            return None

        try:
            caller_max_tokens = kwargs.get("max_tokens", POLLINATIONS_MAX_TOKENS)
            result = await self._cloudflare.generate(
                prompt,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=min(caller_max_tokens, 4096),  # CF limit
                temperature=kwargs.get("temperature", 0.85),
            )
            if result and result.text:
                cleaned = self.clean_ai_response(result.text)
                if cleaned:
                    self._cloudflare_requests += 1
                    self._last_cloud_success = time.time()
                    return AIResponse(
                        text=cleaned,
                        provider=result.provider,
                        model=result.model,
                        tokens_used=result.tokens_used,
                        metadata={**result.metadata, "role": "cloudflare"},
                    )
        except ProviderError as e:
            logger.warning(f"Cloudflare error: {e}.")
        except Exception as e:
            logger.warning(f"Cloudflare unexpected error: {e}.")

        return None

    async def _route_chat(self, prompt: str, system_prompt: str,
                          messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Chat route: LOCAL-FIRST for chat/comments, CLOUD-FIRST for functions.

        route_type:
            "chat" - Local → Pollinations → Cloudflare → static (LOCAL-FIRST)
            "function" - Pollinations → Cloudflare → Local(fallback) → static (CLOUD-FIRST)
            "comment" - Local → Pollinations → Cloudflare → static (LOCAL-FIRST)
        """
        route_type = kwargs.get("route_type", "chat")

        # Determine reasoning effort based on route_type and complexity
        caller_reasoning = kwargs.get("reasoning_effort", None)
        if caller_reasoning:
            reasoning = caller_reasoning
        elif route_type == "comment":
            reasoning = REASONING_CHAT
        elif route_type == "function":
            reasoning = REASONING_COMPLEX
        else:
            # Normal chat -> complexity-based routing
            complexity = _classify_task_complexity(prompt, messages)
            if complexity in ("cloud_only", "complex"):
                reasoning = REASONING_COMPLEX
            else:
                reasoning = REASONING_CHAT

        # ── LOCAL-FIRST routes: chat & comment ──
        if route_type in ("chat", "comment"):
            # Level 0: Local model (RuadaptQwen3-4B-Instruct)
            result = await self._try_local(prompt, system_prompt, messages, **kwargs)
            if result:
                return result

            # Level 1-2: Pollinations (with keys + free API)
            result = await self._try_pollinations(prompt, system_prompt, messages, reasoning, **kwargs)
            if result:
                return result

            # Level 3: Cloudflare
            result = await self._try_cloudflare(prompt, system_prompt, messages, **kwargs)
            if result:
                return result

            # Static fallback - bot ALWAYS responds
            self._total_fallbacks += 1
            logger.error("All providers unavailable! Using static fallback.")
            return AIResponse(
                text=self.get_fallback_response(),
                provider="fallback",
                model="none",
                tokens_used=0,
            )

        # ── CLOUD-FIRST routes: function (posts, VIN, diagnostics) ──
        else:  # route_type == "function"
            # Level 1-2: Pollinations (best quality for public content)
            result = await self._try_pollinations(prompt, system_prompt, messages, reasoning, **kwargs)
            if result:
                return result

            # Level 3: Cloudflare
            result = await self._try_cloudflare(prompt, system_prompt, messages, **kwargs)
            if result:
                return result

            # Level 0: Local as LAST fallback (quality may be lower but better than nothing)
            result = await self._try_local(prompt, system_prompt, messages, **kwargs)
            if result:
                return result

            # Static fallback - bot ALWAYS responds
            self._total_fallbacks += 1
            logger.error("All providers unavailable! Using static fallback.")
            return AIResponse(
                text=self.get_fallback_response(),
                provider="fallback",
                model="none",
                tokens_used=0,
            )

    async def _route_background(self, prompt: str, system_prompt: str,
                                messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Background route: Pollinations -> Cloudflare -> Local -> skip.

        Background tasks (news, channel posts) need quality but are not critical.
        Try cloud first for quality, local as last resort, skip if all fail.
        """
        # Level 1-2: Pollinations - PRIMARY for background (quality matters)
        result = await self._try_pollinations(
            prompt, system_prompt, messages, REASONING_CHAT, **kwargs
        )
        if result:
            return result

        # Level 3: Cloudflare - FALLBACK for background
        result = await self._try_cloudflare(prompt, system_prompt, messages, **kwargs)
        if result:
            return result

        # Level 0: Local as last resort (lower quality but still usable)
        result = await self._try_local(prompt, system_prompt, messages, **kwargs)
        if result:
            return result

        # Background failed - try LOCAL-ONLY post as last resort
        self._total_fallbacks += 1
        logger.warning("Background task: all cloud providers failed. Returning local-only fallback flag.")
        return AIResponse(
            text="",
            provider="none",
            model="none",
            tokens_used=0,
            metadata={"skipped": True, "local_only_fallback": True},
        )

    async def generate_local_post(
        self,
        title: str,
        summary: str = "",
        category: str = "",
    ) -> AIResponse:
        """Generate a channel post using ONLY the local model.

        v67: Now used as PRIMARY posting method when LOCAL_ONLY_POSTING=true.
        When LOCAL_ONLY_POSTING=true, this is called DIRECTLY by channel.py
        and news.py — skipping all cloud providers entirely.

        When LOCAL_ONLY_POSTING=false (legacy), this is used as a last-resort
        fallback after all cloud providers (Pollinations, Cloudflare) fail.

        Key differences from normal chat() with background priority:
          - Much shorter system prompt (4B model needs concise instructions)
          - Direct instruction format instead of elaborate persona
          - 1024 token limit (max for CPU speed)
          - Bypasses all cloud providers entirely
          - Simplified Nastya personality for small model

        Args:
            title: News article title
            summary: News article summary/text to rewrite
            category: News category (auto, tech, general, etc.)

        Returns:
            AIResponse from local model (may have lower quality than cloud)
        """
        if not self._local:
            return AIResponse(
                text="",
                provider="none",
                model="local-ruadapt-qwen3-4b",
                metadata={"error": "Local model not configured"},
            )

        # Try to make local model available
        if not self._local.is_available():
            try:
                await self._local.init()
                if not self._local.is_available():
                    return AIResponse(
                        text="",
                        provider="none",
                        model="local-ruadapt-qwen3-4b",
                        metadata={"error": "Local model not available after re-init"},
                    )
            except Exception as e:
                return AIResponse(
                    text="",
                    provider="none",
                    model="local-ruadapt-qwen3-4b",
                    metadata={"error": f"Local model init error: {e}"},
                )

        # Category-specific context
        category_note = ""
        if category == "auto":
            category_note = "Это авто-новость! Настя фанат BMW M3, разбирается в машинах. "
        elif category == "tech":
            category_note = "Это техно-новость! Настя следит за трендами. "
        elif category == "food":
            category_note = "Это кулинарная новость! Настя любит готовить. "
        elif category == "science":
            category_note = "Это научная новость! Настя любит удивительные факты. "
        elif category == "gaming":
            category_note = "Это игровая новость! Настя играет и следит за индустрией. "
        elif category == "lifestyle":
            category_note = "Это лайфстайл! Настя разбирается в моде и стиле. "
        elif category == "sports":
            category_note = "Это спорт! Настя следит за спортивными событиями. "

        # v12: Improved system prompt for 4B model with anti-hallucination
        # v14: Nastya is multi-faceted — adjust category note to fit the broader persona.
        local_system = (
            "Ты Настя — москвичка, 23 года, блогер, канал @chasnastya. "
            "Пишешь от себя, живо, коротко. "
            "Ты разносторонняя: кино, еда, путешествия, мода, технологии, психология, животные, новости. "
            "Ещё водишь BMW M3, но упоминаешь машину только когда уместно. "
            f"{category_note}"
            "Сленг: прикинь, офигеть, капец, круто, точняк. "
            "ВАЖНО: Не выдумывай факты, цифры, названия. Пиши только то что знаешь. "
            "Без политики. Без markdown. Без звёздочек. Без буллетов. Без заголовков. От первого лица. "
            "3-5 предложений."
        )

        user_msg = (
            f"Новость: {title}\n\n"
            f"{f'Текст: {summary[:500]}' if summary else ''}\n\n"
            f"Задача: Напиши ПОСТ для Telegram-канала на основе этой новости. "
            f"НЕ копируй — напиши СВОЙ текст. Добавь мнение или эмоцию от себя. "
            f"НЕ придумывай несуществующие детали.\n\n"
            f"Пиши компактно — 3-5 предложений. Без заголовков и буллетов."
        )

        # v70: RuadaptQwen3-4B-Instruct — tested & optimized!
        # Instruct version has no <think> tags — confirmed with 20+ test runs.
        # Russian tokenizer makes generation ~2x faster for Russian text.
        # v12: Lower temperature (0.75) reduces hallucination in 4B model.
        result = None
        try:
            result = await self._local.generate(
                user_msg,
                system_prompt=local_system,
                max_tokens=LOCAL_POST_MAX_TOKENS,  # 512 — posts are 3-5 sentences
                temperature=0.75,  # v12: Was 0.85 — lower reduces hallucination
            )
        except Exception as e:
            logger.error(f"LOCAL-ONLY post generation error: {e}")
            return AIResponse(
                text="",
                provider="local",
                model="local-ruadapt-qwen3-4b",
                metadata={"error": f"Local-only error: {e}"},
            )

        if result and result.text:
            cleaned = self.clean_ai_response(result.text)
            if cleaned:
                self._local_requests += 1
                logger.info(
                    "LOCAL-ONLY post generated: %d chars",
                    len(cleaned),
                )
                return AIResponse(
                    text=cleaned,
                    provider="local-only",
                    model=result.model or "local-ruadapt-qwen3-4b",
                    tokens_used=result.tokens_used,
                    metadata={**result.metadata, "role": "local_only_post"},
                )

        return AIResponse(
            text="",
            provider="local",
            model="local-ruadapt-qwen3-4b",
            metadata={"error": "Empty response from local model"},
        )

    async def transcribe_voice(self, ogg_bytes: bytes) -> Optional[str]:
        """Transcribe voice message."""
        return await transcribe_voice_ogg(ogg_bytes)

    async def generate_image(self, prompt: str, size: str = "1024x1024") -> Optional[bytes]:
        """Generate an image using Pollinations (PRIMARY).

        Returns image bytes or None on failure.
        Local model cannot generate images.
        """
        # Pollinations image generation
        if self._pollinations and self._pollinations.is_available():
            try:
                result = await self._pollinations.generate_image(prompt, size=size)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"Pollinations image generation error: {e}.")

        # Cloudflare doesn't support image generation - skip
        return None

    @staticmethod
    def clean_ai_response(text: str) -> str:
        """Aggressively clean AI response artifacts.
        
        v70: Added cleanup for 4B model artifacts:
          - Roleplay actions in asterisks (*смеётся*, *думает*)
          - Excessive emoji spam
          - Trailing incomplete sentences
        """
        if not text:
            return ""

        # Strip SSE artifacts
        sse_patterns = [
            r'data:\s*\{"type"\s*:\s*"start"\s*\}\s*',
            r'data:\s*\{"type"\s*:\s*"error"[^}]*\}\s*',
            r'data:\s*\[DONE\]\s*',
            r'data:\s*\{[^}]*"errorText"[^}]*\}\s*',
        ]
        for pattern in sse_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Strip think tags (v70: Ruadapt Instruct confirmed no <think> tags in 20+ tests)
        # Safety net kept for cloud models and edge cases
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<think\b[^>]*$', '', text, flags=re.IGNORECASE)

        # Strip AI disclaimers
        text = re.sub(r'(?:As an AI|Как AI|Как искусственный интеллект)[^.]*\.', '', text, flags=re.IGNORECASE)

        # Strip prefixes
        for prefix in ["Настя:", "Nastya:", "НАСТЯ:", "Assistant:", "Ответ Насти:"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        # Strip quotes
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]

        text = text.strip("*").strip()

        # Strip markdown
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)  # v70: Also strips roleplay *actions*
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[--]\s+', '', text, flags=re.MULTILINE)

        # v70: Clean up trailing incomplete sentences (4B model sometimes cuts off mid-sentence)
        # Remove trailing text that ends with "..." or "," or incomplete words
        text = re.sub(r'\s*\.{3,}\s*$', '.', text)  # Replace trailing ... with .
        text = re.sub(r',\s*$', '.', text)  # Replace trailing , with .
        # If text ends mid-sentence (no punctuation), add period
        if text and text[-1] not in '.!?…' and not text[-1].isspace():
            text += '.'

        # v70: Reduce excessive emoji spam (max 3 emojis in a row)
        text = re.sub(r'([\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF\U00002600-\U000026FF]){4,}', '', text)

        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        return text

    def get_fallback_response(self) -> str:
        return random.choice(FALLBACK_RESPONSES)

    def get_status(self) -> Dict[str, Any]:
        status = {}
        # Local model status
        status["llama_cpp"] = {
            "available": self._local is not None and self._local.is_available(),
            "role": "PRIMARY (chat + comment) / FALLBACK (function + background)",
            "model_loaded": self._local._loaded if self._local else False,
            "model_path": MODEL_PATH if ENABLE_LOCAL_MODEL else "DISABLED",
        }
        if self._local:
            try:
                status["llama_cpp"].update(self._local.get_stats())
            except Exception:
                pass

        # Pollinations status
        status["pollinations"] = {
            "available": self._pollinations is not None and self._pollinations.is_available(),
            "role": "PRIMARY (function + vision + background) / SECONDARY (chat + comment)",
            "models": len(CHAT_MODELS),
            "vision": True,
            "old_api_fallback": True,
        }
        if self._pollinations:
            try:
                status["pollinations"]["model_stats"] = self._pollinations.get_model_stats()
            except Exception:
                pass

        # Cloudflare status
        status["cloudflare"] = {
            "available": self._cloudflare is not None and self._cloudflare.is_available(),
            "role": "FALLBACK (all routes)",
            "model": "@cf/mistralai/mistral-small-3.1-24b-instruct",
            "vision": True,
            "dual_account": True,
        }
        if self._cloudflare:
            try:
                status["cloudflare"]["account_stats"] = self._cloudflare.get_account_stats()
            except Exception:
                pass

        status["_stats"] = {
            "total_requests": self._total_requests,
            "total_fallbacks": self._total_fallbacks,
            "local_requests": self._local_requests,
            "pollinations_requests": self._pollinations_requests,
            "cloudflare_requests": self._cloudflare_requests,
            "vision_requests": self._vision_requests,
            "strategy": "v70 LOCAL-FIRST: Local(RuadaptQwen3-4B-Instruct,v12-optimized)->Pollinations(KEY1+KEY2+OLD_API)->Cloudflare(Acct1+Acct2)->fallback (chat/comments: LOCAL-first, function: CLOUD-first, background: CLOUD-first+LOCAL-fallback, temp=0.75, rp=1.18, top_k=40)",
        }
        return status
