from cierre.agent.llm import _env


def test_token_pasted_across_two_lines_is_rejoined(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-abc\ndef  \r\n")
    assert _env()["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-abcdef"


def test_no_token_leaves_the_environment_alone(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in _env()
