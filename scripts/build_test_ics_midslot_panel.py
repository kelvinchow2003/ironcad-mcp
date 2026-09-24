"""Rebuild the test.ics frame (same posts/rails/hardware layout) in a BRAND
NEW file, with the panel corrected to sit at the MIDDLE of the T-slot
channel depth on each channel-facing post (7.0mm past the post's inner
face for the PIL4040SNN 40mm profile - measured live via
scripts/inspect_pil_profile.py: channel mouth-to-back-wall depth is exactly
14.000mm on every face, so mid-depth = 7.000mm), instead of the old
test.ics's imprecise hand-placed 11.5mm/8.5mm asymmetric inset.

Frame member target bboxes replicate the ones measured live off test.ics
(scripts/inspect_test_ics.py) so the surrounding frame geometry is
unchanged - only the panel's fit logic differs. Does NOT touch or modify
the original test.ics; opens a fresh scene and saves to a new path.
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


def find_entry(cat_name, entry_name):
    cat = next(cmgr.Catalog[i] for i in range(int(cmgr.Count)) if str(cmgr.Catalog[i].Name) == cat_name)
    return next(cat.Entry[j] for j in range(int(cat.EntryCount)) if str(cat.Entry[j].Name) == entry_name)


pil_entry = find_entry("PIL_DIST", "PIL4040SNN_")
pan_entry = find_entry("PAN_DIST", "PAN___P_N___")

FWD = 0
Z_UNITE = 1
Z_SUBTRACT = 2
TILE = 0.5


def bbox_of(el):
    part = el.QueryInterface(ICAPI.IZPart)
    return [round(v, 6) for v in part.GetBoundingBox(False)]


def translate_to(el, target_min):
    cur = bbox_of(el)
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    dx, dy, dz = (target_min[k] - cur[k] for k in range(3))
    tx, ty, tz = m.GetTranslation()
    m.SetTranslation(tx + dx, ty + dy, tz + dz)
    se.SetPositionTransform(m)


def rotate_chain(el, steps):
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    m.MakeIdentity()
    se.SetPositionTransform(m)
    for axis, deg in steps:
        m = se.GetPositionTransform()
        m.SetRotation(axis, float(deg), True)
        se.SetPositionTransform(m)


# ---------------- frame: posts + rails ------------------------------------
# identity rotation = local Z extrude (verified via scripts/probe_pil_orientation2.py)
# rot (0,0,90) = local X extrude (horizontal rail)

def place_post(length_m, target_min):
    el = pil_entry.InsertElement()
    rotate_chain(el, [])  # identity -> Z-extrude
    block = el.GetFirstChild()
    ef = block.QueryInterface(ICAPI.IZExtrudeFeature)
    ef.ExtrudeDistance[FWD] = length_m
    el.QueryInterface(ICAPI.IZPart).Regenerate()
    translate_to(el, target_min)
    return el


def place_rail(length_m, target_min):
    el = pil_entry.InsertElement()
    rotate_chain(el, [(2, 90.0)])  # X-extrude
    block = el.GetFirstChild()
    ef = block.QueryInterface(ICAPI.IZExtrudeFeature)
    ef.ExtrudeDistance[FWD] = length_m
    el.QueryInterface(ICAPI.IZPart).Regenerate()
    translate_to(el, target_min)
    return el


def place_leaf(entry, target_min):
    el = entry.InsertElement()
    translate_to(el, target_min)
    return el


left_post = place_post(0.5, (0.0, 0.0, 0.0))
top_rail = place_rail(0.5, (0.04, 0.0, 0.46))
bottom_rail = place_rail(0.5, (0.04, 0.0, 0.0))
right_post = place_post(0.42, (0.5, 0.0, 0.04))

# NOTE: no FAS (fastener) or CAP (end cap) hardware inserted here - user
# adds those via IronCAD's built-in one-click add-on themselves (2026-09-24
# instruction, see feedback-no-fasteners-caps-in-builds memory).

for label, el in [("left_post", left_post), ("top_rail", top_rail),
                   ("bottom_rail", bottom_rail), ("right_post", right_post)]:
    print(label, el.Name, bbox_of(el))

# ---------------- panel: tile + Unite + Subtract (proven technique) -------
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
    part.Boolean(tool.QueryInterface(ICAPI.IZPart), Z_SUBTRACT)


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


# ---- corrected sizing (memory: ironcad-catalog-reference, 2026-09-24) ----
# Channel-facing posts (left/right): panel edge lands at the MID-DEPTH of
# the T-slot channel = 7.0mm past the post's inner face (channel depth
# measured 14.000mm exactly on this profile).
# Non-channel rails (top/bottom): panel edge stops exactly at daylight
# (zero inset) - unchanged from before.
INSET = 0.007  # 7mm mid-channel inset, corrected from the old 11.5/8.5mm guess

left_face_x = 0.04       # left post's inner face
right_face_x = 0.5       # right post's inner face
bottom_face_z = 0.04     # bottom rail's daylight face (no inset)
top_face_z = 0.46        # top rail's daylight face (no inset)

panel_x0 = left_face_x - INSET
panel_x1 = right_face_x + INSET
panel_z0 = bottom_face_z
panel_z1 = top_face_z
panel_w = round(panel_x1 - panel_x0, 6)
panel_h = round(panel_z1 - panel_z0, 6)
THK = 0.004
print(f"\npanel target: X[{panel_x0},{panel_x1}] (w={panel_w}) Z[{panel_z0},{panel_z1}] (h={panel_h}) thk={THK}")

panel = build_panel(panel_w, panel_h, THK)
rotate_chain(panel, [(0, 90.0), (1, 90.0)])
# center panel thickness on the post's mid-cross-section (~20mm)
y_center = 0.02
translate_to(panel, (panel_x0, y_center - THK / 2, panel_z0))
print("panel final bbox:", bbox_of(panel))

# ---------------- verify + capture --------------------------------------
els = list(state.iter_top_elements())
print("\ntop-level element count:", len(els))

scene = state.scene()
try:
    scene.AdjustCameraToFitShapeInRect(1400, 1000)
except Exception:
    pass

scratch = r"C:\Users\admin\AppData\Local\Temp\claude\C--Users-admin-Desktop-Ironcad-Automation\29164e27-7ce1-4933-a90d-92ad1c75f5b6\scratchpad"
out_path = os.path.join(scratch, "test_ics_midslot_panel_verify.jpg")
scene.ExportImage(out_path, 3, 1400, 1000, True)
print("capture:", out_path, os.path.exists(out_path))

# ---------------- save to a brand-new file (does not touch test.ics) ----
save_target = r"C:\Users\admin\Downloads\test_midslot_panel_no_hardware.ics"
z_ignore = getattr(state.ICAPI, "Z_LINKS_IGNORE", 5)
scene.SaveAs(save_target, z_ignore, True)
print("saved:", save_target, os.path.exists(save_target))
