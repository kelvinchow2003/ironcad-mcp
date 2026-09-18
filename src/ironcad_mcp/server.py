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


@mcp.prompt(title="Build an IronCAD model from a sketch")
def build_from_sketch(notes: str = "") -> str:
    """Prime the sketch -> catalog build workflow (spec §8).

    Attach this prompt (optionally with notes) alongside an image of the
    drawing/sketch. It encodes the plan -> confirm -> build -> verify -> save loop.
    """
    extra = f"\n\nUser notes:\n{notes}\n" if notes else "\n"
    return (
        "You are building a model in IronCAD 2024 from a sketch/drawing, using "
        "the user's own catalog, via the `ironcad_*` MCP tools.\n"
        f"{extra}"
        "BE CREDIT-EFFICIENT. Every image you capture and every tool round-trip "
        "costs tokens, so PLAN on paper, place in as few calls as possible, and "
        "VERIFY ONCE at the end — not after every part. Follow this loop and do "
        "not one-shot the whole build blind:\n\n"
        "1. GROUND (read-only, cheap): call `ironcad_status` (confirm connected + "
        "read_write mode; if read_only, tell the user to set "
        "IRONCAD_MCP_MODE=read_write and restart, and stop). Call "
        "`ironcad_list_catalog_parts` for the catalog(s) you need and build the "
        "plan ONLY from the real names returned — never invent names.\n"
        "2. PLAN (no tool calls): from the image + notes, produce an ORDERED list "
        "of parts. For EACH part decide how it connects to the others and express "
        "its location RELATIVELY, not as a guessed world coordinate: pick an "
        "already-placed part as the anchor and give a small [dx,dy,dz] offset in "
        "METRES (a 600 mm beam = 0.6). Only the very first part needs an absolute "
        "position (usually [0,0,0]). Fold each part's driving dimensions into a "
        "`parameters` map. PRESENT this plan to the user and wait for explicit "
        "confirmation before touching IronCAD.\n"
        "3. BUILD IN ONE CALL: pass the whole ordered plan to `ironcad_build_parts` "
        "— it takes one backup, places every part (each `relative_to` an earlier "
        "part with an `offset`, plus `parameters` and `instance_name`), and "
        "regenerates once. This is the efficient equivalent of dragging parts and "
        "snapping their anchors together. Read the returned `errors` list; only "
        "re-place the specific parts that failed (via `ironcad_add_catalog_part` "
        "or `ironcad_move_part`, again using `relative_to`/`offset`).\n"
        "4. VERIFY ONCE: call `ironcad_capture_view` a SINGLE time on the finished "
        "build and inspect it. Fix only what is visibly wrong with targeted "
        "relative moves, then re-capture at most once more. Do not capture after "
        "every part.\n"
        "5. SAVE: only on the user's confirmation, `ironcad_save` (in place) or "
        "`ironcad_save_copy` (new path). Never overwrite a different file without "
        "overwrite=True.\n\n"
        "Names are the primary identifier; ambiguous names are refused — "
        "disambiguate with the `catalog` argument or a unique `instance_name`.\n\n"
        "CONNECTING PARTS (the blue/white sphere workflow):\n"
        "  * POSITION where parts meet using `relative_to` + `offset` (in "
        "ironcad_build_parts / add_catalog_part / move_part) — anchor one part to "
        "another plus a metre offset, instead of guessing world coordinates.\n"
        "  * The ANCHOR (the sphere itself) is controllable: `ironcad_get_anchor` / "
        "`ironcad_set_anchor` read and set a part's anchor position (local metres) "
        "and behavior (move_freely/fixed_position/attached_to_surface/"
        "slide_along_surface).\n"
        "  * To make parts MOVE TOGETHER as one unit, after positioning them call "
        "`ironcad_connect_parts([nameA, nameB, ...])` — it groups them into an "
        "assembly (verified). Do this once the group is placed, then move/verify "
        "the assembly as a whole."
    )


def main() -> None:
    """Console-script / module entry point."""
    _logger.info("ironcad-mcp starting (mode=%s)", current_mode())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
