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


# ---- Tier B/C tool modules ---------------------------------------------
from . import tools_read, tools_catalog  # noqa: E402

tools_read.register(mcp)
tools_catalog.register(mcp)


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


def main() -> None:
    """Console-script / module entry point."""
    _logger.info("ironcad-mcp starting (mode=%s)", current_mode())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
