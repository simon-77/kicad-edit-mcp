"""Tests for server.py config loading and tool registration logic."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from server import _KNOWN_TOOLS, _load_config


# ---------------------------------------------------------------------------
# _load_config
# ---------------------------------------------------------------------------


def test_default_config_enables_all_tools(tmp_path: Path) -> None:
    """No config file -> all 7 tools enabled."""
    enabled = _load_config(None)
    assert set(enabled.keys()) == _KNOWN_TOOLS
    assert all(enabled.values()), "All tools should be enabled by default"


def test_explicit_config_all_true(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    lines = ["tools:\n"]
    for name in _KNOWN_TOOLS:
        lines.append(f"  {name}: true\n")
    cfg.write_text("".join(lines))
    enabled = _load_config(cfg)
    assert all(enabled.values())


def test_restrictive_config_disables_tools(tmp_path: Path) -> None:
    """Disabling two tools via config results in fewer enabled tools."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "tools:\n"
        "  list_components: false\n"
        "  update_component: false\n"
    )
    enabled = _load_config(cfg)
    assert enabled["list_components"] is False
    assert enabled["update_component"] is False
    # Other tools still enabled
    assert enabled["get_component"] is True
    assert enabled["rename_net"] is True


def test_restrictive_config_enabled_count(tmp_path: Path) -> None:
    """Disabling 3 tools leaves 4 enabled."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "tools:\n"
        "  list_components: false\n"
        "  update_component: false\n"
        "  rename_net: false\n"
    )
    enabled = _load_config(cfg)
    on_count = sum(1 for v in enabled.values() if v)
    assert on_count == len(_KNOWN_TOOLS) - 3


def test_unknown_tool_in_config_warns(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Unknown tool name in config should print a warning to stderr."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("tools:\n  ghost_tool: true\n")
    _load_config(cfg)
    captured = capsys.readouterr()
    assert "ghost_tool" in captured.err
    assert "WARNING" in captured.err or "unknown" in captured.err.lower()


def test_unknown_tool_does_not_affect_known(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Unknown tool in config must not alter known tool states."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("tools:\n  ghost_tool: false\n")
    enabled = _load_config(cfg)
    assert set(enabled.keys()) == _KNOWN_TOOLS
    assert all(enabled.values())


def test_empty_config_file_enables_all(tmp_path: Path) -> None:
    """Empty YAML file -> all tools enabled (safe default)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("")
    enabled = _load_config(cfg)
    assert all(enabled.values())


def test_nonexistent_explicit_config_raises(tmp_path: Path) -> None:
    """Passing a non-existent explicit config path should raise FileNotFoundError."""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises((FileNotFoundError, OSError)):
        _load_config(missing)


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
