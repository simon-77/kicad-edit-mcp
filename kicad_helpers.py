"""Pure Python helper functions for KiCad file manipulation via sexp surgery.

No MCP imports — these are plain functions wired into server.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import sexpdata

from sexp_surgery import SexpDocument, SexpSpan


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ALWAYS_VISIBLE_PROPS = {"Reference", "Value"}


def _unwrap(value: Any) -> str:
    """Extract plain string from sexpdata value."""
    if isinstance(value, str):
        return value
    if isinstance(value, sexpdata.Symbol):
        return str(value)
    return str(value)


def _prop_value(prop_span: SexpSpan) -> str:
    """Get the value (3rd element) from a property span's node."""
    if prop_span and len(prop_span.node) >= 3:
        return _unwrap(prop_span.node[2])
    return ""


def _escape_sexp_string(s: str) -> str:
    """Escape for s-expression quoting."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def _find_quoted_string(
    text: str, start: int, end: int, index: int = 0
) -> tuple[int, int] | None:
    """Find the Nth (0-indexed) quoted string in text[start:end].

    Returns (start_pos, end_pos) including the quote characters, or None.
    """
    i = start
    count = 0
    while i < end:
        if text[i] == '"':
            q_start = i
            i += 1
            while i < end:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    if count == index:
                        return (q_start, i)
                    count += 1
                    break
                i += 1
        else:
            i += 1
    return None


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
        doc = SexpDocument.load(path)
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    results: list[dict] = []
    for sym_span in doc.find_all("symbol"):
        node = sym_span.node
        # Only schematic instances have lib_id, not lib_symbols definitions
        has_lib_id = any(
            isinstance(c, list) and c and str(c[0]) == "lib_id" for c in node[1:]
        )
        if not has_lib_id:
            continue

        ref_span = doc.get_property(sym_span, "Reference")
        val_span = doc.get_property(sym_span, "Value")
        fp_span = doc.get_property(sym_span, "Footprint")

        ref = _prop_value(ref_span) if ref_span else ""
        if filter is not None and not ref.startswith(filter):
            continue

        results.append(
            {
                "reference": ref,
                "value": _prop_value(val_span) if val_span else "",
                "footprint": _prop_value(fp_span) if fp_span else "",
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

        For multi-unit symbols, properties are read from the first unit
        (canonical) and a ``_units`` metadata key is added with the unit
        count.  Keys starting with ``_`` are metadata and are ignored by
        ``update_component``.

    Raises:
        ValueError: If the file doesn't exist, can't be parsed, or the
            component is not found.
    """
    path = Path(schematic_path)
    if not path.exists():
        raise ValueError(f"Schematic file not found: {schematic_path}")

    try:
        doc = SexpDocument.load(path)
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    unit_spans = doc.find_symbol_units(reference)
    if not unit_spans:
        raise ValueError(f"Component '{reference}' not found in {schematic_path}")

    # Read properties from first unit (canonical)
    sym_span = unit_spans[0]
    props: dict[str, Any] = {}
    for child in sym_span.node[1:]:
        if not (isinstance(child, list) and child and str(child[0]) == "property"):
            continue
        if len(child) < 2:
            continue
        name = _unwrap(child[1])
        child_span = doc.spans.get(id(child))
        if child_span is None:
            continue
        value = _prop_value(child_span)
        hidden = doc.is_property_hidden(child_span)
        props[name] = {"value": value, "visible": not hidden}

    if len(unit_spans) > 1:
        props["_units"] = {"value": str(len(unit_spans)), "visible": False}

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

    # Strip metadata keys injected by get_component (e.g. _units)
    properties = {k: v for k, v in properties.items() if not k.startswith("_")}

    path = Path(schematic_path)
    if not path.exists():
        raise ValueError(f"Schematic file not found: {schematic_path}")

    try:
        doc = SexpDocument.load(path)
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    unit_spans = doc.find_symbol_units(reference)
    if not unit_spans:
        raise ValueError(f"Component '{reference}' not found in {schematic_path}")

    changes: list[str] = []

    for key, value in properties.items():
        if value is None:
            # Delete the property from all units
            deleted = 0
            for sym_span in unit_spans:
                prop_span = doc.get_property(sym_span, key)
                if prop_span is not None:
                    doc.delete_span(prop_span)
                    deleted += 1
            if deleted:
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

            reported = False
            for sym_span in unit_spans:
                prop_span = doc.get_property(sym_span, key)

                if prop_span is not None:
                    # Property exists — surgically replace value
                    vs = doc.get_property_value_span(prop_span)
                    if vs is not None:
                        old_val = vs[2]
                        doc.replace_bytes(vs[0], vs[1], f'"{_escape_sexp_string(raw_value)}"')
                        if not reported:
                            changes.append(f"'{key}': '{old_val}' -> '{raw_value}'")
                            reported = True
                    elif not reported:
                        changes.append(f"'{key}': (could not locate value span)")
                        reported = True

                    # Handle visibility change if explicitly requested
                    if explicit_visible is not None:
                        _update_property_visibility(doc, prop_span, explicit_visible)
                else:
                    # New property — insert before symbol's closing paren
                    default_hide = key not in _ALWAYS_VISIBLE_PROPS
                    hide = (not explicit_visible) if explicit_visible is not None else default_hide

                    at_str = _get_symbol_at(sym_span)
                    hide_str = " (hide yes)" if hide else ""
                    new_prop = (
                        f'\n    (property "{_escape_sexp_string(key)}" '
                        f'"{_escape_sexp_string(raw_value)}" {at_str}\n'
                        f"      (effects (font (size 1.27 1.27)){hide_str})\n"
                        f"    )"
                    )
                    doc.insert_before_end(sym_span, new_prop)
                    if not reported:
                        changes.append(f"added '{key}'='{raw_value}'")
                        reported = True

    n_units = len(unit_spans)

    try:
        doc.save(path)
    except Exception as exc:
        raise ValueError(f"Failed to save schematic: {exc}") from exc

    changes_str = "; ".join(changes) if changes else "no changes"
    units_note = f" ({n_units} units)" if n_units > 1 else ""
    return f"Updated {reference}{units_note}: {changes_str}"


def _get_symbol_at(sym_span: SexpSpan) -> str:
    """Extract (at ...) string from a symbol node, defaulting to (at 0 0 0)."""
    for child in sym_span.node[1:]:
        if isinstance(child, list) and child and str(child[0]) == "at":
            parts = [str(x) for x in child[1:]]
            return f"(at {' '.join(parts)})"
    return "(at 0 0 0)"


def _update_property_visibility(
    doc: SexpDocument, prop_span: SexpSpan, visible: bool
) -> None:
    """Toggle the hide flag in a property span using text scanning.

    Manipulates the text directly within the property span's effects section.
    """
    text = doc.text
    start = prop_span.start
    end = prop_span.end
    prop_text = text[start:end]

    # Find the effects section within the property text
    effects_idx = prop_text.find("(effects")
    if effects_idx == -1:
        # No effects section, nothing to do for hiding
        return

    effects_start = start + effects_idx

    # Find the closing paren of effects section
    depth = 0
    i = effects_start
    in_str = False
    while i < end:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                effects_end = i + 1
                break
        i += 1
    else:
        return

    effects_text = text[effects_start:effects_end]

    if not visible:
        # Need to hide: add (hide yes) or bare hide if not already present
        # Check if already hidden
        if "(hide yes)" in effects_text or " hide)" in effects_text:
            return  # already hidden
        # Insert before effects closing paren
        insert_pos = effects_end - 1
        doc.replace_bytes(insert_pos, insert_pos, " (hide yes)")
    else:
        # Need to show: remove hide token
        # Handle (hide yes) form
        hide_yes_pos = effects_text.find(" (hide yes)")
        if hide_yes_pos != -1:
            abs_pos = effects_start + hide_yes_pos
            doc.replace_bytes(abs_pos, abs_pos + len(" (hide yes)"), "")
            return
        # Handle bare " hide)" form (KiCad 6)
        # Look for " hide)" at end of effects
        bare_pos = effects_text.rfind(" hide)")
        if bare_pos != -1:
            abs_pos = effects_start + bare_pos
            # Replace " hide)" with ")"
            doc.replace_bytes(abs_pos, abs_pos + len(" hide)"), ")")
            return
        # Handle "hide" as Symbol just before closing paren
        bare2_pos = effects_text.rfind(" hide\n")
        if bare2_pos != -1:
            abs_pos = effects_start + bare2_pos
            doc.replace_bytes(abs_pos, abs_pos + len(" hide\n"), "\n")


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
        doc = SexpDocument.load(path)
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    tb_span = doc.find_title_block()
    if tb_span is None:
        raise ValueError("No title_block found in schematic")

    updated: list[str] = []

    # Map field name → (sexp key, value, display name)
    fields: list[tuple[str, Any, str]] = []
    if title is not None:
        fields.append(("title", title, f"title='{title}'"))
    if revision is not None:
        fields.append(("rev", revision, f"revision='{revision}'"))
    if date is not None:
        fields.append(("date", date, f"date='{date}'"))
    if company is not None:
        fields.append(("company", company, f"company='{company}'"))

    for sexp_key, new_val, label in fields:
        _update_title_block_field(doc, tb_span, sexp_key, new_val)
        updated.append(label)

    # Author is special: (comment 1 "...")
    if author is not None:
        _update_title_block_comment(doc, tb_span, 1, author)
        updated.append(f"author='{author}' (comment 1)")

    try:
        doc.save(path)
    except Exception as exc:
        raise ValueError(f"Failed to save schematic: {exc}") from exc

    updated_str = ", ".join(updated) if updated else "no fields provided"
    return f"Updated title block: {updated_str}"


def _update_title_block_field(
    doc: SexpDocument, tb_span: SexpSpan, key: str, new_val: str
) -> None:
    """Update or insert a simple title_block field like (title "...") or (rev "...")."""
    text = doc.text
    tb_text = text[tb_span.start:tb_span.end]

    # Find the field node within tb_text using a simple scan
    # Field format: (key "value")  or  (key "value"\n  )
    search = f"({key} "
    field_idx = tb_text.find(search)
    if field_idx != -1:
        abs_field_start = tb_span.start + field_idx
        # Find the quoted string value within this field
        field_end = _find_node_end(text, abs_field_start, tb_span.end)
        if field_end is None:
            return
        # Find the first quoted string in this field
        qs = _find_quoted_string(text, abs_field_start, field_end, index=0)
        if qs is not None:
            doc.replace_bytes(qs[0], qs[1], f'"{_escape_sexp_string(new_val)}"')
    else:
        # Field doesn't exist, insert before title_block closing paren
        doc.insert_before_end(
            tb_span, f'\n    ({key} "{_escape_sexp_string(new_val)}")'
        )


def _update_title_block_comment(
    doc: SexpDocument, tb_span: SexpSpan, number: int, new_val: str
) -> None:
    """Update or insert (comment N "value") in title_block."""
    text = doc.text
    tb_text = text[tb_span.start:tb_span.end]

    # Format: (comment 1 "value")
    search = f"(comment {number} "
    field_idx = tb_text.find(search)
    if field_idx != -1:
        abs_field_start = tb_span.start + field_idx
        field_end = _find_node_end(text, abs_field_start, tb_span.end)
        if field_end is None:
            return
        # The quoted string is the 2nd quoted string (after key="comment", number is not quoted)
        # Actually: (comment 1 "value") — only 1 quoted string
        qs = _find_quoted_string(text, abs_field_start, field_end, index=0)
        if qs is not None:
            doc.replace_bytes(qs[0], qs[1], f'"{_escape_sexp_string(new_val)}"')
    else:
        doc.insert_before_end(
            tb_span,
            f'\n    (comment {number} "{_escape_sexp_string(new_val)}")',
        )


def _find_node_end(text: str, start: int, limit: int) -> int | None:
    """Find the closing paren of the s-expression starting at text[start]."""
    if start >= limit or text[start] != "(":
        return None
    depth = 0
    i = start
    in_str = False
    while i < limit:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def rename_net(schematic_path: str, old_name: str, new_name: str) -> str:
    """Rename all net labels matching ``old_name`` to ``new_name``.

    Searches local labels, hierarchical labels, AND global labels.

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
        doc = SexpDocument.load(path)
    except Exception as exc:
        raise ValueError(f"Failed to parse schematic: {exc}") from exc

    count = 0
    for label_type in ("label", "hierarchical_label", "global_label"):
        for label_span in doc.find_labels(label_type, text=old_name):
            # The label text is the second element (index 1) of the node
            # Scan for first quoted string in the label span
            vs = _find_quoted_string(
                doc.text, label_span.start, label_span.end, index=0
            )
            if vs is not None:
                doc.replace_bytes(vs[0], vs[1], f'"{_escape_sexp_string(new_name)}"')
                count += 1

    if count == 0:
        return f"No labels named '{old_name}' found — nothing changed"

    try:
        doc.save(path)
    except Exception as exc:
        raise ValueError(f"Failed to save schematic: {exc}") from exc

    return f"Renamed {count} label(s) from '{old_name}' to '{new_name}'"


# ---------------------------------------------------------------------------
# Project helpers (.kicad_pro — plain JSON)
# ---------------------------------------------------------------------------

_NETCLASS_FIELD_MAP = {
    "bus_width": "bus_width",
    "clearance": "clearance",
    "diff_pair_gap": "diff_pair_gap",
    "diff_pair_via_gap": "diff_pair_via_gap",
    "diff_pair_width": "diff_pair_width",
    "line_style": "line_style",
    "microvia_diameter": "microvia_diameter",
    "microvia_drill": "microvia_drill",
    "pcb_color": "pcb_color",
    "priority": "priority",
    "schematic_color": "schematic_color",
    "track_width": "track_width",
    "via_diameter": "via_diameter",
    "via_drill": "via_drill",
    "wire_width": "wire_width",
}

_NETCLASS_DEFAULTS: dict[str, Any] = {
    "bus_width": 12,
    "clearance": 0.2,
    "diff_pair_gap": 0.25,
    "diff_pair_via_gap": 0.25,
    "diff_pair_width": 0.2,
    "line_style": 0,
    "microvia_diameter": 0.3,
    "microvia_drill": 0.1,
    "pcb_color": "rgba(0, 0, 0, 0.000)",
    "priority": 2147483647,
    "schematic_color": "rgba(0, 0, 0, 0.000)",
    "track_width": 0.2,
    "via_diameter": 0.6,
    "via_drill": 0.3,
    "wire_width": 6,
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
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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
        # Legacy KiCad 6 patterns stored inside class dict
        entry["patterns"] = list(cls.get("nets", []))
        # Numeric/string rule fields — include whatever is present
        for field_name in _NETCLASS_FIELD_MAP:
            if field_name in cls:
                entry[field_name] = cls[field_name]
        results.append(entry)

    # KiCad 9 patterns stored in netclass_patterns[] at net_settings level
    netclass_patterns: list[dict] = net_settings.get("netclass_patterns", [])
    for entry in results:
        cls_patterns = [
            p["pattern"]
            for p in netclass_patterns
            if p.get("netclass") == entry["name"] and "pattern" in p
        ]
        entry["patterns"].extend(cls_patterns)

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
        # Copy all fields from Default class as base; fall back to built-in defaults
        default_cls = next((c for c in classes if c.get("name") == "Default"), None)
        base = {
            k: v
            for k, v in (default_cls or _NETCLASS_DEFAULTS).items()
            if k not in ("name", "nets")
        }
        base["name"] = class_name
        target = base
        classes.append(target)

    changes: list[str] = []

    if rules:
        for field_name, value in rules.items():
            old = target.get(field_name)
            target[field_name] = value
            if old != value:
                changes.append(f"{field_name}: {old!r} -> {value!r}")

    if add_pattern is not None:
        patterns: list = net_settings.setdefault("netclass_patterns", [])
        exists = any(
            p.get("netclass") == class_name and p.get("pattern") == add_pattern
            for p in patterns
        )
        if not exists:
            patterns.append({"netclass": class_name, "pattern": add_pattern})
            changes.append(f"added pattern '{add_pattern}'")
        else:
            changes.append(f"pattern '{add_pattern}' already present")

    _save_project(data, path)

    action = "Created" if created else "Updated"
    changes_str = "; ".join(changes) if changes else "no rule changes"
    return f"{action} net class '{class_name}': {changes_str}"
