"""
LLM Provider Client - Unified interface for multiple LLM providers.

The module avoids importing provider SDKs at import time so that the FastAPI
application can still boot in partially configured environments.
"""

import os
import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from app.core.settings import get_settings, ProviderConfig

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """A single chat message."""
    role: str
    content: str


@dataclass
class ChatResponse:
    """Response from a chat completion."""
    text: str
    usage: Dict[str, int]
    latency_ms: int
    raw_provider: str
    model: str
    finish_reason: Optional[str] = None
    error: Optional[str] = None


class ProviderError(Exception):
    """Error from LLM provider."""

    def __init__(self, message: str, provider: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class ProviderClient:
    """Unified LLM provider client backed by litellm."""

    def __init__(self, provider_name: Optional[str] = None):
        self.settings = get_settings()
        self.provider_name = provider_name or self.settings.ACTIVE_PROVIDER_NAME
        self.config = self.settings.get_provider_config(self.provider_name)

    def _get_litellm(self):
        try:
            import litellm  # type: ignore
            litellm.drop_params = True
            litellm.set_verbose = False
            return litellm
        except ImportError as e:
            raise ProviderError(
                "litellm is not installed. Install backend dependencies before using provider-backed features.",
                self.provider_name,
                500,
            ) from e

    def _get_model_string(self, model: Optional[str] = None) -> str:
        model_name = model or self.settings.get_active_model(self.provider_name)
        api_format = getattr(self.config, "api_format", "openai")
        if api_format == "openai":
            return f"openai/{model_name}"
        if self.provider_name == "minimax":
            return f"anthropic/{model_name}"
        if api_format == "anthropic":
            return model_name
        return model_name

    def _get_api_config(self) -> Dict[str, Any]:
        api_key = self.settings.get_runtime_api_key(self.provider_name) or self.config.get_api_key()
        base_url = (
            self.settings.get_runtime_base_url(self.provider_name)
            or self.config.get_base_url()
            or self.settings._get_default_base_url(self.provider_name)
        )

        if not api_key:
            raise ProviderError(
                f"API key not configured for provider '{self.provider_name}'. "
                f"Set environment variable: {self.config.api_key_env}",
                self.provider_name,
                400,
            )

        return {
            "api_key": api_key,
            "api_base": base_url,
            "timeout": self.config.timeout,
        }

    def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ) -> ChatResponse:
        litellm = self._get_litellm()
        api_config = self._get_api_config()
        model_string = self._get_model_string(model)
        messages_dict = [{"role": m.role, "content": m.content} for m in messages]

        start_time = time.time()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.settings.MAX_RETRIES + 1):
            try:
                response = litellm.completion(
                    model=model_string,
                    messages=messages_dict,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    api_key=api_config["api_key"],
                    api_base=api_config["api_base"],
                    timeout=api_config["timeout"],
                    **kwargs,
                )

                latency_ms = int((time.time() - start_time) * 1000)
                choice = response.choices[0]
                text = choice.message.content or ""
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                }

                return ChatResponse(
                    text=text,
                    usage=usage,
                    latency_ms=latency_ms,
                    raw_provider=self.provider_name,
                    model=model or self.settings.get_active_model(self.provider_name),
                    finish_reason=choice.finish_reason,
                )
            except Exception as e:
                last_error = e
                if attempt < self.settings.MAX_RETRIES:
                    backoff = self.settings.RETRY_BACKOFF * (2 ** (attempt - 1))
                    logger.warning(
                        "Provider request failed (attempt %s/%s): %s. Retrying in %ss...",
                        attempt,
                        self.settings.MAX_RETRIES,
                        e,
                        backoff,
                    )
                    time.sleep(backoff)

        error_msg = str(last_error) if last_error else "unknown error"
        logger.error("Provider request failed after %s attempt(s): %s", self.settings.MAX_RETRIES, error_msg)
        raise ProviderError(
            f"Provider '{self.provider_name}' request failed: {error_msg}",
            self.provider_name,
            502,
        )

    def test_connection(self, prompt: str = "Say OK", max_tokens: int = 32) -> ChatResponse:
        messages = [ChatMessage(role="user", content=prompt)]
        return self.chat(messages, max_tokens=max_tokens, temperature=0)

    def get_capabilities(self) -> Dict[str, Any]:
        return {
            "providerName": self.provider_name,
            "model": self.settings.get_active_model(self.provider_name),
            "configured": self.config.is_configured(),
            "timeout": self.config.timeout,
            "maxRetries": self.settings.MAX_RETRIES,
            "sdkInstalled": self._litellm_available(),
        }

    def _litellm_available(self) -> bool:
        try:
            import litellm  # type: ignore  # noqa: F401
            return True
        except ImportError:
            return False


_client: Optional[ProviderClient] = None


def get_provider_client(provider_name: Optional[str] = None) -> ProviderClient:
    global _client
    if provider_name:
        return ProviderClient(provider_name)
    if _client is None:
        _client = ProviderClient()
    return _client


def reset_client():
    global _client
    _client = None
