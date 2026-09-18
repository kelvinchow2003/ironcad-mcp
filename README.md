# ironcad-mcp

A local **MCP server** that wraps **IronCAD 2024**'s COM API (via the
`python-ironcad` / ICAPI package) so Claude can inspect and build models in
*your own* running IronCAD session, referring to catalog parts **by name** —
including the end goal: hand Claude a sketch and have it build it from your
catalog.

> **Windows only.** This is COM/`comtypes`/`pywin32`; it does not run on
> macOS/Linux. IronCAD, this server, and Claude Desktop must all run on the
> **same machine, in the same interactive logged-in session** (COM `attach()` is
> machine- and session-local). Viewing that machine over RDP/TeamViewer is fine;
> it is not part of Claude's path to IronCAD.

## Status — M0–M5 complete, verified against live IronCAD 2024

- ✅ **M0** scaffold, STA COM worker, stdout guard (0-byte leak verified).
- ✅ **M1** discovery — every priority target resolved with live snippets
  (`API_NOTES.md`); `scripts/discover_api.py` reproduces it.
- ✅ **M2** read tools — status/attach/doc-info/list/describe/selection/capture-view.
- ✅ **M3** catalog tools — list/list-parts/info + **`ironcad_add_catalog_part`**
  (instantiate by name; backup-first). Verified: placed & positioned real parts.
- ✅ **M4** edit/save — set-parameter, move (matrix), save/save-copy, read-only
  gating, escape hatch. **SilentMode** suppresses modal dialogs (see below).
- ✅ **M5** `build_from_sketch` MCP prompt (plan → confirm → build → verify → save).

**16 tools** + 1 prompt; 12 mock unit tests. See `tests/MANUAL_TEST.md` for the
live verification log.

### Dialog safety (SilentMode)

On attach the server sets `IZBaseAppSetups.SilentMode = True`. IronCAD modal
dialogs would otherwise freeze the single COM worker thread irrecoverably; with
SilentMode they are suppressed so automation never blocks. `ironcad_status`
reports `silent_mode`.

## Prerequisites

1. **IronCAD 2024** installed and **running** before use (this server never
   launches IronCAD). It must be the newest IronCAD on the machine —
   `python-ironcad` targets the most recent installed version.
2. **Python 3.11+ 64-bit** (verified here on 3.14.7 64-bit). A 32-bit Python
   causes COM load failures.

## Setup

```powershell
# from the project root
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

### ⚠️ One-time elevated COM registration (REQUIRED)

IronCAD's ICAPI COM components must be registered system-wide once, from an
**Administrator** terminal. Until this is done, `ironcad_attach` returns
`api_registered: false` and nothing beyond attach works.

```powershell
# Run from an ELEVATED (Administrator) PowerShell:
& "C:\Users\<you>\Desktop\Ironcad Automation\ironcad-mcp\.venv\Scripts\python.exe" -m python_ironcad
```

It prompts `continue? (y/n)` → `y`, raises a UAC prompt, then runs `Regsvr32 /s`
on four DLLs in `C:\Program Files\IronCAD\2024\bin\`
(`3iICApiIronCADApp.dll`, `3iICApiDrawing.dll`, `3iICApiCore.dll`,
`3iICApiBase.dll`). It finishes with "All COM components registered
successfully." Do this once per machine (re-run after an IronCAD reinstall/upgrade).

> Never run this silently at server runtime — it needs admin and changes
> system-wide COM registration.

## Register with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ironcad": {
      "command": "C:\\Users\\<you>\\Desktop\\Ironcad Automation\\ironcad-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "ironcad_mcp.server"],
      "env": {
        "IRONCAD_MCP_MODE": "read_only",
        "IRONCAD_MCP_BACKUP_DIR": "C:\\Users\\<you>\\ironcad-mcp-backups"
      }
    }
  }
}
```

Flip `IRONCAD_MCP_MODE` to `read_write` to enable building. IronCAD 2024 must be
open first.

## Safety model (spec §7)

- **Default read-only.** All catalog/edit/script writes refuse until
  `IRONCAD_MCP_MODE=read_write`.
- **Backup before every write** to `IRONCAD_MCP_BACKUP_DIR`; if the backup
  fails, the write is aborted.
- **No silent overwrites**, **fail-safe on ambiguous names** (refuse, don't guess).
- The escape-hatch script tool (later milestone) is **not** a sandbox; the mode
  flag reduces *accidental* mutation only.

## Scope expectation (honest)

Reliability tracks how bounded the drawing is. Assemblies of known catalog parts
with clear dimensions are the sweet spot. Ambiguous freeform hand sketches will
take several plan → build → verify iterations. Acceptance is defined against one
representative real drawing of yours, not "any sketch."

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `IRONCAD_MCP_MODE` | `read_only` | `read_only` \| `read_write` |
| `IRONCAD_MCP_BACKUP_DIR` | (unset) | required for any write; timestamped backups land here |
| `IRONCAD_MCP_LOG_LEVEL` | `INFO` | logging level (to stderr) |
| `IRONCAD_MCP_LOG_FILE` | (unset) | optional rotating log file path |

## Layout

See `src/ironcad_mcp/` (server, com_worker, connection, safety, logging_setup),
`scripts/discover_api.py` (M1 spike), `API_NOTES.md` (verified ICAPI calls),
`tests/`.
