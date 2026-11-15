"""Avisa cedo se uma versão nova do claude-agent-sdk quebrar o que o app usa por baixo dos panos."""

import inspect

import anyio
import claude_agent_sdk
from claude_agent_sdk import ClaudeSDKClient


def test_public_methods_used_by_app_exist():
    for name in ("connect", "query", "interrupt", "set_model", "set_permission_mode", "receive_messages",
                 "get_mcp_status", "toggle_mcp_server", "reconnect_mcp_server", "get_server_info", "disconnect"):
        assert hasattr(ClaudeSDKClient, name), name


def test_private_mcp_auth_hook_still_exists():
    source = inspect.getsource(ClaudeSDKClient)
    assert "_query" in source
    from claude_agent_sdk._internal import query as q

    assert hasattr(q.Query, "_send_control_request")


def test_open_process_patch_applies():
    from app import agent  # noqa: F401 - importar aplica o patch

    import sys

    if sys.platform == "win32":
        assert getattr(anyio.open_process, "_rory_patched", False)
    assert claude_agent_sdk.ClaudeAgentOptions
