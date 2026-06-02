"""Pollinations.ai Provider v6.0 — PRIMARY AI provider for Nastya Bot.

v6.0 REWRITE for 2026 API:
  - Model: gpt-oss-20b (GPT-OSS 20B Reasoning LLM) — ONLY available model
  - OpenAI-compatible POST endpoint: text.pollinations.ai/openai
  - Bearer token auth with API key
  - reasoning_effort: 'low' for fast chat, 'medium' for reasoning tasks
  - No vision support (model limitation) — photos handled by caption
  - Proper OpenAI chat completion response parsing
  - Reasoning field stripped from responses (internal thinking)
  - Rate limit handling with cooldown
  - SSE artifact stripping as fallback
"""
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

TEXT_BASE = "https://text.pollinations.ai"

# The ONLY available model on Pollinations (as of 2026-06)
MODEL_ID = "openai"  # Alias for gpt-oss-20b

# Reasoning effort levels: 'none', 'low', 'medium', 'high'
# 'low' = fast responses for chat, 'medium' = for complex questions
REASONING_CHAT = "low"
REASONING_COMPLEX = "medium"


def _strip_reasoning(text: str) -> str:
    """Remove reasoning/thinking content from response.
    
    gpt-oss-20b returns a 'reasoning' field in the message, but
    sometimes it bleeds into the content. Strip any remnants.
    """
    if not text:
        return ""
    # Strip <think/> blocks
    text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)
    # Strip incomplete think tags at end
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
    """Pollinations.ai provider — PRIMARY AI for Nastya Bot.

    Uses gpt-oss-20b (OpenAI-compatible endpoint) with Bearer token auth.
    Reasoning effort configurable: 'low' for chat speed, 'medium' for complex queries.
    No vision support — photo captions handled separately.
    """

    name: str = "pollinations"
    supports_streaming: bool = False
    supports_vision: bool = False  # gpt-oss-20b does NOT support vision

    def __init__(self, api_key: str = "", timeout: float = 45.0):
        super().__init__(api_key=api_key, timeout=timeout)
        self._api_key = api_key
        self._last_429_time: float = 0
        self._429_count: int = 0

    async def init(self) -> None:
        """Initialize httpx async client with connection pooling and auth."""
        headers = {
            "User-Agent": "NastyaBot/41.0",
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=True,
            headers=headers,
        )
        logger.info(
            f"PollinationsProvider initialized: model={MODEL_ID}, "
            f"auth={'yes' if self._api_key else 'anonymous'}, "
            f"timeout={self.timeout}s"
        )

    def is_available(self) -> bool:
        """Available if client is initialized and not in 429 cooldown."""
        if not self._client:
            return False
        # 429 cooldown: 60 seconds after last 429
        if self._429_count > 0 and time.time() - self._last_429_time < 60:
            return False
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate text via Pollinations OpenAI-compatible POST API.

        Uses reasoning_effort='low' for fast chat responses.
        """
        if not self._client:
            await self.init()

        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.85)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")
        reasoning_effort: str = kwargs.get("reasoning_effort", REASONING_CHAT)
        max_tokens: int = kwargs.get("max_tokens", 512)

        messages = self._build_messages(prompt, system_prompt, messages_history)

        payload: Dict[str, Any] = {
            "model": MODEL_ID,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
            "stream": False,
        }

        # Build headers — always include auth if available
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            response = await self._client.post(
                f"{TEXT_BASE}/openai",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()

            raw_text = response.text

            if not raw_text:
                raise ProviderError(self.name, "Empty response from Pollinations", retryable=True)

            # STEP 1: Try JSON chat completion format
            parsed = _parse_json_response(raw_text)
            if parsed:
                cleaned = _strip_reasoning(parsed)
                if cleaned:
                    return AIResponse(
                        text=cleaned,
                        provider=self.name,
                        model=f"pollinations:{MODEL_ID}",
                        tokens_used=0,
                        metadata={"endpoint": "openai", "parsed": "json"},
                    )

            # STEP 2: Try SSE format
            cleaned = _strip_sse_artifacts(raw_text)
            if cleaned:
                cleaned = _strip_reasoning(cleaned)
                if cleaned:
                    return AIResponse(
                        text=cleaned,
                        provider=self.name,
                        model=f"pollinations:{MODEL_ID}",
                        tokens_used=0,
                        metadata={"endpoint": "openai", "parsed": "sse"},
                    )

            # STEP 3: Raw text (last resort)
            final_text = raw_text.strip()
            if "data:" in final_text or "[DONE]" in final_text:
                raise ProviderError(self.name, "Unparsable SSE artifacts", retryable=True)

            final_text = _strip_reasoning(final_text)
            if not final_text:
                raise ProviderError(self.name, "Empty content after cleaning", retryable=True)

            return AIResponse(
                text=final_text,
                provider=self.name,
                model=f"pollinations:{MODEL_ID}",
                tokens_used=0,
                metadata={"endpoint": "openai", "parsed": "raw"},
            )

        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Request timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                self._last_429_time = time.time()
                self._429_count += 1
                logger.warning(f"Pollinations rate-limited (429)! Count={self._429_count}")
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(
                self.name,
                f"HTTP {status}: {exc.response.text[:200]}",
                retryable=retryable,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"Unexpected error: {exc}", retryable=True)

    async def close(self) -> None:
        """Close httpx client."""
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
