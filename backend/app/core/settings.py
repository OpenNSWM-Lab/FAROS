"""
Application Settings - Centralized Configuration

Provides Pydantic-based settings with environment variable support.
Runtime overrides persisted in data/provider_config.json.
"""

import os
import json
import logging
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ProviderConfig(BaseModel):
    """Configuration for a single LLM provider."""
    base_url_env: str = Field(..., description="Environment variable name for base URL")
    api_key_env: str = Field(..., description="Environment variable name for API key")
    default_model: str = Field(..., description="Default model for this provider")
    api_format: str = Field(default="openai", description="Provider API format used by the runtime client")
    timeout: int = Field(default=60, description="Request timeout in seconds")
    extra_headers: Dict[str, str] = Field(default_factory=dict)
    
    def get_base_url(self) -> Optional[str]:
        """Get base URL from environment."""
        return os.getenv(self.base_url_env)
    
    def get_api_key(self) -> Optional[str]:
        """Get API key from environment."""
        return os.getenv(self.api_key_env)
    
    def is_configured(self) -> bool:
        """Check if provider has required configuration."""
        return bool(self.get_api_key())


class Settings(BaseModel):
    """Application settings loaded from environment variables."""
    
    # Data storage
    DATA_DIR: str = Field(
        default_factory=lambda: os.getenv("DATA_DIR", "backend/data"),
        description="Directory for persistent data storage"
    )
    
    # API server
    API_HOST: str = Field(
        default_factory=lambda: os.getenv("API_HOST", "127.0.0.1"),
        description="API server host"
    )
    API_PORT: int = Field(
        default_factory=lambda: int(os.getenv("API_PORT", "8005")),
        description="API server port"
    )
    
    # Active provider configuration
    ACTIVE_PROVIDER_NAME: str = Field(
        default_factory=lambda: os.getenv("ACTIVE_PROVIDER_NAME", "moonshot"),
        description="Currently active LLM provider"
    )
    ACTIVE_MODEL_NAME: Optional[str] = Field(
        default_factory=lambda: os.getenv("ACTIVE_MODEL_NAME"),
        description="Override model name (uses provider default if not set)"
    )
    
    # Request settings
    REQUEST_TIMEOUT: int = Field(
        default_factory=lambda: int(os.getenv("REQUEST_TIMEOUT", "60")),
        description="Default request timeout"
    )
    PLAN_GENERATION_TIMEOUT: int = Field(
        default_factory=lambda: int(os.getenv("PLAN_GENERATION_TIMEOUT", "180")),
        description="Request timeout for long plan-generation LLM calls"
    )
    PAPER_GENERATION_TIMEOUT: int = Field(
        default_factory=lambda: int(os.getenv("PAPER_GENERATION_TIMEOUT", "300")),
        description="Request timeout for long paper-generation LLM calls"
    )
    MAX_RETRIES: int = Field(
        default_factory=lambda: int(os.getenv("MAX_RETRIES", "3")),
        description="Maximum retry attempts"
    )
    RETRY_BACKOFF: float = Field(
        default_factory=lambda: float(os.getenv("RETRY_BACKOFF", "1.0")),
        description="Retry backoff multiplier"
    )
    
    # Provider configurations (static, keys from env)
    PROVIDERS: Dict[str, ProviderConfig] = Field(default_factory=lambda: {
        "moonshot": ProviderConfig(
            base_url_env="MOONSHOT_BASE_URL",
            api_key_env="MOONSHOT_API_KEY",
            default_model="moonshot-v1-8k",
            api_format="openai",
            timeout=60,
            extra_headers={}
        ),
        "kimi": ProviderConfig(
            base_url_env="KIMI_BASE_URL",
            api_key_env="KIMI_API_KEY",
            default_model="kimi",
            api_format="openai",
            timeout=60,
            extra_headers={}
        ),
        "openai": ProviderConfig(
            base_url_env="OPENAI_BASE_URL",
            api_key_env="OPENAI_API_KEY",
            default_model="gpt-4o-2024-08-06",
            api_format="openai",
            timeout=120,
            extra_headers={}
        ),
        "anthropic": ProviderConfig(
            base_url_env="ANTHROPIC_BASE_URL",
            api_key_env="ANTHROPIC_API_KEY",
            default_model="claude-3-5-sonnet-20241022",
            api_format="anthropic",
            timeout=120,
            extra_headers={}
        ),
        "claude": ProviderConfig(
            base_url_env="CLAUDE_BASE_URL",
            api_key_env="CLAUDE_API_KEY",
            default_model="claude-3-5-sonnet-20241022",
            api_format="anthropic",
            timeout=120,
            extra_headers={}
        ),
        "deepseek": ProviderConfig(
            base_url_env="DEEPSEEK_BASE_URL",
            api_key_env="DEEPSEEK_API_KEY",
            default_model="deepseek-chat",
            api_format="openai",
            timeout=60,
            extra_headers={}
        ),
        "zhipu": ProviderConfig(
            base_url_env="ZHIPU_BASE_URL",
            api_key_env="ZHIPU_API_KEY",
            default_model="glm-4",
            api_format="openai",
            timeout=60,
            extra_headers={}
        ),
        "qwen": ProviderConfig(
            base_url_env="QWEN_BASE_URL",
            api_key_env="QWEN_API_KEY",
            default_model="qwen-max",
            api_format="openai",
            timeout=60,
            extra_headers={}
        ),
        "bigmodel": ProviderConfig(
            base_url_env="BIGMODEL_BASE_URL",
            api_key_env="BIGMODEL_API_KEY",
            default_model="glm-4.5-air",
            api_format="openai",
            timeout=90,
            extra_headers={}
        ),
        "minimax": ProviderConfig(
            base_url_env="MINIMAX_BASE_URL",
            api_key_env="MINIMAX_API_KEY",
            default_model="MiniMax-M2.5",
            api_format="anthropic",
            timeout=120,
            extra_headers={}
        ),
    })

    # Runtime overrides (set via API, persisted to JSON)
    _runtime_keys: Dict[str, str] = {}
    _runtime_models: Dict[str, str] = {}
    _runtime_base_urls: Dict[str, str] = {}
    _runtime_active_provider: Optional[str] = None
    _runtime_active_model: Optional[str] = None
    
    def get_provider_config(self, provider_name: Optional[str] = None) -> ProviderConfig:
        """Get configuration for a provider."""
        name = provider_name or self.get_active_provider()
        if name not in self.PROVIDERS:
            raise ValueError(f"Unknown provider: {name}. Available: {list(self.PROVIDERS.keys())}")
        return self.PROVIDERS[name]

    def get_active_provider(self) -> str:
        """Get the current active provider name (runtime override > env)."""
        return self._runtime_active_provider or self.ACTIVE_PROVIDER_NAME

    def get_active_model(self, provider_name: Optional[str] = None) -> str:
        """Get the active model name."""
        name = provider_name or self.get_active_provider()
        # Runtime override per-provider
        if name in self._runtime_models:
            return self._runtime_models[name]
        if self._runtime_active_model and not provider_name:
            return self._runtime_active_model
        if self.ACTIVE_MODEL_NAME:
            return self.ACTIVE_MODEL_NAME
        config = self.get_provider_config(name)
        return config.default_model

    def get_runtime_api_key(self, provider_name: str) -> Optional[str]:
        """Get runtime-set API key for a provider."""
        return self._runtime_keys.get(provider_name)

    def get_runtime_base_url(self, provider_name: str) -> Optional[str]:
        """Get runtime-set base URL for a provider."""
        return self._runtime_base_urls.get(provider_name)

    def set_runtime_key(self, provider_name: str, api_key: str):
        """Set an API key at runtime (also sets env var for litellm)."""
        self._runtime_keys[provider_name] = api_key
        config = self.get_provider_config(provider_name)
        os.environ[config.api_key_env] = api_key
        self._persist_runtime()

    def set_runtime_model(self, provider_name: str, model: str):
        """Set the active model for a provider at runtime."""
        self._runtime_models[provider_name] = model
        self._persist_runtime()

    def set_runtime_base_url(self, provider_name: str, base_url: str):
        """Set base URL at runtime (also sets env var for litellm)."""
        config = self.get_provider_config(provider_name)
        cleaned = (base_url or "").strip()
        if not cleaned:
            raise ValueError("base_url cannot be empty")
        self._runtime_base_urls[provider_name] = cleaned
        os.environ[config.base_url_env] = cleaned
        self._persist_runtime()

    def set_active_provider(self, provider_name: str):
        """Set the active provider at runtime."""
        if provider_name not in self.PROVIDERS:
            raise ValueError(f"Unknown provider: {provider_name}")
        self._runtime_active_provider = provider_name
        self.ACTIVE_PROVIDER_NAME = provider_name
        os.environ["ACTIVE_PROVIDER_NAME"] = provider_name
        self._persist_runtime()

    def set_active_model_global(self, model: str):
        """Set the global active model at runtime."""
        self._runtime_active_model = model
        self.ACTIVE_MODEL_NAME = model
        os.environ["ACTIVE_MODEL_NAME"] = model
        self._persist_runtime()

    def _get_config_path(self) -> str:
        """Path to runtime config JSON."""
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        data_dir = os.path.join(base, "data")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "provider_config.json")

    def _persist_runtime(self):
        """Persist runtime overrides to JSON."""
        try:
            path = self._get_config_path()
            data = {
                "activeProvider": self._runtime_active_provider,
                "activeModel": self._runtime_active_model,
                "keys": {k: v for k, v in self._runtime_keys.items()},
                "models": {k: v for k, v in self._runtime_models.items()},
                "baseUrls": {k: v for k, v in self._runtime_base_urls.items()},
            }
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to persist runtime config: {e}")

    def _load_runtime(self):
        """Load runtime overrides from JSON."""
        try:
            path = self._get_config_path()
            if not os.path.exists(path):
                return
            with open(path, "r") as f:
                data = json.load(f)
            if data.get("activeProvider"):
                self._runtime_active_provider = data["activeProvider"]
                self.ACTIVE_PROVIDER_NAME = data["activeProvider"]
            if data.get("activeModel"):
                self._runtime_active_model = data["activeModel"]
                self.ACTIVE_MODEL_NAME = data["activeModel"]
            for prov, key in data.get("keys", {}).items():
                self._runtime_keys[prov] = key
                if prov in self.PROVIDERS:
                    os.environ[self.PROVIDERS[prov].api_key_env] = key
            for prov, model in data.get("models", {}).items():
                self._runtime_models[prov] = model
            for prov, base_url in data.get("baseUrls", {}).items():
                self._runtime_base_urls[prov] = base_url
                if prov in self.PROVIDERS:
                    os.environ[self.PROVIDERS[prov].base_url_env] = base_url
        except Exception as e:
            logger.warning(f"Failed to load runtime config: {e}")
    
    def get_provider_info(self, provider_name: Optional[str] = None, mask_key: bool = True) -> Dict[str, Any]:
        """Get provider info for API responses (keys masked)."""
        name = provider_name or self.get_active_provider()
        config = self.get_provider_config(name)
        api_key = self.get_runtime_api_key(name) or config.get_api_key()
        
        return {
            "providerName": name,
            "model": self.get_active_model(name),
            "baseUrl": self.get_runtime_base_url(name) or config.get_base_url() or self._get_default_base_url(name),
            "configured": bool(api_key),
            "apiKeySet": bool(api_key),
            "apiKeyMasked": self._mask_key(api_key) if api_key and mask_key else None,
            "timeout": config.timeout,
        }
    
    def _mask_key(self, key: str) -> str:
        """Mask API key for display."""
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"
    
    def _get_default_base_url(self, provider_name: str) -> str:
        """Get default base URL for known providers."""
        defaults = {
            "moonshot": "https://api.moonshot.cn/v1",
            "kimi": "https://api.moonshot.cn/v1",
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com/v1",
            "claude": "https://api.anthropic.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "zhipu": "https://open.bigmodel.cn/api/paas/v4",
            "qwen": "https://dashscope.aliyuncs.com/api/v1",
            "bigmodel": "https://open.bigmodel.cn/api/paas/v4",
            "minimax": "https://api.minimaxi.com/anthropic",
        }
        return defaults.get(provider_name, "")


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings._load_runtime()
    return _settings


def reload_settings() -> Settings:
    """Force reload settings from environment."""
    global _settings
    _settings = Settings()
    return _settings
