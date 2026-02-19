# S-Expression Surgery Engine: Implementation Specification

> **Purpose**: Replace `kicad-sch-api` with a surgical s-expression editing layer built on `sexpdata`. Zero data loss by design — only modified subtrees are rewritten; everything else stays byte-identical.
>
> **Date**: 2026-02-19
> **Status**: Approved for implementation
> **Breaking**: Yes — non-backwards-compatible rewrite of `kicad_helpers.py`

---

## 1. Problem Statement

### 1.1 Current Architecture

The MCP server uses `kicad-sch-api` v0.5.5 to parse `.kicad_sch` files into a typed Python model, modify properties, and re-serialize the entire file. This causes **silent data loss** because the library drops any s-expression node it doesn't understand.

### 1.2 Bugs Discovered (2026-02-19 bug report)

| # | Bug | Root Cause | Severity |
|---|-----|-----------|----------|
| 1 | Mirror flags `(mirror x)` `(mirror y)` dropped | `symbol_parser.py` never parses mirror | Critical |
| 2 | DNP flags reset to `(dnp no)` | `symbol_parser.py:395` hardcodes `(dnp no)` | Critical |
| 3 | Global labels `(global_label ...)` deleted | No `global_label` support in `label_parser.py` | Critical |
| 4 | Intersheetrefs properties lost | Tied to global labels; not parsed | Minor |
| 5 | String escaping corrupted (double-escaped) | ExactFormatter quoting bug | Medium |
| 6 | Text justification `(justify ...)` partially lost | Incomplete justify variant handling | Medium |
| 7 | `fields_autoplaced` changed from `yes` to `no` | Round-trip not preserving value | Low-Medium |

**Root cause**: kicad-sch-api rebuilds the file from an incomplete internal model. Any s-expression node the parser doesn't know about is silently dropped on write.

### 1.3 Why Patching Won't Work

We already have 3 workarounds in `kicad_helpers.py` (lib_symbols monkey-patch, hidden property sync, `__sexp_` preservation). Each KiCad release adds new fields the library will drop. This is whack-a-mole — the architecture is fundamentally lossy.

---

## 2. Decision Record

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Drop kicad-sch-api? | **Yes, entirely** | Architecturally unfixable; 3 critical bugs are in the parser itself |
| Alternative library? | **None — build on sexpdata** | kicad-skip has right idea but is unmaintained, has write bugs, bad formatting. kiutils is dormant. sexpdata is already a dependency. |
| KiCad IPC API? | **Not viable** | Schematic support not ready: no Python bindings, empty protobuf defs, TODOs in C++ handler. PCB-only in KiCad 9.0 |
| Formatting approach? | **Surgical text patching** | Only reformat modified subtrees. Untouched text stays byte-identical. No need to replicate full Prettify algorithm |
| KiCad-identical formatting? | **For modified subtrees only** | Achievable for property/label/title_block nodes. Full-file formatting not needed with surgical approach |
| Backwards compatible? | **No** | Clean rewrite. Same MCP tool API but new internals |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────┐
│  MCP Tools (server.py — unchanged API)          │
│  list_components, get_component, update_component│
│  update_schematic_info, rename_net, etc.         │
├─────────────────────────────────────────────────┤
│  kicad_helpers.py (rewritten)                    │
│  - High-level functions using SexpSurgery        │
│  - Component/label/title_block operations        │
├─────────────────────────────────────────────────┤
│  sexp_surgery.py (NEW — core engine)             │
│  - Parse .kicad_sch with sexpdata                │
│  - Track byte spans of each s-expression node    │
│  - Find nodes by type + attribute queries        │
│  - Replace subtrees surgically in original text  │
│  - Format modified subtrees in KiCad style       │
├─────────────────────────────────────────────────┤
│  sexpdata (existing dependency, unchanged)       │
└─────────────────────────────────────────────────┘
```

### 3.1 Key Design Principle

**The original file text is the source of truth.** We parse it to build an index, but we write back by splicing formatted subtrees into the original text at exact byte positions. Untouched content is never re-serialized.

---

## 4. Core Module: `sexp_surgery.py`

### 4.1 Data Structures

```python
@dataclass
class SexpSpan:
    """Byte span of an s-expression in the original file text."""
    start: int          # byte offset of opening '('
    end: int            # byte offset after closing ')'
    node: list          # parsed sexpdata list
    depth: int          # nesting depth (0 = top-level children of kicad_sch)
    parent_index: int   # index within parent's children (for ordering)

class SexpDocument:
    """Parsed .kicad_sch with byte-span tracking."""
    text: str                       # original file content
    tree: list                      # sexpdata.loads() result
    spans: dict[int, SexpSpan]      # id(node) → SexpSpan mapping
```

### 4.2 Core Operations

```python
class SexpDocument:
    @classmethod
    def load(cls, path: Path) -> "SexpDocument":
        """Parse file, build span index."""
        ...

    def find_all(self, node_type: str) -> list[SexpSpan]:
        """Find all top-level children of given type (e.g. 'symbol', 'wire', 'label')."""
        ...

    def find_symbol(self, reference: str) -> SexpSpan | None:
        """Find symbol node by Reference property value."""
        ...

    def find_labels(self, label_type: str, text: str | None = None) -> list[SexpSpan]:
        """Find label/global_label/hierarchical_label nodes, optionally filtered by text."""
        ...

    def find_title_block(self) -> SexpSpan | None:
        """Find the title_block node."""
        ...

    def get_property(self, symbol_span: SexpSpan, prop_name: str) -> SexpSpan | None:
        """Find a property node within a symbol by name."""
        ...

    def replace_span(self, span: SexpSpan, new_text: str) -> None:
        """Queue a replacement: the byte range [span.start, span.end) will be replaced with new_text."""
        ...

    def insert_before_end(self, parent_span: SexpSpan, new_text: str) -> None:
        """Queue insertion of new_text before parent's closing paren."""
        ...

    def delete_span(self, span: SexpSpan) -> None:
        """Queue deletion of a span (including surrounding whitespace)."""
        ...

    def save(self, path: Path) -> None:
        """Apply all queued replacements and write to disk. Replacements are applied back-to-front to preserve byte offsets."""
        ...
```

### 4.3 Byte-Span Tracking

The critical implementation detail. After `sexpdata.loads()` gives us the tree, we need to know *where* each node lives in the original text. Two approaches:

**Option A — Custom tokenizer pass**: Walk the original text character-by-character tracking parenthesis depth. Record `(start, end)` for each top-level form. Then correlate with sexpdata's parsed tree by matching structure.

**Option B — Modified sexpdata**: Fork or monkey-patch sexpdata to record byte offsets during parsing. More precise but couples us to sexpdata internals.

**Recommended: Option A** — A secondary pass that matches parens is simpler, self-contained, and doesn't depend on sexpdata internals. We only need byte spans for top-level and second-level nodes (symbols, labels, properties within symbols), not deeply nested atoms.

### 4.4 Span-Matching Algorithm

```python
def _build_span_index(text: str, tree: list) -> dict[int, SexpSpan]:
    """Walk text tracking paren depth, correlate with parsed tree nodes.

    Strategy:
    1. Scan text for all '(' positions, tracking depth
    2. For each '(' find matching ')' (respecting nesting and string literals)
    3. Match top-level children to tree[1:] by order
    4. For symbols, recurse one level to index properties
    """
```

### 4.5 Formatting Modified Subtrees

When we replace a property value, we reformat just that property node. The formatter only needs to handle a small set of node types:

#### Property Node Format (KiCad 9)
```
	(property "Name" "Value"
		(at X Y ANGLE)
		(effects
			(font
				(size W H)
			)
			(justify DIR)
			(hide yes)
		)
	)
```

#### Label Node Format
```
	(label "TEXT"
		(at X Y ANGLE)
		(effects
			(font
				(size W H)
			)
			(justify DIR)
		)
		(uuid "...")
	)
```

#### Title Block Format
```
	(title_block
		(title "...")
		(date "...")
		(rev "...")
		(company "...")
		(comment 1 "...")
	)
```

**For value-only changes** (most common case): we don't need to reformat the entire property node. We can find the value string within the span and replace just the string bytes. Example: changing `"100nF"` to `"220nF"` in a property — find the value token position, replace those bytes only.

---

## 5. Rewritten `kicad_helpers.py`

### 5.1 Functions to Rewrite

| Function | Current Lines | Change |
|----------|--------------|--------|
| `list_components()` | 187-222 | Parse with SexpDocument, extract Reference/Value/Footprint from symbol nodes |
| `get_component()` | 225-267 | Parse with SexpDocument, read all properties from symbol's property children |
| `update_component()` | 270-401 | Find symbol span, find property spans within it, replace property value bytes |
| `update_schematic_info()` | 404-479 | Find title_block span, replace individual field values |
| `rename_net()` | 482-528 | Find label/global_label/hierarchical_label spans, replace text values |

### 5.2 Functions to Remove

All kicad-sch-api workarounds become unnecessary:
- `_is_hidden_in_sexp()` — visibility read directly from sexp tree
- `_sync_hidden_properties()` — no longer needed
- `_set_property_hidden()` — no longer needed
- `_extract_lib_symbols()` — no longer needed (lib_symbols never touched)
- `_save_with_lib_symbols()` — no longer needed (surgical save doesn't rebuild)

### 5.3 Functions Unchanged

`list_net_classes()`, `update_net_class()` — these operate on `.kicad_pro` (JSON), not schematics. No change needed.

### 5.4 New Capabilities (from surgical approach)

By operating on the raw s-expression tree, we automatically gain:
- **Global label support** — `rename_net()` can now find `(global_label ...)` nodes
- **DNP preservation** — we never touch `(dnp ...)` unless explicitly asked
- **Mirror preservation** — we never touch `(mirror ...)` nodes
- **Full property preservation** — justify, effects, coordinates all untouched unless explicitly modified
- **Future-proof** — new KiCad fields we don't know about are preserved automatically

---

## 6. KiCad S-Expression Formatting Reference

### 6.1 Source of Truth

KiCad's formatting is produced by a two-phase process in C++:
1. **Phase 1**: `sch_io_kicad_sexpr.cpp` emits flat s-expression tokens via `Print()`
2. **Phase 2**: `kicad_io_utils.cpp::Prettify()` post-processes into indented output

### 6.2 Indentation

| KiCad Version | Indent Character | Indent Size |
|--------------|-----------------|-------------|
| KiCad 7/8 | Space | 2 per level |
| KiCad 9 | Tab | 1 per level |

### 6.3 Number Formatting

- Coordinates in **millimeters** (internal units / IU_PER_MM)
- Format: `{:.10g}` — up to 10 significant digits
- Trailing zeros stripped (e.g., `1.27` not `1.2700`)
- Zero outputs as `0` (not `0.0`)
- Very small values (0 < |x| <= 0.0001): `{:.10f}` then strip trailing zeros
- No scientific notation
- Angles: degrees, same `{:.10g}` format

### 6.4 String Quoting

- Quote character: `"`
- Strings needing quotes: contains space, tab, `(`, `)`, `%`, `{`, `}`, starts with `#`, is empty, contains `-` at non-first position
- Escape sequences: `\n` → `\\n`, `\r` → `\\r`, `\\` → `\\\\`, `"` → `\\"`
- In practice: almost all strings in `.kicad_sch` are quoted

### 6.5 Booleans

`(key yes)` or `(key no)` — never `true`/`false`

### 6.6 Line-Breaking Rules (Prettify algorithm)

- Default: every `(` gets a newline + indentation
- **Exceptions**:
  - Consecutive `(xy ...)` stay on same line if column < 99
  - Short-form tokens (`font`, `stroke`, `fill`, `teardrop`, `offset`, `rotate`, `scale`) may stay single-line in compact mode
  - Token wrapping at column 72
- Closing `)`: same line for single-line lists, own line for multi-line blocks

### 6.7 Why We Don't Need Full Prettify

With surgical editing, we only reformat nodes we modify. A property value change means replacing `"100nF"` with `"220nF"` — the surrounding formatting stays intact. Only when we need to add/restructure a node do we need to format it, and that's a small, predictable structure.

---

## 7. Test Fixtures

### 7.1 Official KiCad Demo Files (for testing)

These files are from the official KiCad source repo and cover all reported bug categories:

| File | Features | License | URL |
|------|----------|---------|-----|
| `demos/cm5_minima/IO.kicad_sch` | mirror x/y, dnp yes, hierarchical_label (8), diverse justify, custom props | Apache-2.0 | https://raw.githubusercontent.com/KiCad/kicad-source-mirror/master/demos/cm5_minima/IO.kicad_sch |
| `demos/flat_hierarchy/pic_programmer.kicad_sch` | global_label (4), mirror x, many components | GPL-3.0 | https://raw.githubusercontent.com/KiCad/kicad-source-mirror/master/demos/flat_hierarchy/pic_programmer.kicad_sch |

### 7.2 Existing Test Fixtures (keep)

- `tests/fixtures/test_schematic.kicad_sch` — KiCad 6 format, 3 components
- `tests/fixtures/test_schematic_v9.kicad_sch` — KiCad 9 format, 3 components
- `tests/fixtures/test_project.kicad_pro` — project file for net class tests

### 7.3 New Test Strategy

**Primary test: diff-based round-trip verification**

```python
def test_roundtrip_no_unintended_changes(fixture_path):
    """Modifying one property must not change any other byte in the file."""
    original = fixture_path.read_text()
    update_component(str(fixture_path), "R1", {"Value": "4k7"})
    modified = fixture_path.read_text()

    # Diff should show ONLY the value change
    import difflib
    diff = list(difflib.unified_diff(
        original.splitlines(), modified.splitlines(), lineterm=""
    ))
    changed_lines = [l for l in diff if l.startswith('+') or l.startswith('-')]
    for line in changed_lines:
        assert "10k" in line or "4k7" in line or line in ('---', '+++')
```

**Secondary tests: feature-specific preservation**

```python
def test_mirror_flags_preserved():
    """Mirror flags must survive any edit."""

def test_dnp_flags_preserved():
    """DNP flags must not be reset."""

def test_global_labels_preserved():
    """Global labels must survive any edit."""

def test_justify_entries_preserved():
    """All justify variants must survive."""

def test_fields_autoplaced_preserved():
    """fields_autoplaced value must not change."""

def test_string_escaping_preserved():
    """Quoted strings must not be double-escaped."""
```

---

## 8. Implementation Plan

### Phase 1: Core Engine (`sexp_surgery.py`)

1. **Byte-span parser**: Read file text, tokenize parentheses, build span index
2. **Tree correlation**: Match spans to sexpdata-parsed nodes
3. **Query API**: `find_symbol()`, `find_labels()`, `find_title_block()`, `get_property()`
4. **Surgical replace**: `replace_span()`, `insert_before_end()`, `delete_span()`, `save()`
5. **Unit tests**: Span tracking accuracy, replace correctness, multi-edit ordering

### Phase 2: Rewrite `kicad_helpers.py`

1. **`list_components()`**: Parse with SexpDocument, walk symbol nodes
2. **`get_component()`**: Find symbol, extract all properties with visibility
3. **`update_component()`**: Find property spans, replace values surgically
4. **`rename_net()`**: Find all label types (local, hierarchical, global), replace text
5. **`update_schematic_info()`**: Find title_block, replace fields

### Phase 3: Integration & Verification

1. **Port all existing tests** to use new implementation
2. **Add round-trip diff tests** with KiCad demo fixtures
3. **Add preservation tests** for every bug in the report (mirror, dnp, global_label, justify, etc.)
4. **Remove kicad-sch-api** from `pyproject.toml` dependencies
5. **Remove old workaround code** (`_extract_lib_symbols`, `_sync_hidden_properties`, etc.)

### Phase 4: Cleanup

1. Update `pyproject.toml` — remove `kicad-sch-api` dep
2. Update `Dockerfile`
3. Update `CLAUDE.md` — remove workaround notes
4. Rebuild Docker image

---

## 9. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Byte-span tracking off by one | Medium | Extensive unit tests on known fixture files |
| sexpdata parsing differs from KiCad's parser | Low | sexpdata handles standard s-expressions; KiCad uses standard format |
| Multi-edit span invalidation (edits shift byte offsets) | High | Apply replacements back-to-front (highest offset first) |
| String escaping edge cases | Medium | Use sexpdata's own quoting for modified values |
| Large files (>10k lines) performance | Low | Linear scan is fine for files under 1MB; KiCad schematics rarely exceed this |
| KiCad format version differences (indentation) | Medium | Detect format version from header, adapt indent character accordingly |

---

## 10. Dependencies After Rewrite

```toml
[project]
dependencies = [
    "fastmcp",
    "sexpdata",    # s-expression parsing (already present)
    # kicad-sch-api REMOVED
]
```

---

## 11. File Inventory

### Files to Create
- `sexp_surgery.py` — core s-expression surgery engine
- `tests/test_sexp_surgery.py` — unit tests for core engine

### Files to Rewrite
- `kicad_helpers.py` — complete rewrite using SexpDocument

### Files to Modify
- `pyproject.toml` — remove `kicad-sch-api` dependency
- `tests/test_helpers.py` — port tests, add round-trip/preservation tests
- `Dockerfile` — no kicad-sch-api to install
- `CLAUDE.md` — remove workaround documentation

### Files Unchanged
- `server.py` — MCP tool API unchanged
- `tests/fixtures/test_project.kicad_pro` — net class tests unchanged

### Test Fixtures to Add
- Download `IO.kicad_sch` from KiCad demos (Apache-2.0) → `tests/fixtures/`
- Download `pic_programmer.kicad_sch` from KiCad demos → `tests/fixtures/`

---

## 12. Current Source Reference

### server.py (unchanged API — 232 lines)

8 MCP tools registered:
- `list_components(schematic_path, filter?)` → `list[dict]`
- `get_component(schematic_path, reference)` → `dict`
- `update_component(schematic_path, reference, properties)` → `str`
- `update_schematic_info(schematic_path, title?, revision?, date?, author?, company?)` → `str`
- `rename_net(schematic_path, old_name, new_name)` → `str`
- `list_net_classes(project_path)` → `list[dict]`
- `update_net_class(project_path, class_name, rules?, add_pattern?)` → `str`

All delegate to `kicad_helpers.*` — the MCP layer is clean and stays unchanged.

### kicad_helpers.py (670 lines — to be rewritten)

Current structure:
- Lines 1-16: Imports (sexpdata, kicad_sch_api)
- Lines 22-53: `_is_hidden_in_sexp()` — KiCad 6/9 hide flag parsing from sexp
- Lines 56-69: `_sync_hidden_properties()` — workaround for parser dropping hide
- Lines 72-93: `_set_property_hidden()` — dual-update hidden_properties + __sexp_
- Lines 104-143: `_extract_lib_symbols()` — workaround: re-read file with sexpdata
- Lines 146-179: `_save_with_lib_symbols()` — workaround: monkey-patch save
- Lines 187-222: `list_components()` — uses Schematic.load + sch.components
- Lines 225-267: `get_component()` — uses comp.properties + __sexp_ filtering
- Lines 270-401: `update_component()` — modifies comp + __sexp_ entries, saves
- Lines 404-479: `update_schematic_info()` — uses sch.set_title_block()
- Lines 482-528: `rename_net()` — iterates sch.labels + sch.hierarchical_labels
- Lines 535-669: Project helpers (JSON-based, unchanged)

### tests/test_helpers.py (396 lines — to be updated)

Current coverage:
- list_components: 4 tests
- get_component: 3 tests
- update_component: 5 tests
- Property visibility: 7 tests
- KiCad 9 regression: 4 tests
- update_schematic_info: 3 tests
- rename_net: 3 tests
- list_net_classes: 3 tests
- update_net_class: 4 tests
- lib_symbols preservation: 4 tests

---

## 13. Investigated but Rejected Alternatives

### kicad-sch-api (current — being removed)
- Typed Python model that re-serializes from incomplete data
- 3 workarounds already in place, 7 more bugs found
- Architecturally unfixable: drops unknown s-expression nodes

### kicad-skip
- **Right idea**: mutates raw sexpdata tree in-place, unknown fields preserved
- **Wrong execution**: write crashes (issue #20), formatting via crude regex (issue #5), 18 open / 0 closed issues, no maintenance since Feb 2024
- **Useful insight**: overlay-on-raw-tree pattern is the correct architecture

### kiutils (previously used by this project)
- Same rebuild-from-model approach as kicad-sch-api
- Dormant maintenance
- Worse than kicad-sch-api for round-trip

### KiCad IPC API
- Official API, NNG sockets + protobuf, introduced in KiCad 8/9
- **Schematic support not ready**: `schematic_commands.proto` is empty, no Python bindings (`kicad-python` only wraps PCB), `deleteItemsInternal()` and `getItemFromDocument()` are TODOs in C++
- PCB editor only, officially documented as such
- Requires running KiCad instance
- **Verdict**: Watch for future KiCad releases, but not usable today

### Full Prettify reimplementation
- KiCad's `Prettify()` is ~200 lines of stateful C++ character processing
- Context flags, column tracking, short-form token detection, xy special-casing
- **Not needed** with surgical approach — we only format the few nodes we modify, not the whole file
