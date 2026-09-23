"""Rebuild B150004-221 hoist-cover frame WITHOUT the 500mm splice/limiter.

Supersedes build_hoist_cover.py's splicing approach. New technique (found
2026-09-19): PIL4040SNN_'s real length driver is NOT the inert 'Abdeckleiste'
catalog parameter -- it's the internal 'Block' feature's IZExtrudeFeature:

    block = element.GetFirstChild()                     # the 'Block' feature
    ef = block.QueryInterface(ICAPI.IZExtrudeFeature)
    ef.ExtrudeDistance[0] = length_m                     # 0 = Z_EXTRUDE_DIRECTION_FORWARD
    element.QueryInterface(ICAPI.IZPart).Regenerate()

This sets ANY length directly (verified live: 76.8mm, 109.6mm, 1076.8mm,
2500mm, 50mm all worked in one step) and the element's `.Name` still
auto-updates correctly for BOM (PIL4040SNN1076.8 etc.) -- no more need to
splice multiple 500mm segments + Boolean-cut the remainder.
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

FWD = 0
IN = 0.0254
W_env = 42.00 * IN + 2 * 0.045
D_env = 17.50 * IN + 2 * 0.045
H = 24.00 * IN
new_W_rail = W_env - 2 * 0.04
new_D_rail = D_env - 2 * 0.04
Wc = W_env - 0.04
Dc = D_env - 0.04
Hc = H - 0.04
print(f"W_env={W_env:.4f} D_env={D_env:.4f} H={H:.4f}  new_W_rail={new_W_rail:.4f} new_D_rail={new_D_rail:.4f}")


def place(rot, pos, length_m):
    """Insert ONE PIL4040SNN_, set its real length directly, position it."""
    el = entry.InsertElement()
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
    return [round(v, 5) for v in part.GetBoundingBox(False)]


all_els = []

# 4 Z-posts, full 24in length in ONE piece (no splice)
for cx in (0.0, Wc):
    for cy in (0.0, Dc):
        el = place([0, -90, 0], (cx, cy, 0.0), H)
        all_els.append(el)
        print("post", el.Name, bbox_of(el))

# 4 X W-rails, full 42.39in-equivalent length in ONE piece (no splice)
for cy in (0.0, Dc):
    for cz in (0.0, Hc):
        el = place([0, 90, -90], (0.04, cy + 0.04, cz), new_W_rail)
        all_els.append(el)
        print("x-rail", el.Name, bbox_of(el))

# 3 of 4 Y D-rails (open side), full length in ONE piece
d_positions = [(0.0, 0.0), (Wc, 0.0), (0.0, Hc), (Wc, Hc)]
d_positions.remove((Wc, Hc))
for cx, cz in d_positions:
    el = place([0, 0, 0], (cx, 0.04, cz + 0.04), new_D_rail)
    all_els.append(el)
    print("y-rail", el.Name, bbox_of(el))

print("\ntotal elements:", len(all_els), "(was 23 with splicing -- now should be 11: 4+4+3)")

els = list(state.iter_top_elements())
mins = [min(bbox_of(e)[k] for e in els) for k in range(3)]
maxs = [max(bbox_of(e)[k + 3] for e in els) for k in range(3)]
print("envelope min:", [round(v, 5) for v in mins])
print("envelope max:", [round(v, 5) for v in maxs])
print("envelope size (in):", [round((maxs[k] - mins[k]) / IN, 3) for k in range(3)])

tol = 0.0005
bboxes = [(str(e.Name), bbox_of(e)) for e in els]
clashes = []
for i in range(len(bboxes)):
    for j in range(i + 1, len(bboxes)):
        a, b = bboxes[i][1], bboxes[j][1]
        ov = [min(a[k + 3], b[k + 3]) - max(a[k], b[k]) for k in range(3)]
        if all(o > tol for o in ov):
            clashes.append((bboxes[i][0], bboxes[j][0], [round(o * 1000, 3) for o in ov]))
print("clash_count:", len(clashes))
for c in clashes[:10]:
    print("  CLASH", c)

asm_el = None
try:
    asm = state.scene().AssembleElements(els)
    asm_el = asm.QueryInterface(ICAPI.IZElement)
    asm_el.Name = "hoist_cover_frame_B150004_221"
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

out_path = r"C:\Users\admin\AppData\Local\Temp\claude\C--Users-admin-Desktop-Ironcad-Automation\7a7026e4-3557-417e-b18b-8b1368ef41b2\scratchpad\hoist_cover_v2_verify.jpg"
scene = state.scene()
try:
    scene.AdjustCameraToFitShapeInRect(1400, 1000)
except Exception:
    pass
scene.ExportImage(out_path, 3, 1400, 1000, True)
print("capture:", out_path, os.path.exists(out_path))
