from __future__ import annotations

import json
import os
import re
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

    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 structured_unsupported: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.structured_unsupported = structured_unsupported


def extract_json_object(raw: str) -> dict[str, Any]:
    """Extract one JSON object from provider text, including fenced or prefixed output.

    A truncated object remains a JSON decode error; callers can then report a
    precise truncation or invalid-JSON diagnosis instead of accepting partial data.
    """
    text = (raw or "").lstrip("\ufeff").strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(value, dict):
            return value
        last_error = json.JSONDecodeError("Expected a JSON object", candidate, 0)
    # Some models prepend a prose sentence before otherwise valid JSON.
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(value, dict):
            return value
    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("No JSON object found", text, 0)


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
    max_output_tokens: int = 0  # 0 = provider-appropriate default
    max_attempts: int = 2  # one transient retry; output errors are never retried here
    retry_backoff_seconds: float = 0.5


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
        try:
            max_output = max(0, int(os.environ.get("MEETINGBRO_LLM_MAX_OUTPUT_TOKENS", "0")))
        except ValueError:
            max_output = 0
        try:
            max_attempts = min(3, max(1, int(os.environ.get("MEETINGBRO_LLM_MAX_ATTEMPTS", "2"))))
        except ValueError:
            max_attempts = 2
        try:
            backoff = max(0.0, min(5.0, float(os.environ.get("MEETINGBRO_LLM_RETRY_BACKOFF_SECONDS", "0.5"))))
        except ValueError:
            backoff = 0.5
        return cls(
            OpenAICompatibleConfig(
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                model=model,
                timeout_seconds=timeout,
                max_output_tokens=max_output,
                max_attempts=max_attempts,
                retry_backoff_seconds=backoff,
            )
        )

    @property
    def model(self) -> str:
        return self._cfg.model

    @property
    def max_output_tokens(self) -> int:
        """Bound for one completion; reasoning-disabled OpenRouter models can emit long JSON."""
        if self._cfg.max_output_tokens > 0:
            return self._cfg.max_output_tokens
        if urlparse(self._cfg.base_url).hostname == "openrouter.ai":
            return 12000
        return 7200

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
        expects_json = response_schema is not None
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
            if expects_json:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "meeting_report", "strict": True, "schema": response_schema,
                }}
                body["provider"] = {"require_parameters": True}
        elif expects_json:
            body["response_format"] = {"type": "json_object"}

        fallback_used = False
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._post(url, body, expects_json=expects_json)
            except LLMResponseError as exc:
                if exc.structured_unsupported:
                    if not fallback_used:
                        fallback_used = True
                        # A provider may reject strict JSON Schema even though it
                        # accepts ordinary chat completions. Retry once with the
                        # prompt's schema only; never silently drop the request.
                        body.pop("response_format", None)
                        body.pop("provider", None)
                        _LOGGER.info(
                            "LLM structured output unsupported model=%s; retrying without response_format",
                            self._cfg.model,
                        )
                        continue
                    raise LLMResponseError(
                        "provider_error",
                        "The model provider rejected the requested structured output parameters.",
                    ) from exc
                if exc.retryable and attempt < self._cfg.max_attempts:
                    delay = self._cfg.retry_backoff_seconds * (2 ** (attempt - 1))
                    if delay > 0:
                        time.sleep(delay)
                    _LOGGER.info(
                        "Retrying transient LLM failure model=%s code=%s attempt=%s",
                        self._cfg.model, exc.code, attempt,
                    )
                    continue
                raise

    def _post(self, url: str, body: dict[str, Any], *, expects_json: bool) -> str:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._cfg.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        start = time.time()
        _LOGGER.debug("LLM request start model=%s url=%s", self._cfg.model, url)
        try:
            with urllib.request.urlopen(req, timeout=self._cfg.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = self._error_body(exc)
            status = exc.code
            marker = re.search(
                r"response[ _-]?format|json[ _-]?schema|structured|require_parameters|no endpoints|unsupported",
                detail,
                re.IGNORECASE,
            )
            structured_unsupported = bool(
                expects_json and status in {400, 404, 422} and
                (marker or (urlparse(url).hostname == "openrouter.ai" and status == 404))
            )
            retryable = status in {408, 429, 500, 502, 503, 504}
            if status in {401, 403}:
                code, message = "model_authentication", f"The summary model service returned HTTP {status}."
            elif status == 429:
                code, message = "model_rate_limited", "The summary model service is rate limiting requests."
            elif status in {408, 504}:
                code, message = "model_timeout", "The summary model service timed out."
            elif structured_unsupported:
                code, message = "structured_output_unsupported", "The provider rejected structured output parameters."
            else:
                code, message = "provider_error", f"The summary model service returned HTTP {status}."
            _LOGGER.warning(
                "LLM HTTP error model=%s url=%s code=%s retryable=%s fallback=%s",
                self._cfg.model, url, status, retryable, structured_unsupported,
            )
            raise LLMResponseError(code, message, retryable=retryable,
                                   structured_unsupported=structured_unsupported) from exc
        except TimeoutError as exc:
            raise LLMResponseError("model_timeout", "The summary model request timed out.", retryable=True) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise LLMResponseError("model_timeout", "The summary model request timed out.", retryable=True) from exc
            raise LLMResponseError("model_connection", "The summary model service could not be reached.", retryable=True) from exc
        except UnicodeDecodeError as exc:
            raise LLMResponseError("invalid_response", "The model provider returned an unreadable response.") from exc
        except OSError as exc:
            raise LLMResponseError("model_connection", "The summary model service could not be reached.", retryable=True) from exc
        finally:
            duration = time.time() - start
            _LOGGER.debug("LLM request finished model=%s duration=%.3fs", self._cfg.model, duration)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            _LOGGER.warning("LLM returned non-JSON data model=%s", self._cfg.model)
            raise LLMResponseError("invalid_response", "The model provider returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise LLMResponseError("invalid_response", "The model provider returned an invalid response.")
        if data.get("error"):
            raise LLMResponseError("provider_error", "The model provider returned an error instead of an answer.")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMResponseError("empty_response", "The model provider returned no completed answer.")
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        if finish_reason in {"error", "content_filter"}:
            raise LLMResponseError("incomplete_response", "The model provider did not complete the answer.")
        message = choice.get("message", {})
        content: Any = ""
        # Primary: standard OpenAI-style chat message content
        if isinstance(message, dict):
            if message.get("refusal"):
                raise LLMResponseError("refusal", "The model declined to generate an answer.")
            # Reasoning is not the final answer, even if it contains JSON.
            content = message.get("content") or message.get("text") or ""
        # Fallbacks for other provider shapes
        if not content:
            content = choice.get("text") or ""
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        elif isinstance(content, dict):
            content = content.get("text") or content.get("content") or ""
        content = content.strip() if isinstance(content, str) else ""
        if not content:
            if finish_reason == "length":
                raise LLMResponseError(
                    "output_truncated",
                    "The model reached its output token limit before completing the answer. Reduce reasoning or increase the output limit.",
                )
            raise LLMResponseError(
                "empty_response",
                "The model returned no final answer. Reasoning text cannot be used as a summary.",
            )
        if finish_reason == "length":
            # Providers sometimes report "length" even when the JSON object is
            # complete. Accept only structurally complete JSON; truncated JSON
            # still fails with an accurate diagnosis.
            if expects_json:
                try:
                    extract_json_object(content)
                except json.JSONDecodeError as exc:
                    raise LLMResponseError(
                        "output_truncated",
                        "The model reached its output token limit before completing the answer. Reduce reasoning or increase the output limit.",
                    ) from exc
            else:
                raise LLMResponseError(
                    "output_truncated",
                    "The model reached its output token limit before completing the answer. Reduce reasoning or increase the output limit.",
                )
        return content

    @staticmethod
    def _error_body(exc: urllib.error.HTTPError) -> str:
        try:
            body = exc.read(4096)
            return body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        except Exception:
            return ""
