"""Preservation tests — verify that the sexp surgery engine doesn't cause
the data-loss bugs documented in SEXP_SURGERY_SPEC.md.

Uses KiCad demo fixtures (IO.kicad_sch, pic_programmer.kicad_sch) which
contain mirror flags, dnp, global_labels, hierarchical_labels, diverse
justify entries, and fields_autoplaced.
"""
from __future__ import annotations

import difflib
import shutil
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import kicad_helpers
from sexp_surgery import SexpDocument

FIXTURES = Path(__file__).parent / "fixtures"
IO_FIXTURE = FIXTURES / "IO.kicad_sch"
PIC_FIXTURE = FIXTURES / "pic_programmer.kicad_sch"
V6_FIXTURE = FIXTURES / "test_schematic.kicad_sch"
V9_FIXTURE = FIXTURES / "test_schematic_v9.kicad_sch"


@pytest.fixture
def io_sch(tmp_path: Path) -> Path:
    dest = tmp_path / "IO.kicad_sch"
    shutil.copy(IO_FIXTURE, dest)
    return dest


@pytest.fixture
def pic_sch(tmp_path: Path) -> Path:
    dest = tmp_path / "pic_programmer.kicad_sch"
    shutil.copy(PIC_FIXTURE, dest)
    return dest


@pytest.fixture
def v6_sch(tmp_path: Path) -> Path:
    dest = tmp_path / "test.kicad_sch"
    shutil.copy(V6_FIXTURE, dest)
    return dest


@pytest.fixture
def v9_sch(tmp_path: Path) -> Path:
    dest = tmp_path / "test_v9.kicad_sch"
    shutil.copy(V9_FIXTURE, dest)
    return dest


# ---------------------------------------------------------------------------
# Bug #1: Mirror flags preserved
# ---------------------------------------------------------------------------


def test_mirror_flags_preserved_after_edit(io_sch: Path) -> None:
    """Mirror flags (mirror x) / (mirror y) must survive any property edit."""
    original = io_sch.read_text()
    mirror_count_before = original.count("(mirror")

    # Find a component to edit
    comps = kicad_helpers.list_components(str(io_sch))
    assert len(comps) > 0
    ref = comps[0]["reference"]
    kicad_helpers.update_component(str(io_sch), ref, {"Value": "EDITED_VALUE"})

    modified = io_sch.read_text()
    mirror_count_after = modified.count("(mirror")
    assert mirror_count_after == mirror_count_before, (
        f"Mirror flags changed: {mirror_count_before} -> {mirror_count_after}"
    )


# ---------------------------------------------------------------------------
# Bug #2: DNP flags preserved
# ---------------------------------------------------------------------------


def test_dnp_flags_preserved_after_edit(io_sch: Path) -> None:
    """DNP flags must not be reset to (dnp no)."""
    original = io_sch.read_text()
    dnp_yes_before = original.count("(dnp yes)")
    dnp_no_before = original.count("(dnp no)")

    comps = kicad_helpers.list_components(str(io_sch))
    ref = comps[0]["reference"]
    kicad_helpers.update_component(str(io_sch), ref, {"Value": "EDITED"})

    modified = io_sch.read_text()
    dnp_yes_after = modified.count("(dnp yes)")
    dnp_no_after = modified.count("(dnp no)")
    assert dnp_yes_after == dnp_yes_before, (
        f"(dnp yes) count changed: {dnp_yes_before} -> {dnp_yes_after}"
    )
    assert dnp_no_after == dnp_no_before, (
        f"(dnp no) count changed: {dnp_no_before} -> {dnp_no_after}"
    )


# ---------------------------------------------------------------------------
# Bug #3: Global labels preserved
# ---------------------------------------------------------------------------


def test_global_labels_preserved_after_edit(pic_sch: Path) -> None:
    """Global labels (global_label ...) must not be deleted on edit."""
    original = pic_sch.read_text()
    gl_count_before = original.count("(global_label")

    comps = kicad_helpers.list_components(str(pic_sch))
    assert len(comps) > 0
    ref = comps[0]["reference"]
    kicad_helpers.update_component(str(pic_sch), ref, {"Value": "EDITED"})

    modified = pic_sch.read_text()
    gl_count_after = modified.count("(global_label")
    assert gl_count_after == gl_count_before, (
        f"Global labels changed: {gl_count_before} -> {gl_count_after}"
    )


def test_global_label_rename(pic_sch: Path) -> None:
    """rename_net must find and rename global_label nodes."""
    doc = SexpDocument.load(pic_sch)
    glabels = doc.find_labels("global_label")
    assert len(glabels) > 0
    # Get first global label text
    first_text = doc.text[glabels[0].start:glabels[0].end]
    # Extract the label text from the first quoted string
    import re
    m = re.search(r'"([^"]+)"', first_text)
    assert m, "Could not find label text"
    old_name = m.group(1)

    result = kicad_helpers.rename_net(str(pic_sch), old_name, "RENAMED_NET")
    assert "Renamed" in result
    assert "RENAMED_NET" in result

    # Verify in file
    modified = pic_sch.read_text()
    assert f'"{old_name}"' not in modified or old_name == "RENAMED_NET"
    assert '"RENAMED_NET"' in modified


# ---------------------------------------------------------------------------
# Bug #5: String escaping preserved
# ---------------------------------------------------------------------------


def test_string_escaping_not_corrupted(v6_sch: Path) -> None:
    """Quoted strings must not be double-escaped on round-trip."""
    original = v6_sch.read_text()
    kicad_helpers.update_component(str(v6_sch), "R1", {"Value": "10k"})
    modified = v6_sch.read_text()
    # No double-escaping should appear
    assert '\\\\"' not in modified
    assert "\\\\n" not in modified


# ---------------------------------------------------------------------------
# Bug #6: Text justification preserved
# ---------------------------------------------------------------------------


def test_justify_entries_preserved(io_sch: Path) -> None:
    """All justify variants must survive any edit."""
    original = io_sch.read_text()
    justify_count_before = original.count("(justify")

    comps = kicad_helpers.list_components(str(io_sch))
    ref = comps[0]["reference"]
    kicad_helpers.update_component(str(io_sch), ref, {"Value": "EDITED"})

    modified = io_sch.read_text()
    justify_count_after = modified.count("(justify")
    assert justify_count_after == justify_count_before, (
        f"Justify entries changed: {justify_count_before} -> {justify_count_after}"
    )


# ---------------------------------------------------------------------------
# Bug #7: fields_autoplaced preserved
# ---------------------------------------------------------------------------


def test_fields_autoplaced_preserved(io_sch: Path) -> None:
    """fields_autoplaced value must not change."""
    original = io_sch.read_text()
    fa_yes_before = original.count("(fields_autoplaced yes)")
    fa_no_before = original.count("(fields_autoplaced no)")

    comps = kicad_helpers.list_components(str(io_sch))
    ref = comps[0]["reference"]
    kicad_helpers.update_component(str(io_sch), ref, {"Value": "EDITED"})

    modified = io_sch.read_text()
    fa_yes_after = modified.count("(fields_autoplaced yes)")
    fa_no_after = modified.count("(fields_autoplaced no)")
    assert fa_yes_after == fa_yes_before
    assert fa_no_after == fa_no_before


# ---------------------------------------------------------------------------
# Round-trip diff tests
# ---------------------------------------------------------------------------


def test_roundtrip_no_unintended_changes_v6(v6_sch: Path) -> None:
    """Modifying one property must not change any other byte in the file."""
    original = v6_sch.read_text()
    kicad_helpers.update_component(str(v6_sch), "R1", {"Value": "4k7"})
    modified = v6_sch.read_text()

    diff = list(difflib.unified_diff(
        original.splitlines(), modified.splitlines(), lineterm=""
    ))
    changed_lines = [l for l in diff if l.startswith("+") or l.startswith("-")]
    for line in changed_lines:
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        assert "10k" in line or "4k7" in line, f"Unexpected changed line: {line!r}"


def test_roundtrip_no_unintended_changes_v9(v9_sch: Path) -> None:
    """KiCad 9: same diff test."""
    original = v9_sch.read_text()
    kicad_helpers.update_component(str(v9_sch), "R1", {"Value": "4k7"})
    modified = v9_sch.read_text()

    diff = list(difflib.unified_diff(
        original.splitlines(), modified.splitlines(), lineterm=""
    ))
    changed_lines = [l for l in diff if l.startswith("+") or l.startswith("-")]
    for line in changed_lines:
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        assert "10k" in line or "4k7" in line, f"Unexpected changed line: {line!r}"


def test_roundtrip_demo_file_minimal_diff(io_sch: Path) -> None:
    """Editing a property in a large real-world schematic should only change that value."""
    original = io_sch.read_text()
    comps = kicad_helpers.list_components(str(io_sch))
    assert len(comps) > 0
    ref = comps[0]["reference"]
    old_value = comps[0]["value"]

    kicad_helpers.update_component(str(io_sch), ref, {"Value": "ROUNDTRIP_TEST"})
    modified = io_sch.read_text()

    diff = list(difflib.unified_diff(
        original.splitlines(), modified.splitlines(), lineterm=""
    ))
    changed_lines = [l for l in diff
                     if (l.startswith("+") or l.startswith("-"))
                     and not l.startswith("--- ") and not l.startswith("+++ ")]
    for line in changed_lines:
        assert old_value in line or "ROUNDTRIP_TEST" in line, (
            f"Unexpected changed line: {line!r}"
        )


# ---------------------------------------------------------------------------
# Hierarchical labels preserved
# ---------------------------------------------------------------------------


def test_hierarchical_labels_preserved(io_sch: Path) -> None:
    """Hierarchical labels must survive any edit."""
    original = io_sch.read_text()
    hl_count_before = original.count("(hierarchical_label")

    comps = kicad_helpers.list_components(str(io_sch))
    ref = comps[0]["reference"]
    kicad_helpers.update_component(str(io_sch), ref, {"Value": "EDITED"})

    modified = io_sch.read_text()
    hl_count_after = modified.count("(hierarchical_label")
    assert hl_count_after == hl_count_before


# ---------------------------------------------------------------------------
# Large file: all components survive round-trip
# ---------------------------------------------------------------------------


def test_all_components_survive_edit_io(io_sch: Path) -> None:
    """All 42 components in IO.kicad_sch must survive a single edit."""
    comps_before = kicad_helpers.list_components(str(io_sch))
    refs_before = sorted(c["reference"] for c in comps_before)

    ref = comps_before[0]["reference"]
    kicad_helpers.update_component(str(io_sch), ref, {"Value": "EDITED"})

    comps_after = kicad_helpers.list_components(str(io_sch))
    refs_after = sorted(c["reference"] for c in comps_after)
    assert refs_before == refs_after


def test_all_components_survive_edit_pic(pic_sch: Path) -> None:
    """All 102 components in pic_programmer.kicad_sch must survive a single edit."""
    comps_before = kicad_helpers.list_components(str(pic_sch))
    refs_before = sorted(c["reference"] for c in comps_before)

    ref = comps_before[0]["reference"]
    kicad_helpers.update_component(str(pic_sch), ref, {"Value": "EDITED"})

    comps_after = kicad_helpers.list_components(str(pic_sch))
    refs_after = sorted(c["reference"] for c in comps_after)
    assert refs_before == refs_after
