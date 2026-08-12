from app.llm.provider_client import should_trust_environment_proxy


def test_qwen_bypasses_ambient_proxy_by_default(monkeypatch):
    monkeypatch.delenv("FAROS_LLM_TRUST_ENV", raising=False)
    assert should_trust_environment_proxy(
        "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ) is False


def test_proxy_policy_can_be_explicitly_overridden(monkeypatch):
    monkeypatch.setenv("FAROS_LLM_TRUST_ENV", "true")
    assert should_trust_environment_proxy(
        "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ) is True


def test_non_qwen_provider_keeps_environment_proxy(monkeypatch):
    monkeypatch.delenv("FAROS_LLM_TRUST_ENV", raising=False)
    assert should_trust_environment_proxy(
        "openai", "https://api.openai.com/v1"
    ) is True
