"""FastMCP server for kicad-edit-mcp.

Loads tool config from config.yaml (optional) and registers only enabled tools.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

import yaml
from fastmcp import FastMCP

import kicad_helpers

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_KNOWN_TOOLS = {
    "list_components",
    "get_component",
    "update_component",
    "update_schematic_info",
    "rename_net",
    "list_net_classes",
    "update_net_class",
}


def _load_config(config_path: Optional[Path]) -> dict[str, bool]:
    """Return mapping of tool_name -> enabled (True/False).

    All tools default to enabled (opt-out model).
    """
    enabled: dict[str, bool] = {name: True for name in _KNOWN_TOOLS}

    if config_path is None:
        # Default: look next to server.py
        default = Path(__file__).parent / "config.yaml"
        if default.exists():
            config_path = default

    if config_path is None:
        return enabled

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    tools_section: dict = raw.get("tools", {})

    for name, value in tools_section.items():
        if name not in _KNOWN_TOOLS:
            print(
                f"kicad-edit-mcp: WARNING unknown tool in config: '{name}'",
                file=sys.stderr,
            )
        else:
            enabled[name] = bool(value)

    return enabled


def _parse_args() -> Optional[Path]:
    parser = argparse.ArgumentParser(
        description="kicad-edit-mcp FastMCP server",
        add_help=True,
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to config.yaml (default: config.yaml next to server.py)",
        default=None,
    )
    args, _ = parser.parse_known_args()
    return Path(args.config) if args.config else None


# Parse args and load config at module level so tool registration happens once.
_config_path = _parse_args()
_enabled = _load_config(_config_path)

# ---------------------------------------------------------------------------
# Startup logging
# ---------------------------------------------------------------------------

_on = [n for n in _KNOWN_TOOLS if _enabled[n]]
_off = [n for n in _KNOWN_TOOLS if not _enabled[n]]
print(f"kicad-edit-mcp: {len(_on)}/{len(_KNOWN_TOOLS)} tools enabled", file=sys.stderr)
if _on:
    print(f"kicad-edit-mcp: enabled: {', '.join(sorted(_on))}", file=sys.stderr)
if _off:
    print(f"kicad-edit-mcp: disabled: {', '.join(sorted(_off))}", file=sys.stderr)

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("kicad-edit-mcp")


def _err(exc: Exception) -> str:
    return f"Error: {exc}"


# ---------------------------------------------------------------------------
# Tool registration (conditional)
# ---------------------------------------------------------------------------

if _enabled["list_components"]:

    @mcp.tool()
    def list_components(schematic_path: str, filter: Optional[str] = None) -> list:
        """List all schematic components with reference, value, and footprint.

        Args:
            schematic_path: Path to a .kicad_sch file.
            filter: Optional reference prefix to filter by (e.g. 'C' for capacitors).
        """
        try:
            return kicad_helpers.list_components(schematic_path, filter)
        except ValueError as exc:
            return [_err(exc)]


if _enabled["get_component"]:

    @mcp.tool()
    def get_component(schematic_path: str, reference: str) -> dict:
        """Get all properties of a single schematic component by reference designator.

        Returns a dict mapping property name to {value, visible}. The 'visible'
        field indicates whether the property text is shown on the schematic.

        Args:
            schematic_path: Path to a .kicad_sch file.
            reference: Exact reference designator, e.g. 'C5'.
        """
        try:
            return kicad_helpers.get_component(schematic_path, reference)
        except ValueError as exc:
            return {"error": str(exc)}


if _enabled["update_component"]:

    @mcp.tool()
    def update_component(
        schematic_path: str, reference: str, properties: dict[str, Any]
    ) -> str:
        """Set or remove properties on a schematic component and save the file.

        Use None as a value to remove a property. The special key 'dnp' sets the
        do-not-populate flag (boolean). For explicit visibility control, pass a dict:
        {"value": "3.3V", "visible": true}. New properties are hidden by default
        (except Reference and Value).

        Args:
            schematic_path: Path to a .kicad_sch file.
            reference: Exact reference designator, e.g. 'C5'.
            properties: Mapping of property name to new value (or None to remove).
        """
        try:
            return kicad_helpers.update_component(schematic_path, reference, properties)
        except ValueError as exc:
            return _err(exc)


if _enabled["update_schematic_info"]:

    @mcp.tool()
    def update_schematic_info(
        schematic_path: str,
        title: Optional[str] = None,
        revision: Optional[str] = None,
        date: Optional[str] = None,
        author: Optional[str] = None,
        company: Optional[str] = None,
    ) -> str:
        """Update title block metadata in a KiCad schematic.

        Only provided fields are updated; omit a field to leave it unchanged.

        Args:
            schematic_path: Path to a .kicad_sch file.
            title: New title string.
            revision: New revision string.
            date: New date string (YYYY-MM-DD recommended).
            author: Author name (stored in title block comment 1).
            company: Company name.
        """
        try:
            return kicad_helpers.update_schematic_info(
                schematic_path, title, revision, date, author, company
            )
        except ValueError as exc:
            return _err(exc)


if _enabled["rename_net"]:

    @mcp.tool()
    def rename_net(schematic_path: str, old_name: str, new_name: str) -> str:
        """Rename all net labels in a schematic from old_name to new_name.

        Searches local, global, and hierarchical labels.

        Args:
            schematic_path: Path to a .kicad_sch file.
            old_name: Exact net label text to find.
            new_name: Replacement text.
        """
        try:
            return kicad_helpers.rename_net(schematic_path, old_name, new_name)
        except ValueError as exc:
            return _err(exc)


if _enabled["list_net_classes"]:

    @mcp.tool()
    def list_net_classes(project_path: str) -> list:
        """List all net classes defined in a KiCad project file.

        Args:
            project_path: Path to a .kicad_pro file.
        """
        try:
            return kicad_helpers.list_net_classes(project_path)
        except ValueError as exc:
            return [_err(exc)]


if _enabled["update_net_class"]:

    @mcp.tool()
    def update_net_class(
        project_path: str,
        class_name: str,
        rules: Optional[dict[str, Any]] = None,
        add_pattern: Optional[str] = None,
    ) -> str:
        """Create or update a net class in a KiCad project file.

        Creates the class if it does not exist. Rules are merged (existing keys
        not in 'rules' are preserved).

        Args:
            project_path: Path to a .kicad_pro file.
            class_name: Net class name, e.g. 'Default' or 'USB'.
            rules: Dict of rule overrides e.g. {'track_width': 0.5, 'clearance': 0.2}.
            add_pattern: Wildcard net pattern to add, e.g. 'USB_D?'.
        """
        try:
            return kicad_helpers.update_net_class(
                project_path, class_name, rules, add_pattern
            )
        except ValueError as exc:
            return _err(exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
