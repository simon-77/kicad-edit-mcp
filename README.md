# kicad-edit-mcp

Minimal MCP server for controlled KiCad schematic and project modifications using [kiutils](https://github.com/mvnmgrx/kiutils). Companion to [kicad-analysis](https://github.com/Seeed-Studio/kicad-mcp) (read-only). Handles property modifications only — no topology changes.

## Philosophy

Only modify properties of existing elements. Never change circuit topology or physical layout. This keeps the server safe to run against production schematics.

## Tools

| Tool | Type | Description |
|------|------|-------------|
| `list_components` | Read | List all components with references, values, and footprints |
| `get_component` | Read | Get all properties of a component by reference designator |
| `update_component` | Write | Set or remove component properties (value, footprint, datasheet, custom fields) |
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

## Configuration

Tools are enabled by default (opt-out model). Disabled tools are not registered with MCP — the LLM cannot see or call them.

```yaml
# .kicad-modify.yaml
tools:
  list_components: true
  get_component: true
  update_component: true
  update_schematic_info: false  # disable if you don't want title block edits
  rename_net: true
  list_net_classes: true
  update_net_class: false       # disable to make net classes read-only
```

Pass the config path via `--config`:

```bash
python server.py --config .kicad-modify.yaml
```
