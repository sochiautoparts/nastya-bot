"""AI Router v60.0 — DUAL-KEY SMART ROUTING (CLOUD-FIRST for posts, LOCAL-FIRST for chat, LOCAL-ONLY for comments)!

АРХИТЕКТУРА v60 — DUAL-KEY SMART ROUTING:
  Баланс обновляется каждый час — экономим!
  
  ПОСТЫ И ФУНКЦИИ (generate_channel_post, background tasks) → CLOUD-FIRST:
    1. PollinationsProvider (44-MODEL, DUAL-KEY) — PRIMARY для постов и функций!
       - Лучшее качество для публичного контента
       - Посты в канале должны быть качественными!
       - KEY1 → KEY2 → free tier — dual-key failover!
    2. LlamaCppProvider — FALLBACK для постов
    3. Static fallback — бот ВСЕГДА отвечает

  ЧАТ (обычные сообщения — ЛОКАЛЬНАЯ МОДЕЛЬ ПЕРВИЧНА):
    1. LlamaCppProvider (Qwen3-4B, n_ctx=4096) — PRIMARY для чата!
       - Экономит баланс облачных моделей
       - Работает мгновенно (5-12с генерация)
    2. PollinationsProvider v17 (44-MODEL, DUAL-KEY) — FALLBACK для чата
    3. Static fallback — бот ВСЕГДА отвечает

  КОММЕНТАРИИ В ГРУППАХ → LOCAL-ONLY:
    - Только локальная модель — никакого облака!
    - Если локальная не справилась — static fallback
    - Экономия баланса на casual комментариях!

  VISION (фото-понимание):
    - Pollinations vision API — Настя ВИДИТ фото!
    - 20 vision-capable моделей

  Если Pollinations не работает → ВСЁ на локальной модели!

import logging
import asyncio
import random
import time
import re
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.pollinations_provider import (
    PollinationsProvider, REASONING_CHAT, REASONING_COMPLEX,
    MODEL_REASONING, MODEL_VISION, CHAT_MODELS,
)
try:
    from ai.providers.llama_cpp_provider import LlamaCppProvider
    _LLAMA_CPP_AVAILABLE = True
except ImportError:
    LlamaCppProvider = None
    _LLAMA_CPP_AVAILABLE = False
from ai.voice import transcribe_voice_ogg
from bot.config import (
    MODEL_PATH, MODEL_N_CTX, MODEL_N_THREADS,
    MODEL_MAX_TOKENS, MODEL_HISTORY_LIMIT,
    POLLINATIONS_API_KEY, POLLINATIONS_API_KEY_2, POLLINATIONS_MAX_TOKENS,
    ENABLE_LOCAL_MODEL,
)

logger = logging.getLogger(__name__)

FALLBACK_RESPONSES = [
    "Ммм... Настя задумалась. Повтори? 🤔",
    "Ой, Настя отвлеклась... Что ты сказал? 😅",
    "Блин, Настя задумалась о вечном... Ещё раз? 💅",
    "Настя не расслышала... Говори ещё! 😏",
    "Ой, мысли улетели! Повтори для Насти? 💭",
]

# ── Task complexity detection for LOCAL-FIRST routing ──
# Keywords that indicate a SIMPLE task (local model can handle)
_SIMPLE_TASK_KEYWORDS = [
    "привет", "как дела", "пока", "спасибо", "ок", "ладно",
    "что делаешь", "скучно", "доброе утро", "добрый день", "добрый вечер",
    "спокойной ночи", "как настроен", "чем занята", "расскажи о себе",
    "кто ты", "что ты", "сколько тебе", "где ты живёшь",
    "хаха", "ахах", "лол", "прикольно", "круто", "класс",
    "да", "нет", "норм", "супер", "окей", "ага", "угу",
    "обнимаю", "целую", "❤", "💕", "😊", "😂",
]

# Keywords that indicate a COMPLEX task (cloud model needed)
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
    # Long messages (>200 chars) are also considered complex
]

# Keywords that MANDATE cloud model (no local fallback possible)
_CLOUD_ONLY_KEYWORDS = [
    "фото", "изображен", "снимок", "сканер", "документ",
    "птс", "стс", "картинку", "посмотри",
]


def _classify_task_complexity(prompt: str, messages: Optional[List[Dict]] = None) -> str:
    """Classify task complexity to route to local or cloud model.
    
    Returns:
        "simple" — local model can handle (saves cloud balance)
        "complex" — cloud model recommended
        "cloud_only" — must use cloud (vision, etc.)
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
    """AI Router v60.0 — DUAL-KEY SMART ROUTING: cloud-first for posts, local-first for chat, local-only for comments.

    Strategy v60: Route based on USE CASE with dual-key failover.
    
    Route for POSTS & FUNCTIONS (route_type="function"):
        Pollinations (44 models, KEY1→KEY2) → LOCAL model → static fallback
        Cloud gives better quality for public content!
    
    Route for USER CHAT (route_type="chat", default):
        LOCAL model → Pollinations (dual-key) → static fallback
        Saves cloud balance for normal conversation.
    
    Route for GROUP COMMENTS (route_type="comment"):
        LOCAL model only → static fallback
        No cloud waste on casual comments!
    
    Route for VISION tasks (photos):
        Pollinations vision (20 models, dual-key) → fallback message
    
    Route for BACKGROUND tasks (news, channel):
        Pollinations (dual-key) → LOCAL model → skip
        Posts need cloud quality!

    If Pollinations is down or both keys depleted → ALL routes fall back to local model!
    """

    def __init__(self, db=None):
        self._pollinations: Optional[PollinationsProvider] = None
        self._local: Optional[LlamaCppProvider] = None
        self._db = db
        self._total_requests: int = 0
        self._total_fallbacks: int = 0
        self._pollinations_requests: int = 0
        self._local_requests: int = 0
        self._local_primary_count: int = 0
        self._cloud_primary_count: int = 0
        self._local_fallback_count: int = 0
        self._vision_requests: int = 0
        # Balance conservation tracking
        self._last_cloud_success: float = 0
        self._cloud_balance_available: bool = True
        self._balance_check_interval: int = 600  # Check every 10 min

    async def init(self) -> None:
        """Initialize providers: LlamaCpp PRIMARY + Pollinations COMPLEX."""
        # ── 1. LlamaCpp — LOCAL PRIMARY (for simple tasks, saves balance!) ──
        if ENABLE_LOCAL_MODEL and MODEL_PATH and _LLAMA_CPP_AVAILABLE and LlamaCppProvider is not None:
            try:
                self._local = LlamaCppProvider(
                    model_path=MODEL_PATH,
                    timeout=45.0,
                    model_config={
                        "n_ctx": max(MODEL_N_CTX, 4096),  # Minimum 4096!
                        "n_batch": 512,  # Faster batch processing
                        "n_threads": MODEL_N_THREADS,
                        "n_gpu_layers": 0,
                        "verbose": False,
                        "use_mmap": True,
                        "use_mlock": False,
                    },
                    gen_config={
                        "max_tokens": max(MODEL_MAX_TOKENS, 300),  # Minimum 300!
                        "temperature": 0.82,
                        "top_p": 0.92,
                        "top_k": 50,
                        "repeat_penalty": 1.12,
                    },
                )
                await self._local.init()
                logger.info("LlamaCppProvider initialized as LOCAL PRIMARY (saves cloud balance!)")
            except Exception as e:
                logger.warning(f"LlamaCppProvider init failed: {e}")
                self._local = None
        else:
            if not _LLAMA_CPP_AVAILABLE:
                logger.info("llama-cpp-python not installed — running cloud-only")
            elif ENABLE_LOCAL_MODEL:
                logger.info("ENABLE_LOCAL_MODEL=true but no MODEL_PATH — running cloud-only")
            else:
                logger.info("Local model DISABLED (ENABLE_LOCAL_MODEL not set) — running cloud-only")

        # ── 2. Pollinations — CLOUD FOR COMPLEX TASKS ──
        try:
            self._pollinations = PollinationsProvider(
                api_key=POLLINATIONS_API_KEY,
                api_key_2=POLLINATIONS_API_KEY_2,
                timeout=45.0,
            )
            await self._pollinations.init()
            model_names = [m[0] for m in CHAT_MODELS]
            logger.info(
                f"PollinationsProvider initialized for COMPLEX TASKS "
                f"({len(CHAT_MODELS)} models: {', '.join(model_names[:5])}...)"
            )
        except Exception as e:
            logger.warning(f"PollinationsProvider init failed: {e}")
            self._pollinations = None

        # Log status
        local_status = "not_installed" if not _LLAMA_CPP_AVAILABLE else ("disabled" if not ENABLE_LOCAL_MODEL else ("active" if self._local and self._local.is_available() else "unavailable"))
        pollinations_status = "active" if self._pollinations and self._pollinations.is_available() else "unavailable"
        model_name = self._local._model_name if self._local and self._local._loaded else "none"

        logger.info(
            f"AI Router v60.0 DUAL-KEY SMART ROUTING initialized: "
            f"local={local_status} (model={model_name}, n_ctx={max(MODEL_N_CTX, 4096)}, ENABLE_LOCAL_MODEL={ENABLE_LOCAL_MODEL}), "
            f"pollinations={pollinations_status} ({len(CHAT_MODELS)} models + vision, dual-key=KEY1+KEY2), "
            f"strategy=chat:LOCAL_FIRST/function:CLOUD_FIRST/comment:LOCAL_ONLY, "
            f"max_tokens={POLLINATIONS_MAX_TOKENS}(cloud)/{max(MODEL_MAX_TOKENS, 300)}(local), history={MODEL_HISTORY_LIMIT}"
        )

    async def close(self) -> None:
        """Close all providers."""
        if self._pollinations:
            try:
                await self._pollinations.close()
            except Exception:
                pass
        if self._local:
            try:
                await self._local.close()
            except Exception:
                pass

    async def chat(self, prompt: str, system_prompt: str = "",
                   messages: Optional[List[Dict]] = None, **kwargs) -> AIResponse:
        """Route chat based on route_type: chat (local-first), function (cloud-first), comment (local-only).
        
        route_type (kwarg):
            "chat" (default) — LOCAL-FIRST: Local → Cloud → Fallback. Saves balance.
            "function" — CLOUD-FIRST: Cloud → Local → Fallback. Better quality for posts.
            "comment" — LOCAL-ONLY: Local → Static fallback. No cloud waste on casual comments.
        
        Both local and cloud models receive FULL context (web search, partner links, etc.)
        The system_prompt already contains all enriched context from chat.py.
        Local model gets a compact version that fits its context window.
        """
        self._total_requests += 1
        priority = kwargs.get("priority", "high")
        route_type = kwargs.get("route_type", "chat")

        if priority == "high":
            return await self._route_chat(prompt, system_prompt, messages, route_type=route_type, **kwargs)
        else:
            return await self._route_background(prompt, system_prompt, messages, **kwargs)

    async def vision(self, prompt: str, image_data: bytes,
                     image_format: str = "jpeg", system_prompt: str = "",
                     **kwargs) -> AIResponse:
        """Route vision request: Pollinations vision (multi-model) → fallback."""
        self._total_requests += 1
        self._vision_requests += 1

        # ── 1. Pollinations Vision — PRIMARY ──
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
                logger.warning(f"Pollinations vision error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations vision unexpected error: {e}")

        # ── 2. No fallback for vision — local model can't see images ──
        self._total_fallbacks += 1
        logger.warning("Vision failed — Pollinations unavailable, local model can't see images")
        return AIResponse(
            text="Ой, Настя не может разглядеть фотку... Попробуй ещё раз? 📸💅",
            provider="fallback",
            model="none",
            tokens_used=0,
            metadata={"role": "vision_fallback"},
        )

    async def _route_chat(self, prompt: str, system_prompt: str,
                          messages: Optional[List[Dict]], route_type: str = "chat", **kwargs) -> AIResponse:
        """Chat route: dispatch based on route_type.
        
        route_type:
            "chat" — LOCAL-FIRST: Local → Cloud → Fallback (saves balance for normal chat)
            "function" — CLOUD-FIRST: Cloud → Local → Fallback (better quality for posts/functions)
            "comment" — LOCAL-ONLY: Local → Static fallback (no cloud waste on casual comments)
        """
        if route_type == "comment":
            # ── COMMENTS IN GROUPS → LOCAL ONLY (no cloud waste!) ──
            return await self._route_local_only(prompt, system_prompt, messages, **kwargs)
        elif route_type == "function":
            # ── POSTS & FUNCTIONS → CLOUD FIRST (better quality for public content!) ──
            return await self._route_cloud_first(prompt, system_prompt, messages, **kwargs)
        else:
            # ── NORMAL CHAT → LOCAL-FIRST (saves cloud balance!) ──
            complexity = _classify_task_complexity(prompt, messages)
            if complexity == "cloud_only":
                return await self._route_cloud_first(prompt, system_prompt, messages, **kwargs)
            elif complexity == "complex":
                return await self._route_cloud_first(prompt, system_prompt, messages, **kwargs)
            else:
                return await self._route_local_first(prompt, system_prompt, messages, **kwargs)

    async def _route_local_first(self, prompt: str, system_prompt: str,
                                  messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """LOCAL-FIRST route: Local → Pollinations → static fallback.
        Used for simple chat tasks to conserve cloud balance.
        
        v59: Local model now receives FULL context (web search, partner links, etc.)
        The system_prompt from chat.py contains web search results and partner links.
        We extract key info and include it in the local model's prompt.
        """
        # ── 1. LlamaCpp — LOCAL PRIMARY ──
        if self._local and self._local.is_available():
            try:
                # Build local system prompt with FULL context from chat.py
                local_base = (
                    "Ты Настя — москвичка, 23 года, блогер, ведёшь канал @chasnastya. "
                    "Владеешь BMW M3 серого цвета. "
                    "Пиши от себя, живо, как в мессенджере. "
                    "Без политики, без markdown. "
                    f"Сейчас {time.strftime('%d.%m.%Y %H:%M')} по Москве."
                )
                # Extract web search and partner context from the full system prompt
                # The full system_prompt contains enriched context from chat.py
                # We include it in the local model's prompt (it will be truncated if too long)
                extra_context = ""
                if system_prompt:
                    # Extract web search results section
                    search_match = re.search(r'🔍.*?(?:⚠️|\Z)', system_prompt, re.DOTALL)
                    if search_match:
                        extra_context += "\n\n" + search_match.group(0)[:600]
                    # Extract partner links section
                    partner_match = re.search(r'Партнёрск.*?(?:естественно\.)', system_prompt, re.DOTALL)
                    if partner_match:
                        extra_context += "\n\n" + partner_match.group(0)[:400]
                    # Extract news context
                    news_match = re.search(r'Свежие новости:.*?(?:ссылку!|$)', system_prompt)
                    if news_match:
                        extra_context += "\n\n" + news_match.group(0)[:300]
                local_system_prompt = local_base + extra_context
                result = await self._local.generate(
                    prompt,
                    system_prompt=local_system_prompt,
                    messages=messages,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._local_requests += 1
                        self._local_primary_count += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "local_primary"},
                        )
            except ProviderError as e:
                logger.warning(f"Local model error (simple task): {e}")
            except Exception as e:
                logger.warning(f"Unexpected local model error: {e}")

        # ── 2. Pollinations — CLOUD FALLBACK ──
        if self._pollinations and self._pollinations.is_available():
            try:
                reasoning = REASONING_CHAT  # Simple task doesn't need reasoning
                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    max_tokens=POLLINATIONS_MAX_TOKENS,
                    reasoning_effort=reasoning,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._pollinations_requests += 1
                        self._cloud_primary_count += 1
                        self._last_cloud_success = time.time()
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "cloud_fallback"},
                        )
            except ProviderError as e:
                logger.warning(f"Pollinations chat error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations unexpected error: {e}")

        # ── 3. Static fallback — bot ALWAYS responds ──
        self._total_fallbacks += 1
        logger.error("All AI providers unavailable! Using static fallback.")
        return AIResponse(
            text=self.get_fallback_response(),
            provider="fallback",
            model="none",
            tokens_used=0,
        )

    async def _route_cloud_first(self, prompt: str, system_prompt: str,
                                  messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """CLOUD-FIRST route: Pollinations → Local → static fallback.
        Used for complex tasks where cloud model quality is needed.
        """
        # ── 1. Pollinations — CLOUD PRIMARY ──
        if self._pollinations and self._pollinations.is_available():
            try:
                reasoning = REASONING_COMPLEX if len(prompt) > 300 else REASONING_CHAT
                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    max_tokens=POLLINATIONS_MAX_TOKENS,
                    reasoning_effort=reasoning,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._pollinations_requests += 1
                        self._cloud_primary_count += 1
                        self._last_cloud_success = time.time()
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "cloud_primary"},
                        )
            except ProviderError as e:
                err_str = str(e)
                if "429" in err_str:
                    logger.warning("Pollinations rate-limited! Falling back to local model.")
                elif "All models failed" in err_str or "402" in err_str:
                    logger.warning("Pollinations unavailable (402/429)! Falling back to local model.")
                else:
                    logger.warning(f"Pollinations chat error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations unexpected error: {e}")

        # ── 2. LlamaCpp — LOCAL FALLBACK (with full context!) ──
        if self._local and self._local.is_available():
            try:
                # Build local system prompt with FULL context
                local_base = (
                    "Ты Настя — москвичка, 23 года, блогер, ведёшь канал @chasnastya. "
                    "Владеешь BMW M3 серого цвета. "
                    "Пиши от себя, живо, как в мессенджере. "
                    "Без политики, без markdown. "
                    f"Сейчас {time.strftime('%d.%m.%Y %H:%M')} по Москве."
                )
                # Extract web search and partner context from the full system prompt
                extra_context = ""
                if system_prompt:
                    search_match = re.search(r'🔍.*?(?:⚠️|\Z)', system_prompt, re.DOTALL)
                    if search_match:
                        extra_context += "\n\n" + search_match.group(0)[:600]
                    partner_match = re.search(r'Партнёрск.*?(?:естественно\.)', system_prompt, re.DOTALL)
                    if partner_match:
                        extra_context += "\n\n" + partner_match.group(0)[:400]
                    news_match = re.search(r'Свежие новости:.*?(?:ссылку!|$)', system_prompt)
                    if news_match:
                        extra_context += "\n\n" + news_match.group(0)[:300]
                local_system_prompt = local_base + extra_context
                result = await self._local.generate(
                    prompt,
                    system_prompt=local_system_prompt,
                    messages=messages,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._local_requests += 1
                        self._local_fallback_count += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "local_fallback"},
                        )
            except ProviderError as e:
                logger.warning(f"Local model chat error: {e}")
            except Exception as e:
                logger.error(f"Unexpected local model error: {e}")

        # ── 3. Static fallback — bot ALWAYS responds ──
        self._total_fallbacks += 1
        logger.error("All AI providers unavailable! Using static fallback.")
        return AIResponse(
            text=self.get_fallback_response(),
            provider="fallback",
            model="none",
            tokens_used=0,
        )

    async def _route_local_only(self, prompt: str, system_prompt: str,
                                 messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """LOCAL-ONLY route: Local model → static fallback.
        Used for comments in groups — no cloud waste on casual comments!
        If local fails, use static fallback (never cloud).
        """
        # ── 1. LlamaCpp — LOCAL ONLY (no cloud for comments!) ──
        if self._local and self._local.is_available():
            try:
                # Build local system prompt for group comments
                local_base = (
                    "Ты Настя — москвичка, 23 года, блогер. "
                    "Пиши коротко, живо, как в мессенджере. "
                    "Без политики, без markdown. "
                    f"Сейчас {time.strftime('%d.%m.%Y %H:%M')} по Москве."
                )
                # Extract any relevant context from system prompt
                extra_context = ""
                if system_prompt:
                    search_match = re.search(r'🔍.*?(?:⚠️|\Z)', system_prompt, re.DOTALL)
                    if search_match:
                        extra_context += "\n\n" + search_match.group(0)[:400]
                local_system_prompt = local_base + extra_context
                result = await self._local.generate(
                    prompt,
                    system_prompt=local_system_prompt,
                    messages=messages,
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
                            metadata={**result.metadata, "role": "comment_local_only"},
                        )
            except ProviderError as e:
                logger.warning(f"Local model error (comment): {e}")
            except Exception as e:
                logger.warning(f"Unexpected local model error (comment): {e}")

        # ── 2. Static fallback — no cloud for comments! ──
        self._total_fallbacks += 1
        logger.warning("Local model unavailable for comment — using static fallback (no cloud waste!).")
        return AIResponse(
            text=self.get_fallback_response(),
            provider="fallback",
            model="none",
            tokens_used=0,
            metadata={"role": "comment_fallback"},
        )

    async def _route_background(self, prompt: str, system_prompt: str,
                                messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Background route: CLOUD-FIRST for quality posts! Cloud → Local → skip.
        v59: Posts and channel content need cloud quality — local as fallback only.
        """
        # ── 1. Pollinations — CLOUD PRIMARY for background (better quality posts!) ──
        if self._pollinations and self._pollinations.is_available():
            try:
                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    max_tokens=300,
                    reasoning_effort=REASONING_CHAT,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._pollinations_requests += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "bg_cloud_primary"},
                        )
            except ProviderError as e:
                logger.warning(f"Pollinations bg error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations bg unexpected: {e}")

        # ── 2. LOCAL model — FALLBACK for background ──
        if self._local and self._local.is_available():
            try:
                # Short system prompt for local model
                local_system_prompt = (
                    "Ты Настя — москвичка, 23 года, блогер, ведёшь канал @chasnastya. "
                    "Пиши живо, с эмоциями, как в мессенджере. "
                    f"Сейчас {time.strftime('%d.%m.%Y %H:%M')} по Москве."
                )
                result = await self._local.generate(
                    prompt,
                    system_prompt=local_system_prompt,
                    messages=messages,
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
                            metadata={**result.metadata, "role": "bg_local_fallback"},
                        )
            except ProviderError as e:
                logger.warning(f"Local bg error: {e}")
            except Exception as e:
                logger.error(f"Unexpected local bg error: {e}")

        # ── 3. Background failed — not critical ──
        self._total_fallbacks += 1
        logger.warning("Background task failed (all providers unavailable). Skipping.")
        return AIResponse(
            text="",
            provider="none",
            model="none",
            tokens_used=0,
            metadata={"skipped": True},
        )

    async def transcribe_voice(self, ogg_bytes: bytes) -> Optional[str]:
        """Transcribe voice message."""
        return await transcribe_voice_ogg(ogg_bytes)

    async def generate_image(self, prompt: str, size: str = "1024x1024") -> Optional[bytes]:
        """Generate an image using Pollinations image API.

        Returns image bytes or None on failure.
        """
        if self._pollinations and self._pollinations.is_available():
            try:
                return await self._pollinations.generate_image(prompt, size=size)
            except Exception as e:
                logger.warning(f"Image generation error: {e}")
        return None

    @staticmethod
    def clean_ai_response(text: str) -> str:
        """Aggressively clean AI response artifacts."""
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

        # Strip think tags (Qwen3, reasoning models)
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<think\b[^>]*$', '', text, flags=re.IGNORECASE)

        # Strip /no_think prefix
        text = re.sub(r'^/no_think\s*', '', text)

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
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[-•]\s+', '', text, flags=re.MULTILINE)

        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        return text

    def get_fallback_response(self) -> str:
        return random.choice(FALLBACK_RESPONSES)

    def get_status(self) -> Dict[str, Any]:
        status = {}
        # Local model status — PRIMARY
        if self._local:
            stats = self._local.get_stats()
            status["local"] = {
                "available": self._local.is_available(),
                "role": "PRIMARY (simple chat)",
                **stats,
            }
        else:
            status["local"] = {"available": False, "role": "PRIMARY (simple chat)", "model_name": "none"}
        # Pollinations status — COMPLEX TASKS
        status["pollinations"] = {
            "available": self._pollinations is not None and self._pollinations.is_available(),
            "role": "COMPLEX TASKS + VISION",
            "models": len(CHAT_MODELS),
            "vision": True,
        }
        if self._pollinations:
            try:
                status["pollinations"]["model_stats"] = self._pollinations.get_model_stats()
            except Exception:
                pass
        status["_stats"] = {
            "total_requests": self._total_requests,
            "total_fallbacks": self._total_fallbacks,
            "local_requests": self._local_requests,
            "local_primary_count": self._local_primary_count,
            "cloud_primary_count": self._cloud_primary_count,
            "pollinations_requests": self._pollinations_requests,
            "local_fallback_count": self._local_fallback_count,
            "vision_requests": self._vision_requests,
            "strategy": "SMART_ROUTING (chat:LOCAL_FIRST, function:CLOUD_FIRST, comment:LOCAL_ONLY)",
        }
        return status
