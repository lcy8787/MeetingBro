from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass
import logging
import time
from pathlib import Path
from typing import Any
from functools import lru_cache

_DOTENV_LOADED = False

_LOGGER = logging.getLogger(__name__)


class LLMResponseError(RuntimeError):
    """A provider response cannot be used as a completed model answer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@lru_cache(maxsize=1)
def _openrouter_reasoning_options() -> dict[str, dict | None]:
    """Public model metadata, fetched once without credentials or completions."""
    try:
        with urllib.request.urlopen('https://openrouter.ai/api/v1/models', timeout=5) as response:
            payload = json.load(response)
        return {item['id']: item.get('reasoning') for item in payload['data'] if isinstance(item, dict) and isinstance(item.get('id'), str)}
    except (OSError, ValueError, KeyError, TypeError):
        _LOGGER.debug('OpenRouter model capabilities are unavailable')
        return {}


def _openrouter_reasoning(model: str) -> dict | None:
    options = _openrouter_reasoning_options()
    if model in options and options[model] is None:
        return None  # Do not require reasoning support from non-reasoning models.
    capabilities = options.get(model) or {}
    efforts = capabilities.get('supported_efforts') or []
    if capabilities.get('mandatory') is False and 'none' in efforts:
        return {'effort': 'none', 'exclude': True}
    effort = next((value for value in ('minimal', 'low', 'medium', 'high') if value in efforts), 'low')
    return {'effort': effort, 'exclude': True}


def _load_dotenv_if_present() -> None:
    """Load a local .env file without adding a runtime dependency.

    Supports both common dotenv lines:
        MEETINGBRO_LLM_API_KEY=...

    and PowerShell-style lines users often paste into .env on Windows:
        $env:MEETINGBRO_LLM_API_KEY="..."

    Existing process environment variables win over .env values.
    """

    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[4] / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            item = line.strip()
            if not item or item.startswith("#") or "=" not in item:
                continue
            key, value = item.split("=", 1)
            key = key.strip()
            if key.lower().startswith("$env:"):
                key = key[5:]
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return


def is_local_ollama_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.port == 11434


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 45.0


class OpenAICompatibleClient:
    """Tiny stdlib client for OpenAI-compatible chat-completions APIs.

    This avoids requiring the OpenAI SDK just to use providers such as LongCat.
    It is intentionally non-streaming because MeetingBro's summary/translation
    workers need one complete result per snapshot.
    """

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self._cfg = config

    @classmethod
    def from_env(cls) -> "OpenAICompatibleClient | None":
        """Build a client from MeetingBro/LongCat environment variables.

        LongCat shortcut:
          LONGCAT_API_KEY=<key>

        Generic OpenAI-compatible config:
          MEETINGBRO_LLM_API_KEY=<key>
          MEETINGBRO_LLM_BASE_URL=https://api.longcat.chat/openai
          MEETINGBRO_LLM_MODEL=LongCat-Flash-Chat
        """

        _load_dotenv_if_present()
        api_key = os.environ.get("MEETINGBRO_LLM_API_KEY") or os.environ.get(
            "LONGCAT_API_KEY"
        )
        base_url = os.environ.get("MEETINGBRO_LLM_BASE_URL")
        if not api_key and base_url and is_local_ollama_url(base_url):
            api_key = "ollama"  # Ollama ignores this compatibility header.
        if not api_key:
            return None

        base_url = os.environ.get("MEETINGBRO_LLM_BASE_URL")
        if not base_url:
            base_url = (
                "https://api.longcat.chat/openai"
                if os.environ.get("LONGCAT_API_KEY")
                else "https://api.openai.com"
            )

        model = os.environ.get("MEETINGBRO_LLM_MODEL")
        if not model:
            model = (
                "LongCat-Flash-Chat"
                if "longcat.chat" in base_url
                else os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            )

        timeout = float(os.environ.get("MEETINGBRO_LLM_TIMEOUT_SECONDS", "45"))
        return cls(
            OpenAICompatibleConfig(
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                model=model,
                timeout_seconds=timeout,
            )
        )

    @property
    def model(self) -> str:
        return self._cfg.model

    def chat(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float = 0.2,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        base_url = self._cfg.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            url = f"{base_url}/chat/completions"
        else:
            url = f"{base_url}/v1/chat/completions"
        body: dict[str, Any] = {
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if urlparse(base_url).hostname == "openrouter.ai":
            # Reasoning models can otherwise spend the whole output allowance
            # thinking and return no answer. Keep room for the actual summary.
            reasoning = _openrouter_reasoning(self._cfg.model)
            if reasoning is not None:
                body["reasoning"] = reasoning
            if response_schema is not None:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "meeting_report", "strict": True, "schema": response_schema,
                }}
                body["provider"] = {"require_parameters": True}
        elif response_schema is not None:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._cfg.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        start = time.time()
        _LOGGER.debug("LLM request start model=%s url=%s", self._cfg.model, url)
        try:
            with urllib.request.urlopen(req, timeout=self._cfg.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            _LOGGER.warning(
                "LLM HTTP error model=%s url=%s code=%s",
                self._cfg.model,
                url,
                exc.code,
            )
            code = 'model_authentication' if exc.code in {401, 403} else 'provider_error'
            raise LLMResponseError(code, f'The summary model service returned HTTP {exc.code}.') from exc
        except TimeoutError:
            raise
        except urllib.error.URLError as exc:
            code = 'model_timeout' if isinstance(exc.reason, TimeoutError) else 'model_connection'
            raise LLMResponseError(code, 'The summary model service could not be reached.') from exc
        finally:
            duration = time.time() - start
            _LOGGER.debug("LLM request finished model=%s duration=%.3fs", self._cfg.model, duration)

        data = json.loads(raw)
        if not isinstance(data, dict):
            raise LLMResponseError('invalid_response', 'The model provider returned an invalid response.')
        if data.get('error'):
            raise LLMResponseError('provider_error', 'The model provider returned an error instead of an answer.')
        choices = data.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMResponseError('empty_response', 'The model provider returned no completed answer.')
        choice = choices[0]
        if choice.get('finish_reason') == 'length':
            raise LLMResponseError('output_truncated', 'The model reached its output token limit before completing the answer. Reduce reasoning or increase the output limit.')
        if choice.get('finish_reason') in {'error', 'content_filter'}:
            raise LLMResponseError('incomplete_response', 'The model provider did not complete the answer.')
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        content = ""
        # Primary: standard OpenAI-style chat message content
        if isinstance(message, dict):
            if message.get('refusal'):
                raise LLMResponseError('refusal', 'The model declined to generate an answer.')
            # Reasoning is not the final answer, even if it contains JSON.
            content = message.get("content") or message.get("text") or ""
        # Fallbacks for other provider shapes
        if not content:
            content = choice.get("text") if isinstance(choice, dict) else ""
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text')
        content = content.strip() if isinstance(content, str) else ''
        if not content:
            raise LLMResponseError('empty_response', 'The model returned no final answer. Reasoning text cannot be used as a summary.')
        return content
