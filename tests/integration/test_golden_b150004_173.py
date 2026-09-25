"""Golden-file regression test (PRODUCTION_READINESS_PLAN Phase 3.2) for the
user's real hand-built b150004_173.ics.

The golden snapshot (tests/golden/b150004_173_real.json) was generated
READ-ONLY from the real file using the now-fixed traversal (ironcad_list_
children + ironcad_get_part_bbox, recursing correctly into every compound
assembly — see Phase 2's GetChildrenZArray fix). This test re-measures the
same file the same way and diffs against that snapshot.

This guards two things at once:
  1. The reference file itself hasn't silently changed/corrupted.
  2. The read tools (list_parts/list_children/get_part_bbox/check_interference)
     keep reporting the SAME numbers for a file that never changes — a
     regression here means a tool-layer change broke measurement, not that
     the design changed.

It is NOT a "does our build match the real design" test — this project's
own build tools don't yet reach BOM parity (see MCP_TEST_REPORT_173.md); a
rebuild-vs-golden comparison is future work once a build script actually
uses the Phase 2 tools (ironcad_add_catalog_assembly for the real
FAS4041/CAL+CAN/CAP4041 hardware) to attempt full parity.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

REAL_FILE = r"C:\Users\admin\Desktop\Kelvin\NORTION\b150004_173.ics"
GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "..", "golden", "b150004_173_real.json")
IN = 0.0254


def _load_golden():
    with open(GOLDEN_PATH) as f:
        return json.load(f)


def _switch_to_real():
    import comtypes.gen.ICAPIIRONCADLib as ICAPI

    from ironcad_mcp.connection import get_state
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


def test_real_file_matches_golden_snapshot(call, attached):
    from ironcad_mcp.com_worker import run_on_com

    active = asyncio.run(run_on_com(_switch_to_real))
    if not active or "b150004_173" not in active:
        pytest.skip(f"reference file not available on this machine: {REAL_FILE}")

    golden = _load_golden()

    doc_info = call("ironcad_get_active_doc_info")
    assert doc_info.get("top_level_count") == golden["top_level_part_count"]

    lst = call("ironcad_list_parts")
    histogram: dict = {}
    mins: list = []
    maxs: list = []
    for p in lst["parts"]:
        if p["type"] == "part":
            bb = call("ironcad_get_part_bbox", name=p["name"], index=p["index"])
            if bb.get("error"):
                continue
            histogram[p["name"]] = histogram.get(p["name"], 0) + 1
            mins.append(bb["min_m"])
            maxs.append(bb["max_m"])
        elif p["type"] == "assembly":
            children = call("ironcad_list_children", name=p["name"],
                             index=p["index"], recursive=True)
            if children.get("error"):
                continue
            for c in children["children"]:
                if c.get("type_code") != 1:
                    continue
                histogram[c["name"]] = histogram.get(c["name"], 0) + 1
                bb = c.get("bbox_m")
                if bb:
                    mins.append(bb["min_m"])
                    maxs.append(bb["max_m"])

    assert histogram == golden["bom_histogram"], (
        "leaf-part BOM histogram changed -- either the real file was modified, "
        "or a read-tool regression is under/over-counting parts"
    )
    assert sum(histogram.values()) == golden["leaf_part_count"]

    gmin = [min(m[k] for m in mins) for k in range(3)]
    gmax = [max(m[k] for m in maxs) for k in range(3)]
    env_in = [(gmax[k] - gmin[k]) / IN for k in range(3)]
    tol = golden["envelope_tolerance_in"]
    for k in range(3):
        assert abs(env_in[k] - golden["envelope_in"][k]) < tol, (
            f"axis {k}: measured {env_in[k]:.3f}in vs golden "
            f"{golden['envelope_in'][k]:.3f}in (tolerance {tol}in)"
        )

    interf = call("ironcad_check_interference", tolerance_mm=0.5)
    assert interf["clash_count"] == golden["clash_count"], (
        f"clash_count changed ({interf['clash_count']} vs golden "
        f"{golden['clash_count']}) -- see golden file's clash_count_note "
        f"before assuming this is a bug"
    )
