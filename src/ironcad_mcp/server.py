"""FastMCP server entry point (spec Section 3, Tier A).

Run as:  python -m ironcad_mcp.server   (stdio transport)

Design notes:
* Logging goes to stderr only (spec 2.2). We do NOT swap ``sys.stdout`` for a
  tripwire here because the MCP stdio transport owns the real stdout for
  JSON-RPC; instead the noisy COM/python_ironcad work is confined to the worker
  thread, which wraps every call in a stdout->stderr redirect.
* The single STA COM worker (spec 2.1) is started/stopped in the FastMCP
  lifespan. Every tool routes COM work through ``run_on_com``.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from mcp.server.fastmcp import FastMCP

from .com_worker import run_on_com, start_worker, stop_worker
from .connection import get_state
from .logging_setup import get_logger, setup_logging
from .safety import current_mode

setup_logging()
_logger = get_logger()


@contextlib.asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[dict]:
    _logger.info("Starting COM worker (STA thread)")
    start_worker()
    try:
        yield {}
    finally:
        _logger.info("Stopping COM worker")
        stop_worker()


mcp = FastMCP("ironcad", lifespan=_lifespan)


# ---- Tier B/C/D/E tool modules -----------------------------------------
from . import tools_read, tools_catalog, tools_edit, tools_script  # noqa: E402

tools_read.register(mcp)
tools_catalog.register(mcp)
tools_edit.register(mcp)
tools_script.register(mcp)


# ---- Tier A: connection & status ---------------------------------------


@mcp.tool()
async def ironcad_status() -> dict:
    """Report whether the server can talk to IronCAD. NEVER throws.

    Returns a dict with:
      connected        - True if attached and the API is usable.
      api_registered   - True/False/None; False means the one-time elevated
                         `python -m python_ironcad` COM registration is needed.
      active_doc_name  - name of the open document, or null.
      ironcad_version  - detected IronCAD version string, or null.
      mode             - 'read_only' or 'read_write' (writes gated in read_only).
      last_error       - human-readable hint if something is wrong, else null.

    Call this first. It is safe whether or not IronCAD is running.
    """
    state = get_state()
    try:
        status = await run_on_com(state.status)
    except Exception as exc:  # noqa: BLE001 - status must never throw
        _logger.exception("ironcad_status failed")
        status = {
            "connected": False,
            "api_registered": None,
            "active_doc_name": None,
            "ironcad_version": None,
            "last_error": f"status check failed: {exc}",
        }
    status["mode"] = current_mode()
    return status


@mcp.tool()
async def ironcad_attach() -> dict:
    """Attach to the already-running IronCAD 2024 instance.

    This server never launches IronCAD (spec Section 0). If nothing is running,
    the result explains that IronCAD must be opened first. If IronCAD is running
    but the API is not registered, the result explains the one-time elevated
    `python -m python_ironcad` step.

    Returns the same shape as ironcad_status (plus 'mode').
    """
    state = get_state()
    status = await run_on_com(state.attach)
    status["mode"] = current_mode()
    return status


@mcp.prompt(title="Interpret a drawing into a build spec")
def interpret_drawing(notes: str = "") -> str:
    """Stage 1 of the two-stage drawing pipeline (spec Phase 5). Vision-only —
    makes ZERO ironcad_* tool calls. Attach alongside the source PDF/image.

    Produces a reviewable `<drawing_name>.spec.json` BEFORE any IronCAD
    interaction, so misreads (wrong BOM axis assignment, a missed catalog
    substitution, a panel-border mixup) are caught for free, not after
    several live IronCAD round-trips already burned interpreting mistakes.
    """
    extra = f"\n\nUser notes:\n{notes}\n" if notes else "\n"
    return (
        "You are interpreting an engineering drawing/sketch into a structured "
        "BUILD SPEC — a separate, reviewable step BEFORE any IronCAD tool call. "
        "Do NOT call any `ironcad_*` tool in this step except "
        "`ironcad_get_catalog_manifest` (no COM needed, safe here) and, if you "
        "already have a live connection, READ-ONLY calls to ground catalog "
        "names (`ironcad_list_catalogs`/`ironcad_list_catalog_parts`) — never a "
        "write.\n"
        f"{extra}"
        "WHY this is a separate step: interpretation mistakes (misreading which "
        "BOM item is a post vs. a rail, missing that a stated profile size has "
        "no catalog match, mixing up which side of a panel borders which "
        "framing member) are FREE to fix here — nothing has touched IronCAD "
        "yet. The SAME mistake caught only after `build_from_sketch` has "
        "started placing parts costs real tool round-trips and possibly a bad "
        "build to undo. A real example: an earlier B150004-173 rebuild "
        "assumed a ~42x17.5x24in short wide box from a first-pass drawing "
        "read; the real design measures 24x21x43in, a tall narrow tower — the "
        "post/rail roles had been swapped. This step exists so that kind of "
        "error is caught on paper, not in IronCAD.\n\n"
        "1. Call `ironcad_get_catalog_manifest()` FIRST — it gives you the real "
        "stock profile sizes, which catalog entries actually work, and which "
        "parameter names apply to which compound assemblies (this is pre-"
        "verified, don't guess any of it from the drawing's own nominal "
        "callouts, e.g. a drawing calling for '45mm' profile has no catalog "
        "match in this install — 40mm or 50mm is the real substitution).\n"
        "2. Read the drawing (PDF/image). For BOM tables that are hard to read "
        "at normal resolution, note that this project's past sessions found "
        "high-resolution CROPPED renders of the BOM region necessary — if your "
        "environment supports it, prefer that over trusting a low-res full-page "
        "read.\n"
        "3. Produce a spec conforming EXACTLY to the schema in "
        "`ironcad_mcp.spec_schema.SPEC_SCHEMA` (source_drawing, "
        "overall_envelope_mm, bom[] each with item_id/role/qty/catalog_match, "
        "optionally panel_relationships[] and joint_connector_family, plus "
        "assumptions[], gaps[], confidence). For EVERY bom item: mark "
        "catalog_match.status 'resolved' (with the EXACT verified catalog "
        "entry_name, and a substitution_note if it isn't the drawing's literal "
        "spec) or 'unresolved' (with why_unresolved) — never leave it "
        "ambiguous. List every assumption made and every real gap — an honest "
        "'unresolved'/gap is far better than a confident guess here.\n"
        "4. Write the spec to `<drawing_name>.spec.json` NEXT TO the source "
        "drawing file (same directory).\n"
        "5. Call `ironcad_validate_spec(spec_path=...)` to check it — fix any "
        "schema errors before showing it to the user.\n"
        "6. ALWAYS show the spec to the user for review — every time, not only "
        "when confidence is low (this is a fixed workflow rule, not a "
        "judgment call). Wait for explicit approval or corrections before "
        "telling them to proceed to `build_from_sketch`. If they correct "
        "something, re-write the spec file and re-validate."
    )


@mcp.prompt(title="Build an IronCAD model from an approved spec")
def build_from_sketch(spec_path: str = "", notes: str = "") -> str:
    """Stage 2 of the two-stage drawing pipeline (spec Phase 5, was the
    original single-stage §8 prompt). Consumes an APPROVED `<drawing_name>.
    spec.json` from `interpret_drawing` — does NOT re-read/re-interpret the
    raw drawing itself. Encodes the plan -> confirm -> build -> verify ->
    save loop against that spec.

    `spec_path` should be the reviewed spec.json's path. If omitted, this
    prompt still works from `notes` alone (e.g. for a quick ad-hoc build with
    no drawing involved) — but for anything built FROM a drawing, always run
    `interpret_drawing` first and pass its spec_path here instead of
    re-deriving the plan from the image in this step.
    """
    spec_line = (
        f"\n\nApproved spec file: {spec_path}\n"
        if spec_path else
        "\n\nNo spec_path given — if you're building from a drawing, STOP and "
        "run the `interpret_drawing` prompt on it first, get the resulting "
        "spec.json reviewed and approved, then re-invoke this prompt with "
        "spec_path set. Only proceed without a spec for a quick ad-hoc build "
        "with no source drawing at all.\n"
    )
    extra = f"\nUser notes:\n{notes}\n" if notes else "\n"
    return (
        "You are building a model in IronCAD 2024 from an APPROVED build spec, "
        "using the user's own catalog, via the `ironcad_*` MCP tools.\n"
        f"{spec_line}{extra}"
        "BE CREDIT-EFFICIENT. Every image you capture and every tool round-trip "
        "costs tokens, so PLAN on paper, place in as few calls as possible, and "
        "VERIFY ONCE at the end — not after every part. Follow this loop and do "
        "not one-shot the whole build blind:\n\n"
        "0. If `spec_path` was given: call `ironcad_validate_spec(spec_path=...)` "
        "FIRST. If it's not valid, STOP and report the errors — do not attempt "
        "to build from a spec that fails its own schema. Read the validated "
        "spec's `bom`/`overall_envelope_mm`/`panel_relationships` as the SOURCE "
        "OF TRUTH for this build; do not re-derive dimensions from the raw "
        "drawing image even if it's attached to this conversation.\n"
        "1. GROUND (read-only, cheap): call `ironcad_status` (confirm connected + "
        "read_write mode; if read_only, tell the user to set "
        "IRONCAD_MCP_MODE=read_write and restart, and stop). Call "
        "`ironcad_get_catalog_manifest()` and `ironcad_list_catalog_parts` for the "
        "catalog(s) the spec's `catalog_match` entries name, and confirm each "
        "`entry_name` the spec claims is 'resolved' actually exists — never "
        "invent or assume a name is still valid.\n"
        "2. PLAN (no tool calls): from the spec's bom + panel_relationships + "
        "overall_envelope_mm, produce an ORDERED list of parts. For EACH part "
        "decide how it connects to the others and express its location "
        "RELATIVELY, not as a guessed world coordinate: pick an already-placed "
        "part as the anchor and give a small [dx,dy,dz] offset in METRES (a "
        "600 mm beam = 0.6). Only the very first part needs an absolute position "
        "(usually [0,0,0]). Fold each part's driving dimensions into a "
        "`parameters` map. For any panel bordering a channel-facing post per "
        "`panel_relationships`, apply the T-slot mid-channel inset rule from "
        "`ironcad_get_catalog_manifest(catalog='PAN_DIST')` rather than the "
        "spec's literal nominal panel size. PRESENT this plan to the user and "
        "wait for explicit confirmation before touching IronCAD (this is "
        "IN ADDITION TO the spec.json review already done in interpret_drawing "
        "— the spec was approved for INTERPRETATION, this plan is approved for "
        "EXECUTION).\n"
        "3. BUILD IN ONE CALL: pass the whole ordered plan to `ironcad_build_parts` "
        "— it takes one backup, places every part (each `relative_to` an earlier "
        "part with an `offset`, plus `parameters` and `instance_name`), and "
        "regenerates once. This is the efficient equivalent of dragging parts and "
        "snapping their anchors together. Read the returned `errors` list; only "
        "re-place the specific parts that failed (via `ironcad_add_catalog_part` "
        "or `ironcad_move_part`, again using `relative_to`/`offset`). For a "
        "compound catalog assembly (e.g. a resolved panel entry that's actually "
        "`PAN___P_N___+PIN4521` or `...+CAL4515SNN___+CAN4501`), use "
        "`ironcad_add_catalog_assembly` instead — it applies parameters AND "
        "regenerates the assembly correctly (a plain part-regenerate does not "
        "rebuild these). For a panel that doesn't fit a compound assembly's "
        "size range or isn't in a framed opening, use `ironcad_build_panel`. "
        "IMPORTANT: for symmetric structures, multiple placed parts often share "
        "one auto-BOM name (e.g. 4 identical posts) — resolve/verify/group them "
        "by `index` (`ironcad_get_part_bbox`/`ironcad_set_extrude_length`/etc.) "
        "or `indices` (`ironcad_connect_parts`), never by renaming (renaming a "
        "stock part destroys its BOM-length name permanently).\n"
        "4. VERIFY BY CALCULATION FIRST (do not rely on screenshots): parts have "
        "real size — a profile/extrusion has a cross-section (e.g. 40x40 mm). At "
        "joints, perpendicular members will INTERPENETRATE if you place their axes "
        "at the same point. MEASURE with `ironcad_get_part_bbox` (read the section "
        "and extents), compute placements so members BUTT rather than overlap "
        "(offset by the section thickness at corners), and confirm the whole build "
        "with `ironcad_check_interference` — it reports any parts whose solids "
        "overlap, computed from geometry. Aim for clash_count 0 among STRUCTURAL "
        "members (a panel recessed into its bordering post's T-slot channel, or "
        "hardware inside a compound catalog assembly clamping onto its own panel/ "
        "rail by design, is an EXPECTED overlap, not a defect — use "
        "`ironcad_list_children` on a flagged assembly to see what's actually "
        "inside it before assuming a clash is real). Only after the math checks "
        "out, call `ironcad_capture_view` ONCE as a final visual confirmation — "
        "not after every part.\n"
        "5. SAVE: only on the user's confirmation, `ironcad_save` (in place) or "
        "`ironcad_save_copy` (new path). Never overwrite a different file without "
        "overwrite=True.\n\n"
        "Names are the primary identifier; ambiguous names are refused — "
        "disambiguate with the `catalog` argument, `index`/`indices`, or a "
        "unique `instance_name` (never on a stock BOM part — see step 3).\n\n"
        "CONNECTING PARTS (the blue/white sphere workflow):\n"
        "  * POSITION where parts meet using `relative_to` + `offset` (in "
        "ironcad_build_parts / add_catalog_part / add_catalog_assembly / "
        "move_part) — anchor one part to another plus a metre offset, instead "
        "of guessing world coordinates.\n"
        "  * The ANCHOR (the sphere itself) is controllable: `ironcad_get_anchor` / "
        "`ironcad_set_anchor` read and set a part's anchor position (local metres) "
        "and behavior (move_freely/fixed_position/attached_to_surface/"
        "slide_along_surface).\n"
        "  * To make parts MOVE TOGETHER as one unit, after positioning them call "
        "`ironcad_connect_parts(names=[...]` and/or `indices=[...])` — it groups "
        "them into an assembly (verified). Do this once the group is placed, "
        "then move/verify the assembly as a whole."
    )


def main() -> None:
    """Console-script / module entry point."""
    _logger.info("ironcad-mcp starting (mode=%s)", current_mode())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
