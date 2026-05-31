"""Pollinations.ai Provider — FREE, no API key needed! ALWAYS available.

FIXED v5.0: 
  - Properly handles SSE/streaming responses from Pollinations
  - Strips data: prefixes and [DONE] markers  
  - Vision via OpenAI-compatible multimodal messages
  - Robust JSON parsing for chat completion responses
  - Never leaks raw SSE artifacts to users
"""
import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

TEXT_BASE = "https://text.pollinations.ai"

TEXT_MODELS = {
    "default": "openai",      # GPT-4o-mini — good Russian + vision
    "fast": "mistral",        # Mistral Small — fast responses
    "reasoning": "deepseek",  # DeepSeek V3 — reasoning tasks
    "vision": "openai",       # openai model supports vision
    "qwen": "qwen",           # Qwen — excellent for multilingual/Russian
}


def _strip_sse_artifacts(text: str) -> str:
    """Remove SSE/streaming artifacts from Pollinations response.
    
    Pollinations sometimes returns Server-Sent Events format:
      data: {"type":"start"}
      data: {"type":"content","content":"Hello"}
      data: {"type":"error","errorText":"..."}
      data: [DONE]
    
    This function extracts the actual content and removes all SSE framing.
    """
    if not text:
        return ""
    
    text = text.strip()
    
    # If it's NOT SSE format, return as-is
    if not text.startswith("data:"):
        return text
    
    # Parse SSE format - extract content from data: lines
    content_parts = []
    has_error = False
    error_msg = ""
    
    for line in text.split("\n"):
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        
        data_str = line[5:].strip()  # Remove "data:" prefix
        
        if data_str == "[DONE]":
            break
        
        # Try to parse as JSON
        try:
            data = json.loads(data_str)
            
            # Handle different SSE message types
            if isinstance(data, dict):
                msg_type = data.get("type", "")
                
                if msg_type == "error":
                    has_error = True
                    error_msg = data.get("errorText", data.get("error", "Unknown error"))
                    logger.warning(f"Pollinations SSE error: {error_msg}")
                    continue
                
                if msg_type == "content":
                    content = data.get("content", data.get("text", ""))
                    if content:
                        content_parts.append(content)
                    continue
                
                # Chat completion format within SSE
                if "choices" in data:
                    for choice in data.get("choices", []):
                        delta = choice.get("delta", {})
                        msg = choice.get("message", {})
                        content = delta.get("content", "") or msg.get("content", "")
                        if content:
                            content_parts.append(content)
                    continue
                
                # Direct content field
                content = data.get("content", data.get("text", data.get("response", "")))
                if content and isinstance(content, str):
                    content_parts.append(content)
        except json.JSONDecodeError:
            # Not JSON - might be plain text content after data:
            if data_str and not data_str.startswith("{"):
                content_parts.append(data_str)
    
    result = "".join(content_parts).strip()
    
    # If we got an error and no content, raise
    if has_error and not result:
        raise ProviderError("pollinations", f"SSE error: {error_msg}", retryable=True)
    
    return result


def _parse_json_response(text: str) -> str:
    """Try to parse response as OpenAI-compatible JSON chat completion."""
    if not text:
        return ""
    
    # Try direct JSON parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            # OpenAI chat completion format
            if "choices" in data:
                for choice in data["choices"]:
                    msg = choice.get("message", {})
                    content = msg.get("content", "")
                    if content:
                        return content
            # Simple format
            if "content" in data:
                return data["content"]
            if "text" in data:
                return data["text"]
            if "response" in data:
                return data["response"]
    except (json.JSONDecodeError, TypeError):
        pass
    
    return ""


class PollinationsProvider(BaseProvider):
    """Pollinations.ai provider — free, no API key required, always available.
    
    FIXED: Properly handles SSE/streaming responses and never leaks artifacts.
    """

    name: str = "pollinations"
    supports_streaming: bool = False
    supports_vision: bool = True  # via openai model

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key="", timeout=timeout)

    async def init(self) -> None:
        """Initialize httpx async client with connection pooling."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            follow_redirects=True,
            headers={
                "User-Agent": "NastyaBot/14.0",
                "Accept": "text/plain, application/json",
            },
        )

    def is_available(self) -> bool:
        """Pollinations is always available — no key needed."""
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate text via Pollinations OpenAI-compatible POST API.
        
        FIXED: Properly handles both plain text and SSE streaming responses.
        """
        if not self._client:
            await self.init()

        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")
        image_base64 = kwargs.get("image_base64")

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Handle vision via multimodal content
        if image_base64 and messages:
            model = TEXT_MODELS["vision"]
            # Only modify the LAST user message for vision
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    existing_content = messages[i].get("content", "")
                    if isinstance(existing_content, str):
                        messages[i] = {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_base64}"
                                    },
                                },
                                {"type": "text", "text": existing_content},
                            ],
                        }
                    break

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,  # IMPORTANT: request non-streaming response
        }

        try:
            response = await self._client.post(
                f"{TEXT_BASE}/",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()

            raw_text = response.text

            if not raw_text:
                raise ProviderError(
                    self.name,
                    "Empty response from Pollinations",
                    retryable=True,
                )

            # STEP 1: Try to parse as JSON chat completion
            parsed = _parse_json_response(raw_text)
            if parsed:
                return AIResponse(
                    text=parsed,
                    provider=self.name,
                    model=f"pollinations:{model}",
                    tokens_used=0,
                    metadata={"endpoint": "text_post", "parsed": "json"},
                )

            # STEP 2: Check for SSE format and strip artifacts
            cleaned = _strip_sse_artifacts(raw_text)
            if cleaned:
                return AIResponse(
                    text=cleaned,
                    provider=self.name,
                    model=f"pollinations:{model}",
                    tokens_used=0,
                    metadata={"endpoint": "text_post", "parsed": "sse_stripped"},
                )

            # STEP 3: Use raw text as last resort (it might be plain text)
            # But filter out any remaining SSE patterns
            final_text = raw_text
            if "data:" in final_text or "[DONE]" in final_text:
                # Still has SSE artifacts — don't use this
                raise ProviderError(
                    self.name,
                    "Response contains unparsable SSE artifacts",
                    retryable=True,
                )

            return AIResponse(
                text=final_text,
                provider=self.name,
                model=f"pollinations:{model}",
                tokens_used=0,
                metadata={"endpoint": "text_post", "parsed": "raw"},
            )

        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Text generation timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
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

    async def generate_with_vision(
        self,
        prompt: str,
        image_data: bytes = b"",
        image_url: str = "",
        **kwargs,
    ) -> AIResponse:
        """Generate response with image understanding via Pollinations openai model."""
        if not self._client:
            await self.init()

        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)

        content_parts: List[Dict[str, Any]] = []
        if image_data:
            b64 = base64.b64encode(image_data).decode("utf-8")
            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}"
                },
            })
        elif image_url:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": image_url},
            })
        content_parts.append({"type": "text", "text": prompt})

        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})

        payload: Dict[str, Any] = {
            "model": TEXT_MODELS["vision"],
            "messages": messages,
            "temperature": temperature,
            "stream": False,  # IMPORTANT: request non-streaming
        }

        try:
            response = await self._client.post(
                f"{TEXT_BASE}/",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            raw_text = response.text

            if not raw_text:
                raise ProviderError(
                    self.name,
                    "Empty vision response from Pollinations",
                    retryable=True,
                )

            # Parse response — same logic as generate()
            parsed = _parse_json_response(raw_text)
            if parsed:
                text = parsed
            else:
                text = _strip_sse_artifacts(raw_text)

            if not text:
                raise ProviderError(
                    self.name,
                    "Could not parse vision response",
                    retryable=True,
                )

            return AIResponse(
                text=text,
                provider=self.name,
                model=f"pollinations:{TEXT_MODELS['vision']}",
                tokens_used=0,
                metadata={"vision": True, "endpoint": "text_post"},
            )

        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Vision request timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(
                self.name,
                f"Vision HTTP {status}: {exc.response.text[:200]}",
                retryable=retryable,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"Vision error: {exc}", retryable=True)
