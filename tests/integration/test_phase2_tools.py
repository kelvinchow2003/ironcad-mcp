"""Live tests for the Phase-2 tools (PRODUCTION_READINESS_PLAN): assembly
child enumeration, compound-catalog-assembly insert, and first-class panel
building. Formalizes verify_phase2_fixes.py from Phase 2 development.
"""
from __future__ import annotations

import asyncio

import pytest

REAL_FILE = r"C:\Users\admin\Desktop\Kelvin\NORTION\b150004_173.ics"


def test_list_children_on_real_hand_built_assembly(call, attached):
    """2.4: assembly-child enumeration must resolve REAL children (previously
    failed silently — GetChildren() returned unusable raw values). Reads the
    user's real hand-built file READ-ONLY; skips cleanly if it isn't present
    on this machine (the reference lives on the developer's own PC)."""
    import comtypes.gen.ICAPIIRONCADLib as ICAPI

    from ironcad_mcp.com_worker import run_on_com
    from ironcad_mcp.connection import get_state

    def switch():
        state = get_state()
        try:
            doc = state.base.GetOpenDocByName(REAL_FILE)
        except Exception:  # noqa: BLE001
            doc = None
        if doc is None:
            try:
                doc = state.base.OpenFile(REAL_FILE, True)  # True = read-only
            except Exception:  # noqa: BLE001
                return None
        state.app.QueryInterface(ICAPI.IZBaseApp).ActiveDoc = doc
        return state.active_doc_name()

    active = asyncio.run(run_on_com(switch))
    if not active or "b150004_173" not in active:
        pytest.skip(f"reference file not available on this machine: {REAL_FILE}")

    lst = call("ironcad_list_parts")
    assemblies = [p for p in lst["parts"] if p["type"] == "assembly"]
    assert assemblies, "expected at least one top-level assembly in the real file"

    target = assemblies[0]
    children = call("ironcad_list_children", name=target["name"], index=target["index"])
    assert not children.get("error"), children
    assert children["count"] > 0
    assert all(c.get("name") for c in children["children"]), (
        "every child must resolve a real name -- regression guard for the "
        "GetChildren() raw-value marshaling bug"
    )
    assert all(c.get("type_code") in (1, 2) for c in children["children"])


def test_add_catalog_assembly_pin4521(call, fresh_scene):
    """2.2/2.3: the PIN4521 panel-sizing family uses Gesamtbreite/Gesamtlaenge."""
    r = call("ironcad_add_catalog_assembly", name="PAN___P_N___+PIN4521",
              catalog="PAN_DIST", position=[0, 0, 0],
              parameters={"Gesamtbreite": 0.7, "Gesamtlaenge": 0.9})
    assert not r.get("error"), r
    assert r["bbox_m"] is not None
    assert r["created"]["parameters"]["Gesamtbreite"]["new"] == 0.7
    assert r["created"]["parameters"]["Gesamtlaenge"]["new"] == 0.9


def test_add_catalog_assembly_cal_can_retention_rail(call, fresh_scene):
    """2.2/2.3: the REAL Robotunits retention-rail family
    (PAN+CAL4515SNN+CAN4501) uses DIFFERENT parameter names: Breite/Hoehe,
    not Gesamtbreite/Gesamtlaenge -- do not assume one name set for both."""
    r = call("ironcad_add_catalog_assembly", name="PAN___P_N___+CAL4515SNN___+CAN4501",
              catalog="PAN_DIST", position=[0, 0, 0],
              parameters={"Breite": 0.8, "Hoehe": 0.6})
    assert not r.get("error"), r
    assert r["bbox_m"] is not None
    assert r["created"]["parameters"]["Breite"]["new"] == 0.8
    assert r["created"]["parameters"]["Hoehe"]["new"] == 0.6


def test_add_catalog_assembly_refuses_leaf_part(call, fresh_scene):
    """Must refuse cleanly (not crash) when pointed at a plain leaf part."""
    r = call("ironcad_add_catalog_assembly", name="PIL4040SNN_", catalog="PIL_DIST",
              position=[0, 0, 0])
    assert r.get("error")
    assert "IZAssembly" in r["error"] or "not a compound" in r["error"]


@pytest.mark.parametrize("orientation,axis_w,axis_h", [
    ("flat_xy", 0, 1),
    ("face_xz", 0, 2),
    ("face_yz", 1, 2),
])
def test_build_panel_orientations(call, fresh_scene, orientation, axis_w, axis_h):
    """2.1: ironcad_build_panel must hit the EXACT target size on the correct
    global axes for each documented orientation."""
    w, h, t = 0.9, 0.5, 0.00635
    r = call("ironcad_build_panel", target_w_m=w, target_h_m=h, thickness_m=t,
              position=[0, 0, 0], orientation=orientation)
    assert not r.get("error"), r
    dims = r["dims_m"]
    assert abs(dims[axis_w] - w) < 0.002, r
    assert abs(dims[axis_h] - h) < 0.002, r


def test_build_panel_rejects_unknown_orientation(call, fresh_scene):
    r = call("ironcad_build_panel", target_w_m=0.5, target_h_m=0.5,
              thickness_m=0.00635, position=[0, 0, 0], orientation="sideways")
    assert r.get("error")
