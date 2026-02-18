"""Tests for server.py env-var config and tool registration logic."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import server as _server_module

_KNOWN_TOOLS = _server_module._KNOWN_TOOLS


def _reload_server(monkeypatch: pytest.MonkeyPatch, env_value: str | None) -> object:
    """Reload server module with DISABLED_TOOLS set (or unset) in the environment.

    Returns the reloaded module so callers can inspect _enabled.
    """
    if env_value is None:
        monkeypatch.delenv("DISABLED_TOOLS", raising=False)
    else:
        monkeypatch.setenv("DISABLED_TOOLS", env_value)
    # Remove cached module so importlib.reload picks up new env
    if "server" in sys.modules:
        del sys.modules["server"]
    return importlib.import_module("server")


# ---------------------------------------------------------------------------
# _KNOWN_TOOLS sanity check
# ---------------------------------------------------------------------------


def test_known_tools_set_has_seven_tools() -> None:
    """Sanity: confirm 7 known tools are declared."""
    assert len(_KNOWN_TOOLS) == 7
    expected = {
        "list_components",
        "get_component",
        "update_component",
        "update_schematic_info",
        "rename_net",
        "list_net_classes",
        "update_net_class",
    }
    assert _KNOWN_TOOLS == expected


# ---------------------------------------------------------------------------
# Env-var config tests
# ---------------------------------------------------------------------------


def test_default_no_env_enables_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DISABLED_TOOLS env var -> all 7 tools enabled."""
    mod = _reload_server(monkeypatch, None)
    assert set(mod._enabled.keys()) == _KNOWN_TOOLS
    assert all(mod._enabled.values()), "All tools should be enabled by default"


def test_empty_env_enables_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """DISABLED_TOOLS="" -> all tools enabled."""
    mod = _reload_server(monkeypatch, "")
    assert all(mod._enabled.values())


def test_disable_single_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """DISABLED_TOOLS=rename_net -> 6 enabled, rename_net disabled."""
    mod = _reload_server(monkeypatch, "rename_net")
    assert mod._enabled["rename_net"] is False
    enabled_count = sum(1 for v in mod._enabled.values() if v)
    assert enabled_count == len(_KNOWN_TOOLS) - 1


def test_disable_multiple_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comma-separated list disables correct tools."""
    mod = _reload_server(monkeypatch, "rename_net,update_net_class")
    assert mod._enabled["rename_net"] is False
    assert mod._enabled["update_net_class"] is False
    enabled_count = sum(1 for v in mod._enabled.values() if v)
    assert enabled_count == len(_KNOWN_TOOLS) - 2


def test_unknown_tool_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Unknown tool name in DISABLED_TOOLS -> warning on stderr."""
    _reload_server(monkeypatch, "ghost_tool")
    captured = capsys.readouterr()
    assert "ghost_tool" in captured.err
    assert "WARNING" in captured.err or "unknown" in captured.err.lower()


def test_unknown_tool_does_not_affect_known(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown tool in DISABLED_TOOLS must not alter known tool states."""
    mod = _reload_server(monkeypatch, "ghost_tool")
    assert set(mod._enabled.keys()) == _KNOWN_TOOLS
    assert all(mod._enabled.values())


def test_whitespace_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace around tool names is stripped correctly."""
    mod = _reload_server(monkeypatch, " rename_net , update_net_class ")
    assert mod._enabled["rename_net"] is False
    assert mod._enabled["update_net_class"] is False
    enabled_count = sum(1 for v in mod._enabled.values() if v)
    assert enabled_count == len(_KNOWN_TOOLS) - 2
