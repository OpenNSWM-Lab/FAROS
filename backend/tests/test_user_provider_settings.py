import json
from concurrent.futures import ThreadPoolExecutor

from cryptography.fernet import Fernet

from app.core.settings import Settings
from app.core.user_context import (
    call_with_current_context,
    get_current_user_id,
    sanitized_subprocess_env,
    use_user,
)
from app.llm import provider_client as provider_client_module


def _isolated_settings(monkeypatch, tmp_path) -> Settings:
    monkeypatch.setenv("FAROS_PROVIDER_CONFIG_DIR", str(tmp_path / "providers"))
    monkeypatch.setenv("FAROS_CREDENTIAL_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("FAROS_ENV_PROVIDER_OWNER", "faros-team")
    for name in ("QWEN_API_KEY", "OPENAI_API_KEY", "MOONSHOT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return Settings()


def test_provider_credentials_are_encrypted_and_isolated_by_user(monkeypatch, tmp_path):
    settings = _isolated_settings(monkeypatch, tmp_path)

    with use_user("faros-team"):
        settings.set_runtime_key("qwen", "sk-team-only-secret")
        settings.set_active_provider("qwen")
        settings.set_runtime_model("qwen", "qwen-max")

    with use_user("faros-judge"):
        settings.set_runtime_key("qwen", "sk-judge-only-secret")
        settings.set_active_provider("openai")
        settings.set_runtime_model("openai", "gpt-4o-mini")

    assert settings.get_api_key("qwen", "faros-team") == "sk-team-only-secret"
    assert settings.get_api_key("qwen", "faros-judge") == "sk-judge-only-secret"
    assert settings.get_active_provider("faros-team") == "qwen"
    assert settings.get_active_provider("faros-judge") == "openai"

    files = list((tmp_path / "providers").glob("*.json"))
    assert len(files) == 2
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "sk-team-only-secret" not in serialized
    assert "sk-judge-only-secret" not in serialized
    for path in files:
        assert path.stat().st_mode & 0o777 == 0o600

    reloaded = Settings()
    assert reloaded.get_api_key("qwen", "faros-team") == "sk-team-only-secret"
    assert reloaded.get_api_key("qwen", "faros-judge") == "sk-judge-only-secret"
    assert reloaded.get_active_model("openai", "faros-judge") == "gpt-4o-mini"


def test_environment_key_is_visible_only_to_declared_owner(monkeypatch, tmp_path):
    settings = _isolated_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("QWEN_API_KEY", "sk-environment-team-key")

    assert settings.get_api_key("qwen", "faros-team") == "sk-environment-team-key"
    assert settings.get_api_key("qwen", "faros-judge") is None


def test_legacy_global_config_migrates_to_team_account(monkeypatch, tmp_path):
    settings = _isolated_settings(monkeypatch, tmp_path)
    legacy_path = tmp_path / "provider_config.json"
    legacy_path.write_text(
        json.dumps({
            "activeProvider": "qwen",
            "keys": {"qwen": "sk-legacy-team-key"},
            "models": {"qwen": "qwen-plus"},
            "baseUrls": {},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAROS_LEGACY_PROVIDER_OWNER", "faros-team")
    monkeypatch.setattr(Settings, "_get_config_path", lambda self: str(legacy_path))

    settings._load_runtime()

    assert settings.get_api_key("qwen", "faros-team") == "sk-legacy-team-key"
    assert settings.get_api_key("qwen", "faros-judge") is None
    encrypted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "providers").glob("*.json")
    )
    assert "sk-legacy-team-key" not in encrypted


def test_provider_client_captures_its_owner(monkeypatch, tmp_path):
    settings = _isolated_settings(monkeypatch, tmp_path)
    settings.set_runtime_key("qwen", "sk-team", user_id="faros-team")
    settings.set_runtime_key("qwen", "sk-judge", user_id="faros-judge")
    monkeypatch.setattr(provider_client_module, "get_settings", lambda: settings)

    with use_user("faros-team"):
        team_client = provider_client_module.get_provider_client("qwen")
    with use_user("faros-judge"):
        judge_client = provider_client_module.get_provider_client("qwen")

    assert team_client is not judge_client
    assert team_client.user_id == "faros-team"
    assert judge_client.user_id == "faros-judge"
    assert team_client._get_api_config()["api_key"] == "sk-team"
    assert judge_client._get_api_config()["api_key"] == "sk-judge"


def test_context_propagates_to_worker_and_subprocess_env_is_sanitized(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "must-not-leak")
    monkeypatch.setenv("FAROS_CREDENTIAL_KEY", "must-not-leak")
    monkeypatch.setenv("FAROS_NON_SECRET_SETTING", "kept")

    with use_user("faros-judge"):
        with ThreadPoolExecutor(max_workers=1) as executor:
            resolved = executor.submit(
                call_with_current_context(get_current_user_id)
            ).result()

    assert resolved == "faros-judge"
    child_env = sanitized_subprocess_env()
    assert "QWEN_API_KEY" not in child_env
    assert "FAROS_CREDENTIAL_KEY" not in child_env
    assert child_env["FAROS_NON_SECRET_SETTING"] == "kept"
