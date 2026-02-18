"""Pure Python helper functions for KiCad file manipulation via kicad-sch-api.

No MCP imports — these are plain functions wired into server.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import sexpdata

from kicad_sch_api import Schematic


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_hidden_in_sexp(sexp: Any) -> bool:
    """Return True if the property S-expression has a hide flag.

    Handles both KiCad 6 bare ``Symbol('hide')`` and KiCad 9
    ``[Symbol('hide'), Symbol('yes')]`` formats.
    """
    if not isinstance(sexp, list):
        return False
    for item in sexp:
        if isinstance(item, list) and len(item) > 0:
            if isinstance(item[0], sexpdata.Symbol) and str(item[0]) == "effects":
                # Inspect the effects section
                for eff_item in item[1:]:
                    # KiCad 9: (hide yes) or bare (hide)
                    if (
                        isinstance(eff_item, list)
                        and len(eff_item) > 0
                        and isinstance(eff_item[0], sexpdata.Symbol)
                        and str(eff_item[0]) == "hide"
                    ):
                        if len(eff_item) == 1:
                            return True  # bare (hide)
                        val = (
                            str(eff_item[1]).lower()
                            if isinstance(eff_item[1], sexpdata.Symbol)
                            else ""
                        )
                        return val in ("yes", "true")
                    # KiCad 6: bare Symbol('hide')
                    if isinstance(eff_item, sexpdata.Symbol) and str(eff_item) == "hide":
                        return True
    return False


def _sync_hidden_properties(comp: Any) -> None:
    """Sync ``hidden_properties`` set from preserved ``__sexp_`` entries.

    The kicad-sch-api parser silently drops bare ``hide`` tokens (KiCad 6
    format).  This function fixes up ``hidden_properties`` by reading the
    preserved S-expressions directly.
    """
    hidden: set[str] = set()
    for key, val in comp._data.properties.items():
        if key.startswith("__sexp_"):
            prop_name = key[len("__sexp_"):]
            if _is_hidden_in_sexp(val):
                hidden.add(prop_name)
    comp._data.hidden_properties = hidden


def _set_property_hidden(comp: Any, prop_name: str, hidden: bool) -> None:
    """Update the hide flag for a property in both ``hidden_properties`` and ``__sexp_``.

    Args:
        comp: ``Component`` object from kicad-sch-api.
        prop_name: Property name, e.g. "Footprint".
        hidden: True to hide, False to show.
    """
    if hidden:
        comp._data.hidden_properties.add(prop_name)
    else:
        comp._data.hidden_properties.discard(prop_name)

    # Also update the preserved S-expression so serialization is consistent.
    sexp_key = f"__sexp_{prop_name}"
    sexp = comp._data.properties.get(sexp_key)
    if sexp is not None:
        from kicad_sch_api.parsers.elements.symbol_parser import SymbolParser

        parser = SymbolParser()
        updated = parser._update_property_hide_flag(list(sexp), hidden)
        comp._data.properties[sexp_key] = updated


_ALWAYS_VISIBLE_PROPS = {"Reference", "Value"}


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
        sch = Schematic.load(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    results: list[dict] = []
    for comp in sch.components:
        ref = comp.reference
        if filter is not None and not ref.startswith(filter):
            continue
        results.append(
            {
                "reference": ref,
                "value": comp.value,
                "footprint": comp.footprint or "",
            }
        )
    return results


def get_component(schematic_path: str, reference: str) -> dict:
    """Return all properties of a single schematic symbol.

    Args:
        schematic_path: Path to a .kicad_sch file.
        reference: Exact reference designator, e.g. "C5".

    Returns:
        Dict mapping property name to a dict with ``value`` and ``visible``
        keys, e.g. ``{"Reference": {"value": "C5", "visible": True},
        "Footprint": {"value": "...", "visible": False}}``.

    Raises:
        ValueError: If the file doesn't exist, can't be parsed, or the
            component is not found.
    """
    path = Path(schematic_path)
    if not path.exists():
        raise ValueError(f"Schematic file not found: {schematic_path}")

    try:
        sch = Schematic.load(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    comp = sch.components.get(reference)
    if comp is None:
        raise ValueError(f"Component '{reference}' not found in {schematic_path}")

    _sync_hidden_properties(comp)

    props: dict[str, Any] = {}
    for name, val in comp.properties.items():
        if name.startswith("__sexp_"):
            continue
        if isinstance(val, dict):
            raw_value = val.get("value", "")
        else:
            raw_value = str(val)
        hidden = name in comp._data.hidden_properties
        props[name] = {"value": raw_value, "visible": not hidden}

    return props


def update_component(
    schematic_path: str,
    reference: str,
    properties: dict[str, Any],
) -> str:
    """Modify properties of a schematic symbol and save the file.

    The ``properties`` dict controls what happens:

    - ``{"Value": "100nF"}`` — set the property to a new value.
    - ``{"Voltage": None}`` — remove the property entirely.
    - ``{"Voltage": {"value": "3.3V", "visible": False}}`` — set value with
      explicit visibility control.  New custom properties default to hidden
      (``visible=False``) unless overridden here.

    Note: The ``"dnp"`` key is not supported and raises ``ValueError``.

    Args:
        schematic_path: Path to a .kicad_sch file.
        reference: Exact reference designator, e.g. "C5".
        properties: Mapping of property key → new value (or ``None`` to
            remove).

    Returns:
        Human-readable success message describing the changes.

    Raises:
        ValueError: If the file doesn't exist, can't be parsed, the
            component is not found, or ``"dnp"`` is passed.
    """
    if "dnp" in properties:
        raise ValueError(
            "'dnp' flag is not supported — use in_bom/on_board or a custom property instead"
        )

    path = Path(schematic_path)
    if not path.exists():
        raise ValueError(f"Schematic file not found: {schematic_path}")

    try:
        sch = Schematic.load(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    comp = sch.components.get(reference)
    if comp is None:
        raise ValueError(f"Component '{reference}' not found in {schematic_path}")

    # Fix hidden_properties from preserved S-expressions before making changes.
    _sync_hidden_properties(comp)

    changes: list[str] = []

    for key, value in properties.items():
        if value is None:
            # Remove the property
            removed = comp.remove_property(key)
            # Also clean up the preserved S-expression and hidden set
            sexp_key = f"__sexp_{key}"
            comp._data.properties.pop(sexp_key, None)
            comp._data.hidden_properties.discard(key)
            if removed:
                changes.append(f"removed '{key}'")
            else:
                changes.append(f"'{key}' not present (no-op)")
        else:
            # Validate rich dict format
            if isinstance(value, dict) and "value" not in value:
                raise ValueError(
                    f"Property '{key}': dict value must have a 'value' key, "
                    f"e.g. {{'value': '3.3V', 'visible': False}}"
                )

            # Normalize to raw_value + explicit_visible
            if isinstance(value, dict) and "value" in value:
                raw_value: str = str(value["value"])
                explicit_visible: Optional[bool] = value.get("visible")
            else:
                raw_value = str(value)
                explicit_visible = None

            # Check if property already exists
            existing_props = {
                k: v for k, v in comp.properties.items() if not k.startswith("__sexp_")
            }
            prop_exists = key in existing_props

            if prop_exists:
                old_val = existing_props[key]
                if isinstance(old_val, dict):
                    old_val = old_val.get("value", "")

                comp.set_property(key, raw_value)

                # Standard properties have dedicated _data fields that
                # _symbol_to_sexp reads directly — update them too.
                if key == "Value":
                    comp._data.value = raw_value
                elif key == "Reference":
                    comp._data.reference = raw_value
                elif key == "Footprint":
                    comp._data.footprint = raw_value

                # Also update value in preserved __sexp_
                sexp_key = f"__sexp_{key}"
                sexp = comp._data.properties.get(sexp_key)
                if sexp is not None and isinstance(sexp, list) and len(sexp) >= 3:
                    sexp = list(sexp)
                    sexp[2] = raw_value
                    comp._data.properties[sexp_key] = sexp

                # Apply explicit visibility if provided (else preserve existing)
                if explicit_visible is not None:
                    _set_property_hidden(comp, key, not explicit_visible)

                changes.append(f"'{key}': '{old_val}' -> '{raw_value}'")
            else:
                # New property — default hide=True for non-Reference/Value props
                default_hide = key not in _ALWAYS_VISIBLE_PROPS
                hide = (not explicit_visible) if explicit_visible is not None else default_hide

                # Use add_property on the underlying SchematicSymbol
                comp._data.add_property(key, raw_value, hidden=hide)
                changes.append(f"added '{key}'='{raw_value}'")

    try:
        sch.save()
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

    Note: author is stored in title block comment 1 by KiCad convention.

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
        sch = Schematic.load(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    # Read current title block values (preserve fields not being updated)
    tb = sch.title_block  # dict: {title, rev, date, company, comments}
    current_title = tb.get("title", "")
    current_rev = tb.get("rev", "")
    current_date = tb.get("date", "")
    current_company = tb.get("company", "")
    current_comments: dict = dict(tb.get("comments", {}))

    updated: list[str] = []

    if title is not None:
        current_title = title
        updated.append(f"title='{title}'")
    if revision is not None:
        current_rev = revision
        updated.append(f"revision='{revision}'")
    if date is not None:
        current_date = date
        updated.append(f"date='{date}'")
    if company is not None:
        current_company = company
        updated.append(f"company='{company}'")
    if author is not None:
        current_comments[1] = author
        updated.append(f"author='{author}' (comment 1)")

    sch.set_title_block(
        title=current_title,
        date=current_date,
        rev=current_rev,
        company=current_company,
        comments=current_comments,
    )

    try:
        sch.save()
    except Exception as exc:
        raise ValueError(f"Failed to save schematic: {exc}") from exc

    updated_str = ", ".join(updated) if updated else "no fields provided"
    return f"Updated title block: {updated_str}"


def rename_net(schematic_path: str, old_name: str, new_name: str) -> str:
    """Rename all net labels matching ``old_name`` to ``new_name``.

    Searches local and hierarchical labels. Note: global labels are not
    exposed by kicad-sch-api's label collection at this time.

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
        sch = Schematic.load(str(path))
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    count = 0

    for label in sch.labels:
        if label.text == old_name:
            label.text = new_name
            count += 1

    for label in sch.hierarchical_labels:
        if label.text == old_name:
            label.text = new_name
            count += 1

    if count == 0:
        return f"No labels named '{old_name}' found — nothing changed"

    try:
        sch.save()
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
