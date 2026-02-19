# kicad-edit-mcp

MCP server for surgical KiCad schematic and project editing. Pairs with [kicad-mcp-server](https://github.com/Seeed-Studio/kicad-mcp) (analysis) to give AI agents structured read/write access to KiCad projects. Uses a custom s-expression surgery engine — only the targeted value changes; everything else stays byte-identical. Property modifications only, no topology changes.

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

## Important: KiCad Must Be Reopened After Edits

KiCad caches files in memory. Changes made by this server are **not visible until you reopen**:

| File edited | Action required |
|-------------|-----------------|
| `.kicad_sch` (schematic) | Close and reopen the schematic |
| `.kicad_pro` (project/net classes) | Close the **entire project** and reopen |

Edit `.kicad_pro` with KiCad closed — KiCad overwrites it on save, discarding external changes.

## Docker (recommended)

Build the image once:

```bash
docker build -t kicad-edit-mcp .
```

### Per-project `.mcp.json`

Mount your KiCad project directory to `/data` inside the container:

```json
{
  "mcpServers": {
    "kicad-edit": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
        "-v", "/path/to/kicad-project:/data",
        "kicad-edit-mcp"]
    }
  }
}
```

With tool lockdown via `DISABLED_TOOLS`:

```json
{
  "mcpServers": {
    "kicad-edit": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
        "-v", "/path/to/kicad-project:/data",
        "kicad-edit-mcp"],
      "env": { "DISABLED_TOOLS": "rename_net,update_net_class" }
    }
  }
}
```

Read-only mount (no writes possible at the filesystem level):

```json
{
  "mcpServers": {
    "kicad-edit": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
        "-v", "/path/to/kicad-project:/data:ro",
        "kicad-edit-mcp"]
    }
  }
}
```

## Installation (local, without Docker)

```bash
pip install fastmcp sexpdata
```

Or with Poetry:

```bash
poetry install
```

## Local Registration

Basic registration (all tools enabled):

```bash
claude mcp add kicad-edit-mcp -- python /path/to/server.py
```

With tool lockdown via env var:

```bash
DISABLED_TOOLS=rename_net,update_net_class claude mcp add kicad-edit-mcp -- python /path/to/server.py
```

## Tool Lockdown (Hard Guardrails)

Tools are enabled by default (opt-out model). **Disabled tools are never registered with MCP** — the LLM cannot see, call, or even know they exist. This is a hard guardrail at the protocol level, not a soft prompt-based restriction.

Set the `DISABLED_TOOLS` environment variable to a comma-separated list of tool names to disable:

```bash
# Disable net editing tools
DISABLED_TOOLS=rename_net,update_net_class python server.py

# Disable all write tools
DISABLED_TOOLS=update_component,update_schematic_info,rename_net,update_net_class python server.py
```

Unknown tool names in `DISABLED_TOOLS` trigger a stderr warning (typo protection). Missing names default to enabled.

For full read-only enforcement without listing every write tool, use a Docker read-only volume mount (`/data:ro`) — the filesystem enforces it regardless of which tools are registered.
