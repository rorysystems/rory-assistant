from app.agent import compact_limit, subagent_model, tool_summary


def test_subagent_ladder():
    assert subagent_model("claude-opus-5-5") == "sonnet"
    assert subagent_model("sonnet") == "haiku"
    assert subagent_model(None) == "sonnet"
    assert subagent_model("opus", "haiku") == "haiku"


def test_compact_limit():
    assert compact_limit({}) == 60000
    assert compact_limit({"auto_compact_at": 0}) == 0
    assert compact_limit({"auto_compact_at": "x"}) == 60000


def test_tool_summary():
    assert tool_summary("Bash", {"command": "ls   -la"}) == "ls -la"
    assert tool_summary("Agent", {"description": "ler", "subagent_type": "explorer"}) == "ler (explorer)"
