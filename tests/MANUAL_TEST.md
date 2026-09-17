# MANUAL_TEST.md — live checklist against IronCAD 2024

Ordered checks against a running IronCAD 2024 with the user's catalog loaded
(spec §10). Run through Claude Desktop unless noted. `[ ]` = not yet reached.

## Prereqs
- [ ] One-time elevated `python -m python_ironcad` completed (see README).
- [ ] IronCAD 2024 open with a simple part AND the user's catalog loaded.
- [ ] Backup dir set (`IRONCAD_MCP_BACKUP_DIR`).

## M0 / M2 — connection & reads
- [x] Server launches; only JSON-RPC on stdout (verified: 0-byte stdout leak in `smoke_m0` and `smoke_tools`).
- [x] `ironcad_status` before registration → `api_registered:false` + fix hint (verified pre-registration).
- [x] `ironcad_attach` after registration → `connected:true` (verified via call_tool).
- [x] `ironcad_get_active_doc_info` → name + top_level_count (18) (verified).
- [x] `ironcad_list_parts` → names/types/ids (verified: 18 parts).
- [~] `ironcad_describe_part(name)` → type/pos/params; ambiguous name refused (built; note many parts share a name so refusal path is the common case).
- [~] `ironcad_get_selection` (built; selection-mgr enumeration members still OPEN).
- [x] `ironcad_capture_view` returns a JPEG image (verified end-to-end; Claude read the render).
- [ ] `ironcad_status` when IronCAD NOT running → clear "open IronCAD first".

## M3 — catalog
- [x] `ironcad_list_catalogs` → 25 loaded catalogs (verified).
- [x] `ironcad_list_catalog_parts` → real part names (verified).
- [~] `ironcad_get_catalog_part_info(name)` → best-effort params (built; full config introspection partial).
- [ ] `ironcad_add_catalog_part(name, pos)` in read_write → instance created; backup first. **NOT run live yet — needs a scratch scene + consent (mutates a real file).**

## M4 — edit / save / gating
- [ ] `ironcad_set_part_parameter` regenerates.
- [ ] `ironcad_move_part` sets transform.
- [ ] `ironcad_save` / `ironcad_save_copy`; save-over-different-file needs overwrite=True.
- [ ] read_only refuses every write cleanly.
- [ ] `ironcad_execute_api_script` needs `allow_writes=True` to mutate; backup first.

## M5 — end-to-end + hardening
- [ ] Recover after IronCAD restart mid-session (attach again, no wedge).
- [ ] Long session: no stdout leakage.
- [ ] Full sketch → plan → confirm → build → verify → save on one representative drawing.
