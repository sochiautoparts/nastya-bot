"""AI Router v61.0 — POLLINATIONS-ONLY ROUTING (no local model, cloud-first everything)!

АРХИТЕКТУРА v61 — POLLINATIONS-ONLY SMART ROUTING:
  Всё через Pollinations — локальная модель УДАЛЕНА!
  Provider's weighted round-robin handles model selection internally.

  CHAT route → Pollinations (provider's weighted round-robin handles model selection)
    - Simple tasks → REASONING_CHAT (fast models, no reasoning)
    - Complex tasks → REASONING_COMPLEX (better models, slight reasoning)
    - Cloud-only tasks → REASONING_COMPLEX

  FUNCTION route → Pollinations (best quality models)
    - REASONING_COMPLEX for high-quality public content

  COMMENT route → Pollinations (fast/cheap models) — NO MORE LOCAL-ONLY
    - REASONING_CHAT — fast models for casual group comments
    - If Pollinations fails → static fallback

  BACKGROUND route → Pollinations (cloud-first for quality posts)
    - REASONING_CHAT — balanced quality for background tasks
    - If Pollinations fails → skip (not critical)

  VISION → Pollinations vision models
    - 20+ vision-capable models
    - If Pollinations fails → fallback message

  When Pollinations raises ProviderError → static fallback (bot ALWAYS responds)

import logging
import random
import time
import re
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.pollinations_provider import (
    PollinationsProvider, REASONING_CHAT, REASONING_COMPLEX,
    MODEL_REASONING, MODEL_VISION, CHAT_MODELS,
)
from ai.voice import transcribe_voice_ogg
from bot.config import (
    POLLINATIONS_API_KEY, POLLINATIONS_API_KEY_2, POLLINATIONS_API_KEY_3, POLLINATIONS_MAX_TOKENS,
)

logger = logging.getLogger(__name__)

FALLBACK_RESPONSES = [
    "Ммм... Настя задумалась. Повтори? 🤔",
    "Ой, Настя отвлеклась... Что ты сказал? 😅",
    "Блин, Настя задумалась о вечном... Ещё раз? 💅",
    "Настя не расслышала... Говори ещё! 😏",
    "Ой, мысли улетели! Повтори для Насти? 💭",
]

# ── Task complexity detection for routing ──
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
        "simple" — fast models sufficient (REASONING_CHAT)
        "complex" — better models recommended (REASONING_COMPLEX)
        "cloud_only" — must use cloud (vision, etc.) (REASONING_COMPLEX)
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
    """AI Router v61.0 — POLLINATIONS-ONLY ROUTING: all routes through Pollinations.

    Strategy v61: Every route goes through PollinationsProvider.
    The provider's weighted round-robin handles model selection internally.
    No local model — Pollinations IS the provider.

    Route for CHAT (route_type="chat", default):
        Pollinations (fast models for simple, quality models for complex) → static fallback

    Route for FUNCTION (route_type="function"):
        Pollinations (best quality models) → static fallback

    Route for COMMENT (route_type="comment"):
        Pollinations (fast/cheap models) → static fallback
        No more local-only — Pollinations handles comments too!

    Route for VISION tasks (photos):
        Pollinations vision (20+ vision models) → fallback message

    Route for BACKGROUND tasks (news, channel):
        Pollinations → skip (not critical)
    """

    def __init__(self, db=None):
        self._pollinations: Optional[PollinationsProvider] = None
        self._db = db
        self._total_requests: int = 0
        self._total_fallbacks: int = 0
        self._pollinations_requests: int = 0
        self._vision_requests: int = 0
        self._last_cloud_success: float = 0

    async def init(self) -> None:
        """Initialize PollinationsProvider — the ONLY provider."""
        try:
            self._pollinations = PollinationsProvider(
                api_key=POLLINATIONS_API_KEY,
                api_key_2=POLLINATIONS_API_KEY_2,
                api_key_3=POLLINATIONS_API_KEY_3,
                timeout=45.0,
            )
            await self._pollinations.init()
            model_names = [m[0] for m in CHAT_MODELS]
            logger.info(
                f"PollinationsProvider initialized as SOLE provider "
                f"({len(CHAT_MODELS)} models: {', '.join(model_names[:5])}...)"
            )
        except Exception as e:
            logger.warning(f"PollinationsProvider init failed: {e}")
            self._pollinations = None

        # Log status
        pollinations_status = "active" if self._pollinations and self._pollinations.is_available() else "unavailable"

        logger.info(
            f"AI Router v61.0 POLLINATIONS-ONLY initialized: "
            f"pollinations={pollinations_status} ({len(CHAT_MODELS)} models + vision, triple-key=KEY1+KEY2+KEY3), "
            f"strategy=chat:POLLINATIONS/function:POLLINATIONS/comment:POLLINATIONS, "
            f"max_tokens={POLLINATIONS_MAX_TOKENS}"
        )

    async def close(self) -> None:
        """Close provider."""
        if self._pollinations:
            try:
                await self._pollinations.close()
            except Exception:
                pass

    async def chat(self, prompt: str, system_prompt: str = "",
                   messages: Optional[List[Dict]] = None, **kwargs) -> AIResponse:
        """Route chat based on route_type — ALL through Pollinations.

        route_type (kwarg):
            "chat" (default) — Pollinations with complexity-based reasoning effort
            "function" — Pollinations with REASONING_COMPLEX (best quality for posts)
            "comment" — Pollinations with REASONING_CHAT (fast models for comments)

        Both routes receive FULL context (web search, partner links, etc.)
        The system_prompt already contains all enriched context from chat.py.
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
        """Route vision request: Pollinations vision (multi-model) → fallback."""
        self._total_requests += 1
        self._vision_requests += 1

        # ── Pollinations Vision — SOLE provider ──
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

        # ── No fallback for vision — local model can't see images ──
        self._total_fallbacks += 1
        logger.warning("Vision failed — Pollinations unavailable")
        return AIResponse(
            text="Ой, Настя не может разглядеть фотку... Попробуй ещё раз? 📸💅",
            provider="fallback",
            model="none",
            tokens_used=0,
            metadata={"role": "vision_fallback"},
        )

    async def _route_chat(self, prompt: str, system_prompt: str,
                          messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Chat route: ALL through Pollinations with complexity-based reasoning.

        route_type:
            "chat" — Pollinations (complexity-based reasoning: simple→none, complex→low)
            "function" — Pollinations (REASONING_COMPLEX for best quality)
            "comment" — Pollinations (REASONING_CHAT for fast responses)
        """
        route_type = kwargs.get("route_type", "chat")

        # Determine reasoning effort based on route_type and complexity
        if route_type == "comment":
            # Comments → fast models (REASONING_CHAT)
            reasoning = REASONING_CHAT
        elif route_type == "function":
            # Functions/posts → best quality (REASONING_COMPLEX)
            reasoning = REASONING_COMPLEX
        else:
            # Normal chat → complexity-based routing
            complexity = _classify_task_complexity(prompt, messages)
            if complexity in ("cloud_only", "complex"):
                reasoning = REASONING_COMPLEX
            else:
                reasoning = REASONING_CHAT

        # ── Pollinations — SOLE provider ──
        if self._pollinations and self._pollinations.is_available():
            try:
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
                        self._last_cloud_success = time.time()
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={
                                **result.metadata,
                                "role": f"pollinations_{route_type}",
                                "complexity_reasoning": reasoning,
                            },
                        )
            except ProviderError as e:
                err_str = str(e)
                if "429" in err_str:
                    logger.warning(f"Pollinations rate-limited! Using static fallback.")
                elif "All models failed" in err_str or "402" in err_str:
                    logger.warning(f"Pollinations unavailable (402/429)! Using static fallback.")
                else:
                    logger.warning(f"Pollinations chat error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations unexpected error: {e}")

        # ── Static fallback — bot ALWAYS responds ──
        self._total_fallbacks += 1
        logger.error("Pollinations unavailable! Using static fallback.")
        return AIResponse(
            text=self.get_fallback_response(),
            provider="fallback",
            model="none",
            tokens_used=0,
        )

    async def _route_background(self, prompt: str, system_prompt: str,
                                messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Background route: Pollinations → skip (not critical).

        Background tasks (news, channel posts) need quality but aren't critical.
        If Pollinations fails, we skip rather than use a fallback message.
        """
        # ── Pollinations — SOLE provider for background ──
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
                            metadata={**result.metadata, "role": "bg_pollinations"},
                        )
            except ProviderError as e:
                logger.warning(f"Pollinations bg error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations bg unexpected: {e}")

        # ── Background failed — not critical ──
        self._total_fallbacks += 1
        logger.warning("Background task failed (Pollinations unavailable). Skipping.")
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
        # Pollinations status — SOLE provider
        status["pollinations"] = {
            "available": self._pollinations is not None and self._pollinations.is_available(),
            "role": "SOLE PROVIDER (chat + function + comment + vision + background)",
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
            "pollinations_requests": self._pollinations_requests,
            "vision_requests": self._vision_requests,
            "strategy": "POLLINATIONS-ONLY (chat:complexity-routed, function:COMPLEX, comment:CHAT, background:CHAT)",
        }
        return status
