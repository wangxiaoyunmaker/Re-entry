"""Small OpenAI-compatible provider wrapper for the behavior extractor.

The provider is deliberately separate from the qualitative prompt and from
Skyline. It handles transport concerns only: authentication, bounded retry,
JSON-mode requests, response extraction, and redacted errors.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import ValidationError


@dataclass(frozen=True)
class ProviderConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    timeout_seconds: float = 45.0
    max_retries: int = 2
    temperature: float = 0.0
    thinking_type: str = "disabled"

    @classmethod
    def from_env(cls) -> "ProviderConfig":
        api_key = os.environ.get("RETRACE_LLM_API_KEY", "").strip()
        if not api_key:
            raise ValidationError(
                "RETRACE_LLM_API_KEY is required; do not put the key in source files"
            )
        return cls(
            api_key=api_key,
            base_url=os.environ.get("RETRACE_LLM_BASE_URL", cls.base_url).rstrip("/"),
            model=os.environ.get("RETRACE_LLM_MODEL", cls.model),
            timeout_seconds=float(os.environ.get("RETRACE_LLM_TIMEOUT", cls.timeout_seconds)),
            max_retries=int(os.environ.get("RETRACE_LLM_MAX_RETRIES", cls.max_retries)),
            thinking_type=os.environ.get("RETRACE_LLM_THINKING", cls.thinking_type),
        )


def _content_from_response(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValidationError("LLM provider response must be a JSON object")
    try:
        content = payload["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValidationError("LLM provider response has no choices[0].message.content") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        value = "".join(part for part in text_parts if isinstance(part, str))
        if value:
            return value
    raise ValidationError("LLM provider returned an unsupported message content shape")


def call_chat_completion(
    system_prompt: str,
    user_prompt: str,
    *,
    config: ProviderConfig | None = None,
) -> str:
    """Call an OpenAI-compatible chat endpoint and return message content.

    Only transport failures with HTTP 429/5xx or transient URL errors are
    retried. A malformed model response is not retried here; the caller's
    schema validator should report it as a data-quality failure.
    """

    cfg = config or ProviderConfig.from_env()
    url = f"{cfg.base_url}/chat/completions"
    body = json.dumps(
        {
            "model": cfg.model,
            "temperature": cfg.temperature,
            "thinking": {"type": cfg.thinking_type},
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    attempts = max(0, cfg.max_retries) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=cfg.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
            return _content_from_response(json.loads(raw))
        except HTTPError as exc:
            last_error = exc
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt == attempts - 1:
                raise ValidationError(
                    f"LLM provider HTTP failure status={exc.code}; response body omitted"
                ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                raise ValidationError(f"LLM provider transport failure: {type(exc).__name__}") from exc
        time.sleep(min(2**attempt, 4))
    raise ValidationError("LLM provider failed after bounded retries") from last_error
