"""Live regression tests for the 3 Phase-1 bugs (PRODUCTION_READINESS_PLAN).

Formalizes the ad-hoc verify_phase1_fixes.py scratchpad script (used to prove
these fixes live during development) into a permanent, reusable suite. See
`ironcad-mcp-project` memory / MCP_TEST_REPORT_173.md for how each bug was
originally found.
"""
from __future__ import annotations


def test_ambiguous_name_resolves_by_index(call, fresh_scene):
    """1.1 (safety.resolve_by_name): an `index` must disambiguate a name
    shared by 2+ parts, not only be consulted when the name is missing.
    Symmetric stock catalog frames hit this constantly (every member cut to
    the same length shares one auto-BOM name)."""
    positions = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]
    placed = []
    for pos in positions:
        r = call("ironcad_add_catalog_part", name="PIL4040SNN_",
                  catalog="PIL_DIST", position=pos)
        assert not r.get("error"), r
        placed.append(r["created"])

    shared_name = placed[0]["name"]
    lst = call("ironcad_list_parts")
    matching = [p for p in lst["parts"] if p["name"] == shared_name]
    assert len(matching) >= 3, "setup failed: parts should share one auto BOM name"

    centers = []
    for p in matching:
        bb = call("ironcad_get_part_bbox", name=shared_name, index=p["index"])
        assert not bb.get("error"), bb
        centers.append(tuple(bb["center_m"]))
    assert len(set(centers)) == len(centers), \
        "each index must resolve to a DIFFERENT part, not silently refuse or alias"


def test_ambiguous_name_without_index_still_refuses(call, fresh_scene):
    """The fix must not weaken the fail-safe: an ambiguous name with NO index
    still has to refuse rather than silently guess."""
    for pos in ([0.0, 0, 0], [1.0, 0, 0]):
        r = call("ironcad_add_catalog_part", name="PIL4040SNN_",
                  catalog="PIL_DIST", position=pos)
        assert not r.get("error"), r
    bb = call("ironcad_get_part_bbox", name="PIL4040SNN500")
    assert bb.get("error") and "Ambiguous" in bb["error"]


def test_unique_exact_match_still_wins_over_a_stray_index(call, fresh_scene):
    """A clean, unambiguous name match must still take priority — index is a
    fallback for missing/ambiguous names, never an override."""
    r1 = call("ironcad_add_catalog_part", name="PIL4040SNN_", catalog="PIL_DIST",
              position=[0.0, 0.0, 0.0])
    assert not r1.get("error"), r1
    renamed = "unique_test_part"
    # Rename (via raw COM -- deliberately not through a BOM-tracked stock-part
    # path, since this is a throwaway scratch part) to make the name unique,
    # then pass a deliberately WRONG index -- the unique NAME match must win.
    from ironcad_mcp.com_worker import run_on_com
    from ironcad_mcp.connection import get_state
    import asyncio

    def do_rename():
        state = get_state()
        for el in state.iter_top_elements():
            el.Name = renamed
            return
    asyncio.run(run_on_com(do_rename))

    bb = call("ironcad_get_part_bbox", name=renamed, index=999)
    assert not bb.get("error"), bb
    assert bb["name"] == renamed


def test_connect_parts_by_indices(call, fresh_scene):
    """1.2 (ironcad_connect_parts): must group same-named siblings via
    `indices`, without needing to rename them (renaming a stock part breaks
    its auto BOM-length name permanently — see tools_catalog.py docstrings)."""
    for pos in ([0.0, 0, 0], [1.0, 0, 0]):
        r = call("ironcad_add_catalog_part", name="PIL4040SNN_",
                  catalog="PIL_DIST", position=pos)
        assert not r.get("error"), r
    lst = call("ironcad_list_parts")
    idxs = [p["index"] for p in lst["parts"] if p["name"] == "PIL4040SNN500"]
    assert len(idxs) == 2
    conn = call("ironcad_connect_parts", indices=idxs, assembly_name="phase1_ci_asm")
    assert not conn.get("error"), conn
    assert conn.get("count") == 2
    assert conn.get("assembly") == "phase1_ci_asm"


def test_connect_parts_still_refuses_fewer_than_two(call, fresh_scene):
    r = call("ironcad_add_catalog_part", name="PIL4040SNN_", catalog="PIL_DIST",
              position=[0.0, 0.0, 0.0])
    assert not r.get("error"), r
    conn = call("ironcad_connect_parts", indices=[0])
    assert conn.get("error")


def test_backward_extrude_is_exact_length(call, fresh_scene):
    """1.3 (ironcad_set_extrude_length): backward=True must produce EXACTLY
    the requested length. Previously it left the other direction's stale
    500mm default untouched, producing target+500mm instead of target."""
    r = call("ironcad_add_catalog_part", name="PIL4040SNN_", catalog="PIL_DIST",
              position=[0.0, 0.0, 0.0])
    assert not r.get("error"), r
    ext = call("ironcad_set_extrude_length", name=r["created"]["name"],
               length_m=0.3, backward=True)
    assert not ext.get("error"), ext
    assert abs(max(ext["dims_m"]) - 0.3) < 0.002, ext
    assert abs(ext["other_direction_length_m"]) < 1e-6, ext
    assert ext["name"] == "PIL4040SNN300"


def test_forward_extrude_unaffected_by_the_fix(call, fresh_scene):
    """The fix (explicitly zeroing the OTHER direction) must be a no-op for
    the ordinary forward-only path that already worked before."""
    r = call("ironcad_add_catalog_part", name="PIL4040SNN_", catalog="PIL_DIST",
              position=[0.0, 0.0, 0.0])
    assert not r.get("error"), r
    ext = call("ironcad_set_extrude_length", name=r["created"]["name"], length_m=0.6)
    assert not ext.get("error"), ext
    assert abs(max(ext["dims_m"]) - 0.6) < 0.002, ext
    assert abs(ext["other_direction_length_m"]) < 1e-6, ext
