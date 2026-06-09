"""Pollinations.ai Provider v20.0 - DUAL API KEY + OLD API FALLBACK!

v20.0 UPDATE - OLD Pollinations API fallback when both keys are depleted:
  - ADDED: Fallback to OLD Pollinations API (text.pollinations.ai / image.pollinations.ai)
    when BOTH API keys are depleted (402/401). The OLD API is FREE, anonymous,
    rate-limited (1 req/IP), and doesn't require authentication.
  - FAILOVER CHAIN: KEY1 -> KEY2 -> OLD API (free) -> ProviderError
  - ADDED: _call_old_api() - calls text.pollinations.ai WITHOUT auth
  - ADDED: _call_old_image_api() - calls image.pollinations.ai via GET (no auth)
  - ADDED: OLD_CHAT_MODELS and OLD_IMAGE_MODELS for free-tier model selection
  - UPDATED: is_available() now returns True even when keys depleted (OLD API fallback)

  v19.0 features retained:
  - Dual key failover (KEY1 + KEY2)
  - 43 chat models with load balancing
  - Depleted keys auto-retry after 600 seconds cooldown
  - IMPORTANT: We NEVER delete models from lists when they fail.
    Pollinations.ai rotates model availability - a failure today doesn't mean
    the model is gone. Circuit breaking handles temporary failures.

  FULL FAILOVER CHAIN:
  - KEY1 -> KEY2 -> OLD API (free, anonymous) -> ProviderError(retryable=True)
  - On 402/401: mark current key as depleted, auto-switch to next
  - When ALL keys depleted: try OLD API with top 3 free models
  - OLD API is rate-limited (429) but always available without keys

  EXPANDED MODEL LIST (43 keyed + 6 old API models!):
  - Full Pollinations catalog coverage
  - All previous models retained (never delete - Pollinations rotates!)
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

# ── Old Pollinations API (FREE, anonymous, rate-limited) ──
OLD_TEXT_URL = "https://text.pollinations.ai"
OLD_IMAGE_URL = "https://image.pollinations.ai"

# Free models confirmed working for Russian language (from testing)
OLD_CHAT_MODELS = [
    "openai",      # GPT-5.4 Nano - best Russian
    "mistral",     # Mistral Small 3.2 - good multilingual
    "deepseek",    # DeepSeek V4 Flash - good Russian
    "llama",       # Llama 3.3 70B - strong
    "qwen",        # Qwen - good multilingual
    "command-r",   # Command R - good
]

OLD_IMAGE_MODELS = [
    "flux",           # Best quality
    "flux-realism",   # Photorealistic
    "flux-pro",       # Professional
    "flux-cablyai",   # Alternative
    "turbo",          # Fast generation
]

# ── Cooldown for depleted API keys (seconds) ──
KEY_COOLDOWN: float = 600.0  # 10 minutes before retrying a depleted key

# ── Model Selection - LOAD BALANCING POOL ──
# Models are ordered by priority/quality for chat
# Each model has: cost tier, vision support, reasoning support

CHAT_MODELS = [
    # (model_name, weight_for_round_robin, supports_vision, cost_tier)
    # Cost tiers: 1=cheapest, 2=cheap, 3=moderate, 4=expensive
    # Models from Pollinations catalog (June 2026)
    # IMPORTANT: We NEVER delete models when they temporarily fail.
    # Pollinations rotates availability - circuit breaking handles it.
    # ── PRIMARY TIER - Fast & Reliable ──
    ("openai",       4, True,  1),   # GPT-5.4 Nano - PRIMARY, fast, vision, best Russian
    ("mistral",      3, True,  1),   # Mistral Small 3.2 - fast, good multilingual, vision
    ("gpt-5.4-mini", 3, True,  2),   # GPT-5.4 Mini - balanced speed & cost
    ("deepseek",     2, False, 1),   # DeepSeek V4 Flash - good Russian, fast, cheap
    ("mistral-4",    2, True,  2),   # Mistral Small 4 - better quality, multimodal
    ("gemma",        2, True,  1),   # Gemma 4 26B - fast MoE, vision + reasoning
    ("llama-scout",  1, True,  1),   # Llama 4 Scout - long context, vision
    # ── QUALITY TIER - Better Responses ──
    ("gpt-5.5",      2, True,  3),   # GPT-5.5 - excellent Russian, reasoning + vision
    ("deepseek-pro", 1, False, 2),   # DeepSeek Pro - better reasoning
    # ── POWERFUL TIER - Vision + Reasoning ──
    ("mistral-large", 1, True,  3),   # Mistral Large - powerful, vision + reasoning
    ("qwen-vision-pro",1, True,  2),  # Qwen Vision Pro - better vision + reasoning
    ("kimi-k2.6",     1, True,  3),   # Kimi K2.6 - latest, better multilingual
    ("nova-fast",     2, True,  2),   # Amazon Nova Fast - good Russian, fast
    ("glm",           1, True,  2),   # ChatGLM - good multilingual
    ("minimax",       1, True,  2),   # MiniMax - good for chat
    # ── CATALOG EXPANSION ──
    ("nova",          1, True,  3),   # Amazon Nova - vision + reasoning, 1M ctx
    ("mistral-small", 2, True,  1),   # Mistral Small - fast, good Russian
    ("polly",         1, True,  2),   # Polly - vision + reasoning
    ("perplexity-fast",1, False, 1),  # Perplexity - fast web search
    ("perplexity",    1, False, 2),   # Perplexity - deep web search, 200k ctx
    ("qwen-vision",   1, True,  2),   # Qwen3 VL - vision specialist
    ("llama",         1, False, 1),   # Llama 3.3 70B - strong reasoning
    ("grok",          1, True,  2),   # Grok - vision, OK Russian
    # ── CODE + REASONING ──
    ("qwen-coder",    1, False, 1),   # Qwen3 Coder 30B - code + reasoning
    ("openai-large",  1, True,  4),   # GPT-5.4 - reasoning model for complex questions
    ("kimi",          1, True,  2),   # Kimi - latest, good multilingual
    ("perplexity-deep",1, False, 2),  # Perplexity Deep - deep web search + reasoning
    # ── POWERFUL MODELS ──
    ("grok-large",    2, False, 3),   # Grok Large - powerful, good Russian
    ("grok-4.3",      1, False, 3),   # Grok 4.3 - latest Grok, best reasoning
    ("perplexity-reasoning",1, False, 2),  # Perplexity Reasoning - web search + reasoning
    ("minimax-m3",    2, False, 2),   # MiniMax M3 - good Russian, fast
    ("step-3.5-flash",1, False, 1),   # Step 3.5 Flash - fast, good for quick chat
    ("openai-reasoning",1, True, 3),  # OpenAI Reasoning - reasoning + VISION!
    ("nova-micro",    2, False, 1),   # Amazon Nova Micro - ultra fast, cheapest
    ("mistral-small-3.2",2, True, 1), # Mistral Small 3.2 - fastest + VISION!
    # ── v17: RESTORED + NEW MODELS - Confirmed in Pollinations catalog! ──
    ("openai-fast",   2, True,  1),   # OpenAI Fast - fastest OpenAI variant
    ("step-flash",    1, True,  2),   # Step Flash - reasoning + vision
    ("qwen-large",    1, True,  3),   # Qwen Large - reasoning + vision, 1M ctx
    ("deepseek-v4",   1, False, 1),   # DeepSeek V4 Flash - fast variant (alias)
    ("llama-3.3",     1, False, 1),   # Llama 3.3 70B (explicit alias)
    ("llama-4-scout", 1, True,  1),   # Llama 4 Scout (explicit alias)
    ("nova-2",        1, True,  2),   # Nova 2 Lite - fast, Russian OK
    # ── v18: NEW MODELS - Confirmed working! ──
    # ("midijourney",   1, False, 1),   # REMOVED: text-to-music model, NOT for chat!
    # Models may come back - we NEVER delete them!
    # Previous REMOVED: gemini, gemini-3.5-flash, llama-maverick, claude, claude-haiku, claude-sonnet
    # These MAY return in the future - Pollinations rotates availability
]

MODEL_REASONING = "openai-large"    # GPT-5.4 - for complex questions
MODEL_VISION = "openai"             # Vision model (same as chat - supports images!)

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
    """Pollinations.ai provider v20.0 - DUAL KEY + OLD API FALLBACK!

    Uses gen.pollinations.ai/v1/chat/completions (OpenAI-compatible).
    Round-robin across 43 models for load distribution.

    FULL FAILOVER CHAIN:
      1. Try KEY1 first (primary key)
      2. On 402/401 from KEY1 -> switch to KEY2
      3. On 402/401 from KEY2 -> try OLD API (text.pollinations.ai, free, no auth)
      4. OLD API also fails -> raise ProviderError(retryable=True)
      5. Depleted keys auto-retry after 600 seconds cooldown

    OLD API FALLBACK (text.pollinations.ai / image.pollinations.ai):
      - Free, anonymous, no authentication required
      - Rate-limited (1 req/IP, 429 on excess)
      - Uses top 3 models from OLD_CHAT_MODELS for text
      - Uses top 2 models from OLD_IMAGE_MODELS for images
      - Always available even when both API keys are depleted

    IMPORTANT: Models are NEVER removed when temporarily unavailable.
    Pollinations.ai rotates model availability - circuit breaking handles it.
    """

    name: str = "pollinations"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", api_key_2: str = "", timeout: float = 45.0):
        super().__init__(api_key=api_key, timeout=timeout)
        # ── Dual API key storage ──
        self._api_key_1: str = api_key
        self._api_key_2: str = api_key_2
        # ── Per-key balance depletion tracking ──
        # 0 = active/never depleted; >0 = timestamp when depleted
        self._key1_depleted_at: float = 0.0
        self._key2_depleted_at: float = 0.0
        # ── Rate-limit tracking ──
        self._last_429_time: float = 0
        self._429_count: int = 0
        # ── Per-model health tracking ──
        self._model_health: Dict[str, Dict] = {}
        # {model_name: {"fail_count": int, "last_fail": float, "last_success": float,
        #                "total_requests": int, "total_failures": int}}
        self._round_robin_index: int = 0
        self._total_requests: int = 0
        self._model_usage: Dict[str, int] = {}  # Track usage per model

    # ── API Key Management ──────────────────────────────────────

    def _is_key_available(self, key_index: int) -> bool:
        """Check if an API key is available (not depleted or cooldown expired).

        Args:
            key_index: 1 for KEY1, 2 for KEY2

        Returns:
            True if key can be used for requests
        """
        depleted_at_map = {
            1: self._key1_depleted_at,
            2: self._key2_depleted_at,
        }
        key_val_map = {
            1: self._api_key_1,
            2: self._api_key_2,
        }

        depleted_at = depleted_at_map.get(key_index, 0)
        key_val = key_val_map.get(key_index, "")

        # No key configured = not available
        if not key_val:
            return False

        # Never depleted = available
        if depleted_at == 0:
            return True

        # Check cooldown
        elapsed = time.time() - depleted_at
        if elapsed >= KEY_COOLDOWN:
            # Cooldown expired - key is available again
            if key_index == 1:
                self._key1_depleted_at = 0
            else:
                self._key2_depleted_at = 0
            logger.info(f"API KEY{key_index} cooldown expired after {elapsed:.0f}s - retrying")
            return True

        return False

    def _mark_key_depleted(self, key_index: int) -> None:
        """Mark an API key as depleted (balance exhausted).

        The key will be automatically retried after KEY_COOLDOWN seconds.

        Args:
            key_index: 1 for KEY1, 2 for KEY2
        """
        if key_index == 1:
            self._key1_depleted_at = time.time()
        else:
            self._key2_depleted_at = time.time()
        logger.warning(
            f"API KEY{key_index} depleted (402/401). "
            f"Will auto-retry after {KEY_COOLDOWN}s cooldown."
        )

    def _get_active_key_tier(self) -> Tuple[str, int]:
        """Determine which key/tier to use for the next request.

        Returns:
            Tuple of (api_key_or_empty_string, key_tier)
            key_tier: 1=KEY1, 2=KEY2, 0=no available key
        """
        # Try KEY1 first
        if self._is_key_available(1):
            return self._api_key_1, 1
        # Then KEY2
        if self._is_key_available(2):
            return self._api_key_2, 2
        # No keys available
        return "", 0

    def _get_key_status_summary(self) -> str:
        """Get a human-readable summary of key statuses."""
        parts = []
        for idx, (key_val, depleted_at) in enumerate([
            (self._api_key_1, self._key1_depleted_at),
            (self._api_key_2, self._key2_depleted_at),
        ], start=1):
            if key_val:
                if self._is_key_available(idx):
                    parts.append(f"KEY{idx}=active")
                else:
                    remaining = KEY_COOLDOWN - (time.time() - depleted_at)
                    parts.append(f"KEY{idx}=depleted({remaining:.0f}s)")
            else:
                parts.append(f"KEY{idx}=not_set")
        return ", ".join(parts)

    # ── Initialization ──────────────────────────────────────────

    async def init(self) -> None:
        """Initialize httpx async client with connection pooling."""
        headers = {
            "User-Agent": "NastyaBot/62.0",
            "Accept": "application/json",
        }
        # Use KEY1 for initial client headers (will be overridden per-request)
        active_key, _ = self._get_active_key_tier()
        if active_key:
            headers["Authorization"] = f"Bearer {active_key}"

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

        key_status = self._get_key_status_summary()
        logger.info(
            f"PollinationsProvider v20 initialized: {len(CHAT_MODELS)} chat models, "
            f"vision={MODEL_VISION}, reasoning={MODEL_REASONING}, "
            f"keys=[{key_status}], "
            f"timeout={self.timeout}s"
        )

    def is_available(self) -> bool:
        """Available if client is initialized and not in global 429 cooldown.

        Note: Even with both keys depleted, OLD API fallback is still available.
        We only block on rate-limit cooldowns.
        """
        if not self._client:
            return False
        if self._429_count > 3 and time.time() - self._last_429_time < 30:
            return False
        return True

    # ── Model Health Tracking ───────────────────────────────────

    def _is_model_healthy(self, model_name: str) -> bool:
        """Check if a specific model is healthy enough to use."""
        health = self._model_health.get(model_name)
        if not health:
            return True  # Unknown model = assume healthy

        # If model is temporarily disabled (high fail count), check cooldown
        if health.get("fail_count", 0) >= 10:
            # Even for high fail counts, allow retry after longer cooldown (10 min)
            if time.time() - health["last_fail"] < 600:
                return False
            else:
                # Reset after long cooldown - models may become available again
                health["fail_count"] = 0
                return True

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

    # ── Model Selection ─────────────────────────────────────────

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
            # ALL models unhealthy - reset all and try primary
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

    # ── API Call with Dual-Key Failover ───────────────────────

    async def _call_api(self, model: str, messages: List[Dict],
                         temperature: float, max_tokens: int,
                         reasoning_effort: str = REASONING_CHAT) -> AIResponse:
        """Make a single API call to Pollinations with dual-key failover.

        KEY FAILOVER ORDER:
          1. Try with KEY1 (primary)
          2. On 402/401 -> mark KEY1 depleted, retry with KEY2
          3. On 402/401 -> mark KEY2 depleted, raise ProviderError(retryable=True)

        NOTE: Free tier is NOT used - it returns 401.

        Args:
            model: Model name to use
            messages: Chat messages array
            temperature: Sampling temperature
            max_tokens: Maximum response tokens
            reasoning_effort: Reasoning effort level

        Returns:
            AIResponse with the model's output

        Raises:
            ProviderError: On API failure (retryable=True for transient errors)
        """
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
            "stream": False,
        }

        # ── Build key tier list: active keys only (no free tier!) ──
        # Each tier is (api_key_string, tier_index)
        tiers_to_try: List[Tuple[str, int]] = []

        # Check which keys are available - KEY1 -> KEY2
        if self._is_key_available(1):
            tiers_to_try.append((self._api_key_1, 1))
        if self._is_key_available(2):
            tiers_to_try.append((self._api_key_2, 2))

        # No free tier fallback - it returns 401!

        if not tiers_to_try:
            # All keys depleted
            raise ProviderError(
                self.name,
                f"All API keys (KEY1/KEY2) are depleted for model {model}. "
                f"Wait for cooldown ({KEY_COOLDOWN}s) or add new keys.",
                retryable=True,
            )

        last_error: Optional[Exception] = None

        for api_key, tier_index in tiers_to_try:
            try:
                headers: Dict[str, str] = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                response = await self._client.post(
                    f"{BASE_URL}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()

                # ── Parse successful response ──
                raw_text = response.text
                if not raw_text:
                    # Reasoning models sometimes return empty body - treat as retryable
                    raise ProviderError(
                        self.name,
                        f"Empty response from model {model} (reasoning model may need more time)",
                        retryable=True,
                    )

                result = self._parse_response_text(raw_text, model)
                if result and result.text:
                    return result

                # Reasoning models may return content that's all thinking tags
                # After stripping, it's empty - treat as a soft failure
                if result is None and reasoning_effort != REASONING_CHAT:
                    raise ProviderError(
                        self.name,
                        f"Reasoning model {model} returned empty content after stripping thinking tags",
                        retryable=True,
                    )

                raise ProviderError(
                    self.name,
                    f"Unparsable/empty content from {model}",
                    retryable=True,
                )

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code

                # ── 402/401: Balance depleted or unauthorized -> switch key ──
                if status in (401, 402):
                    if tier_index > 0:
                        self._mark_key_depleted(tier_index)
                        tier_label = f"KEY{tier_index}"
                    else:
                        tier_label = "unknown"

                    remaining_tiers = len(tiers_to_try) - tiers_to_try.index((api_key, tier_index)) - 1
                    logger.warning(
                        f"HTTP {status} from {model} via {tier_label} - "
                        f"{'switching to next key tier' if remaining_tiers > 0 else 'all key tiers exhausted'}"
                    )

                    last_error = exc
                    continue  # Try next tier

                # ── 429: Rate limited -> short cooldown, no key switch needed ──
                if status == 429:
                    self._last_429_time = time.time()
                    self._429_count += 1
                    raise ProviderError(
                        self.name,
                        f"Rate-limited (429) from {model}",
                        retryable=True,
                    )

                # ── Other HTTP errors ──
                retryable = status in (500, 502, 503, 504)
                raise ProviderError(
                    self.name,
                    f"HTTP {status} from {model}: {exc.response.text[:200]}",
                    retryable=retryable,
                )

            except httpx.TimeoutException as exc:
                raise ProviderError(
                    self.name,
                    f"Timeout from {model}: {exc}",
                    retryable=True,
                )

            except ProviderError:
                raise

            except Exception as exc:
                raise ProviderError(
                    self.name,
                    f"Unexpected error from {model}: {exc}",
                    retryable=True,
                )

        # ── All tiers failed with 402/401 ──
        raise ProviderError(
            self.name,
            f"All API key tiers (KEY1/KEY2) returned 402/401 for model {model}. "
            f"Last error: {last_error}.",
            retryable=True,
        )

    # ── OLD API Fallback (FREE, no auth) ────────────────────────

    async def _call_old_api(self, model: str, messages: List[Dict],
                             temperature: float, max_tokens: int) -> AIResponse:
        """Fallback: call OLD Pollinations API (text.pollinations.ai) WITHOUT auth.

        Free, anonymous, rate-limited (1 req/IP). Used when ALL API keys are depleted.
        The old API uses OpenAI-compatible format but on different endpoint.
        """
        if not self._client:
            await self.init()

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        # NO Authorization header - this is the free, anonymous endpoint
        headers: Dict[str, str] = {"Content-Type": "application/json"}

        try:
            response = await self._client.post(
                f"{OLD_TEXT_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()

            raw_text = response.text
            if not raw_text:
                raise ProviderError(
                    self.name,
                    f"Empty response from OLD API model {model}",
                    retryable=True,
                )

            result = self._parse_response_text(raw_text, model)
            if result and result.text:
                result.metadata["endpoint"] = "old_api"
                result.metadata["auth"] = "none"
                return result

            raise ProviderError(
                self.name,
                f"Unparsable content from OLD API model {model}",
                retryable=True,
            )

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                raise ProviderError(
                    self.name,
                    f"OLD API rate-limited (429) for {model} - free tier limit",
                    retryable=True,
                )
            raise ProviderError(
                self.name,
                f"OLD API HTTP {status} from {model}: {exc.response.text[:200]}",
                retryable=status in (500, 502, 503, 504),
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                self.name,
                f"OLD API timeout from {model}: {exc}",
                retryable=True,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                self.name,
                f"OLD API unexpected error from {model}: {exc}",
                retryable=True,
            )

    async def _call_old_image_api(self, prompt: str, size: str = "1024x1024",
                                    model: str = "flux") -> Optional[bytes]:
        """Fallback: generate image using OLD Pollinations API (image.pollinations.ai) WITHOUT auth.

        Free, anonymous. Returns raw image bytes.
        """
        if not self._client:
            await self.init()

        # Old API uses GET request with query params
        # Format: https://image.pollinations.ai/prompt/{encoded_prompt}?model={model}&width=W&height=H
        try:
            from urllib.parse import quote as _url_quote

            encoded_prompt = _url_quote(prompt, safe='')
            w, h = size.split('x') if 'x' in size else ('1024', '1024')
            url = f"{OLD_IMAGE_URL}/prompt/{encoded_prompt}?model={model}&width={w}&height={h}&nologo=true&nofeed=true"

            response = await self._client.get(
                url,
                timeout=60.0,
            )
            response.raise_for_status()

            content_type = response.headers.get('content-type', '')
            if 'image' in content_type and len(response.content) > 500:
                return response.content

            return None

        except Exception as e:
            logger.warning(f"OLD image API error: {e}")
            return None

    def _parse_response_text(self, raw_text: str, model: str) -> Optional[AIResponse]:
        """Parse raw API response text into an AIResponse.

        Tries multiple parsing strategies:
          1. JSON chat completion format (choices[0].message.content)
          2. SSE streaming format (data: lines)
          3. Raw text (last resort)

        Args:
            raw_text: Raw response body text
            model: Model name for metadata

        Returns:
            AIResponse if parsing succeeded, None if content is empty/unparsable
        """
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
            # JSON parsed but reasoning stripping left it empty - return None
            # This allows the caller to treat it as a soft failure for reasoning models
            return None

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
            return None

        # STEP 3: Raw text (last resort)
        final_text = raw_text.strip()
        if "data:" in final_text or "[DONE]" in final_text:
            return None  # Unparsable SSE artifacts

        final_text = _strip_reasoning(final_text)
        if not final_text:
            return None

        return AIResponse(
            text=final_text,
            provider=self.name,
            model=f"pollinations:{model}",
            tokens_used=0,
            metadata={"endpoint": "v1/chat/completions", "parsed": "raw", "model": model},
        )

    # ── Text Generation ─────────────────────────────────────────

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate text via Pollinations with MULTI-MODEL load balancing.

        Tries multiple models if primary fails (429, timeout, etc.).
        Each model attempt uses dual-key failover (KEY1 -> KEY2).
        When ALL cloud models fail, raises ProviderError(retryable=True).
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

        # Try each model in order (max 3 attempts)
        last_error: Optional[Exception] = None
        tried_models = []
        for model in models_to_try[:3]:
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
                err_str = str(e)
                tried_models.append(model)

                if "429" in err_str:
                    self._record_model_failure(model)
                    logger.warning(f"Model {model} rate-limited (429), trying next model...")
                elif "402" in err_str or "401" in err_str:
                    # Key depletion handled inside _call_api
                    # If we got here, ALL tiers failed for this model
                    self._record_model_failure(model)
                    logger.warning(
                        f"Model {model} failed with 402/401 across all key tiers, "
                        f"trying next model..."
                    )
                elif "All API key tiers" in err_str:
                    # All keys depleted for this model
                    self._record_model_failure(model)
                    logger.warning(f"Model {model}: all tiers depleted, trying next model...")
                elif "All API keys" in err_str:
                    # All keys depleted globally
                    self._record_model_failure(model)
                    logger.warning(f"Model {model}: all keys depleted, trying next model...")
                else:
                    self._record_model_failure(model)
                    logger.warning(f"Model {model} error: {e}, trying next...")
                continue
            except Exception as e:
                last_error = e
                tried_models.append(model)
                self._record_model_failure(model)
                logger.warning(f"Model {model} unexpected error: {e}, trying next...")
                continue

        # ── ALL models failed with keys ── Try OLD API fallback (free, anonymous) ──
        key_status = self._get_key_status_summary()
        logger.warning(
            f"All keyed models failed (tried {tried_models}). "
            f"Key status: [{key_status}]. Trying OLD API fallback..."
        )

        # Try old Pollinations API (free, no auth)
        for old_model in OLD_CHAT_MODELS[:3]:
            try:
                result = await self._call_old_api(
                    model=old_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if result and result.text:
                    self._record_model_success(old_model)
                    logger.info(f"OLD API fallback succeeded with model {old_model}")
                    return result
            except ProviderError as e:
                logger.warning(f"OLD API model {old_model} failed: {e}")
                continue
            except Exception as e:
                logger.warning(f"OLD API model {old_model} error: {e}")
                continue

        # ── EVERYTHING failed ──
        logger.error(
            f"All providers failed (keyed: {tried_models}, old_api: {OLD_CHAT_MODELS[:3]}). "
            f"Key status: [{key_status}]."
        )
        raise ProviderError(
            self.name,
            f"All providers failed (keyed: {tried_models}, old_api tried). "
            f"Keys=[{key_status}]. Last error: {last_error}.",
            retryable=True,
        )

    # ── Vision Generation ───────────────────────────────────────

    async def generate_vision(self, prompt: str, image_data: bytes,
                               image_format: str = "jpeg", **kwargs) -> AIResponse:
        """Generate response with image understanding via Pollinations vision.

        Tries multiple vision-capable models with dual-key failover.
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

        # Try vision-capable models in order
        vision_models = [
            "openai", "mistral-4", "mistral", "qwen-vision-pro", "qwen-vision",
            "gemma", "kimi-k2.6", "nova", "nova-fast",
            "mistral-small", "polly", "llama-scout", "grok",
            "openai-large", "kimi", "openai-reasoning",
            "mistral-small-3.2", "llama-4-scout",
            "step-flash", "qwen-large", "nova-2",  # v17: expanded vision models
            "gpt-5.5",  # v18: GPT-5.5 also supports vision
        ]
        # Filter to healthy ones
        healthy_vision = [m for m in vision_models if self._is_model_healthy(m)]
        if not healthy_vision:
            healthy_vision = [MODEL_VISION]  # Always try primary

        last_error: Optional[Exception] = None
        for model in healthy_vision[:2]:  # Max 2 attempts for vision
            try:
                # Use dual-key failover for vision
                result = await self._call_api(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort=REASONING_CHAT,
                )
                if result and result.text:
                    self._record_model_success(model)
                    # Add vision mode to metadata
                    result.metadata["mode"] = "vision"
                    return result

            except ProviderError as e:
                self._record_model_failure(model)
                last_error = e
                err_str = str(e)
                if "429" in err_str:
                    logger.warning(f"Vision model {model} rate-limited, trying next...")
                elif "402" in err_str or "401" in err_str or "All API key tiers" in err_str or "All API keys" in err_str:
                    logger.warning(f"Vision model {model} all tiers depleted, trying next...")
                else:
                    logger.warning(f"Vision model {model} error: {e}, trying next...")
                continue
            except Exception as e:
                self._record_model_failure(model)
                last_error = e
                logger.warning(f"Vision model {model} unexpected error: {e}, trying next...")
                continue

        # ── Try OLD API fallback for vision ──
        for old_model in OLD_CHAT_MODELS[:2]:
            try:
                result = await self._call_old_api(
                    model=old_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if result and result.text:
                    self._record_model_success(old_model)
                    result.metadata["mode"] = "vision_old_api"
                    logger.info(f"OLD API vision fallback succeeded with {old_model}")
                    return result
            except ProviderError as e:
                logger.warning(f"OLD API vision model {old_model} failed: {e}")
                continue
            except Exception as e:
                logger.warning(f"OLD API vision model {old_model} error: {e}")
                continue

        raise ProviderError(
            self.name,
            f"All vision models failed. Last error: {last_error}",
            retryable=True,
        )

    # ── Image Generation ────────────────────────────────────────

    async def generate_image(self, prompt: str, size: str = "1024x1024",
                              model: str = "flux") -> Optional[bytes]:
        """Generate an image using Pollinations image API.

        Uses dual-key failover: KEY1 -> KEY2.
        Returns image bytes or None on failure.
        """
        if not self._client:
            await self.init()

        payload = {
            "prompt": prompt,
            "size": size,
            "model": model,
        }

        # ── Dual-key failover for image generation ──
        tiers_to_try: List[Tuple[str, int]] = []
        if self._is_key_available(1):
            tiers_to_try.append((self._api_key_1, 1))
        if self._is_key_available(2):
            tiers_to_try.append((self._api_key_2, 2))
        # No free tier - it returns 401

        if not tiers_to_try:
            logger.warning("All API keys depleted for image generation")
            return None

        for api_key, tier_index in tiers_to_try:
            try:
                headers: Dict[str, str] = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

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

                # No image data in response
                return None

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 402) and tier_index > 0:
                    self._mark_key_depleted(tier_index)
                    logger.warning(
                        f"Image gen HTTP {status} via KEY{tier_index}, "
                        f"trying next tier..."
                    )
                    continue
                # Other errors - give up
                logger.warning(f"Image generation HTTP {status}: {exc.response.text[:200]}")
                return None

            except Exception as e:
                logger.warning(f"Image generation failed: {e}")
                return None

        # ── Try OLD image API fallback (free, no auth) ──
        logger.info("Trying OLD image API fallback (image.pollinations.ai)...")
        for img_model in OLD_IMAGE_MODELS[:2]:
            try:
                img_bytes = await self._call_old_image_api(
                    prompt=prompt, size=size, model=img_model,
                )
                if img_bytes:
                    logger.info(f"OLD image API succeeded with model {img_model}")
                    return img_bytes
            except Exception as e:
                logger.warning(f"OLD image API model {img_model} failed: {e}")
                continue

        return None

    # ── Stats & Cleanup ─────────────────────────────────────────

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
        stats["_key_status"] = self._get_key_status_summary()
        return stats

    async def close(self) -> None:
        """Close httpx client."""
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
