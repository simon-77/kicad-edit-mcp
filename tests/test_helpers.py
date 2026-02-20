"""Integration tests for kicad_helpers.py functions."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import kicad_helpers

FIXTURES = Path(__file__).parent / "fixtures"
SCH_FIXTURE = FIXTURES / "test_schematic.kicad_sch"
SCH_V9_FIXTURE = FIXTURES / "test_schematic_v9.kicad_sch"
SCH_MULTI_FIXTURE = FIXTURES / "test_multiunit.kicad_sch"
PRO_FIXTURE = FIXTURES / "test_project.kicad_pro"


@pytest.fixture()
def sch(tmp_path: Path) -> Path:
    """Copy schematic fixture to tmp_path for safe mutation."""
    dest = tmp_path / "test.kicad_sch"
    shutil.copy(SCH_FIXTURE, dest)
    return dest


@pytest.fixture()
def sch_v9(tmp_path: Path) -> Path:
    """Copy KiCad 9 schematic fixture to tmp_path for safe mutation."""
    dest = tmp_path / "test_v9.kicad_sch"
    shutil.copy(SCH_V9_FIXTURE, dest)
    return dest


@pytest.fixture()
def sch_multi(tmp_path: Path) -> Path:
    """Copy multi-unit schematic fixture to tmp_path for safe mutation."""
    dest = tmp_path / "test_multi.kicad_sch"
    shutil.copy(SCH_MULTI_FIXTURE, dest)
    return dest


@pytest.fixture()
def pro(tmp_path: Path) -> Path:
    """Copy project fixture to tmp_path for safe mutation."""
    dest = tmp_path / "test.kicad_pro"
    shutil.copy(PRO_FIXTURE, dest)
    return dest


# ---------------------------------------------------------------------------
# list_components
# ---------------------------------------------------------------------------


def test_list_components_returns_all(sch: Path) -> None:
    comps = kicad_helpers.list_components(str(sch))
    refs = [c["reference"] for c in comps]
    assert "R1" in refs
    assert "C1" in refs
    assert "U1" in refs
    assert len(comps) == 3


def test_list_components_filter_c(sch: Path) -> None:
    comps = kicad_helpers.list_components(str(sch), filter="C")
    assert len(comps) == 1
    assert comps[0]["reference"] == "C1"


def test_list_components_filter_no_match(sch: Path) -> None:
    comps = kicad_helpers.list_components(str(sch), filter="X")
    assert comps == []


def test_list_components_missing_file() -> None:
    with pytest.raises(ValueError, match="not found"):
        kicad_helpers.list_components("/nonexistent/path.kicad_sch")


# ---------------------------------------------------------------------------
# get_component
# ---------------------------------------------------------------------------


def test_get_component_r1(sch: Path) -> None:
    props = kicad_helpers.get_component(str(sch), "R1")
    assert props["Reference"]["value"] == "R1"
    assert props["Value"]["value"] == "10k"
    assert "Footprint" in props


def test_get_component_c1(sch: Path) -> None:
    props = kicad_helpers.get_component(str(sch), "C1")
    assert props["Value"]["value"] == "100nF"


def test_get_component_missing_raises(sch: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        kicad_helpers.get_component(str(sch), "MISSING")


# ---------------------------------------------------------------------------
# update_component
# ---------------------------------------------------------------------------


def test_update_component_set_value(sch: Path) -> None:
    result = kicad_helpers.update_component(str(sch), "R1", {"Value": "4k7"})
    assert "R1" in result
    # Verify file changed
    props = kicad_helpers.get_component(str(sch), "R1")
    assert props["Value"]["value"] == "4k7"


def test_update_component_remove_property(sch: Path) -> None:
    # Datasheet exists in fixture; remove it
    result = kicad_helpers.update_component(str(sch), "R1", {"Datasheet": None})
    assert "removed" in result or "Datasheet" in result
    props = kicad_helpers.get_component(str(sch), "R1")
    assert "Datasheet" not in props


def test_update_component_dnp_raises(sch: Path) -> None:
    """dnp key must raise ValueError — feature removed."""
    with pytest.raises(ValueError, match="dnp"):
        kicad_helpers.update_component(str(sch), "C1", {"dnp": True})


def test_update_component_missing_ref(sch: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        kicad_helpers.update_component(str(sch), "MISSING", {"Value": "x"})


def test_update_component_rejects_malformed_dict(sch: Path) -> None:
    with pytest.raises(ValueError, match="must have a 'value' key"):
        kicad_helpers.update_component(str(sch), "R1", {"Voltage": {"foo": "bar"}})


# ---------------------------------------------------------------------------
# Property visibility
# ---------------------------------------------------------------------------


def test_update_preserves_hidden_properties(sch: Path) -> None:
    """Changing Value must not alter the hidden state of Footprint."""
    kicad_helpers.update_component(str(sch), "R1", {"Value": "4k7"})
    props = kicad_helpers.get_component(str(sch), "R1")
    # Footprint was hidden in fixture — must remain hidden after update
    assert props["Footprint"]["visible"] is False


def test_new_property_defaults_hidden(sch: Path) -> None:
    """New custom properties are created with hide=True by default."""
    kicad_helpers.update_component(str(sch), "R1", {"Voltage": "3.3V"})
    props = kicad_helpers.get_component(str(sch), "R1")
    assert "Voltage" in props
    assert props["Voltage"]["value"] == "3.3V"
    assert props["Voltage"]["visible"] is False


def test_new_reference_value_default_visible(sch: Path) -> None:
    """Reference/Value properties added as new are visible by convention."""
    props = kicad_helpers.get_component(str(sch), "R1")
    assert props["Reference"]["visible"] is True
    assert props["Value"]["visible"] is True


def test_explicit_visibility_override(sch: Path) -> None:
    """Rich dict format with visible=True overrides default hidden behaviour."""
    kicad_helpers.update_component(
        str(sch), "R1", {"Voltage": {"value": "3.3V", "visible": True}}
    )
    props = kicad_helpers.get_component(str(sch), "R1")
    assert props["Voltage"]["value"] == "3.3V"
    assert props["Voltage"]["visible"] is True


def test_get_component_returns_visibility(sch: Path) -> None:
    """get_component returns {value, visible} dicts for every property."""
    props = kicad_helpers.get_component(str(sch), "R1")
    for key, entry in props.items():
        assert isinstance(entry, dict), f"{key} should be a dict"
        assert "value" in entry, f"{key} missing 'value'"
        assert "visible" in entry, f"{key} missing 'visible'"
    # Known visibilities from fixture
    assert props["Reference"]["visible"] is True
    assert props["Value"]["visible"] is True
    assert props["Footprint"]["visible"] is False
    assert props["Datasheet"]["visible"] is False


def test_update_component_file_changed(sch: Path) -> None:
    kicad_helpers.update_component(str(sch), "U1", {"Value": "ESP32"})
    # Read back to confirm persistence
    props = kicad_helpers.get_component(str(sch), "U1")
    assert props["Value"]["value"] == "ESP32"


# ---------------------------------------------------------------------------
# KiCad 9 regression tests — (hide yes) format preservation
# ---------------------------------------------------------------------------


def test_kicad9_get_component_visibility(sch_v9: Path) -> None:
    """KiCad 9 fixture: (hide yes) parsed correctly as visible=False."""
    props = kicad_helpers.get_component(str(sch_v9), "R1")
    assert props["Reference"]["visible"] is True
    assert props["Value"]["visible"] is True
    assert props["Footprint"]["visible"] is False
    assert props["Datasheet"]["visible"] is False


def test_kicad9_update_preserves_hidden(sch_v9: Path) -> None:
    """KiCad 9: updating Value must not make Footprint/Datasheet visible."""
    kicad_helpers.update_component(str(sch_v9), "R1", {"Value": "4k7"})
    props = kicad_helpers.get_component(str(sch_v9), "R1")
    assert props["Footprint"]["visible"] is False
    assert props["Datasheet"]["visible"] is False
    assert props["Value"]["value"] == "4k7"


def test_kicad9_roundtrip_format(sch_v9: Path) -> None:
    """KiCad 9: saved file must preserve (hide yes) tokens."""
    kicad_helpers.update_component(str(sch_v9), "R1", {"Value": "4k7"})
    raw = sch_v9.read_text()
    assert "(hide yes)" in raw


def test_kicad9_all_components_preserved(sch_v9: Path) -> None:
    """KiCad 9: all 3 components survive round-trip."""
    kicad_helpers.update_component(str(sch_v9), "C1", {"Value": "220nF"})
    comps = kicad_helpers.list_components(str(sch_v9))
    refs = [c["reference"] for c in comps]
    assert "R1" in refs
    assert "C1" in refs
    assert "U1" in refs
    assert len(comps) == 3


# ---------------------------------------------------------------------------
# update_schematic_info
# ---------------------------------------------------------------------------


def test_update_schematic_info_title(sch: Path) -> None:
    result = kicad_helpers.update_schematic_info(str(sch), title="New Title")
    assert "title" in result.lower()
    raw = sch.read_text()
    assert '"New Title"' in raw


def test_update_schematic_info_revision(sch: Path) -> None:
    result = kicad_helpers.update_schematic_info(str(sch), revision="2.1")
    assert "revision" in result.lower()
    raw = sch.read_text()
    assert '"2.1"' in raw


def test_update_schematic_info_no_args(sch: Path) -> None:
    result = kicad_helpers.update_schematic_info(str(sch))
    assert "no fields" in result.lower()


# ---------------------------------------------------------------------------
# rename_net
# ---------------------------------------------------------------------------


def test_rename_net_success(sch: Path) -> None:
    result = kicad_helpers.rename_net(str(sch), "SPI1_SCK", "SPI_CLK")
    assert "1" in result  # renamed 1 label
    assert "SPI_CLK" in result


def test_rename_net_verifies_in_file(sch: Path) -> None:
    kicad_helpers.rename_net(str(sch), "SPI1_SCK", "SPI_CLK")
    raw = sch.read_text()
    assert '"SPI_CLK"' in raw
    assert '"SPI1_SCK"' not in raw


def test_rename_net_no_match(sch: Path) -> None:
    result = kicad_helpers.rename_net(str(sch), "NONEXISTENT", "NEW")
    assert "nothing changed" in result.lower() or "0" in result


# ---------------------------------------------------------------------------
# list_net_classes
# ---------------------------------------------------------------------------


def test_list_net_classes_default(pro: Path) -> None:
    classes = kicad_helpers.list_net_classes(str(pro))
    assert len(classes) >= 1
    names = [c["name"] for c in classes]
    assert "Default" in names


def test_list_net_classes_default_has_rules(pro: Path) -> None:
    classes = kicad_helpers.list_net_classes(str(pro))
    default = next(c for c in classes if c["name"] == "Default")
    assert "track_width" in default
    assert "clearance" in default
    assert "via_diameter" in default


def test_list_net_classes_missing_file() -> None:
    with pytest.raises(ValueError, match="not found"):
        kicad_helpers.list_net_classes("/nonexistent/file.kicad_pro")


# ---------------------------------------------------------------------------
# update_net_class
# ---------------------------------------------------------------------------


def test_update_net_class_create_new(pro: Path) -> None:
    result = kicad_helpers.update_net_class(str(pro), "USB")
    assert "Created" in result
    assert "USB" in result
    # New class must have all 16 KiCad 9 required fields
    import json
    data = json.loads(pro.read_text())
    classes = data["net_settings"]["classes"]
    usb = next(c for c in classes if c["name"] == "USB")
    for field in (
        "bus_width", "clearance", "diff_pair_gap", "diff_pair_via_gap",
        "diff_pair_width", "line_style", "microvia_diameter", "microvia_drill",
        "pcb_color", "priority", "schematic_color", "track_width",
        "via_diameter", "via_drill", "wire_width",
    ):
        assert field in usb, f"Missing field: {field}"


def test_update_net_class_copies_default_fields(pro: Path) -> None:
    """New class inherits Default's field values (not hardcoded fallbacks)."""
    import json
    # Give Default non-standard track_width
    kicad_helpers.update_net_class(str(pro), "Default", rules={"track_width": 0.5})
    kicad_helpers.update_net_class(str(pro), "USB")
    data = json.loads(pro.read_text())
    classes = data["net_settings"]["classes"]
    usb = next(c for c in classes if c["name"] == "USB")
    assert usb["track_width"] == 0.5


def test_update_net_class_add_pattern(pro: Path) -> None:
    result = kicad_helpers.update_net_class(
        str(pro), "USB", add_pattern="USB_D?"
    )
    assert "USB_D?" in result
    # Verify pattern in netclass_patterns[], not inside class dict
    import json
    data = json.loads(pro.read_text())
    net_settings = data["net_settings"]
    patterns = net_settings.get("netclass_patterns", [])
    assert any(
        p.get("netclass") == "USB" and p.get("pattern") == "USB_D?"
        for p in patterns
    ), "Pattern not found in netclass_patterns"
    # list_net_classes should surface it
    classes = kicad_helpers.list_net_classes(str(pro))
    usb = next((c for c in classes if c["name"] == "USB"), None)
    assert usb is not None
    assert "USB_D?" in usb["patterns"]


def test_update_net_class_update_rules(pro: Path) -> None:
    result = kicad_helpers.update_net_class(
        str(pro), "Default", rules={"track_width": 0.5}
    )
    assert "Updated" in result
    classes = kicad_helpers.list_net_classes(str(pro))
    default = next(c for c in classes if c["name"] == "Default")
    assert default["track_width"] == 0.5


def test_update_net_class_duplicate_pattern_noop(pro: Path) -> None:
    # Add pattern twice — second should be no-op
    kicad_helpers.update_net_class(str(pro), "USB", add_pattern="USB_D?")
    result = kicad_helpers.update_net_class(
        str(pro), "USB", add_pattern="USB_D?"
    )
    assert "already present" in result
    # Confirm only one entry in netclass_patterns
    import json
    data = json.loads(pro.read_text())
    patterns = data["net_settings"].get("netclass_patterns", [])
    matches = [
        p for p in patterns
        if p.get("netclass") == "USB" and p.get("pattern") == "USB_D?"
    ]
    assert len(matches) == 1


def test_save_project_trailing_newline(pro: Path) -> None:
    """_save_project must write a trailing newline."""
    kicad_helpers.update_net_class(str(pro), "Default", rules={"track_width": 0.3})
    content = pro.read_bytes()
    assert content.endswith(b"\n"), "Project file must end with newline"


# ---------------------------------------------------------------------------
# lib_symbols preservation (regression for kicad-sch-api v0.5.6 bugs)
# ---------------------------------------------------------------------------


def test_lib_symbols_preserved_after_update(sch: Path) -> None:
    """lib_symbols must survive round-trip through update_component."""
    original = sch.read_text()
    assert '(symbol "Device:R"' in original

    kicad_helpers.update_component(str(sch), "R1", {"Value": "4k7"})

    saved = sch.read_text()
    assert '(symbol "Device:R"' in saved
    assert '(symbol "Device:C"' in saved
    assert '(symbol "Device:IC"' in saved


def test_lib_symbols_preserved_after_rename_net(sch: Path) -> None:
    """lib_symbols must survive round-trip through rename_net."""
    kicad_helpers.rename_net(str(sch), "SPI1_SCK", "SPI_CLK")
    saved = sch.read_text()
    assert '(symbol "Device:R"' in saved
    assert '(symbol "Device:C"' in saved
    assert '(symbol "Device:IC"' in saved


def test_lib_symbols_preserved_after_info_update(sch: Path) -> None:
    """lib_symbols must survive round-trip through update_schematic_info."""
    kicad_helpers.update_schematic_info(str(sch), title="New Title")
    saved = sch.read_text()
    assert '(symbol "Device:R"' in saved
    assert '(symbol "Device:C"' in saved
    assert '(symbol "Device:IC"' in saved


def test_kicad9_lib_symbols_preserved_after_update(sch_v9: Path) -> None:
    """KiCad 9: lib_symbols must survive round-trip through update_component."""
    kicad_helpers.update_component(str(sch_v9), "R1", {"Value": "4k7"})
    saved = sch_v9.read_text()
    assert '(symbol "Device:R"' in saved
    assert '(symbol "Device:C"' in saved
    assert '(symbol "Device:IC"' in saved


# ---------------------------------------------------------------------------
# Multi-unit symbol support
# ---------------------------------------------------------------------------


def test_get_component_multiunit_reports_units(sch_multi: Path) -> None:
    """get_component on a 3-unit symbol reports _units metadata."""
    props = kicad_helpers.get_component(str(sch_multi), "U2")
    assert props["Value"]["value"] == "LM358"
    assert props["_units"]["value"] == "3"


def test_get_component_singleunit_no_units_key(sch_multi: Path) -> None:
    """Single-unit symbols should not have _units metadata."""
    props = kicad_helpers.get_component(str(sch_multi), "R1")
    assert "_units" not in props


def test_update_component_multiunit_updates_all(sch_multi: Path) -> None:
    """update_component must update all units of a multi-unit symbol."""
    result = kicad_helpers.update_component(
        str(sch_multi), "U2",
        {"Datasheet": "https://example.com/datasheet/lm358-rev2"},
    )
    assert "3 units" in result

    # Re-parse and verify all 3 units have the new datasheet
    from sexp_surgery import SexpDocument
    doc = SexpDocument.load(Path(sch_multi))
    units = doc.find_symbol_units("U2")
    assert len(units) == 3
    for unit in units:
        ds = doc.get_property(unit, "Datasheet")
        assert ds is not None
        text = doc.text[ds.start:ds.end]
        assert "lm358-rev2" in text


def test_update_component_multiunit_add_new_prop(sch_multi: Path) -> None:
    """Adding a new property to a multi-unit symbol should add it to all units."""
    kicad_helpers.update_component(
        str(sch_multi), "U2",
        {"MPN": "LM358ADR"},
    )

    from sexp_surgery import SexpDocument
    doc = SexpDocument.load(Path(sch_multi))
    units = doc.find_symbol_units("U2")
    assert len(units) == 3
    for unit in units:
        mpn = doc.get_property(unit, "MPN")
        assert mpn is not None, f"MPN missing on unit at offset {unit.start}"
        text = doc.text[mpn.start:mpn.end]
        assert "LM358ADR" in text


def test_update_component_multiunit_remove_prop(sch_multi: Path) -> None:
    """Removing a property from a multi-unit symbol should remove from all units."""
    kicad_helpers.update_component(
        str(sch_multi), "U2", {"Datasheet": None},
    )

    from sexp_surgery import SexpDocument
    doc = SexpDocument.load(Path(sch_multi))
    units = doc.find_symbol_units("U2")
    assert len(units) == 3
    for unit in units:
        ds = doc.get_property(unit, "Datasheet")
        assert ds is None, f"Datasheet still present on unit at offset {unit.start}"


def test_update_component_singleunit_unchanged(sch_multi: Path) -> None:
    """Single-unit symbols should still work normally."""
    result = kicad_helpers.update_component(
        str(sch_multi), "R1", {"Value": "4k7"},
    )
    assert "units" not in result
    props = kicad_helpers.get_component(str(sch_multi), "R1")
    assert props["Value"]["value"] == "4k7"


def test_units_metadata_not_written_on_roundtrip(sch_multi: Path) -> None:
    """_units key from get_component must not be written as a real property."""
    props = kicad_helpers.get_component(str(sch_multi), "U2")
    assert "_units" in props
    # Round-trip: pass entire props dict back to update_component
    kicad_helpers.update_component(str(sch_multi), "U2", props)

    from sexp_surgery import SexpDocument
    doc = SexpDocument.load(Path(sch_multi))
    for unit in doc.find_symbol_units("U2"):
        assert doc.get_property(unit, "_units") is None


def test_update_component_multiunit_multiple_props(sch_multi: Path) -> None:
    """Multiple property changes in one call across all units."""
    kicad_helpers.update_component(
        str(sch_multi), "U2",
        {"Value": "LM358B", "MPN": "LM358ADR"},
    )

    from sexp_surgery import SexpDocument
    doc = SexpDocument.load(Path(sch_multi))
    units = doc.find_symbol_units("U2")
    assert len(units) == 3
    for unit in units:
        val = doc.get_property(unit, "Value")
        assert val is not None
        assert "LM358B" in doc.text[val.start:val.end]
        mpn = doc.get_property(unit, "MPN")
        assert mpn is not None
        assert "LM358ADR" in doc.text[mpn.start:mpn.end]
