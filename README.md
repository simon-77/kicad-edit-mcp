# kicad-edit-mcp

Minimal MCP server for controlled KiCad schematic and project modifications using [kiutils](https://github.com/mvnmgrx/kiutils). Companion to [kicad-analysis](https://github.com/Seeed-Studio/kicad-mcp) (read-only). Handles property modifications only — no topology changes.

## Philosophy

**Only modify properties of existing elements. Never change circuit topology or physical layout.**

What's included:
- Component properties (values, ratings, MPNs, datasheets, DNP flags)
- Title block metadata (revision, date, author)
- Net label renaming (e.g. `Net-(U1-PA5)` → `SPI1_SCK`)
- Net class rules (track widths, clearances, diff pair settings)

What's deliberately excluded:
- Adding or deleting components (changes circuit topology)
- Adding or deleting wires (changes connectivity)
- Moving components or traces (spatial — LLM can't see layout)
- PCB modifications (physical layout — use KiCad GUI)
- Library editing (affects all projects using that library)

This makes the server safe to run against production schematics — the worst case is a wrong property value, easily caught in review and reverted via git.

## Tools

| Tool | Type | Description |
|------|------|-------------|
| `list_components` | Read | List all components with references, values, and footprints |
| `get_component` | Read | Get all properties of a component by reference designator, including visibility info |
| `update_component` | Write | Set or remove component properties; supports visibility control via `{"value": ..., "visible": bool}` |
| `update_schematic_info` | Write | Modify title block metadata (title, revision, date, author, company) |
| `rename_net` | Write | Rename net labels throughout a schematic |
| `list_net_classes` | Read | List net class rules and pattern assignments |
| `update_net_class` | Write | Create or modify net class rules and pattern assignments |

## Installation

```bash
pip install fastmcp kiutils pyyaml
```

Or with Poetry:

```bash
poetry install
```

## Claude Code Registration

Basic registration (all tools enabled):

```bash
claude mcp add kicad-edit-mcp -- python /path/to/server.py
```

With a custom config file:

```bash
claude mcp add kicad-edit-mcp -- python /path/to/server.py --config /path/to/config.yaml
```

Per-project override — place `.kicad-modify.yaml` in your project root:

```bash
claude mcp add kicad-edit-mcp -- python /path/to/server.py --config .kicad-modify.yaml
```

## Tool Lockdown (Hard Guardrails)

Tools are enabled by default (opt-out model). **Disabled tools are never registered with MCP** — the LLM cannot see, call, or even know they exist. This is a hard guardrail at the protocol level, not a soft prompt-based restriction.

Use this to enforce per-project safety policies:

```yaml
# .kicad-modify.yaml — read-only mode (no write tools)
tools:
  list_components: true
  get_component: true
  update_component: false
  update_schematic_info: false
  rename_net: false
  list_net_classes: true
  update_net_class: false
```

Or allow writes but lock down net classes:

```yaml
# Only disable what you need to protect
tools:
  update_net_class: false       # net classes are read-only
```

Missing keys default to `true` (enabled). Unknown keys trigger a stderr warning (typo protection).

Pass the config path via `--config`:

```bash
python server.py --config .kicad-modify.yaml
```
