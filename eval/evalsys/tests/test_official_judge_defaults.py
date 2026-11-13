"""Default judge routes must not send credentials to an implicit relay."""

import pytest

from evalsys.scard.judge import AnthropicSCardJudge, OpenAIResponsesSCardJudge
from evalsys.scard.rubric_judge import ClaudeCodeRubricJudge, OpenAIResponsesRubricJudge


@pytest.mark.parametrize("judge_class,endpoint,key_name", [
    (OpenAIResponsesRubricJudge, "https://api.openai.com/v1", "OPENAI_API_KEY"),
    (OpenAIResponsesSCardJudge, "https://api.openai.com/v1", "OPENAI_API_KEY"),
    (ClaudeCodeRubricJudge, "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
    (AnthropicSCardJudge, "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
])
def test_default_endpoint_and_explicit_override(monkeypatch, judge_class, endpoint, key_name):
    for name in ("GAMEBENCH_VLM_BASE_URL", "GAMEBENCH_VLM_KEY_ENV", "OPENAI_BASE_URL",
                 "ANTHROPIC_BASE_URL", "CLAUDE_BASE_URL", "AUTO_CODE_BASE_URL",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY",
                 "AUTO_CODE_API_KEY", "MICU_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(key_name, "official-test-only")
    make = getattr(judge_class, "from_env", judge_class)
    judge = make()
    assert judge.base_url == endpoint
    if hasattr(judge, "key_env"):
        assert judge.key_env == key_name
    else:
        assert judge.api_key == "official-test-only"

    monkeypatch.setenv("GAMEBENCH_VLM_BASE_URL", "https://provider.example.invalid/v1")
    monkeypatch.setenv("GAMEBENCH_VLM_KEY_ENV", "TEST_JUDGE_KEY")
    monkeypatch.setenv("TEST_JUDGE_KEY", "custom-test-only")
    judge = make()
    assert judge.base_url == "https://provider.example.invalid/v1"
    if hasattr(judge, "key_env"):
        assert judge.key_env == "TEST_JUDGE_KEY"
    else:
        assert judge.api_key == "custom-test-only"


@pytest.mark.parametrize("backend,endpoint,key_name", [
    ("codex", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    ("claude", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
])
def test_agent_backend_infers_the_matching_official_provider(backend, endpoint, key_name):
    from evalsys.taskgen.matrix import AgentConfig, _agent_provider

    route = _agent_provider(AgentConfig(backend=backend))
    assert route["base_url"] == endpoint
    assert route["key_env"] == key_name

    route = _agent_provider(AgentConfig(backend=backend,
                                       base_url="https://provider.example.invalid/v1",
                                       key_env="TEST_AGENT_KEY"))
    assert route["base_url"] == "https://provider.example.invalid/v1"
    assert route["key_env"] == "TEST_AGENT_KEY"
