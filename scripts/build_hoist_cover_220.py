"""B150004-220 full "SOURCE HOIST COVER" assembly: 1x FRONT subassembly
(B150004-221, has an end-cap panel closing the far end) + 2x BACK
subassembly (B150004-173, both open-ended) chained along +X, each unit's
own real drawing envelope preserved (42.00in W-rail / 17.50in D-rail /
24.00in H-post nominal, PIL4040SNN_ 40x40mm substituted for the drawing's
45mm profile per user direction -- rail lengths recomputed so the OUTER
envelope still matches the drawing, per the project's standing
dimension-planning rule).

Simplifications flagged to the user (no clean catalog match / low
structural significance):
  - Item 9 (173 only): 1x McMaster 3-1/2" corner bracket + item 3 (1x 5.75in
    rail instead of one of the three 17.50in D-rails) forming a small
    internal bracket near the drawing's clearance-silhouette callout. Not
    modeled; all D-rails built at the recomputed 17.50in-equivalent length
    for both unit styles.
  - Corner brackets / mounting feet (BOM items 7,8,9,10 in both subs) have
    no clean catalog equivalent (see ironcad-catalog-reference memory) --
    not modeled, matches the earlier 221-only build.
"""
import sys, os, math
sys.path.insert(0, r"C:\Users\admin\Desktop\Ironcad Automation\ironcad-mcp\src")
os.environ.setdefault("IRONCAD_MCP_MODE", "read_write")

import pythoncom
pythoncom.CoInitialize()

from ironcad_mcp.connection import get_state

state = get_state()
state.attach()
import comtypes.gen.ICAPIIRONCADLib as ICAPI

iapp = state.app.QueryInterface(ICAPI.IZIronCADApp)
iapp.OpenNewScene(False, True, "")
print("fresh scene:", state.active_doc_name())

cmgr = state.catalog_mgr()
pil_cat = next(cmgr.Catalog[i] for i in range(int(cmgr.Count)) if str(cmgr.Catalog[i].Name) == "PIL_DIST")
pil_entry = next(pil_cat.Entry[j] for j in range(int(pil_cat.EntryCount)) if str(pil_cat.Entry[j].Name) == "PIL4040SNN_")
pan_cat = next(cmgr.Catalog[i] for i in range(int(cmgr.Count)) if str(cmgr.Catalog[i].Name) == "PAN_DIST")
pan_entry = next(pan_cat.Entry[j] for j in range(int(pan_cat.EntryCount)) if str(pan_cat.Entry[j].Name) == "PAN___P_N___")

FWD = 0
IN = 0.0254
Z_UNITE = 1
Z_SUBTRACT = 2
TILE = 0.5  # native panel footprint, metres

# ---------------- shared per-unit envelope (identical for 221 & 173) ------
W_env = 42.00 * IN + 2 * 0.045
D_env = 17.50 * IN + 2 * 0.045
H = 24.00 * IN
new_W_rail = W_env - 2 * 0.04
new_D_rail = D_env - 2 * 0.04
Wc = W_env - 0.04
Dc = D_env - 0.04
Hc = H - 0.04
THK = 0.25 * IN
SLOT_INSET = 0.003
FOOT_LEN = 1.45 * IN  # foot-stub length: real H envelope (25.45in) minus item6 post nominal (24.00in)
print(f"per-unit envelope: W={W_env:.4f} D={D_env:.4f} H={H:.4f}  (m)")
print(f"per-unit envelope: W={W_env/IN:.2f} D={D_env/IN:.2f} H={H/IN:.2f}  (in)")


def place_extrusion(rot, pos, length_m):
    el = pil_entry.InsertElement()
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    for axis, deg in zip((0, 1, 2), rot):
        if deg:
            m.SetRotation(axis, float(deg), True)
    m.SetTranslation(*pos)
    se.SetPositionTransform(m)
    block = el.GetFirstChild()
    ef = block.QueryInterface(ICAPI.IZExtrudeFeature)
    ef.ExtrudeDistance[FWD] = length_m
    el.QueryInterface(ICAPI.IZPart).Regenerate()
    return el


def bbox_of(el):
    part = el.QueryInterface(ICAPI.IZPart)
    return [round(v, 6) for v in part.GetBoundingBox(False)]


def _panel_tile(x, y, thickness_m):
    el = pan_entry.InsertElement()
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    m.MakeIdentity()
    m.SetTranslation(x + 0.1914, y + 0.0515, 0.0)
    se.SetPositionTransform(m)
    block = el.GetFirstChild()
    ef = block.QueryInterface(ICAPI.IZExtrudeFeature)
    ef.ExtrudeDistance[FWD] = thickness_m
    el.QueryInterface(ICAPI.IZPart).Regenerate()
    return el


def _trim_x(el, target_w, row_y):
    part = el.QueryInterface(ICAPI.IZPart)
    tool = _panel_tile(target_w, row_y, 0.05)
    tool_part = tool.QueryInterface(ICAPI.IZPart)
    part.Boolean(tool_part, Z_SUBTRACT)


def build_panel(target_w, target_h, thickness_m):
    n_cols = max(1, math.ceil(round(target_w / TILE, 6)))
    n_rows = max(1, math.ceil(round(target_h / TILE, 6)))

    def build_row(row_y):
        tiles = [_panel_tile(c * TILE, row_y, thickness_m) for c in range(n_cols)]
        row_el = tiles[0]
        row_part = row_el.QueryInterface(ICAPI.IZPart)
        for t in tiles[1:]:
            row_part.Boolean(t.QueryInterface(ICAPI.IZPart), Z_UNITE)
        if n_cols * TILE > target_w + 1e-9:
            _trim_x(row_el, target_w, row_y)
        return row_el

    rows = [build_row(r * TILE) for r in range(n_rows)]
    panel_el = rows[0]
    panel_part = panel_el.QueryInterface(ICAPI.IZPart)
    for r in rows[1:]:
        panel_part.Boolean(r.QueryInterface(ICAPI.IZPart), Z_UNITE)

    if n_rows * TILE > target_h + 1e-9:
        y_tool_row = build_row(target_h)
        panel_part.Boolean(y_tool_row.QueryInterface(ICAPI.IZPart), Z_SUBTRACT)

    return panel_el


def rotate_chain(el, steps):
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    m.MakeIdentity()
    se.SetPositionTransform(m)
    for axis, deg in steps:
        m = se.GetPositionTransform()
        m.SetRotation(axis, float(deg), True)
        se.SetPositionTransform(m)


def translate_to(el, target_min):
    cur = bbox_of(el)
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    dx, dy, dz = (target_min[k] - cur[k] for k in range(3))
    tx, ty, tz = m.GetTranslation()
    m.SetTranslation(tx + dx, ty + dy, tz + dz)
    se.SetPositionTransform(m)


def shift_x(el, dx):
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    tx, ty, tz = m.GetTranslation()
    m.SetTranslation(tx + dx, ty, tz)
    se.SetPositionTransform(m)


def build_unit(x_offset, add_cap, label):
    """Build one subassembly box (frame + panels) at the given global X
    offset. add_cap=True builds the item1 end-cap panel (221 front-sub
    style); add_cap=False leaves that D-face open (173 back-sub style)."""
    els = []

    for cx in (0.0, Wc):
        for cy in (0.0, Dc):
            els.append(place_extrusion([0, -90, 0], (cx + x_offset, cy, 0.0), H))
            # foot stub: short extrusion continuing below the post's bottom
            # face (z<0), sized so total post+foot height matches the
            # drawing's true overall H envelope (25.45in vs item6's 24.00in
            # nominal post length -- the ~1.45in delta is this foot).
            els.append(place_extrusion([0, -90, 0], (cx + x_offset, cy, -FOOT_LEN), FOOT_LEN))
    for cy in (0.0, Dc):
        for cz in (0.0, Hc):
            els.append(place_extrusion([0, 90, -90], (0.04 + x_offset, cy + 0.04, cz), new_W_rail))
    d_positions = [(0.0, 0.0), (Wc, 0.0), (0.0, Hc), (Wc, Hc)]
    d_positions.remove((Wc, Hc))
    for cx, cz in d_positions:
        els.append(place_extrusion([0, 0, 0], (cx + x_offset, 0.04, cz + 0.04), new_D_rail))

    # Panels are sized to the FULL frame envelope on this face (W_env/D_env/H
    # -- the actual built-frame span), not the drawing's nominal BOM panel
    # size (which is a bit smaller and left visible gaps at one edge since
    # placement started flush at local (0,0) instead of centered). Sizing to
    # the real envelope + local origin (0,0) already matching the frame's
    # own origin means the panel fills the opening edge-to-edge with no gap.
    item2a = build_panel(W_env, H, THK)
    rotate_chain(item2a, [(0, 90.0), (1, 90.0)])
    translate_to(item2a, (0.0, SLOT_INSET, 0.0))
    shift_x(item2a, x_offset)
    els.append(item2a)

    item2b = build_panel(W_env, H, THK)
    rotate_chain(item2b, [(0, 90.0), (1, 90.0)])
    translate_to(item2b, (0.0, D_env - SLOT_INSET, 0.0))
    shift_x(item2b, x_offset)
    els.append(item2b)

    item3 = build_panel(W_env, D_env, THK)
    translate_to(item3, (0.0, 0.0, H - SLOT_INSET))
    shift_x(item3, x_offset)
    els.append(item3)

    if add_cap:
        item1 = build_panel(D_env, H, THK)
        rotate_chain(item1, [(2, 90.0), (1, 90.0)])
        translate_to(item1, (Wc + 0.04 - SLOT_INSET, 0.0, 0.0))
        shift_x(item1, x_offset)
        els.append(item1)

    print(f"unit '{label}' @ x_offset={x_offset/IN:.2f}in: {len(els)} elements")
    return els


# ---------------- build the 3-segment chain ---------------------------
# BACK (173-style, no end cap) x2, then FRONT (221-style, capped) at the
# outward far end -- matches the drawing: only the FRONT sub is a closed
# terminal end, the rest of the tunnel (2x BACK) stays open.
all_els = []
back1 = build_unit(0.0 * W_env, add_cap=False, label="back#1 (173)")
back2 = build_unit(1.0 * W_env, add_cap=False, label="back#2 (173)")
front = build_unit(2.0 * W_env, add_cap=True, label="front (221)")
all_els += back1 + back2 + front

# ---------------- group + verify + capture -----------------------------
def group(els, name):
    if not els:
        return None
    asm = state.scene().AssembleElements(els)
    asm_el = asm.QueryInterface(ICAPI.IZElement)
    asm_el.Name = name
    return asm_el

asm_back1 = group(back1, "B150004_173_unit1")
asm_back2 = group(back2, "B150004_173_unit2")
asm_front = group(front, "B150004_221_unit")

top_groups = [g for g in (asm_back1, asm_back2, asm_front) if g is not None]

mins = [min(bbox_of(e)[k] for e in all_els) for k in range(3)]
maxs = [max(bbox_of(e)[k + 3] for e in all_els) for k in range(3)]
print("\ntotal elements:", len(all_els))
print("envelope min (m):", [round(v, 5) for v in mins])
print("envelope max (m):", [round(v, 5) for v in maxs])
print("envelope size (in):", [round((maxs[k] - mins[k]) / IN, 3) for k in range(3)])

master = None
try:
    master_asm = state.scene().AssembleElements(top_groups)
    master = master_asm.QueryInterface(ICAPI.IZElement)
    master.Name = "B150004_220_SOURCE_HOIST_COVER"
    print("master assembly ->", master.Name)
except Exception as exc:
    print("master assemble failed (non-fatal):", exc)

try:
    target_el = master if master is not None else top_groups[0]
    se_top = state.scene_element(target_el)
    m = se_top.GetPositionTransform()
    m.SetRotation(1, -35.0, True)
    m.SetRotation(2, -30.0, True)
    se_top.SetPositionTransform(m)
except Exception as exc:
    print("view rotate skipped:", exc)

out_path = r"C:\Users\admin\AppData\Local\Temp\claude\C--Users-admin-Desktop-Ironcad-Automation\b5c7b6f9-60e5-4c3e-b522-d895e0df865d\scratchpad\hoist_cover_220_verify.jpg"
scene = state.scene()
try:
    scene.AdjustCameraToFitShapeInRect(1600, 1000)
except Exception:
    pass
scene.ExportImage(out_path, 3, 1600, 1000, True)
print("capture:", out_path, os.path.exists(out_path))
