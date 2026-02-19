"""S-expression surgery engine for KiCad schematic files.

Parses .kicad_sch files with byte-span tracking so we can surgically
replace individual nodes without re-serializing the entire file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sexpdata


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

    def __init__(self, text: str, tree: list, spans: dict[int, SexpSpan]) -> None:
        self.text = text
        self.tree = tree
        self.spans = spans
        self._replacements: list[tuple[int, int, str]] = []

    @classmethod
    def load(cls, path: Path) -> "SexpDocument":
        """Parse file, build span index."""
        text = path.read_text(encoding="utf-8")
        tree = sexpdata.loads(text)
        spans = _build_span_index(text, tree)
        return cls(text, tree, spans)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def find_all(self, node_type: str) -> list[SexpSpan]:
        """Find all top-level children of given type (depth=0 in our indexing)."""
        results: list[SexpSpan] = []
        for node in self.tree[1:]:
            if isinstance(node, list) and node and str(node[0]) == node_type:
                span = self.spans.get(id(node))
                if span is not None:
                    results.append(span)
        return results

    def find_symbol(self, reference: str) -> SexpSpan | None:
        """Find a schematic symbol by its Reference property value."""
        for sym_span in self.find_all("symbol"):
            # Only consider schematic instances (have lib_id), not lib_symbols
            node = sym_span.node
            if not _has_child_key(node, "lib_id"):
                continue
            ref_span = self.get_property(sym_span, "Reference")
            if ref_span is None:
                continue
            val = _property_value(ref_span.node)
            if val == reference:
                return sym_span
        return None

    def find_labels(
        self, label_type: str, text: str | None = None
    ) -> list[SexpSpan]:
        """Find label nodes, optionally filtered by text value."""
        results: list[SexpSpan] = []
        for span in self.find_all(label_type):
            node = span.node
            if len(node) >= 2:
                label_text = _unwrap_string(node[1])
                if text is None or label_text == text:
                    results.append(span)
        return results

    def find_title_block(self) -> SexpSpan | None:
        """Find the title_block node."""
        spans = self.find_all("title_block")
        return spans[0] if spans else None

    def get_property(self, symbol_span: SexpSpan, prop_name: str) -> SexpSpan | None:
        """Find a property node within a symbol by name."""
        node = symbol_span.node
        for i, child in enumerate(node[1:], start=1):
            if not (isinstance(child, list) and child and str(child[0]) == "property"):
                continue
            if len(child) >= 2 and _unwrap_string(child[1]) == prop_name:
                return self.spans.get(id(child))
        return None

    def get_property_value_span(
        self, prop_span: SexpSpan
    ) -> tuple[int, int, str] | None:
        """Find the value string's byte position within a property node.

        Property format: (property "Name" "Value" ...)
        Returns (start, end, current_value) of the "Value" string INCLUDING quotes,
        or None if not found.
        """
        # The value token is the 3rd element (index 2) in the property node
        node = prop_span.node
        if len(node) < 3:
            return None
        value = node[2]
        raw_value = _unwrap_string(value)

        # Scan the property text to find the second quoted string
        text = self.text
        pos = prop_span.start
        end = prop_span.end
        quote_count = 0

        i = pos
        while i < end:
            ch = text[i]
            if ch == '"':
                # Start of a quoted string
                quote_start = i
                i += 1
                while i < end:
                    if text[i] == '\\':
                        i += 2  # skip escape
                        continue
                    if text[i] == '"':
                        i += 1
                        break
                    i += 1
                quote_count += 1
                if quote_count == 2:
                    # This is the value string
                    return (quote_start, i, raw_value)
            else:
                i += 1
        return None

    def is_property_hidden(self, prop_span: SexpSpan) -> bool:
        """Check if a property has a hide flag.

        Handles both KiCad 6 bare `hide` symbol and KiCad 9 `(hide yes)` format.
        """
        node = prop_span.node
        # Look in effects sub-node
        for child in node[1:]:
            if not (isinstance(child, list) and child and str(child[0]) == "effects"):
                continue
            # Check direct children of effects
            for eff_child in child[1:]:
                if isinstance(eff_child, sexpdata.Symbol) and str(eff_child) == "hide":
                    return True
                if (
                    isinstance(eff_child, list)
                    and eff_child
                    and str(eff_child[0]) == "hide"
                ):
                    if len(eff_child) >= 2 and str(eff_child[1]) == "yes":
                        return True
        return False

    # ------------------------------------------------------------------
    # Mutation API
    # ------------------------------------------------------------------

    def replace_span(self, span: SexpSpan, new_text: str) -> None:
        """Queue replacement of the byte range [span.start, span.end)."""
        self._replacements.append((span.start, span.end, new_text))

    def replace_bytes(self, start: int, end: int, new_text: str) -> None:
        """Queue a raw byte-range replacement."""
        self._replacements.append((start, end, new_text))

    def insert_before_end(self, parent_span: SexpSpan, new_text: str) -> None:
        """Queue insertion of new_text before parent's closing paren."""
        pos = parent_span.end - 1
        self._replacements.append((pos, pos, new_text))

    def delete_span(self, span: SexpSpan) -> None:
        """Queue deletion of a span including preceding whitespace."""
        start = span.start
        while start > 0 and self.text[start - 1] in (" ", "\t", "\n", "\r"):
            start -= 1
        self._replacements.append((start, span.end, ""))

    def save(self, path: Path) -> None:
        """Apply all queued replacements back-to-front and write to disk."""
        result = self.text
        for start, end, new_text in sorted(
            self._replacements, key=lambda r: r[0], reverse=True
        ):
            result = result[:start] + new_text + result[end:]
        path.write_text(result, encoding="utf-8")
        self._replacements.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _unwrap_string(value: Any) -> str:
    """Extract a plain Python string from a sexpdata value."""
    if isinstance(value, str):
        return value
    if isinstance(value, sexpdata.Symbol):
        return str(value)
    return str(value)


def _property_value(prop_node: list) -> str:
    """Return the value field (index 2) of a property node as a string."""
    if len(prop_node) >= 3:
        return _unwrap_string(prop_node[2])
    return ""


def _has_child_key(node: list, key: str) -> bool:
    """Return True if node has a child list starting with Symbol(key)."""
    for child in node[1:]:
        if isinstance(child, list) and child and str(child[0]) == key:
            return True
    return False


# ---------------------------------------------------------------------------
# Byte-span tracking
# ---------------------------------------------------------------------------


def _build_span_index(text: str, tree: list) -> dict[int, SexpSpan]:
    """Scan text character-by-character to find s-expression spans, then
    correlate with sexpdata-parsed tree nodes by order.

    Returns a dict mapping id(node) → SexpSpan for:
    - Each depth-1 node (direct children of kicad_sch root)
    - Each depth-2 node (children of depth-1 nodes, e.g. properties inside symbols)
    """
    # Step 1: collect spans for all parenthesised forms, grouped by (scanner_depth)
    # scanner_depth=0 → the root (kicad_sch ...) itself
    # scanner_depth=1 → direct children of root  → our SexpSpan.depth=0
    # scanner_depth=2 → children of depth-1 nodes → our SexpSpan.depth=1

    depth1_spans: list[tuple[int, int]] = []   # (start, end) for scanner_depth=1
    depth2_by_parent: dict[int, list[tuple[int, int]]] = {}
    # map from depth1 span start → list of (start, end) for its depth-2 children

    current_depth = 0
    in_string = False
    i = 0
    n = len(text)

    # Stack entries: (start_pos, depth_at_open, depth1_start_if_applicable)
    open_stack: list[tuple[int, int, int]] = []  # (start, current_depth_before_open, depth1_parent_start)

    while i < n:
        ch = text[i]

        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        if ch == "(":
            # Push onto stack: record position and depth BEFORE incrementing
            depth1_parent = open_stack[-1][2] if open_stack else -1
            # For scanner depth 2 children, we want to know the depth-1 ancestor
            if current_depth == 1:
                # This open paren starts a depth-1 node
                open_stack.append((i, current_depth, i))
            elif current_depth == 2:
                # Depth-2 child; its depth-1 ancestor start is the top of stack
                parent_start = open_stack[-1][0] if open_stack else -1
                open_stack.append((i, current_depth, parent_start))
            else:
                open_stack.append((i, current_depth, -1))
            current_depth += 1
            i += 1
            continue

        if ch == ")":
            current_depth -= 1
            if open_stack:
                start, depth_at_open, d1_parent = open_stack.pop()
                end = i + 1
                if depth_at_open == 1:
                    depth1_spans.append((start, end))
                elif depth_at_open == 2:
                    if d1_parent not in depth2_by_parent:
                        depth2_by_parent[d1_parent] = []
                    depth2_by_parent[d1_parent].append((start, end))
            i += 1
            continue

        i += 1

    # Step 2: correlate depth-1 spans with tree[1:] by order
    spans: dict[int, SexpSpan] = {}
    tree_children = tree[1:]  # skip the root Symbol ('kicad_sch')

    if len(depth1_spans) != len(tree_children):
        # Fallback: best-effort, zip to shorter length
        pass

    for idx, (tree_node, (start, end)) in enumerate(
        zip(tree_children, depth1_spans)
    ):
        sp = SexpSpan(
            start=start,
            end=end,
            node=tree_node,
            depth=0,
            parent_index=idx,
        )
        spans[id(tree_node)] = sp

        # Step 3: for nodes that have children (symbols, title_block, lib_symbols)
        # index depth-2 children
        if not isinstance(tree_node, list):
            continue

        child_spans = depth2_by_parent.get(start, [])
        tree_node_children = tree_node[1:]  # skip the node type Symbol

        # Filter to list-type children only (atoms don't have spans)
        list_children = [c for c in tree_node_children if isinstance(c, list)]

        for cidx, (child_node, (cstart, cend)) in enumerate(
            zip(list_children, child_spans)
        ):
            csp = SexpSpan(
                start=cstart,
                end=cend,
                node=child_node,
                depth=1,
                parent_index=cidx,
            )
            spans[id(child_node)] = csp

    return spans
