"""Shared fixtures for the LIVE integration suite (PRODUCTION_READINESS_PLAN
Phase 3.1). These tests drive the real, registered MCP tool functions —
exactly the dispatch path a real MCP client hits, minus the stdio JSON-RPC
transport — against an ALREADY-RUNNING IronCAD 2024 instance. This server
never launches IronCAD itself (see README), and neither does this suite.

Opt-in only, skipped by default so a plain `pytest` stays mock-only. To
actually run this suite, open IronCAD 2024 first, then:

    IRONCAD_MCP_LIVE_TESTS=1 pytest tests/integration -v

Every test that mutates the scene works in a FRESH, UNSAVED scratch scene via
the `fresh_scene` fixture and never calls a save tool — these tests never
touch or risk any real .ics file. The one test that reads a real file
(test_phase2_tools.py's list_children test) opens it read-only and skips
cleanly if that file isn't present on the current machine.
"""
from __future__ import annotations

import asyncio
import os

import pytest

LIVE = os.environ.get("IRONCAD_MCP_LIVE_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="live IronCAD integration tests are opt-in; set IRONCAD_MCP_LIVE_TESTS=1 "
           "(with IronCAD 2024 already open) to run them",
)


@pytest.fixture(scope="session")
def mcp_worker(tmp_path_factory):
    """Start the real COM worker once for the whole live session; ensure
    read_write mode + a scratchpad-only backup dir; stop the worker at the
    end. Yields the registered FastMCP `mcp` instance."""
    os.environ["IRONCAD_MCP_MODE"] = "read_write"
    os.environ.setdefault(
        "IRONCAD_MCP_BACKUP_DIR", str(tmp_path_factory.mktemp("ironcad_mcp_backups"))
    )
    from ironcad_mcp.com_worker import start_worker, stop_worker
    from ironcad_mcp.server import mcp as _mcp

    start_worker()
    try:
        yield _mcp
    finally:
        stop_worker()


@pytest.fixture(scope="session")
def call(mcp_worker):
    """Sync helper: call(tool_name, **kwargs) -> the tool's raw dict result.
    Wraps asyncio.run() per call (each test in this suite is a plain sync
    function, matching the one existing async-tool test in test_smoke_mock.py
    — no pytest-asyncio config needed)."""
    def _call(tool_name: str, **kwargs):
        tool = mcp_worker._tool_manager.get_tool(tool_name)
        if tool is None:
            raise RuntimeError(f"Unknown tool: {tool_name}")
        return asyncio.run(tool.run(kwargs, convert_result=False))
    return _call


@pytest.fixture(scope="session")
def attached(call):
    """Ensure IronCAD is attached once per session; skip the WHOLE live
    session cleanly (not an error) if it isn't actually running."""
    st = call("ironcad_status")
    if not st.get("connected"):
        st = call("ironcad_attach")
    if not st.get("connected"):
        pytest.skip(f"could not attach to a running IronCAD instance: {st}")
    return st


@pytest.fixture()
def fresh_scene(attached):
    """Open a brand-new UNSAVED scene before a test that mutates geometry, so
    each test is isolated and nothing is ever at risk of touching a real
    file. Yields the new scene's name (e.g. 'Scene41'), then CLOSES it on
    teardown.

    The close-on-teardown matters: a live IronCAD session found live
    2026-09-24 that opening a fresh scene per test without ever closing it
    eventually exhausts the running IronCAD instance — after enough
    accumulated open scratch scenes in one session, `OpenNewScene` itself
    starts failing with a raw COMError ("The server threw an exception" /
    "The remote procedure call failed"), not a clean, catchable condition.
    Closing each scratch scene immediately after its test keeps a long
    integration run (or a long dev session doing lots of manual live
    probing) from silently degrading IronCAD itself.
    """
    import comtypes.gen.ICAPIIRONCADLib as ICAPI

    from ironcad_mcp.com_worker import run_on_com
    from ironcad_mcp.connection import get_state

    def _new():
        state = get_state()
        iapp = state.app.QueryInterface(ICAPI.IZIronCADApp)
        iapp.OpenNewScene(False, True, "")
        return state.active_doc_name(), state.active_doc()

    name, doc = asyncio.run(run_on_com(_new))
    try:
        yield name
    finally:
        def _close():
            try:
                doc.Close2(True)  # force-close, discard (never saved anyway)
            except Exception:  # noqa: BLE001
                try:
                    doc.Close()
                except Exception:  # noqa: BLE001
                    pass
        asyncio.run(run_on_com(_close))
