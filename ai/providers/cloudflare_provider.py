"""Cloudflare Workers AI Provider - dual-account failover with native binding.

Uses the native Workers AI binding endpoint (NOT the OpenAI-compatible one):
  POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}

Model: @cf/mistralai/mistral-small-3.1-24b-instruct
  - Mistral Small 3.1, 24B parameters
  - Supports vision via ``image_url`` in messages
  - Free tier: 10,000 requests/day per account token

Dual-account failover:
  - Account 1 is tried first; on 401/403/429 it is marked limited and
    Account 2 takes over automatically.
  - Per-account daily request counters auto-reset at midnight.
  - When both accounts are exhausted a ``ProviderError(retryable=True)`` is raised.

v4.0: Complete rewrite — native Workers AI binding, dual-account failover,
      Mistral Small 3.1 with vision, <think>-tag stripping.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "@cf/mistralai/mistral-small-3.1-24b-instruct"
"""Primary model — Mistral Small 3.1 with vision support."""

DAILY_REQUEST_LIMIT = 10_000
"""Per-account daily request cap (Cloudflare free tier)."""

CF_API_BASE = "https://api.cloudflare.com/client/v4/accounts"

# Regex to strip <think>…</think> blocks (Mistral reasoning artefacts)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


# ---------------------------------------------------------------------------
# Per-account state
# ---------------------------------------------------------------------------

class _AccountState:
    """Track rate-limit status and daily request count for one CF account."""

    __slots__ = ("account_id", "token", "daily_count", "count_date",
                 "rate_limited", "unauthorized")

    def __init__(self, account_id: str, token: str) -> None:
        self.account_id = account_id
        self.token = token
        self.daily_count: int = 0
        self.count_date: str = ""      # YYYY-MM-DD in UTC
        self.rate_limited: bool = False
        self.unauthorized: bool = False

    # ---- daily counter helpers ----

    def _today_utc(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def increment(self) -> None:
        """Increment daily counter, resetting if the date rolled over."""
        today = self._today_utc()
        if self.count_date != today:
            self.daily_count = 0
            self.count_date = today
            # If we crossed midnight, the new day resets rate-limit flags too
            # (the limit is per calendar day).
            self.rate_limited = False
        self.daily_count += 1

    def remaining(self) -> int:
        """Requests remaining today."""
        today = self._today_utc()
        if self.count_date != today:
            return DAILY_REQUEST_LIMIT
        return max(0, DAILY_REQUEST_LIMIT - self.daily_count)

    def is_depleted(self) -> bool:
        return self.remaining() <= 0

    def mark_rate_limited(self) -> None:
        self.rate_limited = True
        logger.warning(
            "CF account %s…%s rate-limited (%d/%d today)",
            self.account_id[:4], self.account_id[-4:],
            self.daily_count, DAILY_REQUEST_LIMIT,
        )

    def mark_unauthorized(self) -> None:
        self.unauthorized = True
        logger.error(
            "CF account %s…%s unauthorized (bad token?)",
            self.account_id[:4], self.account_id[-4:],
        )

    def is_usable(self) -> bool:
        """True if account can still accept requests right now."""
        return not self.unauthorized and not self.is_depleted()


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class CloudflareProvider(BaseProvider):
    """Cloudflare Workers AI provider with dual-account failover.

    Uses the **native** Workers AI binding endpoint:
        ``POST /client/v4/accounts/{account_id}/ai/run/{model}``

    Not the OpenAI-compatible ``/v1/chat/completions`` endpoint.

    Configuration (constructor or env vars):
      - *account_id_1* / *token_1*: Primary CF account (env:
        ``CLOUDFLARE_ACCOUNT_ID`` / ``CLOUDFLARE_API_TOKEN``)
      - *account_id_2* / *token_2*: Secondary CF account (env:
        ``CLOUDFLARE_ACCOUNT_ID_2`` / ``CLOUDFLARE_API_TOKEN_2``)

    The provider maintains per-account daily request counters that
    auto-reset at midnight UTC.  When one account hits its 10 000/day
    limit or receives a 429/401/403, requests automatically route to the
    other account.
    """

    name: str = "cloudflare"
    supports_streaming: bool = False
    supports_vision: bool = True

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        account_id_1: str = "",
        token_1: str = "",
        account_id_2: str = "",
        token_2: str = "",
        timeout: float = 30.0,
    ) -> None:
        # We do NOT call super().__init__() with a single api_key because
        # this provider manages two independent tokens.
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

        # Build account states — fall back to env vars when not passed
        self._accounts: List[_AccountState] = []

        aid1 = account_id_1 or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        tok1 = token_1 or os.environ.get("CLOUDFLARE_API_TOKEN", "")
        if aid1 and tok1:
            self._accounts.append(_AccountState(aid1, tok1))

        aid2 = account_id_2 or os.environ.get("CLOUDFLARE_ACCOUNT_ID_2", "")
        tok2 = token_2 or os.environ.get("CLOUDFLARE_API_TOKEN_2", "")
        if aid2 and tok2:
            self._accounts.append(_AccountState(aid2, tok2))

        # Set api_key attribute for BaseProvider.is_available() compatibility
        self.api_key = tok1 or tok2 or ""

        # Which account index to try first (updated by failover logic)
        self._active_index: int = 0

        logger.info(
            "CloudflareProvider: %d account(s) configured",
            len(self._accounts),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Create a shared ``httpx.AsyncClient`` (no per-account base URL)."""
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        logger.debug("CloudflareProvider: httpx client initialised")

    async def close(self) -> None:
        """Shut down the httpx client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """True if **at least one** account is usable right now."""
        return any(acc.is_usable() for acc in self._accounts)

    # ------------------------------------------------------------------
    # Public API — text generation
    # ------------------------------------------------------------------

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate a text response using Mistral Small 3.1.

        Args:
            prompt: The user's current message.
            **kwargs:
                system_prompt (str): System instructions.
                messages (List[Dict]): Conversation history.
                model (str): Model override (full CF model ID).
                temperature (float): Sampling temperature.
                max_tokens (int): Max tokens to generate.

        Returns:
            AIResponse with provider='cloudflare', model='cf:mistral-small-3.1'.

        Raises:
            ProviderError: On failure with retryable=True if another
                account might work, False otherwise.
        """
        await self._ensure_client()

        model: str = kwargs.get("model", DEFAULT_MODEL)
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        max_tokens: int = kwargs.get("max_tokens", 4096)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        messages = self._build_messages(prompt, system_prompt, messages_history)

        return await self._call_api(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # ------------------------------------------------------------------
    # Public API — vision
    # ------------------------------------------------------------------

    async def generate_vision(
        self,
        prompt: str,
        image_data: bytes,
        image_format: str = "jpeg",
        **kwargs,
    ) -> AIResponse:
        """Generate a response that includes an image for the model to analyse.

        Mistral Small 3.1 accepts images via the ``image_url`` content type
        inside the messages array — the same format used by OpenAI-compatible
        vision APIs.

        Args:
            prompt: Text prompt / question about the image.
            image_data: Raw image bytes.
            image_format: MIME subtype (``"jpeg"``, ``"png"``, ``"webp"``).
            **kwargs: Same as :meth:`generate` (system_prompt, messages, …).

        Returns:
            AIResponse with ``metadata.vision=True``.

        Raises:
            ProviderError: On failure.
        """
        await self._ensure_client()

        model: str = kwargs.get("model", DEFAULT_MODEL)
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        max_tokens: int = kwargs.get("max_tokens", 4096)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Encode image as base64 data-URI
        b64 = base64.b64encode(image_data).decode("ascii")
        data_uri = f"data:image/{image_format};base64,{b64}"

        # Inject image into the last user message as multi-part content
        messages = self._inject_image(messages, data_uri, prompt)

        response = await self._call_api(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Tag the response so callers know vision was used
        response.metadata["vision"] = True
        return response

    # ------------------------------------------------------------------
    # Core API call with dual-account failover
    # ------------------------------------------------------------------

    async def _call_api(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AIResponse:
        """Execute a single API request with dual-account failover.

        Iterates through accounts starting from the active one.
        On 429 → mark account rate-limited, try next.
        On 401/403 → mark account unauthorized, try next.
        On success → parse response, increment counter, return.

        Raises:
            ProviderError: If all accounts fail.
        """
        if not self._accounts:
            raise ProviderError(self.name, "No accounts configured", retryable=False)

        payload: Dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Determine the order to try accounts — active index first
        order = self._account_order()

        last_error: Optional[ProviderError] = None

        for idx in order:
            acc = self._accounts[idx]

            if acc.unauthorized:
                continue  # permanently bad until restart / config change
            if acc.is_depleted():
                logger.info(
                    "CF account %s…%s depleted (%d used today), skipping",
                    acc.account_id[:4], acc.account_id[-4:],
                    acc.daily_count,
                )
                continue

            url = f"{CF_API_BASE}/{acc.account_id}/ai/run/{model}"
            headers = {
                "Authorization": f"Bearer {acc.token}",
                "Content-Type": "application/json",
            }

            try:
                logger.debug(
                    "CF → account %s…%s model=%s",
                    acc.account_id[:4], acc.account_id[-4:], model,
                )
                resp = await self._client.post(url, json=payload, headers=headers)  # type: ignore[union-attr]

                # ---- handle HTTP errors before parsing ----
                if resp.status_code == 429:
                    acc.mark_rate_limited()
                    last_error = ProviderError(
                        self.name,
                        f"Account {acc.account_id[:4]}…{acc.account_id[-4:]} "
                        f"rate-limited (429)",
                        retryable=True,
                    )
                    continue

                if resp.status_code in (401, 403):
                    acc.mark_unauthorized()
                    last_error = ProviderError(
                        self.name,
                        f"Account {acc.account_id[:4]}…{acc.account_id[-4:]} "
                        f"unauthorized ({resp.status_code})",
                        retryable=False,
                    )
                    continue

                resp.raise_for_status()

                # ---- parse native Workers AI response ----
                data = resp.json()
                text = self._parse_response_text(data)

                if not text:
                    last_error = ProviderError(
                        self.name,
                        f"Empty response from model {model}",
                        retryable=True,
                    )
                    continue

                # Strip <think> reasoning tags
                text = _THINK_RE.sub("", text).strip()

                # Track usage
                acc.increment()
                self._active_index = idx  # remember working account

                # Extract token counts if available
                usage = self._extract_usage(data)

                return AIResponse(
                    text=text,
                    provider=self.name,
                    model=f"cf:mistral-small-3.1",
                    tokens_used=usage.get("total_tokens", 0),
                    finish_reason=usage.get("finish_reason", ""),
                    metadata={
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "cf_model": model,
                        "account_index": idx,
                        "account_daily_count": acc.daily_count,
                    },
                )

            except httpx.TimeoutException as exc:
                last_error = ProviderError(
                    self.name,
                    f"Timeout calling model {model}: {exc}",
                    retryable=True,
                )
                continue

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 404:
                    logger.warning("CF model %s not found (404)", model)
                    last_error = ProviderError(
                        self.name,
                        f"Model {model} not found (404)",
                        retryable=False,
                    )
                    continue
                if status in (500, 502, 503, 504):
                    last_error = ProviderError(
                        self.name,
                        f"Server error {status} for model {model}",
                        retryable=True,
                    )
                    continue
                last_error = ProviderError(
                    self.name,
                    f"HTTP {status}: {exc.response.text[:200]}",
                    retryable=False,
                )
                continue

            except ProviderError:
                raise  # don't wrap our own errors

            except Exception as exc:
                last_error = ProviderError(
                    self.name,
                    f"Unexpected error with model {model}: {exc}",
                    retryable=True,
                )
                continue

        # All accounts exhausted
        if last_error:
            raise last_error
        raise ProviderError(
            self.name,
            "All Cloudflare accounts exhausted or unavailable",
            retryable=True,
        )

    # ------------------------------------------------------------------
    # Response parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response_text(data: Dict[str, Any]) -> str:
        """Extract the generated text from a native Workers AI response.

        Expected format::

            {
              "result": {"response": "The generated text"},
              "success": true
            }

        But we also handle a few variations defensively.
        """
        # Native binding format
        if "result" in data and isinstance(data["result"], dict):
            return data["result"].get("response", "")

        # Flat format (sometimes returned for text-only models)
        if "response" in data and isinstance(data["response"], str):
            return data["response"]

        # Result as plain string
        if "result" in data and isinstance(data["result"], str):
            return data["result"]

        return ""

    @staticmethod
    def _extract_usage(data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract token usage from the response, if present.

        The native binding may include usage in ``result.meta`` or at the
        top level depending on the model.
        """
        result: Dict[str, Any] = {}

        # Top-level usage (some models)
        if "usage" in data and isinstance(data["usage"], dict):
            result.update(data["usage"])

        # Nested in result.meta
        meta = data.get("result", {})
        if isinstance(meta, dict):
            if "meta" in meta and isinstance(meta["meta"], dict):
                result.update(meta["meta"].get("usage", {}))
            # Some models put tokens at result level
            if "prompt_tokens" in meta:
                result.setdefault("prompt_tokens", meta["prompt_tokens"])
            if "completion_tokens" in meta:
                result.setdefault("completion_tokens", meta["completion_tokens"])

        # Compute total if not already present
        if "total_tokens" not in result:
            pt = result.get("prompt_tokens", 0)
            ct = result.get("completion_tokens", 0)
            if pt or ct:
                result["total_tokens"] = pt + ct

        return result

    # ------------------------------------------------------------------
    # Vision helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_image(
        messages: List[Dict[str, Any]],
        data_uri: str,
        fallback_text: str,
    ) -> List[Dict[str, Any]]:
        """Add an ``image_url`` content block to the last user message.

        Mistral Small 3.1 accepts the OpenAI-compatible multi-part content
        format where a user message contains both ``text`` and ``image_url``
        parts.

        If the last user message is a plain string it is converted to a
        list with a text part + an image part.  If it is already a list the
        image part is appended.
        """
        # Deep-copy to avoid mutating caller's list
        msgs = [dict(m) for m in messages]

        # Find the last user message
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "user":
                content = msgs[i].get("content", fallback_text)

                image_part = {
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                }

                if isinstance(content, str):
                    msgs[i]["content"] = [
                        {"type": "text", "text": content},
                        image_part,
                    ]
                elif isinstance(content, list):
                    # Already multi-part — append image
                    msgs[i]["content"] = list(content) + [image_part]

                return msgs

        # No user message found — prepend one
        msgs.append({
            "role": "user",
            "content": [
                {"type": "text", "text": fallback_text},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        })
        return msgs

    # ------------------------------------------------------------------
    # Account ordering
    # ------------------------------------------------------------------

    def _account_order(self) -> List[int]:
        """Return account indices to try, preferring the active one.

        Usable accounts come first; depleted / rate-limited / unauthorized
        accounts are included at the end as a last resort (they might
        become usable between checks in edge cases).
        """
        n = len(self._accounts)
        if n <= 1:
            return list(range(n))

        # Start from active index, then wrap around
        order = []
        for offset in range(n):
            idx = (self._active_index + offset) % n
            order.append(idx)

        # Sort: usable first, then the rest
        usable = [i for i in order if self._accounts[i].is_usable()]
        unusable = [i for i in order if not self._accounts[i].is_usable()]
        return usable + unusable

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> None:
        """Initialise the httpx client if it hasn't been created yet."""
        if self._client is None:
            await self.init()
        if self._client is None:
            raise ProviderError(
                self.name,
                "Not initialised (no accounts or httpx failure)",
                retryable=False,
            )
