from app.llm.provider_client import (
    qwen_thinking_enabled,
    requests_json_object,
    should_force_ipv4,
    should_trust_environment_proxy,
)


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


def test_direct_qwen_connection_forces_ipv4_by_default(monkeypatch):
    monkeypatch.delenv("FAROS_LLM_FORCE_IPV4", raising=False)
    assert should_force_ipv4(
        "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", False,
    ) is True


def test_proxy_owned_qwen_routing_does_not_force_ipv4(monkeypatch):
    monkeypatch.delenv("FAROS_LLM_FORCE_IPV4", raising=False)
    assert should_force_ipv4(
        "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", True,
    ) is False


def test_force_ipv4_policy_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("FAROS_LLM_FORCE_IPV4", "false")
    assert should_force_ipv4(
        "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", False,
    ) is False


def test_openai_json_object_contract_is_recognized_as_structured_output():
    assert requests_json_object({"type": "json_object"}) is True
    assert requests_json_object({"type": "text"}) is False
    assert requests_json_object(None) is False


def test_qwen_thinking_is_disabled_by_default_and_can_be_enabled(monkeypatch):
    monkeypatch.delenv("FAROS_QWEN_ENABLE_THINKING", raising=False)
    assert qwen_thinking_enabled() is False

    monkeypatch.setenv("FAROS_QWEN_ENABLE_THINKING", "true")
    assert qwen_thinking_enabled() is True
