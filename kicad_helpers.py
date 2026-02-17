"""Pure Python helper functions for KiCad file manipulation via kiutils.

No MCP imports — these are plain functions wired into server.py by BD-003.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from kiutils.items.common import Property, TitleBlock
from kiutils.schematic import Schematic


# ---------------------------------------------------------------------------
# Schematic helpers
# ---------------------------------------------------------------------------


def list_components(schematic_path: str, filter: Optional[str] = None) -> list[dict]:
    """Return a summary list of all schematic symbols.

    Args:
        schematic_path: Path to a .kicad_sch file.
        filter: Optional reference prefix, e.g. "C" for capacitors.  Only
            symbols whose Reference starts with this string are returned.

    Returns:
        List of dicts with keys ``reference``, ``value``, ``footprint``.

    Raises:
        ValueError: If the file does not exist or cannot be parsed.
    """
    path = Path(schematic_path)
    if not path.exists():
        raise ValueError(f"Schematic file not found: {schematic_path}")

    try:
        schematic = Schematic.from_file(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    results: list[dict] = []
    for sym in schematic.schematicSymbols:
        props = {p.key: p.value for p in sym.properties}
        reference = props.get("Reference", "")
        if filter is not None and not reference.startswith(filter):
            continue
        results.append(
            {
                "reference": reference,
                "value": props.get("Value", ""),
                "footprint": props.get("Footprint", ""),
            }
        )
    return results


def get_component(schematic_path: str, reference: str) -> dict:
    """Return all properties of a single schematic symbol.

    Args:
        schematic_path: Path to a .kicad_sch file.
        reference: Exact reference designator, e.g. "C5".

    Returns:
        Dict mapping property name to value, e.g.
        ``{"Reference": "C5", "Value": "100nF", ...}``.

    Raises:
        ValueError: If the file doesn't exist, can't be parsed, or the
            component is not found.
    """
    path = Path(schematic_path)
    if not path.exists():
        raise ValueError(f"Schematic file not found: {schematic_path}")

    try:
        schematic = Schematic.from_file(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    for sym in schematic.schematicSymbols:
        props = {p.key: p.value for p in sym.properties}
        if props.get("Reference") == reference:
            return props

    raise ValueError(f"Component '{reference}' not found in {schematic_path}")


def update_component(
    schematic_path: str,
    reference: str,
    properties: dict[str, Any],
) -> str:
    """Modify properties of a schematic symbol and save the file.

    The ``properties`` dict controls what happens:
    - ``{"Value": "100nF"}`` — set the property to a new value.
    - ``{"Voltage": None}`` — remove the property entirely.
    - ``{"dnp": True}`` — set the ``dnp`` flag on the symbol (boolean).

    Args:
        schematic_path: Path to a .kicad_sch file.
        reference: Exact reference designator, e.g. "C5".
        properties: Mapping of property key → new value (or ``None`` to
            remove).  The special key ``"dnp"`` controls the do-not-populate
            flag rather than a text property.

    Returns:
        Human-readable success message describing the changes.

    Raises:
        ValueError: If the file doesn't exist, can't be parsed, or the
            component is not found.
    """
    path = Path(schematic_path)
    if not path.exists():
        raise ValueError(f"Schematic file not found: {schematic_path}")

    try:
        schematic = Schematic.from_file(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    target = None
    for sym in schematic.schematicSymbols:
        sym_props = {p.key: p.value for p in sym.properties}
        if sym_props.get("Reference") == reference:
            target = sym
            break

    if target is None:
        raise ValueError(f"Component '{reference}' not found in {schematic_path}")

    changes: list[str] = []

    for key, value in properties.items():
        # Special-case: dnp is a struct field, not a text property
        if key == "dnp":
            old_dnp = target.dnp
            target.dnp = bool(value) if value is not None else False
            changes.append(f"dnp={target.dnp} (was {old_dnp})")
            continue

        if value is None:
            # Remove the property
            before = len(target.properties)
            target.properties = [p for p in target.properties if p.key != key]
            removed = before - len(target.properties)
            if removed:
                changes.append(f"removed '{key}'")
            else:
                changes.append(f"'{key}' not present (no-op)")
        else:
            # Set or update
            existing = next((p for p in target.properties if p.key == key), None)
            if existing is not None:
                old_val = existing.value
                existing.value = str(value)
                changes.append(f"'{key}': '{old_val}' -> '{value}'")
            else:
                # Create a new property; kiutils needs a Property object
                new_prop = Property(key=key, value=str(value))
                target.properties.append(new_prop)
                changes.append(f"added '{key}'='{value}'")

    try:
        schematic.to_file()
    except Exception as exc:
        raise ValueError(f"Failed to save schematic: {exc}") from exc

    changes_str = "; ".join(changes) if changes else "no changes"
    return f"Updated {reference}: {changes_str}"


def update_schematic_info(
    schematic_path: str,
    title: Optional[str] = None,
    revision: Optional[str] = None,
    date: Optional[str] = None,
    author: Optional[str] = None,
    company: Optional[str] = None,
) -> str:
    """Update title block metadata in a schematic.

    Note: kiutils TitleBlock has no ``author`` field (KiCad stores it in
    comment 1 by convention).  This function uses comment slot 1 for the
    author when provided.

    Args:
        schematic_path: Path to a .kicad_sch file.
        title: New title string.
        revision: New revision string.
        date: New date string (YYYY-MM-DD recommended).
        author: Author name (stored in title block comment 1).
        company: Company name.

    Returns:
        Human-readable success message listing updated fields.

    Raises:
        ValueError: If the file doesn't exist or cannot be parsed/saved.
    """
    path = Path(schematic_path)
    if not path.exists():
        raise ValueError(f"Schematic file not found: {schematic_path}")

    try:
        schematic = Schematic.from_file(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    # Ensure title block exists
    if schematic.titleBlock is None:
        schematic.titleBlock = TitleBlock()

    tb = schematic.titleBlock
    updated: list[str] = []

    if title is not None:
        tb.title = title
        updated.append(f"title='{title}'")
    if revision is not None:
        tb.revision = revision
        updated.append(f"revision='{revision}'")
    if date is not None:
        tb.date = date
        updated.append(f"date='{date}'")
    if company is not None:
        tb.company = company
        updated.append(f"company='{company}'")
    if author is not None:
        # KiCad stores author in comment 1 by convention
        tb.comments[1] = author
        updated.append(f"author='{author}' (comment 1)")

    try:
        schematic.to_file()
    except Exception as exc:
        raise ValueError(f"Failed to save schematic: {exc}") from exc

    updated_str = ", ".join(updated) if updated else "no fields provided"
    return f"Updated title block: {updated_str}"


def rename_net(schematic_path: str, old_name: str, new_name: str) -> str:
    """Rename all net labels matching ``old_name`` to ``new_name``.

    Searches local labels, global labels, and hierarchical labels.

    Args:
        schematic_path: Path to a .kicad_sch file.
        old_name: Exact net label text to find.
        new_name: Replacement net label text.

    Returns:
        Human-readable success message with the count of renamed labels.

    Raises:
        ValueError: If the file doesn't exist or cannot be parsed/saved.
    """
    path = Path(schematic_path)
    if not path.exists():
        raise ValueError(f"Schematic file not found: {schematic_path}")

    try:
        schematic = Schematic.from_file(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    count = 0

    for label in schematic.labels:
        if label.text == old_name:
            label.text = new_name
            count += 1

    for label in schematic.globalLabels:
        if label.text == old_name:
            label.text = new_name
            count += 1

    for label in schematic.hierarchicalLabels:
        if label.text == old_name:
            label.text = new_name
            count += 1

    if count == 0:
        return f"No labels named '{old_name}' found — nothing changed"

    try:
        schematic.to_file()
    except Exception as exc:
        raise ValueError(f"Failed to save schematic: {exc}") from exc

    return f"Renamed {count} label(s) from '{old_name}' to '{new_name}'"


# ---------------------------------------------------------------------------
# Project helpers (.kicad_pro — plain JSON)
# ---------------------------------------------------------------------------

_NETCLASS_FIELD_MAP = {
    "clearance": "clearance",
    "track_width": "track_width",
    "via_diameter": "via_diameter",
    "via_drill": "via_drill",
    "microvia_diameter": "microvia_diameter",
    "microvia_drill": "microvia_drill",
    "diff_pair_width": "diff_pair_width",
    "diff_pair_gap": "diff_pair_gap",
}


def _load_project(project_path: str) -> tuple[dict, Path]:
    """Load a .kicad_pro JSON file and return (data, path).

    Raises:
        ValueError: If the file does not exist or is not valid JSON.
    """
    path = Path(project_path)
    if not path.exists():
        raise ValueError(f"Project file not found: {project_path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in project file: {exc}") from exc
    return data, path


def _save_project(data: dict, path: Path) -> None:
    """Save project data back to disk as pretty JSON."""
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_net_classes(project_path: str) -> list[dict]:
    """Return all net classes defined in a KiCad project file.

    Args:
        project_path: Path to a .kicad_pro file.

    Returns:
        List of dicts.  Each dict always has a ``name`` key and a
        ``patterns`` list (possibly empty).  Other keys depend on what is
        stored in the file (e.g. ``track_width``, ``clearance``,
        ``via_diameter``, ``via_drill``).

    Raises:
        ValueError: If the file doesn't exist or is not valid JSON.
    """
    data, _ = _load_project(project_path)

    net_settings: dict = data.get("net_settings", {})

    # KiCad 6+ stores net classes under net_settings.classes
    raw_classes: list[dict] = net_settings.get("classes", [])

    results: list[dict] = []
    for cls in raw_classes:
        entry: dict = {}
        # Name
        entry["name"] = cls.get("name", "")
        # Patterns (net wildcard assignments)
        entry["patterns"] = list(cls.get("nets", []))
        # Numeric rule fields — include whatever is present
        for field_name in _NETCLASS_FIELD_MAP:
            if field_name in cls:
                entry[field_name] = cls[field_name]
        results.append(entry)

    return results


def update_net_class(
    project_path: str,
    class_name: str,
    rules: Optional[dict[str, Any]] = None,
    add_pattern: Optional[str] = None,
) -> str:
    """Create or update a net class in a KiCad project file.

    If the net class does not exist it is created.  Rules are merged
    (existing keys not in ``rules`` are preserved).

    Args:
        project_path: Path to a .kicad_pro file.
        class_name: Name of the net class, e.g. ``"Default"`` or ``"USB"``.
        rules: Dict of rule overrides such as
            ``{"track_width": 0.5, "clearance": 0.2}``.
        add_pattern: A wildcard net pattern to add to the class, e.g.
            ``"USB_D?"``.  Duplicates are silently ignored.

    Returns:
        Human-readable success message.

    Raises:
        ValueError: If the file doesn't exist or is not valid JSON.
    """
    data, path = _load_project(project_path)

    net_settings = data.setdefault("net_settings", {})
    classes: list[dict] = net_settings.setdefault("classes", [])

    # Find existing class
    target: Optional[dict] = None
    for cls in classes:
        if cls.get("name") == class_name:
            target = cls
            break

    created = target is None
    if created:
        target = {"name": class_name, "nets": []}
        classes.append(target)

    changes: list[str] = []

    if rules:
        for field_name, value in rules.items():
            old = target.get(field_name)
            target[field_name] = value
            if old != value:
                changes.append(f"{field_name}: {old!r} -> {value!r}")

    if add_pattern is not None:
        nets: list = target.setdefault("nets", [])
        if add_pattern not in nets:
            nets.append(add_pattern)
            changes.append(f"added pattern '{add_pattern}'")
        else:
            changes.append(f"pattern '{add_pattern}' already present")

    _save_project(data, path)

    action = "Created" if created else "Updated"
    changes_str = "; ".join(changes) if changes else "no rule changes"
    return f"{action} net class '{class_name}': {changes_str}"
