"""Rebuild the 12-edge PIL4040SNN_ cube frame so the ACTUAL measured side is
exactly 500mm (not 500mm nominal beam length -> 660mm actual, as in the
earlier build / the user's screenshot).

Geometry (all lengths in metres, cross-section 0.04 x 0.04):
  Envelope target: [0,0.5]^3 exactly.
  4 Z-posts run the FULL native 500mm length (no cut needed) at the 4
    (x,y) corner footprints {0, 0.46}.
  8 horizontal beams (4 along X, 4 along Y) are natively 500mm too, but
    must fit BETWEEN the posts -> shortened to 420mm via a real Boolean
    Subtract (IZPart.Boolean(toolPart, Z_SUBTRACT)) using a second,
    sacrificial PIL4040SNN_ instance as the cutting tool (verified live:
    the tool part is consumed by the subtract, no separate cleanup
    needed). Only PIL4040SNN_ stock is used anywhere, per instruction.

Opens a brand-new unsaved scene first so the result is clean. Nothing is
saved to disk.
"""
import sys, os
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
cat = next(cmgr.Catalog[i] for i in range(int(cmgr.Count)) if str(cmgr.Catalog[i].Name) == "PIL_DIST")
entry = next(cat.Entry[j] for j in range(int(cat.EntryCount)) if str(cat.Entry[j].Name) == "PIL4040SNN_")

Z_SUBTRACT = 2
S = 0.46   # far corner footprint start (so footprint = [S, S+0.04])
L = 0.5    # target cube side

def insert(rot, pos):
    el = entry.InsertElement()
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    for axis, deg in zip((0, 1, 2), rot):
        if deg:
            m.SetRotation(axis, float(deg), True)
    m.SetTranslation(*pos)
    se.SetPositionTransform(m)
    return el

def bbox_of(el):
    part = el.QueryInterface(ICAPI.IZPart)
    return [round(v, 5) for v in part.GetBoundingBox(False)]

created = {"posts": [], "x_beams": [], "y_beams": []}

# --- 4 Z-posts: full 500mm, no cut ---
# IMPORTANT: never set el.Name on these catalog parts. IronCAD auto-derives
# the name from the part's real geometry (PIL4040SNN_ -> PIL4040SNN500 etc.)
# and the BOM depends on that auto name; overwriting el.Name breaks the link
# PERMANENTLY (verified live: RegenerateParts does not restore it). We track
# our own bookkeeping via el.Id instead of renaming.
for cx in (0.0, S):
    for cy in (0.0, S):
        el = insert([0, -90, 0], (cx, cy, 0.0))
        bb = bbox_of(el)
        created["posts"].append({"name": str(el.Name), "id": int(el.Id), "bbox": bb})
        print("post", el.Name, "id=", int(el.Id), bb)

# --- 4 X-beams: insert long, subtract the 80mm excess with a sacrificial tool ---
for cy in (0.0, S):
    for cz in (0.0, S):
        target = insert([0, -90, -90], (0.54, cy, cz))
        tool = insert([0, 90, -90], (0.46, cy + 0.04, cz))
        target_part = target.QueryInterface(ICAPI.IZPart)
        tool_part = tool.QueryInterface(ICAPI.IZPart)
        target_part.Boolean(tool_part, Z_SUBTRACT)
        bb = bbox_of(target)
        created["x_beams"].append({"name": str(target.Name), "id": int(target.Id), "bbox": bb})
        print("x-beam", target.Name, "id=", int(target.Id), bb)

# --- 4 Y-beams: same idea, no rotation needed (default already runs +Y) ---
for cx in (0.0, S):
    for cz in (0.0, S):
        target = insert([0, 0, 0], (cx, 0.04, cz + 0.04))
        tool = insert([0, 0, 0], (cx, 0.46, cz + 0.04))
        target_part = target.QueryInterface(ICAPI.IZPart)
        tool_part = tool.QueryInterface(ICAPI.IZPart)
        target_part.Boolean(tool_part, Z_SUBTRACT)
        bb = bbox_of(target)
        created["y_beams"].append({"name": str(target.Name), "id": int(target.Id), "bbox": bb})
        print("y-beam", target.Name, "id=", int(target.Id), bb)

try:
    state.scene().RegenerateParts(True)
except Exception as exc:
    print("RegenerateParts skipped:", exc)
try:
    state.scene().Update()
except Exception as exc:
    print("Update skipped:", exc)

# --- verify overall envelope from all 12 elements ---
els = list(state.iter_top_elements())
print("\ntop-level element count:", len(els))
mins = [min(bbox_of(e)[k] for e in els) for k in range(3)]
maxs = [max(bbox_of(e)[k + 3] for e in els) for k in range(3)]
print("overall envelope min:", [round(v, 5) for v in mins])
print("overall envelope max:", [round(v, 5) for v in maxs])
print("overall side lengths (mm):", [round((maxs[k] - mins[k]) * 1000, 3) for k in range(3)])

# --- pairwise AABB interference check (0.5mm tolerance) ---
tol = 0.0005
bboxes = [(str(e.Name), bbox_of(e)) for e in els]
clashes = []
for i in range(len(bboxes)):
    for j in range(i + 1, len(bboxes)):
        a, b = bboxes[i][1], bboxes[j][1]
        ov = [min(a[k + 3], b[k + 3]) - max(a[k], b[k]) for k in range(3)]
        if all(o > tol for o in ov):
            clashes.append((bboxes[i][0], bboxes[j][0], [round(o * 1000, 3) for o in ov]))
print("\nclash_count:", len(clashes))
for c in clashes:
    print("  CLASH", c)

# --- connect into one assembly (matches prior build convention) ---
asm_el = None
try:
    asm = state.scene().AssembleElements(els)
    asm_el = asm.QueryInterface(ICAPI.IZElement)
    asm_el.Name = "cube_frame_500"
    print("\nassembled ->", asm_el.Name)
except Exception as exc:
    print("assemble failed (non-fatal):", exc)

# --- rotate view a bit (rotate the whole assembly; no saved cameras) ---
try:
    target_el = asm_el if asm_el is not None else els[0]
    se_top = state.scene_element(target_el)
    m = se_top.GetPositionTransform()
    m.SetRotation(1, -35.0, True)
    m.SetRotation(2, -30.0, True)
    se_top.SetPositionTransform(m)
except Exception as exc:
    print("view rotate skipped:", exc)

out_path = r"C:\Users\admin\AppData\Local\Temp\claude\C--Users-admin-Desktop-Ironcad-Automation\7a7026e4-3557-417e-b18b-8b1368ef41b2\scratchpad\cube_500_verify.jpg"
scene = state.scene()
try:
    scene.AdjustCameraToFitShapeInRect(1200, 900)
except Exception:
    pass
scene.ExportImage(out_path, 3, 1200, 900, True)
print("\ncapture written to:", out_path, "exists:", os.path.exists(out_path))
