"""Pollinations.ai Provider v14.0 — EXPANDED 30+ MODEL LOAD BALANCING + IMAGE GEN!

v14.0 UPDATE — 30 models, image generation, no restrictions, full responses:
  - PRIMARY: 'openai' (GPT-5.4 Nano) — fast, vision-capable
  - BACKUP 1: 'mistral' (Mistral Small 3.2) — fast, good Russian
  - BACKUP 2: 'gpt-5.4-mini' (GPT-5.4 Mini) — balanced, fast
  - BACKUP 3: 'deepseek' (DeepSeek V4 Flash) — reasoning, cheap
  - BACKUP 4: 'mistral-4' (Mistral Small 4) — better, multimodal
  - BACKUP 5: 'gemma' (Gemma 4 26B) — fast MoE, vision
  - BACKUP 6: 'llama-scout' (Llama 4 Scout) — long ctx, vision
  - BACKUP 7: 'openai-fast' (GPT-5 Nano) — emergency fallback
  - QUALITY: 'gpt-5.5' — latest GPT model, reasoning + vision
  - REASONING: 'deepseek-pro' — DeepSeek Pro, better reasoning
  - POWER: 'mistral-large' — powerful, vision, reasoning, 256k ctx
  - POWER: 'qwen-vision-pro' — better vision + reasoning
  - POWER: 'kimi-k2.6' — latest Kimi, better multilingual
  - POWER: 'nova-fast' — Amazon Nova fast, good Russian
  - POWER: 'glm' — ChatGLM, good multilingual + Chinese/Russian
  - POWER: 'minimax' — MiniMax, good for chat
  - POWER: 'qwen-large' — Qwen Large, powerful reasoning
  - v55: NEW MODELS from Pollinations catalog!
  - NEW: 'nova' — Amazon Nova, vision + reasoning, 1M ctx
  - NEW: 'mistral-small' — Mistral Small, fast, good Russian
  - NEW: 'polly' — Polly, vision + reasoning
  - NEW: 'perplexity-fast' — Perplexity, fast web search
  - NEW: 'perplexity' — Perplexity, deep web search, 200k ctx
  - NEW: 'qwen-vision' — Qwen3 VL, vision specialist
  - NEW: 'llama' — Llama 3.3 70B, strong reasoning
  - NEW: 'step-flash' — Step Flash, fast + vision
  - REASONING: 'openai-large' (GPT-5.4) — for complex questions
  - VISION: 'openai' — supports image input!
  - IMAGE GEN: Pollinations /v1/images/generations (flux model)
  - REMOVED: grok-large (500), grok-4.3 (timeout), gemini (402),
             gemini-3.5-flash (402), llama-maverick (402), kimi (timeout)
"""
import base64
import json
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

BASE_URL = "https://gen.pollinations.ai"

# ── Model Selection — LOAD BALANCING POOL ──
# Models are ordered by priority/quality for chat
# Each model has: cost tier, vision support, reasoning support

CHAT_MODELS = [
    # (model_name, weight_for_round_robin, supports_vision, cost_tier)
    # Cost tiers: 1=cheapest, 2=cheap, 3=moderate, 4=expensive
    # Models from Pollinations catalog (June 2026) — tested and verified!
    ("openai",       4, True,  1),   # GPT-5.4 Nano — PRIMARY, fast, vision, cheapest
    ("mistral",      3, True,  1),   # Mistral Small 3.2 — fast, good multilingual
    ("gpt-5.4-mini", 3, True,  2),   # GPT-5.4 Mini — balanced speed & cost
    ("deepseek",     2, False, 1),   # DeepSeek V4 Flash — reasoning, cheap
    ("mistral-4",    2, True,  2),   # Mistral Small 4 — better, multimodal
    ("gemma",        2, True,  1),   # Gemma 4 26B — fast MoE, vision + reasoning
    ("llama-scout",  1, True,  1),   # Llama 4 Scout — long context, vision
    ("openai-fast",  1, True,  1),   # GPT-5 Nano — ultra fast fallback
    # v49: Quality models
    ("gpt-5.5",      2, True,  3),   # GPT-5.5 — latest model, reasoning + vision
    ("deepseek-pro", 1, False, 2),   # DeepSeek Pro — better reasoning
    # v49: Powerful models
    ("mistral-large", 1, True,  3),   # Mistral Large — powerful, vision + reasoning
    ("qwen-vision-pro",1, True,  2),  # Qwen Vision Pro — better vision + reasoning
    ("kimi-k2.6",     1, True,  3),   # Kimi K2.6 — latest, better multilingual
    ("nova-fast",     2, True,  2),   # Amazon Nova Fast — good Russian, fast
    ("glm",           1, True,  2),   # ChatGLM — good multilingual
    ("minimax",       1, True,  2),   # MiniMax — good for chat
    ("qwen-large",    1, True,  3),   # Qwen Large — powerful reasoning
    # v55: NEW tested and verified models!
    ("nova",          1, True,  3),   # Amazon Nova — vision + reasoning, 1M ctx
    ("mistral-small", 2, True,  1),   # Mistral Small — fast, good Russian
    ("polly",         1, True,  2),   # Polly — vision + reasoning
    ("perplexity-fast",1, False, 1),  # Perplexity — fast web search
    ("perplexity",    1, False, 2),   # Perplexity — deep web search, 200k ctx
    ("qwen-vision",   1, True,  2),   # Qwen3 VL — vision specialist
    ("llama",         1, False, 1),   # Llama 3.3 70B — strong reasoning
    ("grok",          1, True,  2),   # Grok — vision, good Russian (was 500, working now)
    # v56: NEW tested and verified models!
    ("qwen-coder",    1, False, 1),   # Qwen3 Coder 30B — code + reasoning, good for technical
    ("openai-large",  1, True,  4),   # GPT-5.4 — reasoning model for complex questions
    ("kimi",          1, True,  2),   # Kimi — latest, good multilingual (was timeout, working now)
    ("perplexity-deep",1, False, 2),  # Perplexity Deep — deep web search + reasoning
    # REMOVED v55: grok-large (500 Internal Server Error)
    # REMOVED v55: grok-4.3 (timeout issues)
    # REMOVED v55: gemini (402 Payment Required)
    # REMOVED v55: gemini-3.5-flash (402 Payment Required)
    # REMOVED v55: llama-maverick (402 Payment Required)
    # REMOVED v55: claude-fast (402 Payment Required)
    # REMOVED v55: kimi (timeout issues)
]

MODEL_REASONING = "openai-large"    # GPT-5.4 — for complex questions
MODEL_VISION = "openai"             # Vision model (same as chat — supports images!)

# Reasoning effort levels: 'none', 'minimal', 'low', 'medium', 'high'
REASONING_CHAT = "none"       # Fastest for regular chat
REASONING_COMPLEX = "low"     # Slight reasoning for complex questions


def _strip_reasoning(text: str) -> str:
    """Remove reasoning/thinking content from response."""
    if not text:
        return ""
    text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<think\b[^>]*$', '', text, flags=re.IGNORECASE)
    return text.strip()


def _strip_sse_artifacts(text: str) -> str:
    """Remove SSE/streaming artifacts from Pollinations response."""
    if not text:
        return ""
    text = text.strip()
    if not text.startswith("data:"):
        return text

    content_parts = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            data = json.loads(data_str)
            if isinstance(data, dict):
                msg_type = data.get("type", "")
                if msg_type == "error":
                    logger.warning(f"Pollinations SSE error: {data.get('errorText', '')}")
                    continue
                if msg_type == "content":
                    c = data.get("content", data.get("text", ""))
                    if c:
                        content_parts.append(c)
                    continue
                if "choices" in data:
                    for choice in data.get("choices", []):
                        delta = choice.get("delta", {})
                        msg = choice.get("message", {})
                        c = delta.get("content", "") or msg.get("content", "")
                        if c:
                            content_parts.append(c)
                    continue
                c = data.get("content", data.get("text", data.get("response", "")))
                if c and isinstance(c, str):
                    content_parts.append(c)
        except json.JSONDecodeError:
            if data_str and not data_str.startswith("{"):
                content_parts.append(data_str)

    return "".join(content_parts).strip()


def _parse_json_response(text: str) -> Optional[str]:
    """Try to parse response as OpenAI-compatible JSON chat completion."""
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if "choices" in data:
                for choice in data["choices"]:
                    msg = choice.get("message", {})
                    content = msg.get("content", "")
                    if content:
                        return content
            if "content" in data:
                return data["content"]
            if "text" in data:
                return data["text"]
    except (json.JSONDecodeError, TypeError):
        pass
    return None


class PollinationsProvider(BaseProvider):
    """Pollinations.ai provider v8.0 — MULTI-MODEL LOAD BALANCING!

    Uses gen.pollinations.ai/v1/chat/completions (OpenAI-compatible).
    Round-robin across multiple models for load distribution.
    Automatic failover on 429/rate-limit errors.
    Supports vision via multimodal content format (image_url with base64).
    """

    name: str = "pollinations"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 45.0):
        super().__init__(api_key=api_key, timeout=timeout)
        self._api_key = api_key
        self._last_429_time: float = 0
        self._429_count: int = 0
        # ── Per-model health tracking ──
        self._model_health: Dict[str, Dict] = {}
        # {model_name: {"fail_count": int, "last_fail": float, "last_success": float, "total_requests": int, "total_failures": int}}
        self._round_robin_index: int = 0
        self._total_requests: int = 0
        self._model_usage: Dict[str, int] = {}  # Track usage per model

    async def init(self) -> None:
        """Initialize httpx async client with connection pooling and auth."""
        headers = {
            "User-Agent": "NastyaBot/43.0",
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
            follow_redirects=True,
            headers=headers,
        )

        # Initialize health tracking for all models
        for model_name, _, _, _ in CHAT_MODELS:
            self._model_health[model_name] = {
                "fail_count": 0,
                "last_fail": 0,
                "last_success": 0,
                "total_requests": 0,
                "total_failures": 0,
            }
        self._model_health[MODEL_REASONING] = {
            "fail_count": 0, "last_fail": 0, "last_success": 0,
            "total_requests": 0, "total_failures": 0,
        }

        logger.info(
            f"PollinationsProvider v8 initialized: {len(CHAT_MODELS)} chat models, "
            f"vision={MODEL_VISION}, reasoning={MODEL_REASONING}, "
            f"auth={'yes' if self._api_key else 'anonymous'}, "
            f"timeout={self.timeout}s"
        )

    def is_available(self) -> bool:
        """Available if client is initialized and not in global 429 cooldown."""
        if not self._client:
            return False
        if self._429_count > 3 and time.time() - self._last_429_time < 30:
            return False
        return True

    def _is_model_healthy(self, model_name: str) -> bool:
        """Check if a specific model is healthy enough to use."""
        health = self._model_health.get(model_name)
        if not health:
            return True  # Unknown model = assume healthy

        # If model is permanently disabled (402 payment required)
        if health.get("fail_count", 0) >= 100:
            return False  # Never retry permanently disabled models

        # If model failed recently, apply cooldown
        if health["fail_count"] >= 3:
            # Cooldown: 60s after 3+ consecutive failures
            if time.time() - health["last_fail"] < 60:
                return False
            else:
                # Reset after cooldown
                health["fail_count"] = 0
                return True

        # If model failed once/twice, short cooldown (15s)
        if health["fail_count"] > 0:
            if time.time() - health["last_fail"] < 15:
                return False

        return True

    def _record_model_success(self, model_name: str) -> None:
        """Record successful request for a model."""
        health = self._model_health.get(model_name)
        if health:
            health["fail_count"] = 0
            health["last_success"] = time.time()
            health["total_requests"] += 1
        self._model_usage[model_name] = self._model_usage.get(model_name, 0) + 1
        self._total_requests += 1

    def _record_model_failure(self, model_name: str) -> None:
        """Record failed request for a model."""
        health = self._model_health.get(model_name)
        if health:
            health["fail_count"] += 1
            health["last_fail"] = time.time()
            health["total_failures"] += 1
            health["total_requests"] += 1

    def _select_model(self, prefer_model: str = "", need_vision: bool = False,
                      need_reasoning: bool = False) -> str:
        """Select the best available model using weighted round-robin.

        Args:
            prefer_model: Preferred model to use (if healthy)
            need_vision: Must support image input
            need_reasoning: Need reasoning capability

        Returns:
            Model name to use
        """
        # If a specific model is preferred and healthy, use it
        if prefer_model and self._is_model_healthy(prefer_model):
            return prefer_model

        # For reasoning, use the reasoning model
        if need_reasoning:
            if self._is_model_healthy(MODEL_REASONING):
                return MODEL_REASONING
            # Fallback: any model that's healthy
            logger.warning(f"Reasoning model {MODEL_REASONING} unhealthy, falling back to chat model")

        # Filter healthy models
        candidates = []
        for model_name, weight, supports_vision, cost_tier in CHAT_MODELS:
            if need_vision and not supports_vision:
                continue
            if self._is_model_healthy(model_name):
                candidates.append((model_name, weight))

        if not candidates:
            # ALL models unhealthy — reset all and try primary
            logger.warning("ALL models unhealthy! Resetting health and trying primary.")
            for h in self._model_health.values():
                h["fail_count"] = 0
            if need_vision:
                return MODEL_VISION
            return CHAT_MODELS[0][0]

        # Weighted random selection
        total_weight = sum(w for _, w in candidates)
        r = random.uniform(0, total_weight)
        cumulative = 0
        for model_name, weight in candidates:
            cumulative += weight
            if r <= cumulative:
                return model_name

        # Fallback
        return candidates[0][0]

    def _select_model_round_robin(self, need_vision: bool = False) -> str:
        """Select next model using round-robin (for fair load distribution).

        Only cycles through healthy models that meet requirements.
        """
        # Filter healthy models
        healthy = []
        for model_name, weight, supports_vision, cost_tier in CHAT_MODELS:
            if need_vision and not supports_vision:
                continue
            if self._is_model_healthy(model_name):
                healthy.append(model_name)

        if not healthy:
            return MODEL_VISION if need_vision else CHAT_MODELS[0][0]

        # Round-robin through healthy models
        self._round_robin_index = self._round_robin_index % len(healthy)
        selected = healthy[self._round_robin_index]
        self._round_robin_index += 1
        return selected

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate text via Pollinations with MULTI-MODEL load balancing.

        Tries multiple models if primary fails (429, timeout, etc.)
        This ensures the bot keeps working even under high load.
        """
        if not self._client:
            await self.init()

        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.85)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")
        reasoning_effort: str = kwargs.get("reasoning_effort", REASONING_CHAT)
        max_tokens: int = kwargs.get("max_tokens", 800)

        # Build messages array
        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Select models to try
        need_reasoning = reasoning_effort in ("medium", "high")
        primary_model = self._select_model(
            need_vision=False,
            need_reasoning=need_reasoning,
        )

        # Build list of models to try: primary first, then fallbacks
        models_to_try = [primary_model]
        # Add other healthy models as fallbacks
        for model_name, _, supports_vision, _ in CHAT_MODELS:
            if model_name != primary_model and self._is_model_healthy(model_name):
                models_to_try.append(model_name)

        # If reasoning, add reasoning model
        if need_reasoning and primary_model != MODEL_REASONING:
            if self._is_model_healthy(MODEL_REASONING):
                models_to_try.insert(1, MODEL_REASONING)

        # Try each model in order
        last_error = None
        for model in models_to_try[:3]:  # Max 3 attempts
            try:
                result = await self._call_api(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort if need_reasoning else REASONING_CHAT,
                )
                if result and result.text:
                    self._record_model_success(model)
                    return result
            except ProviderError as e:
                last_error = e
                self._record_model_failure(model)
                err_str = str(e)
                if "429" in err_str:
                    logger.warning(f"Model {model} rate-limited (429), trying next model...")
                elif "PAYMENT_REQUIRED" in err_str or "402" in err_str:
                    logger.warning(f"Model {model} payment required (402) — permanently disabling!")
                    # Permanently disable this model — it requires paid plan
                    if model in self._model_health:
                        self._model_health[model]["fail_count"] = 999  # Effectively permanent
                        self._model_health[model]["last_fail"] = time.time() + 86400 * 30  # 30 days cooldown
                else:
                    logger.warning(f"Model {model} error: {e}, trying next...")
                continue
            except Exception as e:
                last_error = e
                self._record_model_failure(model)
                logger.warning(f"Model {model} unexpected error: {e}, trying next...")
                continue

        # All models failed
        raise ProviderError(
            self.name,
            f"All models failed (tried {len(models_to_try[:3])}). Last error: {last_error}",
            retryable=True,
        )

    async def _call_api(self, model: str, messages: List[Dict],
                         temperature: float, max_tokens: int,
                         reasoning_effort: str = REASONING_CHAT) -> AIResponse:
        """Make a single API call to Pollinations with a specific model."""
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
            "stream": False,
        }

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        response = await self._client.post(
            f"{BASE_URL}/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()

        raw_text = response.text

        if not raw_text:
            raise ProviderError(self.name, f"Empty response from model {model}", retryable=True)

        # STEP 1: Try JSON chat completion format
        parsed = _parse_json_response(raw_text)
        if parsed:
            cleaned = _strip_reasoning(parsed)
            if cleaned:
                return AIResponse(
                    text=cleaned,
                    provider=self.name,
                    model=f"pollinations:{model}",
                    tokens_used=0,
                    metadata={"endpoint": "v1/chat/completions", "parsed": "json", "model": model},
                )

        # STEP 2: Try SSE format
        cleaned = _strip_sse_artifacts(raw_text)
        if cleaned:
            cleaned = _strip_reasoning(cleaned)
            if cleaned:
                return AIResponse(
                    text=cleaned,
                    provider=self.name,
                    model=f"pollinations:{model}",
                    tokens_used=0,
                    metadata={"endpoint": "v1/chat/completions", "parsed": "sse", "model": model},
                )

        # STEP 3: Raw text (last resort)
        final_text = raw_text.strip()
        if "data:" in final_text or "[DONE]" in final_text:
            raise ProviderError(self.name, f"Unparsable SSE artifacts from {model}", retryable=True)

        final_text = _strip_reasoning(final_text)
        if not final_text:
            raise ProviderError(self.name, f"Empty content after cleaning from {model}", retryable=True)

        return AIResponse(
            text=final_text,
            provider=self.name,
            model=f"pollinations:{model}",
            tokens_used=0,
            metadata={"endpoint": "v1/chat/completions", "parsed": "raw", "model": model},
        )

    async def generate_vision(self, prompt: str, image_data: bytes,
                               image_format: str = "jpeg", **kwargs) -> AIResponse:
        """Generate response with image understanding via Pollinations vision.

        Tries multiple vision-capable models for reliability.
        """
        if not self._client:
            await self.init()

        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.85)
        max_tokens: int = kwargs.get("max_tokens", 600)

        # Encode image as base64 data URI
        mime_type = f"image/{image_format}"
        b64_image = base64.b64encode(image_data).decode("utf-8")
        data_uri = f"data:{mime_type};base64,{b64_image}"

        # Build messages with multimodal content
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        user_content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}},
        ]
        messages.append({"role": "user", "content": user_content})

        # Try vision-capable models in order (v55: expanded with tested backups)
        vision_models = ["openai", "mistral-4", "mistral", "qwen-vision-pro", "qwen-vision",
                        "gemma", "openai-fast", "kimi-k2.6", "nova", "nova-fast",
                        "mistral-small", "polly", "llama-scout", "grok",
                        "openai-large", "kimi"]
        # Filter to healthy ones
        healthy_vision = [m for m in vision_models if self._is_model_healthy(m)]
        if not healthy_vision:
            healthy_vision = [MODEL_VISION]  # Always try primary

        last_error = None
        for model in healthy_vision[:2]:  # Max 2 attempts for vision
            try:
                payload: Dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "reasoning_effort": REASONING_CHAT,
                    "stream": False,
                }

                headers = {"Content-Type": "application/json"}
                if self._api_key:
                    headers["Authorization"] = f"Bearer {self._api_key}"

                response = await self._client.post(
                    f"{BASE_URL}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()

                raw_text = response.text

                if not raw_text:
                    raise ProviderError(self.name, f"Empty vision response from {model}", retryable=True)

                parsed = _parse_json_response(raw_text)
                if parsed:
                    cleaned = _strip_reasoning(parsed)
                    if cleaned:
                        self._record_model_success(model)
                        return AIResponse(
                            text=cleaned,
                            provider=self.name,
                            model=f"pollinations:{model}",
                            tokens_used=0,
                            metadata={"endpoint": "v1/chat/completions", "mode": "vision", "model": model},
                        )

                cleaned = _strip_sse_artifacts(raw_text)
                if cleaned:
                    cleaned = _strip_reasoning(cleaned)
                    if cleaned:
                        self._record_model_success(model)
                        return AIResponse(
                            text=cleaned,
                            provider=self.name,
                            model=f"pollinations:{model}",
                            tokens_used=0,
                            metadata={"endpoint": "v1/chat/completions", "mode": "vision", "parsed": "sse", "model": model},
                        )

                final_text = _strip_reasoning(raw_text.strip())
                if final_text:
                    self._record_model_success(model)
                    return AIResponse(
                        text=final_text,
                        provider=self.name,
                        model=f"pollinations:{model}",
                        tokens_used=0,
                        metadata={"endpoint": "v1/chat/completions", "mode": "vision", "parsed": "raw", "model": model},
                    )

                raise ProviderError(self.name, f"Empty vision content from {model}", retryable=True)

            except httpx.TimeoutException as exc:
                self._record_model_failure(model)
                last_error = exc
                logger.warning(f"Vision model {model} timeout, trying next...")
                continue
            except httpx.HTTPStatusError as exc:
                self._record_model_failure(model)
                status = exc.response.status_code
                if status == 429:
                    self._last_429_time = time.time()
                    self._429_count += 1
                retryable = status in (429, 500, 502, 503, 504)
                if not retryable:
                    raise ProviderError(
                        self.name,
                        f"Vision HTTP {status} from {model}: {exc.response.text[:200]}",
                        retryable=False,
                    )
                last_error = exc
                logger.warning(f"Vision model {model} HTTP {status}, trying next...")
                continue
            except ProviderError:
                raise
            except Exception as exc:
                self._record_model_failure(model)
                last_error = exc
                logger.warning(f"Vision model {model} error: {exc}, trying next...")
                continue

        raise ProviderError(
            self.name,
            f"All vision models failed. Last error: {last_error}",
            retryable=True,
        )

    async def generate_image(self, prompt: str, size: str = "1024x1024",
                              model: str = "flux") -> Optional[bytes]:
        """Generate an image using Pollinations image API.

        Returns image bytes or None on failure.
        Uses /v1/images/generations endpoint with base64 response.
        """
        if not self._client:
            await self.init()

        try:
            payload = {
                "prompt": prompt,
                "size": size,
                "model": model,
            }

            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            response = await self._client.post(
                f"{BASE_URL}/v1/images/generations",
                json=payload,
                headers=headers,
                timeout=60.0,
            )
            response.raise_for_status()

            data = response.json()
            if "data" in data and data["data"]:
                b64 = data["data"][0].get("b64_json", "")
                if b64:
                    return base64.b64decode(b64)

        except Exception as e:
            logger.warning(f"Image generation failed: {e}")

        return None

    def get_model_stats(self) -> Dict[str, Any]:
        """Get statistics about model usage and health."""
        stats = {}
        for model_name, health in self._model_health.items():
            stats[model_name] = {
                "healthy": self._is_model_healthy(model_name),
                "fail_count": health.get("fail_count", 0),
                "total_requests": health.get("total_requests", 0),
                "total_failures": health.get("total_failures", 0),
                "usage_count": self._model_usage.get(model_name, 0),
            }
        stats["_total_requests"] = self._total_requests
        stats["_429_count"] = self._429_count
        return stats

    async def close(self) -> None:
        """Close httpx client."""
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
