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
- [~] `ironcad_get_selection` (built; selection-mgr enumeration members now mapped — API_NOTES §9b — enumeration wiring still TODO).
- [x] `ironcad_get_anchor(name)` → anchor xyz + behavior (anchor read VERIFIED live 2026-09-18, `scripts/test_anchor_write.py`).
- [x] `ironcad_capture_view` returns a JPEG image (verified end-to-end; Claude read the render).
- [ ] `ironcad_status` when IronCAD NOT running → clear "open IronCAD first".

## M3 — catalog
- [x] `ironcad_list_catalogs` → 25 loaded catalogs (verified).
- [x] `ironcad_list_catalog_parts` → real part names (verified).
- [~] `ironcad_get_catalog_part_info(name)` → best-effort params (built; full config introspection partial).
- [x] `ironcad_add_catalog_part(name, catalog, position, instance_name)` in read_write → **VERIFIED** on a scratch scene: two PIL4140SNN_ beams placed (one at [0.3,0,0]), renamed, moved via matrix, backup handled (file-copy; None for unsaved), visually confirmed.

## M4 — edit / save / gating  (VERIFIED 2026-09-18)
- [x] `ironcad_set_part_parameter` sets IZParameter.Value + RegenerateParts (verified; not modal).
- [x] `ironcad_move_part` sets transform via matrix (verified: moved a beam).
- [x] `ironcad_save_copy` writes a copy (verified on a linked-part scene — SilentMode prevents the wedge); `ironcad_save` refuses in-place on an unsaved doc; save-over-different-file needs overwrite=True.
- [x] read_only refuses every write cleanly (verified).
- [x] `ironcad_execute_api_script` eval/exec work; `allow_writes=True` needs read_write (verified refusal).
- [x] **SilentMode** enabled on attach — modal dialogs suppressed; reported in `ironcad_status`.

## Efficiency + connect (2026-09-18)
- [x] `relative_to` + `offset` on add_catalog_part / move_part (place relative to another part — avoids absolute-coordinate guessing).
- [x] `ironcad_build_parts([...])` batch placement (one backup, one regen) — reduces round-trips + verification captures.
- [x] **Anchor primitives VERIFIED live** (`scripts/test_anchor_write.py`, scratch scene): GetAnchorTransform/SetAnchorTransform, AnchorTransformComponentValue, AnchorBehavior get/set (1→2). Wired: `ironcad_get_anchor` / `ironcad_set_anchor`.
- [x] **AssembleElements VERIFIED live** (`scripts/test_link_write.py`, scratch scene): `AssembleElements([A,B])` → assembly with 2 children, both re-parented (move together). Plain Python list marshals the VARIANT array. Wired: `ironcad_connect_parts`.
- [x] `ironcad_connect_parts` end-to-end via the MCP server in read_write — **VERIFIED 2026-09-18** (FastMCP call_tool + COM worker): attach→build_parts (A[0,0,0], B relative_to A +[0.4,0,0]) → connect_parts → `list_parts` shows ONE top-level `claude_e2e_asm` (type=assembly) with 2 children. Backups skipped (unsaved scratch). Nothing saved.
- [ ] `Solve`/constraint mating and `MoveChild` re-parenting (not yet tested; not needed for basic grouping).

## M5 — end-to-end + hardening
- [ ] Recover after IronCAD restart mid-session (attach again, no wedge).
- [ ] Long session: no stdout leakage.
- [ ] Full sketch → plan → confirm → build → verify → save on one representative drawing.
