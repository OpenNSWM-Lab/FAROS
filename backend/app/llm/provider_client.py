"""
LLM Provider Client - Unified interface for multiple LLM providers.

The module avoids importing provider SDKs at import time so that the FastAPI
application can still boot in partially configured environments.
"""

import os
import time
import logging
from urllib.parse import urlparse
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from app.core.settings import get_settings, ProviderConfig

logger = logging.getLogger(__name__)


def should_trust_environment_proxy(provider_name: str, base_url: str) -> bool:
    """Decide whether the OpenAI-compatible client should inherit shell proxies.

    WSL commonly inherits a Windows localhost proxy that is not usable from the
    Linux network namespace. DashScope is directly reachable in the target
    deployment, so Qwen bypasses ambient proxies unless explicitly overridden.
    """
    explicit = os.environ.get("FAROS_LLM_TRUST_ENV")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}

    hostname = (urlparse(base_url).hostname or "").lower()
    return provider_name.lower() != "qwen" and not hostname.endswith(".aliyuncs.com")


def should_force_ipv4(provider_name: str, base_url: str, trust_env: bool) -> bool:
    """Avoid unusable IPv6 routes for direct DashScope connections.

    Explicit proxy use is left untouched because the proxy owns DNS and routing.
    """
    explicit = os.environ.get("FAROS_LLM_FORCE_IPV4")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    hostname = (urlparse(base_url).hostname or "").lower()
    return (
        not trust_env
        and provider_name.lower() == "qwen"
        and hostname.endswith(".aliyuncs.com")
    )


def requests_json_object(response_format: Any) -> bool:
    """Recognize the OpenAI-compatible strict JSON response contract."""

    return (
        isinstance(response_format, dict)
        and str(response_format.get("type", "")).strip().lower() == "json_object"
    )


def qwen_thinking_enabled() -> bool:
    """Keep interactive pipelines responsive unless reasoning is explicitly requested."""

    return os.getenv("FAROS_QWEN_ENABLE_THINKING", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


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
        structured_output = bool(kwargs.pop("structured_output", False)) or requests_json_object(
            kwargs.get("response_format")
        )
        if structured_output:
            kwargs.setdefault("response_format", {"type": "json_object"})
            kwargs.setdefault(
                "timeout",
                float(os.getenv("FAROS_STRUCTURED_LLM_TIMEOUT", "90")),
            )
        if self.provider_name.lower() == "qwen":
            extra_body = dict(kwargs.get("extra_body") or {})
            extra_body.setdefault(
                "enable_thinking",
                False if structured_output else qwen_thinking_enabled(),
            )
            kwargs["extra_body"] = extra_body
        api_config = self._get_api_config()
        if "timeout" in kwargs and kwargs["timeout"]:
            api_config["timeout"] = kwargs["timeout"]
            kwargs = {k: v for k, v in kwargs.items() if k != "timeout"}
        model_name = model or self.settings.get_active_model(self.provider_name)
        messages_dict = [{"role": m.role, "content": m.content} for m in messages]
        api_format = getattr(self.config, "api_format", "openai")

        # Provider latency is a duration, so it must not use the adjustable wall clock.
        start_time = time.perf_counter()
        retries = 0
        max_retries = (
            max(0, int(os.getenv("FAROS_STRUCTURED_LLM_MAX_RETRIES", "1")))
            if structured_output
            else self.settings.MAX_RETRIES
        )
        last_error = None

        while retries <= max_retries:
            try:
                if api_format == "openai":
                    response = self._chat_via_openai_sdk(
                        api_config, model_name, messages_dict, temperature, max_tokens, **kwargs
                    )
                else:
                    response = self._chat_via_litellm(
                        api_config, model_name, messages_dict, temperature, max_tokens, **kwargs
                    )

                latency_ms = int((time.perf_counter() - start_time) * 1000)
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
                    model=model_name,
                    finish_reason=choice.finish_reason,
                )
            except Exception as e:
                last_error = e
                retries += 1
                if retries <= max_retries:
                    backoff = self.settings.RETRY_BACKOFF * (2 ** (retries - 1))
                    logger.warning(
                        "Provider request failed (attempt %s/%s): %s. Retrying in %ss...",
                        retries,
                        max_retries,
                        e,
                        backoff,
                    )
                    time.sleep(backoff)

        error_msg = str(last_error)
        logger.error("Provider request failed after %s retries: %s", max_retries, error_msg)
        raise ProviderError(
            f"Provider '{self.provider_name}' request failed: {error_msg}",
            self.provider_name,
            502,
        )

    def _chat_via_openai_sdk(
        self,
        api_config: Dict[str, Any],
        model_name: str,
        messages_dict: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ):
        """Use the openai SDK directly — avoids litellm's httpx issues on Windows."""
        import httpx
        from openai import OpenAI

        trust_env = should_trust_environment_proxy(
            self.provider_name, api_config["api_base"]
        )
        transport = None
        if should_force_ipv4(self.provider_name, api_config["api_base"], trust_env):
            transport = httpx.HTTPTransport(local_address="0.0.0.0")
        with httpx.Client(trust_env=trust_env, transport=transport) as http_client:
            with OpenAI(
                api_key=api_config["api_key"],
                base_url=api_config["api_base"],
                timeout=api_config["timeout"],
                http_client=http_client,
            ) as client:
                # Merge extra arguments while respecting the SDK's parameter names
                extra = {k: v for k, v in kwargs.items() if k not in ("api_key", "api_base", "timeout")}
                return client.chat.completions.create(
                    model=model_name,
                    messages=messages_dict,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **extra,
                )

    def _chat_via_litellm(
        self,
        api_config: Dict[str, Any],
        model_name: str,
        messages_dict: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ):
        """Fallback to litellm for non-openai providers (e.g., anthropic-format)."""
        litellm = self._get_litellm()
        model_string = self._get_model_string(model_name)
        return litellm.completion(
            model=model_string,
            messages=messages_dict,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_config["api_key"],
            api_base=api_config["api_base"],
            timeout=api_config["timeout"],
            **kwargs,
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
