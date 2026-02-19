"""Unit tests for sexp_surgery.py core engine."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sexp_surgery import SexpDocument, SexpSpan

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_V6 = FIXTURES / "test_schematic.kicad_sch"
FIXTURE_V9 = FIXTURES / "test_schematic_v9.kicad_sch"


@pytest.fixture(params=["v6", "v9"])
def fixture_path(request, tmp_path):
    """Parametrize over both KiCad 6 and 9 fixtures, copied to tmp so we can modify."""
    src = FIXTURE_V6 if request.param == "v6" else FIXTURE_V9
    dest = tmp_path / src.name
    shutil.copy(src, dest)
    return dest


@pytest.fixture
def doc_v6(tmp_path):
    dest = tmp_path / FIXTURE_V6.name
    shutil.copy(FIXTURE_V6, dest)
    return SexpDocument.load(dest), dest


@pytest.fixture
def doc_v9(tmp_path):
    dest = tmp_path / FIXTURE_V9.name
    shutil.copy(FIXTURE_V9, dest)
    return SexpDocument.load(dest), dest


# ---------------------------------------------------------------------------
# 1. test_load_parses_file
# ---------------------------------------------------------------------------

def test_load_parses_file_v6():
    doc = SexpDocument.load(FIXTURE_V6)
    assert doc.tree is not None
    assert len(doc.tree) > 1
    assert str(doc.tree[0]) == "kicad_sch"


def test_load_parses_file_v9():
    doc = SexpDocument.load(FIXTURE_V9)
    assert doc.tree is not None
    assert str(doc.tree[0]) == "kicad_sch"


# ---------------------------------------------------------------------------
# 2. test_span_tracking_accuracy
# ---------------------------------------------------------------------------

def test_span_tracking_accuracy(fixture_path):
    doc = SexpDocument.load(fixture_path)
    text = doc.text
    for node_id, span in doc.spans.items():
        excerpt = text[span.start:span.end]
        assert excerpt.startswith("("), (
            f"Span start={span.start} end={span.end} does not start with '(': {excerpt[:30]!r}"
        )
        assert excerpt.endswith(")"), (
            f"Span start={span.start} end={span.end} does not end with ')': {excerpt[-30:]!r}"
        )


# ---------------------------------------------------------------------------
# 3. test_find_all_symbols — 3 schematic symbols per fixture
# ---------------------------------------------------------------------------

def test_find_all_symbols(fixture_path):
    doc = SexpDocument.load(fixture_path)
    # find_all("symbol") returns ALL symbol nodes including lib_symbols children
    # find_symbol checks for lib_id to filter schematic instances
    symbols = [
        s for s in doc.find_all("symbol")
        if any(
            isinstance(c, list) and c and str(c[0]) == "lib_id"
            for c in s.node[1:]
        )
    ]
    assert len(symbols) == 3, f"Expected 3 schematic symbols, got {len(symbols)}"


# ---------------------------------------------------------------------------
# 4. test_find_symbol_by_reference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref", ["R1", "C1", "U1"])
def test_find_symbol_by_reference(fixture_path, ref):
    doc = SexpDocument.load(fixture_path)
    span = doc.find_symbol(ref)
    assert span is not None, f"Symbol {ref} not found"
    assert isinstance(span, SexpSpan)


# ---------------------------------------------------------------------------
# 5. test_find_symbol_missing
# ---------------------------------------------------------------------------

def test_find_symbol_missing(fixture_path):
    doc = SexpDocument.load(fixture_path)
    span = doc.find_symbol("Z99")
    assert span is None


# ---------------------------------------------------------------------------
# 6. test_find_labels
# ---------------------------------------------------------------------------

def test_find_labels(fixture_path):
    doc = SexpDocument.load(fixture_path)
    labels = doc.find_labels("label")
    assert len(labels) == 1
    assert doc.text[labels[0].start:labels[0].start + 6] == "(label"


def test_find_labels_by_text(fixture_path):
    doc = SexpDocument.load(fixture_path)
    labels = doc.find_labels("label", text="SPI1_SCK")
    assert len(labels) == 1

    labels_missing = doc.find_labels("label", text="NONEXISTENT")
    assert len(labels_missing) == 0


# ---------------------------------------------------------------------------
# 7. test_find_title_block
# ---------------------------------------------------------------------------

def test_find_title_block(fixture_path):
    doc = SexpDocument.load(fixture_path)
    tb = doc.find_title_block()
    assert tb is not None
    assert doc.text[tb.start:tb.start + 12] == "(title_block"


# ---------------------------------------------------------------------------
# 8. test_get_property
# ---------------------------------------------------------------------------

def test_get_property(fixture_path):
    doc = SexpDocument.load(fixture_path)
    sym = doc.find_symbol("R1")
    assert sym is not None
    prop = doc.get_property(sym, "Value")
    assert prop is not None
    assert str(prop.node[0]) == "property"


def test_get_property_missing(fixture_path):
    doc = SexpDocument.load(fixture_path)
    sym = doc.find_symbol("R1")
    assert sym is not None
    prop = doc.get_property(sym, "NonExistentProp")
    assert prop is None


# ---------------------------------------------------------------------------
# 9. test_get_property_value_span
# ---------------------------------------------------------------------------

def test_get_property_value_span(fixture_path):
    doc = SexpDocument.load(fixture_path)
    sym = doc.find_symbol("R1")
    assert sym is not None
    prop = doc.get_property(sym, "Value")
    assert prop is not None
    result = doc.get_property_value_span(prop)
    assert result is not None
    start, end, value = result
    assert value == "10k"
    assert doc.text[start:end] == '"10k"'


def test_get_property_value_span_capacitor(fixture_path):
    doc = SexpDocument.load(fixture_path)
    sym = doc.find_symbol("C1")
    assert sym is not None
    prop = doc.get_property(sym, "Value")
    assert prop is not None
    start, end, value = doc.get_property_value_span(prop)
    assert value == "100nF"
    assert doc.text[start:end] == '"100nF"'


# ---------------------------------------------------------------------------
# 10. test_replace_span_single
# ---------------------------------------------------------------------------

def test_replace_span_single(doc_v6):
    doc, path = doc_v6
    tb = doc.find_title_block()
    assert tb is not None
    original_text = doc.text[tb.start:tb.end]
    new_text = original_text.replace('"Test Schematic"', '"New Title"')
    doc.replace_span(tb, new_text)
    doc.save(path)
    result = path.read_text(encoding="utf-8")
    assert '"New Title"' in result
    assert '"Test Schematic"' not in result


# ---------------------------------------------------------------------------
# 11. test_replace_multiple_back_to_front
# ---------------------------------------------------------------------------

def test_replace_multiple_back_to_front(tmp_path):
    """Multiple replacements must all be applied correctly."""
    src = tmp_path / "test.kicad_sch"
    shutil.copy(FIXTURE_V6, src)
    doc = SexpDocument.load(src)

    r1_sym = doc.find_symbol("R1")
    c1_sym = doc.find_symbol("C1")
    assert r1_sym is not None
    assert c1_sym is not None

    r1_val = doc.get_property(r1_sym, "Value")
    c1_val = doc.get_property(c1_sym, "Value")
    assert r1_val is not None
    assert c1_val is not None

    r1_vs = doc.get_property_value_span(r1_val)
    c1_vs = doc.get_property_value_span(c1_val)
    assert r1_vs is not None
    assert c1_vs is not None

    doc.replace_bytes(r1_vs[0], r1_vs[1], '"4k7"')
    doc.replace_bytes(c1_vs[0], c1_vs[1], '"220nF"')
    doc.save(src)

    result = src.read_text(encoding="utf-8")
    assert '"4k7"' in result
    assert '"220nF"' in result
    assert '"10k"' not in result
    assert '"100nF"' not in result


# ---------------------------------------------------------------------------
# 12. test_roundtrip_no_change
# ---------------------------------------------------------------------------

def test_roundtrip_no_change(fixture_path):
    """Load and save without edits — file must be byte-identical."""
    original = fixture_path.read_text(encoding="utf-8")
    doc = SexpDocument.load(fixture_path)
    doc.save(fixture_path)
    result = fixture_path.read_text(encoding="utf-8")
    assert result == original


# ---------------------------------------------------------------------------
# 13. test_surgical_value_replace
# ---------------------------------------------------------------------------

def test_surgical_value_replace(fixture_path):
    """Change property value, only value bytes change."""
    import difflib
    original = fixture_path.read_text(encoding="utf-8")

    doc = SexpDocument.load(fixture_path)
    sym = doc.find_symbol("C1")
    prop = doc.get_property(sym, "Value")
    vs = doc.get_property_value_span(prop)
    assert vs is not None

    doc.replace_bytes(vs[0], vs[1], '"220nF"')
    doc.save(fixture_path)
    modified = fixture_path.read_text(encoding="utf-8")

    diff = list(difflib.unified_diff(
        original.splitlines(), modified.splitlines(), lineterm=""
    ))
    changed_lines = [l for l in diff if l.startswith("+") or l.startswith("-")]
    # Only lines containing the old or new value should change
    for line in changed_lines:
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        assert "100nF" in line or "220nF" in line, (
            f"Unexpected changed line: {line!r}"
        )


# ---------------------------------------------------------------------------
# 14. test_delete_span
# ---------------------------------------------------------------------------

def test_delete_span(tmp_path):
    src = tmp_path / "test.kicad_sch"
    shutil.copy(FIXTURE_V6, src)
    doc = SexpDocument.load(src)

    labels = doc.find_labels("label")
    assert len(labels) == 1
    doc.delete_span(labels[0])
    doc.save(src)

    result = src.read_text(encoding="utf-8")
    assert "(label" not in result


# ---------------------------------------------------------------------------
# 15. test_kicad6_and_v9_both_work (parametrized via fixture_path)
# ---------------------------------------------------------------------------

def test_kicad6_and_v9_both_work(fixture_path):
    """Smoke test: basic operations work on both fixture versions."""
    doc = SexpDocument.load(fixture_path)
    assert doc.find_symbol("R1") is not None
    assert doc.find_symbol("C1") is not None
    assert doc.find_symbol("U1") is not None
    assert doc.find_title_block() is not None
    assert len(doc.find_labels("label")) == 1


# ---------------------------------------------------------------------------
# 16. test_is_property_hidden
# ---------------------------------------------------------------------------

def test_is_property_hidden_kicad6():
    """KiCad 6: hidden property uses bare `hide` symbol."""
    doc = SexpDocument.load(FIXTURE_V6)
    sym = doc.find_symbol("R1")
    assert sym is not None

    # Footprint and Datasheet are hidden, Reference and Value are not
    ref_prop = doc.get_property(sym, "Reference")
    val_prop = doc.get_property(sym, "Value")
    fp_prop = doc.get_property(sym, "Footprint")
    ds_prop = doc.get_property(sym, "Datasheet")

    assert ref_prop is not None
    assert val_prop is not None
    assert fp_prop is not None
    assert ds_prop is not None

    assert not doc.is_property_hidden(ref_prop), "Reference should not be hidden"
    assert not doc.is_property_hidden(val_prop), "Value should not be hidden"
    assert doc.is_property_hidden(fp_prop), "Footprint should be hidden"
    assert doc.is_property_hidden(ds_prop), "Datasheet should be hidden"


def test_is_property_hidden_kicad9():
    """KiCad 9: hidden property uses `(hide yes)` form."""
    doc = SexpDocument.load(FIXTURE_V9)
    sym = doc.find_symbol("R1")
    assert sym is not None

    ref_prop = doc.get_property(sym, "Reference")
    val_prop = doc.get_property(sym, "Value")
    fp_prop = doc.get_property(sym, "Footprint")
    ds_prop = doc.get_property(sym, "Datasheet")

    assert not doc.is_property_hidden(ref_prop), "Reference should not be hidden"
    assert not doc.is_property_hidden(val_prop), "Value should not be hidden"
    assert doc.is_property_hidden(fp_prop), "Footprint should be hidden"
    assert doc.is_property_hidden(ds_prop), "Datasheet should be hidden"


# ---------------------------------------------------------------------------
# Additional edge case: insert_before_end
# ---------------------------------------------------------------------------

def test_insert_before_end(tmp_path):
    src = tmp_path / "test.kicad_sch"
    shutil.copy(FIXTURE_V6, src)
    doc = SexpDocument.load(src)

    tb = doc.find_title_block()
    assert tb is not None
    doc.insert_before_end(tb, '\n    (comment 1 "hello")')
    doc.save(src)

    result = src.read_text(encoding="utf-8")
    assert '(comment 1 "hello")' in result
    # The title_block closing paren should still be there
    assert "(title_block" in result
