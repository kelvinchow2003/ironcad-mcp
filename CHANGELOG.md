# Changelog

## Unreleased — 2026-09-24 full retest

Full live retest of all 28 tools, both prompts, and the refusal paths, plus a
25-extrusion / 7-panel factory workstation (`builds/factory_workstation.ics`)
built only through the tool layer.

### Fixed
- `safety.backup_active_doc`: backups taken within the same second shared
  one filename, so the second copy silently replaced the first (for example,
  the pre-save snapshot was lost when a write followed a save in the same
  second). Names now get a `_NN` suffix on collision. The copy is also
  retried on a Windows sharing violation, which happens when IronCAD is still
  holding the file just after `IZDoc.Save()`.
- `ironcad_save`: the save-as overwrite guard now runs *before* the backup,
  so refusing to overwrite reports the real reason instead of a spurious
  backup failure.

### Added
- `ironcad_capture_view(view=...)`: camera presets (`iso`, `iso_rear`,
  `front`, `back`, `left`, `right`, `top`). A fresh scene's default camera
  can sit below the floor looking up, which made the verification render
  unreadable. `IZCamera` setters need a SAFEARRAY of doubles
  (`array('d')`); a Python tuple is rejected with E_INVALIDARG.

## 0.2.0 — 2026-09-24

Implements Phases 1–9 of `PRODUCTION_READINESS_PLAN.md`, following the
quality-test findings in `MCP_TEST_REPORT_173.md` (a fresh live build vs. the
user's real hand-built B150004-173, both run against this MCP's own tool
layer).

### Fixed (Phase 1 — confirmed bugs)
- `safety.resolve_by_name`: `index` is now consulted whenever plain name
  resolution is ambiguous OR missing, not only when there are zero matches.
  Previously an ambiguous name (the common case for symmetric stock frames —
  identically-cut members share one auto-BOM name) silently ignored a passed
  `index` and always refused.
- `ironcad_connect_parts`: added an `indices` parameter (alongside `names`)
  so same-named siblings can be grouped without the BOM-destroying rename
  workaround.
- `ironcad_set_extrude_length(backward=True)`: now explicitly zeroes the
  *other* direction's `ExtrudeDistance` slot. Previously it left that slot at
  its stale catalog default (typically 500mm), producing `target + 500mm`
  instead of `target`.
- `com_worker.ComWorker._loop`: a watchdog-timeout-cancelled future no longer
  crashes the worker thread (found via the Phase 4.3 watchdog's own test —
  `future.set_result`/`set_exception` on an already-cancelled future used to
  raise `InvalidStateError` uncaught, killing the whole COM worker after any
  single timeout).

### Added (Phase 2 — new tools)
- `ironcad_list_children` — assembly child enumeration. Root-caused and fixed
  a real gap: `IZElement.GetChildren()` returns unusable raw values on this
  IronCAD build; the working path is `GetChildrenZArray()` + explicit
  `QueryInterface(IZElement)` per item. This also fixed `ironcad_check_
  interference`, which had been silently skipping the contents of any grouped
  assembly.
- `ironcad_add_catalog_assembly` — insert a compound catalog entry (e.g. the
  real Robotunits `PAN+CAL4515SNN+CAN4501` panel-retention-rail system) by
  name, set its parameters, and regenerate it correctly (`IZAssembly.
  RegenerateParts(True)`, not a plain part regenerate).
- `ironcad_build_panel` — first-class tile→weld→trim Boolean panel builder,
  promoted out of ad-hoc scripts, with 3 verified orientations.
- `_element_bbox` now falls back to `IZAssembly.GetBoundingBox` when
  `IZPart` QI fails, so a compound assembly's overall envelope can be
  measured directly without descending into its children.

### Added (Phase 3 — testing)
- `tests/integration/` — an opt-in live suite (`IRONCAD_MCP_LIVE_TESTS=1`)
  covering every Phase 1/2 fix and tool against a real, running IronCAD
  instance, isolated in fresh scratch scenes that are now properly closed on
  teardown (see "Fixed" above about the resource-exhaustion issue this
  uncovered).
- `tests/golden/b150004_173_real.json` — a snapshot of the user's real
  hand-built design's full recursive BOM + envelope, generated read-only via
  the Phase 2 traversal fix. This correction superseded an earlier
  (incomplete, pre-fix) envelope reading for the same file.

### Added (Phase 4 — error handling & observability)
- New typed errors: `GeometryError`, `ComUnavailableError`,
  `UnsupportedCatalogPart` (alongside the existing `WriteRefused`/
  `NameResolutionError`).
- COM worker watchdog: a per-call timeout (`IRONCAD_MCP_COM_TIMEOUT_S`,
  default 30s, 0 disables) so a wedged call surfaces a clear error instead of
  hanging the MCP connection forever.
- Per-call structured logging in the COM worker (label/duration/outcome for
  every `run_on_com` call).

### Added (Phase 5 — two-stage drawing pipeline)
- `interpret_drawing` prompt (vision-only, zero tool calls) producing a
  `<drawing_name>.spec.json`.
- `spec_schema.py` (JSON Schema + validator) and the `ironcad_validate_spec`
  tool.
- `build_from_sketch` rewired to consume an approved spec instead of
  re-reasoning over the raw drawing each time, with an always-on review gate.

### Added (Phase 6 — catalog knowledge as data)
- `data/catalog_manifest.json` — structured decode of the loaded catalogs
  (profile sizes in stock, real vs. inert parameters, known-failing entries,
  recommended tool per family), exposed via `ironcad_get_catalog_manifest`.
- `scripts/verify_catalog_manifest.py` — live drift-checker (run by hand
  after a catalog update, not part of CI).

### Added (Phase 7 — safety & audit hardening)
- Backup retention: `IRONCAD_MCP_BACKUP_RETENTION_COUNT` (default 20 per
  source file, 0 disables) prunes old backups after each new one.
- Write-audit trail: every `backup_active_doc` call appends a line to
  `<backup_dir>/audit.log.jsonl` (source, backup path, optional `action`
  label, timestamp).
- `ironcad_execute_api_script` now logs a `SCRIPT_AUDIT:` line (full code +
  outcome) per call and returns a best-effort `dangerous_calls_detected` list
  (Save/Delete/subprocess/... substring scan — visibility, not a block).

### Added (Phase 8 — packaging)
- `scripts/setup.ps1` — idempotent venv/install bootstrap, IronCAD detection,
  and a ready-to-paste `claude_desktop_config.json` snippet with real paths.
- Version bumped `0.1.0` → `0.2.0`.

### Changed (Phase 9 — docs)
- README's Status section rewritten to distinguish "core primitives
  verified" from "BOM-faithful pipeline," linking to
  `PRODUCTION_READINESS_PLAN.md` / `MCP_TEST_REPORT_173.md`.
- Tool docstrings updated throughout with known limitations and the fixes
  above.

### Notable findings along the way
- The real `b150004_173.ics`'s true envelope is **27.085 × 42.7 × 44.268 in**
  with 58 leaf parts (see the golden snapshot) — earlier sessions' BOM
  reading had post/rail roles swapped, producing a wrong ~42×17.5×24in target
  for rebuilds. Any future rebuild of this drawing should start from the
  golden file's measured numbers, not the old assumption.
- Opening a fresh scratch scene per operation without ever closing it
  exhausts a running IronCAD instance after enough accumulation — hit live
  during this work (IronCAD crashed and had to be relaunched). Any future
  live-probing script should close scenes it's done with.
