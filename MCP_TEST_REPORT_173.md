# ironcad-mcp quality report — B150004-173 test build vs. real hand-built design

**Date:** 2026-09-24
**Method:** Drove the actual registered FastMCP tool functions in-process
(`mcp._tool_manager.get_tool(name).run(kwargs)` — the same dispatch path a real
MCP client hits, minus the stdio JSON-RPC transport) from a driver script,
against the live, already-running IronCAD 2024 session. Full transcript:
`mcp_driver_log.txt` / `run5.log` in the scratchpad; raw structured results in
`mcp_test_results.json`. Driver script: `mcp_driver.py` (scratchpad).

No real file was ever saved to. The test build lives in a fresh, unsaved
IronCAD scene and was optionally copied to
`...\scratchpad\mcp_test_b150004_173.ics` (new file, scratchpad only). The
user's real `b150004_173.ics` was only read via `GetOpenDocByName` (it was
already open as a tab in this IronCAD session) + the read-only MCP tools;
its on-disk timestamp (2026-09-17) is unchanged by this session.

## 1. Did the build succeed end-to-end?

**Yes, but not on the first attempt, and not via the workflow the tools'
own docstrings recommend.** Three real bugs/gaps were hit live:

1. **Ambiguous-name resolution silently defeats the documented "index
   fallback."** Every write/read tool that takes `name` + optional `index`
   (`ironcad_get_part_bbox`, `ironcad_set_extrude_length`,
   `ironcad_set_part_parameter`, `ironcad_move_part`, `ironcad_set_anchor`,
   `ironcad_describe_part`) resolves via `safety.resolve_by_name`, which
   checks for an *ambiguous exact-name match first* and only consults
   `index` when *zero* parts share the name. The moment 2+ same-length
   catalog parts exist (i.e. essentially always, for a symmetric T-slot
   frame — 4 posts, 4 rails, 3 panels all land on identical auto-BOM names
   like `PIL4040SNN609.6`), **every one of these calls refuses** with
   `"Ambiguous name ... use the index"` — despite an index having been
   passed. Confirmed live for all 10 frame members and all 3 panels in the
   first run (see `run3.log`). This makes `ironcad_build_parts`'s own
   documented pattern ("batch-place then resize/verify by name+index")
   **not actually usable** for any catalog family whose auto-name collapses
   on identical geometry — which is most stock T-slot members. Workaround
   used: interleave `ironcad_add_catalog_part` + `ironcad_set_extrude_length`
   one member at a time so only one part ever shares the default name —
   this defeats the "one batch call" credit-efficiency the tools are
   designed around, turning a 1-round-trip build into ~20.
   `ironcad_connect_parts` has **no index parameter at all**, so it could
   not group same-named siblings by position under any workaround short of
   renaming (which breaks BOM tracking, per the tools' own warnings) — it
   was not usable in this build at all.

2. **No first-class panel/Boolean tool exists.** Building the 3 plexiglass
   panels (tiling `PAN___P_N___` catalog units and `Boolean`-uniting them
   to an arbitrary size) has no dedicated `ironcad_*` tool — the only path
   is the Tier-E escape hatch, `ironcad_execute_api_script`. This is an
   honest tool (still part of the MCP surface), but it means panel-building
   knowledge lives entirely in ad-hoc Python snippets, not in a reusable,
   documented tool signature — a real gap for "hand it a drawing and have
   it build."

3. **`ironcad_execute_api_script`'s scene-wide `RegenerateParts(True)`
   pattern breaks once a Boolean-tiled part exists in the scene** — calling
   it raises a bare `COMError(-2147467263, 'Not implemented')` (confirmed
   live in isolation via `debug_panel.py`). `Boolean()` itself succeeds;
   only the scene-wide regenerate afterward fails. The working recipe
   (already used in the prior `build_b150004_173.py` harness, and reused
   here) is to rely on the per-part `IZPart.Regenerate()` called inside each
   tile instead — but nothing in the MCP layer documents this pitfall.

4. **Extrude-direction depends on the specific rotation applied, and is not
   foreseeable without testing.** The W-rails (`rotation_deg=[0,-90,-90]`)
   grow their default *forward* extrude toward **-X**, not +X — the
   opposite of the D-rails (unrotated) and posts (`[0,-90,0]`), which both
   grow correctly in their expected +axis. First attempt anchored the
   W-rails at their near corner and got a rail correctly *sized* (986.8 mm)
   but built **entirely outside the intended envelope** (spanning
   roughly x=[-0.95, +0.04] m instead of [0.04, 1.03] m) — this alone blew
   the overall X envelope out to ~79–60 in before being caught by inspecting
   raw bboxes. The `backward=True` flag on `ironcad_set_extrude_length` does
   **not** fix this — it sets an independent `ExtrudeDistance[1]` while
   leaving the catalog default's `ExtrudeDistance[0]` (500 mm) untouched,
   producing an element 500 mm longer than intended (confirmed:
   `PIL4040SNN1486.8` for a 986.8 mm target). The actual fix was
   anchoring the W-rail insertion at its *far* corner instead. This is
   exactly the kind of orientation-specific gotcha the tool's own docstring
   flags as "untested" — and it cost real trial-and-error here.

After these workarounds, the build completed cleanly: 10 frame members + 3
panels placed, all correctly sized and positioned, 0 fastener/cap parts
(per the standing no-hardware rule), verified by bbox + interference +
capture. `ironcad_status`/`ironcad_attach`, catalog listing, and the core
placement/resize calls all worked exactly as documented once the naming and
orientation issues were worked around.

## 2. Dimensional accuracy — three numbers per axis

| Axis | Drawing/BOM nominal (assumed by prior scripts) | **My MCP build** (measured, escape-hatch bbox dump) | **Real hand-built file** (measured, read-only) |
|---|---|---|---|
| "42-ish" member | 42.00 in | **42.00 in** (X) | **24.05 in** (X) |
| "17.5/24-ish" member | 17.50 in / 24.00 in | **17.63 in** (Y) / **24.13 in** (Z) | **21.04 in** (Y) / **43.15 in** (Z) |

**Headline finding, not just a rounding issue:** my build (and the BOM
reading baked into the prior `build_b150004_173.py` script it's based on)
assumed a short, wide box — 42 in wide × 17.5 in deep × 24 in tall. The real
file measures out to a **tall, narrow tower** — 24.05 × 21.04 × **43.15 in
tall**. The set of three dimensions barely overlaps between the two
({42, 17.6, 24.1} vs {24.05, 21.04, 43.15}). The real design's 4 identical
long members (`PIL4140SNN42.24`) are **42.24 in and stand vertically** (Z is
the long axis in the real file), the opposite of what the drawing BOM
comment in the prior script assumed ("4x 24.00in H-posts" vertical, "4x
42.00in W-rails" horizontal). Either the original PDF BOM reading had the
post/rail roles swapped, or `b150004_173.ics` on disk is oriented/scaled
differently than the drawing subassembly it's named for. Either way: **my
build matches its own (possibly wrong) assumption well** (42.00 in exact on
X; Y/Z within 0.6% — the small residual is leftover panel-tile offset
constants carried over from the prior script's legacy 45 mm-profile math,
not a fresh fitting error), **but it does not match the real design's
actual proportions.** This is the single most important accuracy finding in
this report.

## 3. Structural fidelity — BOM / technique comparison

| | My MCP build | Real hand-built file |
|---|---|---|
| Profile | `PIL4040SNN_` (40×40 mm) — substituted | `PIL4140SNN_` (41×40 mm) |
| Posts/rails | 4 posts + 4 W-rails + 2 D-rails = 10 extrusions, all correctly sized/positioned, 0 clashes between them | 4× `PIL4140SNN42.24` + 2× `Assembly1` (internals not resolved — `GetChildren()` on these two assemblies returned children whose `.Name`/`.Type` reads silently failed, a further read-tool gap) + 1× `PIL4140SNN5.75` stub |
| Connectors | **None** (per standing rule — user adds via IronCAD's one-click add-on) | **8× `FAS4041`** articulating connectors, placed loose at post ends |
| Panels | 3× Boolean-tiled `PAN___P_N___`, bare (no retention rail/cap parts), recessed ~3 mm into the slot mid-depth | 3× compound assemblies **`PAN___P_N___ + CAL4515SNN____ + CAN4501`** — the real panel-retention-rail + end-cap system |
| Grouping | Attempted `ironcad_connect_parts`; refused (ambiguous names, no index param) — build left as 13 ungrouped top-level parts | Real file groups related hardware into named sub-assemblies |
| Top-level part count | 13 | 18 (undercounts true part count — `Assembly1`/panel-assemblies each hide several more children) |

The MCP build gets the **easy 80%** right: right catalog family (closest
available profile), right general recipe (extrude + recess-fit panel), zero
clashes among the structural members themselves, and correctly honors the
no-fastener rule. It cannot currently reproduce, and has **no tool-level
path to** reproduce: the 41×40 mm profile (catalog has no such entry loaded
under `PIL_DIST` in this session — confirmed via `ironcad_list_catalog_parts`,
14 entries, `PIL4040SNN_` present, no 41×40 variant), the real
`FAS4041`-based joinery (excluded by design, not a gap), or the
`CAL4515SNN_`/`CAN4501` panel-retention-rail system (there is no MCP
tool for compound/nested catalog assemblies — `ironcad_add_catalog_part`
inserts one entry at a time and has no concept of "insert this named
sub-assembly combo"). The bare-tile panel substitute is dimensionally
plausible (correctly sized, correctly recessed) but is **not held the same
way** — it's a solid, buttless slab against the slot wall, not a panel
captured by a dedicated retention-rail-and-cap system that can be removed
for service, which is presumably the real design's whole point.

## 4. Interference results

`ironcad_check_interference(tolerance_mm=0.5)`: **13 parts, 78 pairs
checked, 16 "clashes"** — every single one is a panel-into-rail overlap of
3.0 mm or 6.35 mm depth (the deliberate recess-into-slot amount / half the
panel thickness), never a post-to-post or post-to-rail overlap. **0 real
structural clashes.** This matches this project's own documented caveat
(recessed-panel-vs-solid-post AABB overlap is an expected simplification,
not a defect) — `ironcad_check_interference` is doing exactly its job here
and the result is exactly what the design intends.

## 5. Bottom line

**Usable today for a rough structural mockup from a drawing; not yet usable
for a BOM-faithful, connector-real Robotunits model.** The tool layer's
core placement/measurement primitives work as documented once you know
their pitfalls, but three concrete gaps stand between this and a trustworthy
"drawing → real IronCAD model" pipeline:

1. **Fix `resolve_by_name`/`_resolve_top_element` to try the `index`
   fallback whenever it's supplied and valid, even when the name is
   ambiguous** — not only when the name has zero matches. This one change
   would make `ironcad_build_parts` + individual resize/bbox/interference
   calls actually composable for any symmetric structure (which is most of
   them), and is a small, surgical fix (one function in `safety.py`) with
   an outsized effect on every other tool in `tools_edit.py`/`tools_read.py`.
2. **Give `ironcad_connect_parts` (and ideally the resize/bbox tools) an
   `indices` list alternative to `names`**, since same-named siblings are
   the common case, not the exception, for stock catalog frames.
3. **Add a first-class "insert compound catalog assembly" and/or
   "boolean-tile a panel to size" tool**, instead of leaving both behind the
   generic script escape hatch — this is what would actually close the gap
   toward the real `PAN+CAL+CAN` retention-rail system and reduce reliance
   on ad-hoc Python for a routine, repeated operation.

A fourth, cheaper win: **re-verify the B150004-173 BOM's post/rail axis
assignment against the real file's actual measured geometry** (this session
found they don't match) before trusting any future rebuild's target
dimensions.

## Artifacts

- Render of my test build: `...\scratchpad\mcp_build_b150004_173_test.jpg`
- Test build (unsaved scene, saved as a new file, scratchpad only, never
  overwrites anything real): `...\scratchpad\mcp_test_b150004_173.ics`
- Full structured results (bboxes, interference, errors):
  `...\scratchpad\mcp_test_results.json`
- Full run transcript: `...\scratchpad\run5.log` (clean final run),
  `run3.log`/`run4.log` (earlier runs showing the bugs above as first
  encountered)
- Driver script used to exercise the tool layer: `...\scratchpad\mcp_driver.py`
