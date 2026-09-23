"""Standalone build of B150004-173 "SOURCE HOIST COVER, BACK SUBASSEMBLY"
(Nordion), from scratch, PIL4040SNN_ 40x40mm T-slot substituted for the
drawing's 45mm profile (no 45mm size in this install's PIL_DIST catalog --
see ironcad-catalog-reference memory). Rail lengths recomputed so the
OUTER envelope still matches the drawing's real dimensions.

BOM read from b150004_173.SLDDRW.pdf (verified via high-res crop of the
title-block BOM table):
  item1  x2  B150004-182  plexiglass w/ insert, 0.25in thick, 20.9 x 42.7  (front/back, W x H)
  item2  x1  B150004-183  plexiglass w/ insert, 0.25in thick, 18.2 x 42.7  (top, W x D)
  item3  x1  T-slot 45mm sq, 5.75in long     -- small stub, NOT a D-rail: front elevation
                                                 crop shows it + item9 as a small bracket
                                                 assembly mounted mid-span on the BOTTOM
                                                 front W-rail (near the drawing's human-
                                                 clearance silhouette callout, which is a
                                                 reference symbol, not geometry). MODELED
                                                 below as a real PIL4040SNN_ stub standing
                                                 vertically on the bottom-front rail (user
                                                 flagged this as missing in a first pass --
                                                 now added).
  item4  x2  T-slot 45mm sq, 17.50in long    (D-rails -- only 2 of 4 positions; both TOP
                                                per the drawing's own item4 qty=2, no bottom
                                                D-rail -- underside stays open, consistent
                                                with this being an open-ended "back" unit)
  item5  x4  T-slot 45mm sq, 24.00in long    (H corner posts)
  item6  x4  T-slot 45mm sq, 42.00in long    (W-rails, all 4 positions)
  item7  x4  mounting foot (McMaster 5537T414)             -- NOT modeled
  item8  x4  3-way corner bracket (McMaster 5537T292)      -- NOT modeled
  item9  x1  corner bracket, 3-1/2in (McMaster 5537T349)   -- APPROXIMATED as a small
                                                               generic gusset block (no
                                                               exact McMaster L-bracket
                                                               catalog part found) bridging
                                                               the item3 stub to the rail.
  item10 x4  corner bracket, 1-3/4in (McMaster 5537T938)   -- NOT modeled

NOTE, flagged to user: unlike B150004-221 (3 of 4 D-rail positions), this
unit's drawing BOM only calls out 2 D-rails total. Read from the front
elevation view (no D-rail visible mid-height/bottom, item3/9 bracket is a
separate small mid-span accessory on the bottom W-rail, not a structural
D-rail) the most consistent placement is BOTH D-rails at the TOP (the top
plexiglass panel needs edge support there); the bottom stays fully open
except for the item3/9 stub. This is an assumption from the available
views, not a directly-dimensioned certainty -- flag if wrong.

Frame/panel recipe (posts, rails, T-slot recess, foot-stub for the overall
height delta) mirrors build_hoist_cover_220.py's build_unit(add_cap=False),
reused standalone here as a fresh from-scratch build per user request
(not reusing the user's own hand-built b150004_173.ics, which used a
different PIL4140 + FAS4041/CAL4515SNN/CAN4501 technique).
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
FOOT_LEN = 1.45 * IN  # foot-stub: true overall H (25.45in per drawing) minus item5 nominal 24.00in
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
# Only 2 D-rail positions per this drawing's BOM (item4 qty=2) -- both at
# the TOP (supports the top panel's edges); bottom stays open. See module
# docstring for the reasoning/assumption.
for cx in (0.0, Wc):
    els.append(place_extrusion([0, 0, 0], (cx, 0.04, Hc + 0.04), new_D_rail))

# Panels sized to the full built-frame envelope (fills opening edge-to-edge).
item1a = build_panel(W_env, H, THK)
rotate_chain(item1a, [(0, 90.0), (1, 90.0)])
translate_to(item1a, (0.0, SLOT_INSET, 0.0))
els.append(item1a)

item1b = build_panel(W_env, H, THK)
rotate_chain(item1b, [(0, 90.0), (1, 90.0)])
translate_to(item1b, (0.0, D_env - SLOT_INSET, 0.0))
els.append(item1b)

item2 = build_panel(W_env, D_env, THK)
translate_to(item2, (0.0, 0.0, H - SLOT_INSET))
els.append(item2)

# item3: 5.75in T-slot stub -- user confirmed HORIZONTAL (not the vertical
# post orientation from the first attempt), lying flat along the W-axis on
# top of the bottom-front rail, same rotation recipe as the main W-rails,
# at the W-position the drawing dimensions (21.81in out of 45.39in overall
# -- read as a fraction of the built W envelope since 45.39 is the
# drawing's own 45mm-profile envelope, not ours). item9: small gusset
# block approximating the McMaster corner bracket bridging the stub to the
# rail (no exact catalog part for that bracket -- see module docstring).
ITEM3_LEN = 5.75 * IN
x_frac = 21.81 / 45.39
stub_x = x_frac * Wc
item3 = place_extrusion([0, 90, -90], (stub_x, 0.04, 0.04), ITEM3_LEN)
els.append(item3)

item9 = build_panel(1.0 * IN, 1.0 * IN, 0.25 * IN)
translate_to(item9, (stub_x - 0.5 * IN, 0.04, 0.04))
els.append(item9)

print(f"built {len(els)} elements")

# ---------------- group + verify + capture ---------------------------------
asm = state.scene().AssembleElements(els)
asm_el = asm.QueryInterface(ICAPI.IZElement)
asm_el.Name = "B150004_173_SOURCE_HOIST_COVER_BACK"

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
out_path = os.path.join(scratch, "b150004_173_build_verify.jpg")
scene = state.scene()
try:
    scene.AdjustCameraToFitShapeInRect(1600, 1000)
except Exception:
    pass
scene.ExportImage(out_path, 3, 1600, 1000, True)
print("capture:", out_path, os.path.exists(out_path))

# ---------------- save --------------------------------------------------
# b150004_173_rebuild.ics is locked by a stale open doc from an earlier
# run still resident in this same IronCAD session (closing/copying over it
# both failed with COM/permission errors) -- save under a new name instead
# rather than fight the lock.
save_target = r"C:\Users\admin\Desktop\Kelvin\NORTION\b150004_173_rebuild_v3.ics"
z_ignore = getattr(state.ICAPI, "Z_LINKS_IGNORE", 5)
scene.SaveAs(save_target, z_ignore, True)
print("saved:", save_target, os.path.exists(save_target))
