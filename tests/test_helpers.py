"""Integration tests for kicad_helpers.py functions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import kicad_helpers

FIXTURES = Path(__file__).parent / "fixtures"
SCH_FIXTURE = FIXTURES / "test_schematic.kicad_sch"
PRO_FIXTURE = FIXTURES / "test_project.kicad_pro"


@pytest.fixture()
def sch(tmp_path: Path) -> Path:
    """Copy schematic fixture to tmp_path for safe mutation."""
    dest = tmp_path / "test.kicad_sch"
    shutil.copy(SCH_FIXTURE, dest)
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
    assert props["Reference"] == "R1"
    assert props["Value"] == "10k"
    assert "Footprint" in props


def test_get_component_c1(sch: Path) -> None:
    props = kicad_helpers.get_component(str(sch), "C1")
    assert props["Value"] == "100nF"


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
    assert props["Value"] == "4k7"


def test_update_component_remove_property(sch: Path) -> None:
    # Datasheet exists in fixture; remove it
    result = kicad_helpers.update_component(str(sch), "R1", {"Datasheet": None})
    assert "removed" in result or "Datasheet" in result
    props = kicad_helpers.get_component(str(sch), "R1")
    assert "Datasheet" not in props


def test_update_component_set_dnp(sch: Path) -> None:
    result = kicad_helpers.update_component(str(sch), "C1", {"dnp": True})
    assert "dnp=True" in result


def test_update_component_missing_ref(sch: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        kicad_helpers.update_component(str(sch), "MISSING", {"Value": "x"})


def test_update_component_file_changed(sch: Path) -> None:
    mtime_before = sch.stat().st_mtime
    kicad_helpers.update_component(str(sch), "U1", {"Value": "ESP32"})
    # Read back to confirm persistence
    props = kicad_helpers.get_component(str(sch), "U1")
    assert props["Value"] == "ESP32"


# ---------------------------------------------------------------------------
# update_schematic_info
# ---------------------------------------------------------------------------


def test_update_schematic_info_title(sch: Path) -> None:
    result = kicad_helpers.update_schematic_info(str(sch), title="New Title")
    assert "title" in result.lower()
    # Verify by reloading
    from kiutils.schematic import Schematic
    s = Schematic.from_file(str(sch))
    assert s.titleBlock is not None
    assert s.titleBlock.title == "New Title"


def test_update_schematic_info_revision(sch: Path) -> None:
    result = kicad_helpers.update_schematic_info(str(sch), revision="2.1")
    assert "revision" in result.lower()
    from kiutils.schematic import Schematic
    s = Schematic.from_file(str(sch))
    assert s.titleBlock.revision == "2.1"


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
    from kiutils.schematic import Schematic
    s = Schematic.from_file(str(sch))
    texts = [lbl.text for lbl in s.labels]
    assert "SPI_CLK" in texts
    assert "SPI1_SCK" not in texts


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


def test_update_net_class_add_pattern(pro: Path) -> None:
    result = kicad_helpers.update_net_class(
        str(pro), "USB", add_pattern="USB_D?"
    )
    assert "USB_D?" in result
    # Verify persisted
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
