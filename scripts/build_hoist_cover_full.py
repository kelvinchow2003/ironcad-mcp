"""Full B150004-221 hoist-cover build: frame (long extrusions, direct-length
technique) + the 3 plexiglass panels (tile + Boolean-Unite to exceed the
native 500x500mm panel stock, then Boolean-Subtract to trim to exact size).

Panel sizes from the drawing's BOM (cross-checked against item4/5/6 rail
lengths in the earlier build):
  item1 (side,  qty1): DEPTH x HEIGHT = 18.2 x 20.9 in
  item2 (front/back, qty2): HEIGHT x WIDTH = 20.9 x 42.7 in
  item3 (top,   qty1): DEPTH x WIDTH  = 18.2 x 42.7 in
  thickness (all): 0.25in
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

# ---------------- frame geometry (unchanged from the direct-length build) --
W_env = 42.00 * IN + 2 * 0.045
D_env = 17.50 * IN + 2 * 0.045
H = 24.00 * IN
new_W_rail = W_env - 2 * 0.04
new_D_rail = D_env - 2 * 0.04
Wc = W_env - 0.04
Dc = D_env - 0.04
Hc = H - 0.04
print(f"W_env={W_env:.4f} D_env={D_env:.4f} H={H:.4f}")


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


frame_els = []
for cx in (0.0, Wc):
    for cy in (0.0, Dc):
        frame_els.append(place_extrusion([0, -90, 0], (cx, cy, 0.0), H))
for cy in (0.0, Dc):
    for cz in (0.0, Hc):
        frame_els.append(place_extrusion([0, 90, -90], (0.04, cy + 0.04, cz), new_W_rail))
d_positions = [(0.0, 0.0), (Wc, 0.0), (0.0, Hc), (Wc, Hc)]
d_positions.remove((Wc, Hc))
for cx, cz in d_positions:
    frame_els.append(place_extrusion([0, 0, 0], (cx, 0.04, cz + 0.04), new_D_rail))
print("frame elements:", len(frame_els))


# ---------------- panel builder: tile + Unite (grow) + Subtract (trim) -----
def _panel_tile(x, y, thickness_m):
    """Insert one native panel tile with its low corner at local (x,y,0)."""
    el = pan_entry.InsertElement()
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    m.MakeIdentity()
    # native identity bbox corner sits at (-0.1914,-0.0515); shift to (x,y).
    m.SetTranslation(x + 0.1914, y + 0.0515, 0.0)
    se.SetPositionTransform(m)
    block = el.GetFirstChild()
    ef = block.QueryInterface(ICAPI.IZExtrudeFeature)
    ef.ExtrudeDistance[FWD] = thickness_m
    el.QueryInterface(ICAPI.IZPart).Regenerate()
    return el


def _trim_x(el, target_w, row_y):
    """Subtract everything in el beyond x=target_w (tool = 1 native tile)."""
    part = el.QueryInterface(ICAPI.IZPart)
    tool = _panel_tile(target_w, row_y, 0.05)  # thick tool, guarantees full cut
    tool_part = tool.QueryInterface(ICAPI.IZPart)
    part.Boolean(tool_part, Z_SUBTRACT)


def build_panel(target_w, target_h, thickness_m):
    """Return one element sized exactly target_w x target_h x thickness_m,
    footprint [0,target_w] x [0,target_h] in local XY, built from native
    500x500mm panel stock via tile+Unite (grow) + Subtract (trim)."""
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
        y_tool_row = build_row(target_h)  # a fresh trimmed-to-width strip as the Y cutting tool
        panel_part.Boolean(y_tool_row.QueryInterface(ICAPI.IZPart), Z_SUBTRACT)

    return panel_el


THK = 0.25 * IN

# build_panel(target_w, target_h, thk): local X=target_w, Y=target_h.
# Chosen w/h order per panel matches the rotation recipe applied below
# (each verified empirically in scratchpad/probe_panel_rot3.py).
print("\nbuilding item1 (side panel, local X=depth, Y=height)...")
item1 = build_panel(18.2 * IN, 20.9 * IN, THK)
print(" item1", item1.Name, bbox_of(item1))

print("building item2 (front panel, local X=width, Y=height)...")
item2a = build_panel(42.7 * IN, 20.9 * IN, THK)
print(" item2a", item2a.Name, bbox_of(item2a))

print("building item2 (back panel, local X=width, Y=height)...")
item2b = build_panel(42.7 * IN, 20.9 * IN, THK)
print(" item2b", item2b.Name, bbox_of(item2b))

print("building item3 (top panel, local X=width, Y=depth)...")
item3 = build_panel(42.7 * IN, 18.2 * IN, THK)
print(" item3", item3.Name, bbox_of(item3))

# ---------------- position panels against the frame -------------------
# Rotation recipes empirically verified (scratchpad/probe_panel_rot3.py):
# each step is its OWN GetPositionTransform/SetPositionTransform round-trip
# (chaining both rotations on one matrix object before setting does NOT
# give the same result for this part family -- rotation about axis 0 alone
# is a no-op, only takes effect when applied as a separate step after
# another axis's rotation has already been committed).
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
    """Shift el (already rotated) so its bbox min lands at target_min."""
    cur = bbox_of(el)
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    dx, dy, dz = (target_min[k] - cur[k] for k in range(3))
    tx, ty, tz = m.GetTranslation()
    m.SetTranslation(tx + dx, ty + dy, tz + dz)
    se.SetPositionTransform(m)


# PANEL-IN-SLOT FIT (user correction 2026-09-19): PIL4040SNN_'s cross-section
# sketch (read via IZProfile.GetLineData on its 'Block' feature's 2D sketch)
# shows each of the 4 faces has a T-slot channel centered on that face: a
# ~14mm-wide mouth with a straight jaw ~4.5mm deep before the T-undercut
# begins (measured: mouth walls at u=12.34mm and u=26.34mm on a 40mm face,
# straight depth from v=0.67mm to v=5.17mm). Panels must sit RECESSED into
# that slot -- not surface-mounted flush on the outer face like before.
# INSET stays comfortably inside the measured straight-jaw depth (0.67mm
# to 5.17mm), leaving margin on both sides.
SLOT_INSET = 0.003  # 3mm recess from the outer face into the slot channel

# item1 (local X=depth "u", Y=height "v") -> want global X=thickness,
# Y=depth(u), Z=height(v). Recipe [(Z,+90),(Y,+90)] gives (X,Y,Z)=
# (thick,u,v) -- verified. Recessed into the open D-side face's slot
# (posts' outer face is at x=Wc+0.04; panel sits SLOT_INSET inward of it).
rotate_chain(item1, [(2, 90.0), (1, 90.0)])
translate_to(item1, (Wc + 0.04 - SLOT_INSET, 0.0, 0.0))

# item2 (local X=width "u", Y=height "v") -> want global X=width(u),
# Y=thickness, Z=height(v). Recipe [(X,+90),(Y,+90)] gives (X,Y,Z)=
# (u,thick,v) -- verified. Front face recessed SLOT_INSET inward (+Y) from
# y=0; back face recessed SLOT_INSET inward (-Y) from y=D_env.
rotate_chain(item2a, [(0, 90.0), (1, 90.0)])
translate_to(item2a, (0.0, SLOT_INSET, 0.0))
rotate_chain(item2b, [(0, 90.0), (1, 90.0)])
translate_to(item2b, (0.0, D_env - SLOT_INSET, 0.0))

# item3 (local X=width "u", Y=depth "v") -> want global X=width(u),
# Y=depth(v), Z=thickness -- IDENTITY (no rotation). Recessed SLOT_INSET
# inward (-Z) from the top face at z=H.
translate_to(item3, (0.0, 0.0, H - SLOT_INSET))

for el, label in [(item1, "item1"), (item2a, "item2a"),
                   (item2b, "item2b"), (item3, "item3")]:
    print(label, "final bbox:", bbox_of(el))

# ---------------- verify + capture --------------------------------------
els = list(state.iter_top_elements())
print("\ntop-level element count:", len(els))
mins = [min(bbox_of(e)[k] for e in els) for k in range(3)]
maxs = [max(bbox_of(e)[k + 3] for e in els) for k in range(3)]
print("envelope min:", [round(v, 5) for v in mins])
print("envelope max:", [round(v, 5) for v in maxs])
print("envelope size (in):", [round((maxs[k] - mins[k]) / IN, 3) for k in range(3)])

asm_el = None
try:
    asm = state.scene().AssembleElements(els)
    asm_el = asm.QueryInterface(ICAPI.IZElement)
    asm_el.Name = "hoist_cover_full_B150004_221"
    print("assembled ->", asm_el.Name)
except Exception as exc:
    print("assemble failed (non-fatal):", exc)

try:
    target_el = asm_el if asm_el is not None else els[0]
    se_top = state.scene_element(target_el)
    m = se_top.GetPositionTransform()
    m.SetRotation(1, -35.0, True)
    m.SetRotation(2, -30.0, True)
    se_top.SetPositionTransform(m)
except Exception as exc:
    print("view rotate skipped:", exc)

out_path = r"C:\Users\admin\AppData\Local\Temp\claude\C--Users-admin-Desktop-Ironcad-Automation\7a7026e4-3557-417e-b18b-8b1368ef41b2\scratchpad\hoist_cover_full_verify.jpg"
scene = state.scene()
try:
    scene.AdjustCameraToFitShapeInRect(1400, 1000)
except Exception:
    pass
scene.ExportImage(out_path, 3, 1400, 1000, True)
print("capture:", out_path, os.path.exists(out_path))
