"""
Application Settings - Centralized Configuration

Provides Pydantic-based settings with environment variable support.
Runtime provider overrides are encrypted and persisted per authenticated user.
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field, PrivateAttr

from app.core.user_context import get_current_user_id, normalize_user_id

logger = logging.getLogger(__name__)


@dataclass
class UserProviderState:
    keys: Dict[str, str] = field(default_factory=dict)
    models: Dict[str, str] = field(default_factory=dict)
    base_urls: Dict[str, str] = field(default_factory=dict)
    active_provider: Optional[str] = None
    active_model: Optional[str] = None


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
        v = os.getenv(self.base_url_env)
        return v.strip() if v else None

    def get_api_key(self) -> Optional[str]:
        """Get API key from environment."""
        v = os.getenv(self.api_key_env)
        return v.strip() if v else None
    
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

    # ---- Sandbox / Code Agent settings ----
    SANDBOX_DEFAULT_BACKEND: str = Field(
        default_factory=lambda: os.getenv("SANDBOX_DEFAULT_BACKEND", "subprocess"),
        description="Default sandbox backend: 'docker' or 'subprocess'"
    )
    SANDBOX_MAX_CONCURRENT: int = Field(
        default_factory=lambda: int(os.getenv("SANDBOX_MAX_CONCURRENT", "4")),
        description="Maximum concurrent sandbox instances"
    )
    SANDBOX_TTL_SEC: int = Field(
        default_factory=lambda: int(os.getenv("SANDBOX_TTL_SEC", "3600")),
        description="Time-to-live for sandbox instances before automatic reclamation"
    )
    SANDBOX_DOCKER_IMAGE: str = Field(
        default_factory=lambda: os.getenv("SANDBOX_DOCKER_IMAGE", "python:3.12-slim"),
        description="Default Docker image for the Docker sandbox backend"
    )
    SANDBOX_MEM_LIMIT: str = Field(
        default_factory=lambda: os.getenv("SANDBOX_MEM_LIMIT", "512m"),
        description="Memory limit for Docker sandbox containers"
    )
    SANDBOX_CPU_QUOTA: int = Field(
        default_factory=lambda: int(os.getenv("SANDBOX_CPU_QUOTA", "50000")),
        description="CPU quota for sandbox containers (100000 = 1 core)"
    )

    # ---- Agent loop settings ----
    AGENT_MAX_ITERATIONS: int = Field(
        default_factory=lambda: int(os.getenv("AGENT_MAX_ITERATIONS", "3")),
        description="Maximum repair/retry iterations for autonomous agent loop"
    )
    AGENT_EXECUTION_TIMEOUT: int = Field(
        default_factory=lambda: int(os.getenv("AGENT_EXECUTION_TIMEOUT", "300")),
        description="Default execution timeout per agent iteration (seconds)"
    )
    AGENT_SANDBOX_TIMEOUT: int = Field(
        default_factory=lambda: int(os.getenv("AGENT_SANDBOX_TIMEOUT", "600")),
        description="Absolute maximum sandbox execution timeout (seconds)"
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
            timeout=300,
            extra_headers={}
        ),
        "bailian": ProviderConfig(
            base_url_env="BAILIAN_BASE_URL",
            api_key_env="BAILIAN_API_KEY",
            default_model="qwen-plus",
            api_format="openai",
            timeout=300,
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
        "novita": ProviderConfig(
            base_url_env="NOVITA_BASE_URL",
            api_key_env="NOVITA_API_KEY",
            default_model="moonshotai/kimi-k3",
            api_format="openai",
            timeout=120,
            extra_headers={}
        ),
    })

    _user_runtime: Dict[str, UserProviderState] = PrivateAttr(default_factory=dict)
    _loaded_users: set[str] = PrivateAttr(default_factory=set)
    _runtime_lock: threading.RLock = PrivateAttr(default_factory=threading.RLock)
    _credential_cipher: Optional[Fernet] = PrivateAttr(default=None)

    def _resolve_user_id(self, user_id: Optional[str] = None) -> str:
        return normalize_user_id(user_id, fallback=get_current_user_id())

    def _state_for(self, user_id: Optional[str] = None) -> UserProviderState:
        resolved = self._resolve_user_id(user_id)
        with self._runtime_lock:
            if resolved not in self._loaded_users:
                self._load_user_runtime(resolved)
            return self._user_runtime[resolved]
    
    def get_provider_config(self, provider_name: Optional[str] = None) -> ProviderConfig:
        """Get configuration for a provider."""
        name = provider_name or self.get_active_provider()
        if name not in self.PROVIDERS:
            raise ValueError(f"Unknown provider: {name}. Available: {list(self.PROVIDERS.keys())}")
        return self.PROVIDERS[name]

    def get_active_provider(self, user_id: Optional[str] = None) -> str:
        """Get the current user's active provider."""
        return self._state_for(user_id).active_provider or self.ACTIVE_PROVIDER_NAME

    def get_active_model(
        self,
        provider_name: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Get the current user's active model name."""
        state = self._state_for(user_id)
        name = provider_name or self.get_active_provider(user_id)
        if name in state.models:
            return state.models[name]
        if state.active_model and not provider_name:
            return state.active_model
        if self.ACTIVE_MODEL_NAME:
            return self.ACTIVE_MODEL_NAME
        config = self.get_provider_config(name)
        return config.default_model

    def get_runtime_api_key(
        self,
        provider_name: str,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """Get a runtime API key scoped to one authenticated user."""
        return self._state_for(user_id).keys.get(provider_name)

    def get_api_key(
        self,
        provider_name: str,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve a user key, with environment credentials limited to one owner."""
        resolved = self._resolve_user_id(user_id)
        runtime_key = self.get_runtime_api_key(provider_name, resolved)
        if runtime_key:
            return runtime_key
        environment_owner = normalize_user_id(
            os.getenv("FAROS_ENV_PROVIDER_OWNER", "local")
        )
        if resolved == environment_owner:
            return self.get_provider_config(provider_name).get_api_key()
        return None

    def get_runtime_base_url(
        self,
        provider_name: str,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """Get runtime-set base URL for a provider."""
        return self._state_for(user_id).base_urls.get(provider_name)

    def set_runtime_key(
        self,
        provider_name: str,
        api_key: str,
        user_id: Optional[str] = None,
    ) -> None:
        """Set an encrypted, user-scoped runtime API key."""
        self.get_provider_config(provider_name)
        cleaned = (api_key or "").strip()
        if not cleaned:
            raise ValueError("api_key cannot be empty")
        resolved = self._resolve_user_id(user_id)
        with self._runtime_lock:
            self._state_for(resolved).keys[provider_name] = cleaned
            self._persist_runtime(resolved)

    def clear_runtime_key(
        self,
        provider_name: str,
        user_id: Optional[str] = None,
    ) -> bool:
        self.get_provider_config(provider_name)
        resolved = self._resolve_user_id(user_id)
        with self._runtime_lock:
            removed = self._state_for(resolved).keys.pop(provider_name, None) is not None
            if removed:
                self._persist_runtime(resolved)
            return removed

    def set_runtime_model(
        self,
        provider_name: str,
        model: str,
        user_id: Optional[str] = None,
    ) -> None:
        """Set the active model for a provider at runtime."""
        self.get_provider_config(provider_name)
        resolved = self._resolve_user_id(user_id)
        with self._runtime_lock:
            self._state_for(resolved).models[provider_name] = model
            self._persist_runtime(resolved)

    def set_runtime_base_url(
        self,
        provider_name: str,
        base_url: str,
        user_id: Optional[str] = None,
    ) -> None:
        """Set a user-scoped provider base URL."""
        self.get_provider_config(provider_name)
        cleaned = (base_url or "").strip()
        if not cleaned:
            raise ValueError("base_url cannot be empty")
        resolved = self._resolve_user_id(user_id)
        with self._runtime_lock:
            self._state_for(resolved).base_urls[provider_name] = cleaned
            self._persist_runtime(resolved)

    def set_active_provider(
        self,
        provider_name: str,
        user_id: Optional[str] = None,
    ) -> None:
        """Set the current user's active provider."""
        if provider_name not in self.PROVIDERS:
            raise ValueError(f"Unknown provider: {provider_name}")
        resolved = self._resolve_user_id(user_id)
        with self._runtime_lock:
            self._state_for(resolved).active_provider = provider_name
            self._persist_runtime(resolved)

    def set_active_model_global(
        self,
        model: str,
        user_id: Optional[str] = None,
    ) -> None:
        """Set the current user's global active model override."""
        resolved = self._resolve_user_id(user_id)
        with self._runtime_lock:
            self._state_for(resolved).active_model = model
            self._persist_runtime(resolved)

    def _get_config_path(self) -> str:
        """Path to the legacy global runtime config JSON."""
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        data_dir = os.path.join(base, "data")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "provider_config.json")

    def _provider_config_root(self) -> Path:
        configured = os.getenv("FAROS_PROVIDER_CONFIG_DIR", "").strip()
        if configured:
            root = Path(configured).expanduser().resolve()
        else:
            root = Path(self._get_config_path()).parent / "provider_configs"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        return root

    def _user_config_path(self, user_id: str) -> Path:
        resolved = self._resolve_user_id(user_id)
        digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
        return self._provider_config_root() / f"{resolved}-{digest}.json"

    def _get_credential_cipher(self) -> Fernet:
        if self._credential_cipher is not None:
            return self._credential_cipher

        configured = os.getenv("FAROS_CREDENTIAL_KEY", "").strip()
        if configured:
            raw_key = configured.encode("ascii")
        else:
            key_path = self._provider_config_root() / ".credential-key"
            try:
                raw_key = key_path.read_bytes().strip()
            except FileNotFoundError:
                raw_key = Fernet.generate_key()
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw_key + b"\n")
            try:
                key_path.chmod(0o600)
            except OSError:
                pass

        try:
            self._credential_cipher = Fernet(raw_key)
        except (ValueError, TypeError) as exc:
            raise ValueError("FAROS_CREDENTIAL_KEY is not a valid Fernet key") from exc
        return self._credential_cipher

    def _encrypt_key(self, api_key: str) -> str:
        return self._get_credential_cipher().encrypt(api_key.encode("utf-8")).decode("ascii")

    def _decrypt_key(self, encrypted: str, *, user_id: str, provider_name: str) -> str:
        try:
            return self._get_credential_cipher().decrypt(encrypted.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise ValueError(
                f"Cannot decrypt credential for user '{user_id}', provider '{provider_name}'"
            ) from exc

    def _persist_runtime(self, user_id: Optional[str] = None) -> None:
        resolved = self._resolve_user_id(user_id)
        state = self._state_for(resolved)
        path = self._user_config_path(resolved)
        data = {
            "schemaVersion": 2,
            "userId": resolved,
            "activeProvider": state.active_provider,
            "activeModel": state.active_model,
            "keys": {
                provider: self._encrypt_key(value)
                for provider, value in state.keys.items()
            },
            "models": dict(state.models),
            "baseUrls": dict(state.base_urls),
        }

        temp_path: Optional[str] = None
        try:
            fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            path.chmod(0o600)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def _load_user_runtime(self, user_id: str) -> None:
        resolved = self._resolve_user_id(user_id)
        path = self._user_config_path(resolved)
        state = UserProviderState()
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("userId") not in (None, resolved):
                raise ValueError(f"Provider config owner mismatch for '{resolved}'")
            state.active_provider = data.get("activeProvider")
            state.active_model = data.get("activeModel")
            state.models = {
                str(provider): str(model)
                for provider, model in data.get("models", {}).items()
                if provider in self.PROVIDERS and str(model).strip()
            }
            state.base_urls = {
                str(provider): str(base_url)
                for provider, base_url in data.get("baseUrls", {}).items()
                if provider in self.PROVIDERS and str(base_url).strip()
            }
            state.keys = {
                str(provider): self._decrypt_key(
                    str(encrypted),
                    user_id=resolved,
                    provider_name=str(provider),
                )
                for provider, encrypted in data.get("keys", {}).items()
                if provider in self.PROVIDERS and encrypted
            }
        self._user_runtime[resolved] = state
        self._loaded_users.add(resolved)

    def _migrate_legacy_runtime(self) -> None:
        path = Path(self._get_config_path())
        if not path.exists():
            return
        owner = normalize_user_id(
            os.getenv("FAROS_LEGACY_PROVIDER_OWNER")
            or os.getenv("FAROS_DEFAULT_USER")
            or "local"
        )
        target = self._user_config_path(owner)
        if target.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            state = UserProviderState(
                keys={
                    str(provider): str(key)
                    for provider, key in data.get("keys", {}).items()
                    if provider in self.PROVIDERS and str(key).strip()
                },
                models={
                    str(provider): str(model)
                    for provider, model in data.get("models", {}).items()
                    if provider in self.PROVIDERS and str(model).strip()
                },
                base_urls={
                    str(provider): str(base_url)
                    for provider, base_url in data.get("baseUrls", {}).items()
                    if provider in self.PROVIDERS and str(base_url).strip()
                },
                active_provider=data.get("activeProvider"),
                active_model=data.get("activeModel"),
            )
            self._user_runtime[owner] = state
            self._loaded_users.add(owner)
            self._persist_runtime(owner)
            logger.info("Migrated legacy provider configuration to user '%s'", owner)
        except Exception as exc:
            logger.warning("Failed to migrate legacy provider configuration: %s", exc)

    def _load_runtime(self) -> None:
        """Initialize legacy migration and the current user's provider state."""
        with self._runtime_lock:
            self._migrate_legacy_runtime()
            self._state_for(get_current_user_id())
    
    def get_provider_info(
        self,
        provider_name: Optional[str] = None,
        mask_key: bool = True,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get provider info for API responses (keys masked)."""
        resolved = self._resolve_user_id(user_id)
        name = provider_name or self.get_active_provider(resolved)
        config = self.get_provider_config(name)
        api_key = self.get_api_key(name, resolved)
        
        return {
            "providerName": name,
            "model": self.get_active_model(name, resolved),
            "baseUrl": self.get_runtime_base_url(name, resolved) or config.get_base_url() or self._get_default_base_url(name),
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
            "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "bigmodel": "https://open.bigmodel.cn/api/paas/v4",
            "minimax": "https://api.minimaxi.com/anthropic",
            "novita": "https://api.novita.ai/openai/v1",
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
    _settings._load_runtime()
    return _settings
