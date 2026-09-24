"""Try candidate rotations on a fresh PIL4040SNN_ insert each time, print
resulting bbox, to find which rotation gives a Z-vertical 40x40xH post and
which gives an X-horizontal 500(or H)x40x40 rail. Throwaway scene."""
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
pil_cat = next(cmgr.Catalog[i] for i in range(int(cmgr.Count)) if str(cmgr.Catalog[i].Name) == "PIL_DIST")
pil_entry = next(pil_cat.Entry[j] for j in range(int(pil_cat.EntryCount)) if str(pil_cat.Entry[j].Name) == "PIL4040SNN_")


def try_rot(rot):
    el = pil_entry.InsertElement()
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    m.MakeIdentity()
    se.SetPositionTransform(m)
    for axis, deg in zip((0, 1, 2), rot):
        if deg:
            m = se.GetPositionTransform()
            m.SetRotation(axis, float(deg), True)
            se.SetPositionTransform(m)
    m2 = se.GetPositionTransform()
    m2.SetTranslation(0.0, 0.0, 0.0)
    se.SetPositionTransform(m2)
    part = el.QueryInterface(ICAPI.IZPart)
    bb = [round(v, 6) for v in part.GetBoundingBox(False)]
    dims = [round(bb[3]-bb[0],6), round(bb[4]-bb[1],6), round(bb[5]-bb[2],6)]
    print(f"rot={rot}: bbox={bb} dims(X,Y,Z)={dims}")
    return el


for rot in [
    (0, 0, 0),
    (90, 0, 0),
    (-90, 0, 0),
    (0, 90, 0),
    (0, -90, 0),
    (0, 0, 90),
    (0, 0, -90),
    (90, 90, 0),
    (0, 90, -90),
]:
    try_rot(rot)
