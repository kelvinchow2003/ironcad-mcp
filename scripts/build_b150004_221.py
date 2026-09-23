"""Standalone build of B150004-221 "SOURCE HOIST COVER, FRONT SUBASSEMBLY"
(Nordion), from scratch, PIL4040SNN_ 40x40mm T-slot substituted for the
drawing's 45mm profile (no 45mm size in this install's PIL_DIST catalog --
see ironcad-catalog-reference memory). Rail lengths recomputed so the
OUTER envelope still matches the drawing's real dimensions (per the
project's dimension-planning rule), not the drawing's nominal rail length.

BOM read from b150004_221.SLDDRW.pdf (verified via high-res crop of the
title-block BOM table):
  item1  x1  B150004-181  plexiglass w/ insert, 0.25in thick, 18.2 x 20.9  (end cap, D x H)
  item2  x2  B150004-182  plexiglass w/ insert, 0.25in thick, 20.9 x 42.7  (front/back, W x H)
  item3  x1  B150004-183  plexiglass w/ insert, 0.25in thick, 18.2 x 42.7  (top, W x D)
  item4  x4  T-slot 45mm sq, 42.00in long   (W-rails, all 4 positions)
  item5  x3  T-slot 45mm sq, 17.50in long   (D-rails, 3 of 4 positions -- far/cap-side top open)
  item6  x4  T-slot 45mm sq, 24.00in long   (H corner posts)
  item7  x4  mounting foot (McMaster 5537T414)          -- NOT modeled, no clean catalog match
  item8  x4  3-way corner bracket (McMaster 5537T292)   -- NOT modeled, no clean catalog match
  item9  x6  corner bracket, 1-3/4in (McMaster 5537T938)-- NOT modeled, no clean catalog match

Recipe (posts/rails/panels, rotation, T-slot recess, foot-stub for the
overall-height delta) mirrors the previously-verified build in
scripts/build_hoist_cover_full.py / build_hoist_cover_220.py's build_unit()
-- reused here as a standalone single-unit script per user request to
rebuild fresh (not reusing the user's own hand-built b150004_221.ics, which
used a different PIL4140 + FAS4041/CAL4515SNN/CAN4501 technique).
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

# ---------------- envelope (drawing nominal + 2x profile-width delta) -----
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
FOOT_LEN = 1.45 * IN  # foot-stub: true overall H (25.45in per drawing) minus item6 nominal 24.00in
print(f"envelope: W={W_env:.4f} D={D_env:.4f} H={H:.4f}  (m)  ->  "
      f"{W_env/IN:.2f} x {D_env/IN:.2f} x {H/IN:.2f} in")


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


# ---------------- build the frame -----------------------------------------
els = []

for cx in (0.0, Wc):
    for cy in (0.0, Dc):
        els.append(place_extrusion([0, -90, 0], (cx, cy, 0.0), H))
        # foot stub continuing below the post's bottom face, sized so total
        # post+foot height matches the drawing's true overall H envelope.
        els.append(place_extrusion([0, -90, 0], (cx, cy, -FOOT_LEN), FOOT_LEN))
for cy in (0.0, Dc):
    for cz in (0.0, Hc):
        els.append(place_extrusion([0, 90, -90], (0.04, cy + 0.04, cz), new_W_rail))
# 3 of 4 D-rail positions -- far (cap) top corner omitted (matches the
# drawing's own item5 qty=3, verified previously against the 220 assembly
# drawing to <0.2in on 2 of 3 axes).
d_positions = [(0.0, 0.0), (Wc, 0.0), (0.0, Hc), (Wc, Hc)]
d_positions.remove((Wc, Hc))
for cx, cz in d_positions:
    els.append(place_extrusion([0, 0, 0], (cx, 0.04, cz + 0.04), new_D_rail))

# Panels sized to the full built-frame envelope (fills opening edge-to-edge).
item2a = build_panel(W_env, H, THK)
rotate_chain(item2a, [(0, 90.0), (1, 90.0)])
translate_to(item2a, (0.0, SLOT_INSET, 0.0))
els.append(item2a)

item2b = build_panel(W_env, H, THK)
rotate_chain(item2b, [(0, 90.0), (1, 90.0)])
translate_to(item2b, (0.0, D_env - SLOT_INSET, 0.0))
els.append(item2b)

item3 = build_panel(W_env, D_env, THK)
translate_to(item3, (0.0, 0.0, H - SLOT_INSET))
els.append(item3)

# item1: end-cap panel closing the far (Wc) D x H face.
item1 = build_panel(D_env, H, THK)
rotate_chain(item1, [(2, 90.0), (1, 90.0)])
translate_to(item1, (Wc + 0.04 - SLOT_INSET, 0.0, 0.0))
els.append(item1)

print(f"built {len(els)} elements")

# ---------------- group + verify + capture ---------------------------------
asm = state.scene().AssembleElements(els)
asm_el = asm.QueryInterface(ICAPI.IZElement)
asm_el.Name = "B150004_221_SOURCE_HOIST_COVER_FRONT"

mins = [min(bbox_of(e)[k] for e in els) for k in range(3)]
maxs = [max(bbox_of(e)[k + 3] for e in els) for k in range(3)]
print("envelope min (m):", [round(v, 5) for v in mins])
print("envelope max (m):", [round(v, 5) for v in maxs])
print("envelope size (in):", [round((maxs[k] - mins[k]) / IN, 3) for k in range(3)])

try:
    se_top = state.scene_element(asm_el)
    m = se_top.GetPositionTransform()
    m.SetRotation(1, -35.0, True)
    m.SetRotation(2, -30.0, True)
    se_top.SetPositionTransform(m)
except Exception as exc:
    print("view rotate skipped:", exc)

scratch = r"C:\Users\admin\AppData\Local\Temp\claude\C--Users-admin-Desktop-Ironcad-Automation\bc173ed2-eda5-4e94-bcb5-abedd115eb0d\scratchpad"
out_path = os.path.join(scratch, "b150004_221_build_verify.jpg")
scene = state.scene()
try:
    scene.AdjustCameraToFitShapeInRect(1600, 1000)
except Exception:
    pass
scene.ExportImage(out_path, 3, 1600, 1000, True)
print("capture:", out_path, os.path.exists(out_path))

# ---------------- save --------------------------------------------------
save_target = r"C:\Users\admin\Desktop\Kelvin\NORTION\b150004_221_rebuild.ics"
z_ignore = getattr(state.ICAPI, "Z_LINKS_IGNORE", 5)
scene.SaveAs(save_target, z_ignore, True)
print("saved:", save_target, os.path.exists(save_target))
