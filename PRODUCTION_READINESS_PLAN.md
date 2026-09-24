# ironcad-mcp — Production Readiness Plan

Status baseline: M0–M5 complete per README; live-verified against a real
drawing (B150004-173) in `MCP_TEST_REPORT_173.md` (2026-09-24). That test
found 3 concrete tool-layer bugs, 2 missing tool categories, and one BOM/data
error, and rated the server "usable for a rough structural mockup; not yet
BOM-faithful or connector-real." This plan closes that gap.

Scope note: "production ready" here means *reliable and trustworthy for
you, running locally against your own IronCAD + catalog* — not a multi-tenant
SaaS. No auth system, no multi-user concerns, no cloud deployment. The bar is:
it doesn't corrupt real files, it fails loudly and clearly instead of silently
wrong, its outputs are dimensionally/BOM-trustworthy without hand-checking
every build, and a new build on a new drawing doesn't require re-discovering
bugs this project has already hit once.

Phases are ordered by leverage (cheap+high-impact first), not by milestone
number. Each has concrete tasks, files touched, and a done-when check.

---

## Phase 1 — Fix the 3 confirmed correctness bugs (highest leverage, cheapest)

These were hit live in the 2026-09-24 test and block the tools' *own*
documented workflows. Fix before adding anything new — new features built on
top of `resolve_by_name` inherit this bug otherwise.

### 1.1 `resolve_by_name` ignores `index` whenever the name is ambiguous
**File:** `src/ironcad_mcp/safety.py::resolve_by_name`
Currently: exact-name match is checked first; `index_fallback` is only
consulted when there are **zero** name matches. The moment 2+ parts share a
name (the normal case for symmetric T-slot frames — every unrenamed stock
part with the same cut length auto-shares a BOM name), passing `index` does
nothing and the call refuses as "ambiguous."
**Fix:** when `index_fallback` is not None, check it *first* against the full
`items` list (regardless of name-match count) and only fall through to
name-based resolution if no `index_fallback` was given, or the given index
isn't present. Keep the ambiguous-refusal behavior for the *no-index-given*
case unchanged (that's correct fail-safe behavior).
**Also update:** every caller in `tools_edit.py`/`tools_read.py`/
`tools_catalog.py` that calls `resolve_by_name` — confirm they already pass
`index` through from their tool signature (some may need a new `index: int
| None = None` parameter added if missing).
**Done when:** a regression test places 4 identical-length parts, then
resolves each of the 4 by name+index successfully (currently fails after the
2nd).

### 1.2 `ironcad_connect_parts` has no index-based grouping path
**File:** `src/ironcad_mcp/tools_catalog.py` (or wherever `connect_parts` lives)
**Fix:** add an `indices: list[int] | None` parameter as an alternative to
`names: list[str]` — resolves each entry directly by top-level index instead
of by name, so symmetric/same-named siblings can be grouped without renaming
(renaming destroys BOM auto-naming, per existing project rule).
**Done when:** the B150004-173 test build's 10 frame members (which share
names in pairs/quads) can be grouped into one assembly via indices in one
call.

### 1.3 Extrude "forward" direction is unpredictable after a compound rotation; `backward=True` doesn't compensate
**File:** `src/ironcad_mcp/tools_edit.py::ironcad_set_extrude_length`
Currently: `backward=True` sets `ExtrudeDistance[1]` (a second, independent
distance slot) while leaving `ExtrudeDistance[0]` (default 500 mm) untouched
— producing a part 500 mm longer than intended instead of correcting
direction.
**Fix, in order of preference:**
  a. Best: after setting rotation, read back the actual bbox delta for a
     probe/trial length change and determine which `ExtrudeDistance` index
     is "growing" in the direction the caller intended (toward `relative_to`
     / the far end), then set only that index and zero/ignore the other.
  b. If (a) is too fragile across profile families, at minimum: make
     `backward=True` also **zero out `ExtrudeDistance[0]`** (or read+report
     both indices so the caller can reconcile), so the documented flag
     produces the length the caller asked for, not `length + 500mm`.
  c. Document the actual per-rotation-recipe direction behavior for every
     rotation recipe already in memory (`[0,-90,0]`, `[0,-90,-90]`, no
     rotation, etc.) directly in the tool's docstring, so future callers
     don't have to rediscover it via trial-and-error bbox dumps.
**Done when:** a W-rail placed with `rotation_deg=[0,-90,-90]` and a target
length comes out at the target length, anchored at the corner the caller
specified (near OR far), without manual "anchor the far corner instead"
workarounds.

### 1.4 (Data hygiene, not a code bug) Re-verify B150004-173's target envelope
The test found the real `b150004_173.ics` measures 24.05 × 21.04 × 43.15 in
(tall tower) vs. the ~42 × 17.5 × 24 in assumption baked into
`scripts/build_b150004_173.py` and the memory notes describing its BOM. Before
any future rebuild of this specific drawing, re-derive the target dims from
the real file's *measured* geometry (already have the read-only inspection
method from the test), not from the old BOM reading. This is a one-time data
fix, not a systemic one — flagged here so it isn't silently reused.

---

## Phase 2 — Close the two missing first-class tool categories

The test's #2 and #3 top recommendations. Both currently only reachable via
the raw `ironcad_execute_api_script` escape hatch, which means the knowledge
lives in ad-hoc scripts, not a stable, testable, documented API surface.

### 2.1 First-class panel-sizing tool
**New tool:** `ironcad_build_panel(target_w_m, target_h_m, thickness_m,
position, rotation_recipe?, catalog_entry="PAN___P_N___")` — wraps the
already-proven tile→weld(Boolean UNITE)→trim(Boolean SUBTRACT) recipe from
`scripts/build_hoist_cover_full.py` into `tools_catalog.py`/`tools_edit.py`,
including the per-axis `rotate_chain` two-step rotation quirk already
documented in memory for this part family. Also expose the simpler
`PAN___P_N___+PIN4521` `Gesamtbreite`/`Gesamtlaenge` parametric path as a
second, preferred code path when the target size is within that part's valid
range (needs the one open question from memory resolved first — see 2.3).
**Done when:** building a panel of arbitrary W×H via one tool call reproduces
the dimensional accuracy already achieved by the ad-hoc script, with 0 manual
Python.

### 2.2 First-class compound-catalog-assembly insert
**New tool:** `ironcad_add_catalog_assembly(name, catalog=None, position,
relative_to?, offset?, sub_parameters?: dict[str, dict])` — for catalog
entries that are themselves multi-part assemblies (e.g.
`PAN___P_N___+PIN4521`, `PAN___P_N___+CAL4515SNN____+CAN4501`). Needs a
reliable way to enumerate/target the children of such an assembly for
parameter-setting (the test found `GetChildren()` on `Assembly1`-style nodes
silently failed to read `.Name`/`.Type` on some children — fix that read path
first, see 2.4).
**Done when:** `PAN___P_N___+CAL4515SNN____+CAN4501` (the real
panel-retention-rail system used in the user's hand-built designs) can be
inserted and parameterized through this tool, closing the single largest
structural-fidelity gap the test identified.

### 2.3 Resolve the open `Gesamtbreite`/`Gesamtlaenge` range question
Memory flags this as "not yet verified: the valid range... whether it can
exceed the panel's underlying 500mm stock." Spike this live (small script,
same pattern as prior discovery spikes) before wiring 2.1's preferred path —
if the range is bounded, the tool needs a fallback to the tile/weld/trim
technique above that range, and callers need to know which path was used.

### 2.4 Fix assembly-child enumeration read gap
**File:** `src/ironcad_mcp/tools_read.py` (wherever `_iter_leaf_parts` /
`GetChildren` traversal lives)
The test hit a silent failure reading `.Name`/`.Type` on children of
`Assembly1`-style nodes in a real file. Root-cause it (likely the same
"assembly's `GetFirstChild()` descends into the FEATURE tree not sibling
children" quirk already documented for `AssembleElements` results) and fix
the traversal, or at minimum surface a clear error instead of a silent
`_safe()`-swallowed failure — silent failures are exactly what makes a tool
un-trustworthy for BOM-faithful reads.
**Done when:** `ironcad_describe_part`/`ironcad_list_parts` on the real
`b150004_173.ics` fully resolves both `Assembly1` nodes' children.

---

## Phase 3 — Testing: prove it stays correct, not just that it worked once

Current state: 12–20 mock unit tests (no live IronCAD) + a manual test log +
one-off scratchpad scripts per session. Nothing regresses automatically.

### 3.1 Live integration test suite (opt-in, marked, skipped by default)
Add `tests/integration/` gated by an env var (`IRONCAD_MCP_LIVE_TESTS=1`) and
a pytest marker, so CI/default `pytest` stays mock-only but a deliberate run
against the real, running IronCAD exercises the actual tool dispatch path —
formalize the pattern the test agent used (`mcp._tool_manager.get_tool(name)
.run(kwargs)` against a live worker) as a reusable fixture instead of a
one-off driver script per session. Cover at minimum: attach/status,
add_catalog_part + index-based resolve (regression test for 1.1),
build_parts batch, connect_parts by indices (regression test for 1.2),
set_extrude_length forward+backward (regression test for 1.3), build_panel
(2.1), interference check on a known-good and known-bad layout, save/save_copy
round-trip into the scratchpad only.

### 3.2 Golden-file regression tests per known drawing
For each drawing already built once (B150004-173, -221, -220, the 500mm
cube), commit a small JSON "golden" file (expected overall envelope, BOM
part-name histogram, expected clash_count) generated from a known-good build,
and a test that rebuilds fresh and diffs against it. This is what would have
caught the B150004-173 dimension mismatch (Phase 1.4) automatically instead
of via a one-off audit. Store golden files under `tests/golden/`.

### 3.3 Expand the mock suite for every new/changed tool
Every fix in Phase 1 and every new tool in Phase 2 needs a mock-level test
(no live IronCAD) covering its argument validation, safety gating (read-only
refusal, backup-before-write), and name/index resolution edge cases — this is
cheap and should be non-negotiable per PR going forward, not a separate
future task.

### 3.4 A minimal CI story
Even without a hosted runner (this needs a live Windows box with IronCAD to
fully test), at minimum wire the mock suite into a pre-commit/pre-push hook
or a documented "run before you consider a change done" command, so the
existing 12+ mock tests don't silently bit-rot as tools change shape.

---

## Phase 4 — Error handling, observability, and failure containment

### 4.1 Structured error taxonomy
Today, tool failures surface as raw Python exceptions / COMError messages
(e.g. `COMError(-2147467263, 'Not implemented')` bubbling straight to the
caller for the `RegenerateParts` gap). Define a small set of typed error
categories (`WriteRefused`, `NameResolutionError` already exist in
`safety.py` — extend with `GeometryError`, `ComUnavailableError`,
`UnsupportedCatalogPart`) so an LLM caller (or a human reading logs) can
branch on *kind* of failure, not string-match a COM error code.

### 4.2 Document every known COM pitfall as a caught, explained error
Every "gotcha" already in memory (`RegenerateParts(True)` after a Boolean op,
0/E_POINTER `eLinksSaveOptions`, panel-family rotation no-op-on-first-axis,
PNG export unsupported) should be a `try/except` in the relevant tool that
raises a clear, specific message — not something the *next* session has to
rediscover by hitting the same wedge. Cross-reference `API_NOTES.md` and the
memory file `ironcad-mcp-project.md` as the source list; this phase is
"promote tribal knowledge into code," not new discovery.

### 4.3 COM worker health & recovery
The STA worker thread is a single point of failure — if a modal dialog ever
does slip through (SilentMode covers known cases, not guaranteed all), the
whole server wedges. Add: a watchdog timeout on `run_on_com` calls (configurable,
e.g. 30s) that at least surfaces "the COM worker appears wedged, likely a modal
dialog; restart IronCAD and the server" instead of hanging the MCP connection
forever; consider (lower priority) a supervisor that can force-restart the
worker thread/process.

### 4.4 Logging polish
Confirm `IRONCAD_MCP_LOG_FILE` rotation actually works under real usage
volume (build sessions can be long); add a per-tool-call structured log line
(tool name, args summary, duration, success/failure) so a session's activity
can be reconstructed from the log alone — useful both for debugging and for
the "what did the agent actually do to my file" audit trail production use
demands.

---

## Phase 5 — The two-stage drawing pipeline (already designed, not built)

Per the existing agreed design in memory
(`feature-plan-two-stage-drawing-pipeline`): split drawing interpretation
from IronCAD execution so misreads (the exact B150004-173 axis-swap bug this
session found) are caught in a cheap, tool-free review step instead of after
several live IronCAD round-trips.

### 5.1 `interpret_drawing` MCP prompt (vision-only, zero tool calls)
Outputs `<drawing_name>.spec.json` next to the source drawing: overall
envelope (**explicitly re-measured/cross-checked against any reference model
if one exists**, not just read off the drawing's dimension callouts — this
would have caught 1.4 at authoring time), BOM per item (role, nominal
size/qty, `catalog_match`: resolved/unresolved + which catalog part or why
not, referencing the machine-readable catalog manifest from Phase 6), joint/
connector family, panel-border relationships, assumptions list, gaps list,
confidence score.

### 5.2 JSON schema + validator for the spec file
A concrete schema (not just prose) so `build_from_sketch` can validate its
input mechanically and refuse to build from a malformed/incomplete spec.

### 5.3 Rewire `build_from_sketch` to require an approved spec
Modify the existing prompt in `server.py` so it consumes
`<drawing_name>.spec.json` (after the user has reviewed/edited it) instead of
re-reasoning over the raw image. Keep the existing plan→confirm→build→
verify→save loop structure, just fed from structured data instead of fresh
vision reasoning each time.

### 5.4 Always-on review gate
Per the agreed design: the spec is shown for review every time, not only on
low confidence. Implement this as a hard requirement in the prompt text (no
silent auto-approve path), matching what was already decided with the user.

---

## Phase 6 — Formalize catalog knowledge as data, not memory prose

Right now the decoded catalog meaning (PIL/FAS/CAP/CAS/BAS/PAN prefixes,
which parts fail `InsertElement()`, which parameters are real vs. inert,
profile sizes in stock) lives in a free-text memory file. That's fine for an
LLM session but is not something the server itself can validate against or
that `interpret_drawing` (5.1) can programmatically consult.

### 6.1 `catalog_manifest.json` (or `.yaml`) shipped in the repo
Structured version of `ironcad-catalog-reference.md`: per catalog, per
prefix family — profile sizes in stock, settable vs. inert parameters (with
the real parameter name, e.g. `Dicke`, `Gesamtbreite`), known-failing entries
(config-dialog-gated), and the recommended tool/technique for that family
(direct param / `ironcad_set_extrude_length` / Boolean tile-weld-trim /
compound-assembly insert once 2.2 exists). Generate an initial version from
the memory file, but make it the new source of truth going forward — update
it, not the prose memory, whenever a new catalog fact is discovered.

### 6.2 A "verify catalog manifest" script
Since memory already warns "re-verify with a live probe... this is a
snapshot, not an API contract" — write `scripts/verify_catalog_manifest.py`
that live-checks each manifest entry (does `InsertElement()` still succeed,
is the parameter still settable, has the stock size list changed) and
reports drift. Run it after any IronCAD/catalog update, not on every commit.

### 6.3 `interpret_drawing` and `ironcad_list_catalog_parts`/`describe_part`
consult the manifest for substitution decisions (closest available profile
size, which panel technique applies) instead of relying on the LLM's own
memory of the catalog each session.

---

## Phase 7 — Safety & audit hardening

### 7.1 Escape-hatch script tool: tighten, don't remove
`ironcad_execute_api_script` is explicitly documented as "not a sandbox."
For production use: keep it (Phase 2 reduces reliance on it, doesn't
eliminate the need for an escape hatch), but (a) require `allow_writes` to be
explicit per-call (already true per M4 notes — confirm), (b) log the full
script text + result on every call to a dedicated audit log distinct from
general debug logging, (c) consider a lightweight static check that flags
obviously dangerous calls (`Save`, `SaveAs`, `os.remove`, etc.) for a
confirmation echo in the tool's return value even though it already ran —
visibility, not a hard block.

### 7.2 Backup retention & pruning
`IRONCAD_MCP_BACKUP_DIR` currently only grows. Add a documented retention
policy (e.g. keep last N per source file, or prune older than X days) so
production usage doesn't silently fill a disk over months of sessions —
small, but a real "production" concern this spec-stage project hasn't
needed yet.

### 7.3 Write-audit trail
Per 4.4's structured logging, specifically ensure every write-tier call
records: which file was backed up (path), the backup's path, and a diff
summary (parts added/moved/renamed/deleted) — this is the difference between
"the log shows I called a tool" and "I can reconstruct exactly what changed
in the user's file if something looks wrong later."

---

## Phase 8 — Packaging, setup, and versioning

### 8.1 Automate what's currently manual setup pain
The one-time elevated COM registration, venv creation, and
`claude_desktop_config.json` editing are all manual today. At minimum, add a
single `scripts/setup.ps1` that: checks Python version/bitness, creates the
venv, installs deps, checks IronCAD version detected, and prints (or
optionally applies, with confirmation) the exact `claude_desktop_config.json`
snippet with the right absolute paths already filled in — reduces "production
readiness" friction for re-setup after a machine change, not for other users
(this is still a single-machine local tool).

### 8.2 Version pin hygiene
The `mcp<2` pin is a real future landmine (per the pyproject comment) — track
whether an `mcp` 2.x migration (`FastMCP`→`MCPServer` rename) is worth doing
proactively vs. reactively; at minimum, add a note/issue so it isn't
forgotten silently until a fresh install pulls an incompatible version.

### 8.3 Changelog / version bump discipline
`pyproject.toml` is still `0.1.0`. Once Phase 1–2 land, bump to `0.2.0` and
start a `CHANGELOG.md` — cheap, and makes "which fixes are actually in the
server I'm running" answerable without reading git log, especially relevant
since this project doesn't yet auto-restart on every code change (the user
has to restart Claude Desktop's MCP connection to pick up server changes).

---

## Phase 9 — Documentation polish

### 9.1 Update README's "Status" section
It currently claims "M0–M5 complete, verified against live IronCAD 2024"
unconditionally — after this plan, it should distinguish "core primitives
verified" from "BOM-faithful drawing-to-model pipeline," and link to
`MCP_TEST_REPORT_173.md` as the honest baseline, updating it again once
Phase 1–5 land.
### 9.2 Docstring audit
Every tool docstring should state its *known limitations* inline (the
extrude-direction quirk, the ambiguous-name behavior pre/post Phase 1, which
catalog families it's been verified against) — an LLM caller reads these
docstrings live every session; they're the actual "documentation" that
matters most here, more than the README.

---

## Suggested sequencing

1. **Phase 1** (bug fixes) — do first, everything else builds on correct
   name/index resolution and extrude direction.
2. **Phase 3.1–3.3** (tests) — write regression tests *as part of* fixing
   Phase 1, not after; they're the proof the fixes work and stay working.
3. **Phase 2** (missing tools) — the highest-value net-new capability; closes
   the "connector-real" gap the test flagged as the top structural fidelity
   issue.
4. **Phase 6** (catalog manifest) — do before Phase 5, since
   `interpret_drawing` depends on it.
5. **Phase 5** (two-stage pipeline) — the biggest workflow change; do once
   the primitives under it (Phase 1, 2, 6) are solid, so it isn't built on
   top of known-buggy foundations.
6. **Phase 4, 7, 8, 9** — ongoing hardening/polish, interleave as convenient;
   none of them block the others and all are cheap relative to Phase 2/5.
