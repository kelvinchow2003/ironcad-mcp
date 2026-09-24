"""Read-only-ish probe (creates a throwaway new scene, does not touch test.ics):
insert one PIL4040SNN_ with identity transform and print its bbox, to confirm
which local axis it extrudes along by default before building a new frame.
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
pil_cat = next(cmgr.Catalog[i] for i in range(int(cmgr.Count)) if str(cmgr.Catalog[i].Name) == "PIL_DIST")
pil_entry = next(pil_cat.Entry[j] for j in range(int(pil_cat.EntryCount)) if str(pil_cat.Entry[j].Name) == "PIL4040SNN_")

el = pil_entry.InsertElement()
part = el.QueryInterface(ICAPI.IZPart)
print("default bbox:", [round(v,6) for v in part.GetBoundingBox(False)])

se = state.scene_element(el)
m = se.GetPositionTransform()
print("default translation:", m.GetTranslation())
