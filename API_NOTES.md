# API_NOTES.md — verified ICAPI findings

> Source of truth for every ICAPI call the tools use (spec §5). Only **verified**
> findings here (confirmed live against IronCAD 2024 with the user's catalog and
> a real work file open). Never fabricate a method name; unknowns are OPEN.

Environment:

| Item | Value |
|---|---|
| OS | Windows 10 Pro 19045, single interactive session |
| Python | 3.14.7, 64-bit |
| IronCAD | 2024 = v26.0, `C:\Program Files\IronCAD\2024\` |
| python-ironcad | 0.1.21 (COM via **comtypes 1.4.16**); pywin32 312 for `pythoncom` STA |
| mcp | pinned **1.30.0** (`<2`) |
| Type library | `bin\ICApiIronCAD.tlb` → `comtypes.gen.ICAPIIRONCADLib as ICAPI` |

---

## 0. Attach + resolve base app — the WORKING path

`python_ironcad`'s `IronCAD.GetIZBaseApp()` is **broken on this comtypes version**:
it does `self.comobject.ZIronCADApp.QueryInterface(...)`, but `attach()` yields a
bare `POINTER(IUnknown)` with no `.ZIronCADApp` → `AttributeError`. (This looks
identical to "API not registered"; the real fix once the DLLs are registered is
to **QueryInterface ourselves**.)

```python
import pythoncom; pythoncom.CoInitialize()        # on the STA worker thread
import comtypes, comtypes.client
import python_ironcad                               # prints a banner to STDOUT -> wrap in redirect
import comtypes.gen.ICAPIIRONCADLib as ICAPI

app  = comtypes.client.GetActiveObject("IronCAD.Application")   # POINTER(IUnknown)
base = app.QueryInterface(ICAPI.IZBaseApp)          # ✅ works once DLLs registered
```

The `ZIronCADApp` coclass implements: `IZIronCADApp, IZBaseApp, IZObjectCreator,
IZModelQueryUtility, IZMathUtility, IZBaseAppSetups, IZBaseAppOptions,
IZElementSupport`. QI `app` to whichever you need.

> One-time elevated `python -m python_ironcad` (regsvr32 of 4 DLLs) is still
> required first — see README. Detect the pre-registration state and surface the
> hint (implemented in `connection.py`).

## 1. Catalogs (P1) — VERIFIED, incl. the keystone

```python
cmgr = base.CatalogMgr                      # -> IZCatalogMgr
cmgr.Count                                  # e.g. 25
cat  = cmgr.ActiveCatalog                   # -> IZCatalog  (or cmgr.Catalog[i])
cat  = cmgr.Catalog[i]                      # named property, 0-based
cat.Name; cat.EntryCount                    # str; int
entry = cat.Entry[j]                        # -> IZCatalogEntry (0-based)
entry.Name                                  # str, the part name Claude grounds on
entry.IsCatalogGroup()                      # bool (a group vs a droppable part)
```

Live sample (this machine): 25 catalogs incl. user set `PIL_DIST`, `PAN_DIST`,
`MBS`, `LIN`, `Lean_Line`, `Connectors`, `Side_guide_and_SC`, `Stand`, plus stock
`Starter/Shapes/Sheet Metal/...`. Entry names e.g. `PIL4140SNN_`, `FAS4041`.

**KEYSTONE — instantiate a catalog part by name (programmatic drag):**
```python
element = entry.InsertElement()             # -> IZElement, dropped into ACTIVE scene
# (No screen coords. entry.DropObject(x,y) is the GUI-coord variant — avoid.)
```
`InsertElement()` is confirmed present on `IZCatalogEntry`; **not yet called live**
(it is a scene mutation → gated to read_write + backup in M3).

To find an entry by name: iterate catalogs → `Entry[j].Name`. `_case_insensitive_`
is set on these dispinterfaces, but resolve exact-first and refuse ambiguity.

## 2. View / image export (P2) — VERIFIED (visual loop works)

On `IZSceneDoc`:
```python
scene.ExportImage(bstrFileName, eFormat, lWidth, lHeight, vbFitScene)  # HRESULT
# variants: ExportImageEx / ExportImage2 / ExportImage3 / ExportImageWithOptions
scene.AdjustCameraToFitShapeInRect(w, h)    # fit view before export
```
**eFormat codes (empirically probed on this build):**

| code | format |
|---|---|
| 1 | BMP |
| 3 | **JPEG**  ← use this for Claude (readable; PNG not supported) |
| 7 | TIFF |
| 9 | GIF |
| 0,2,4,5,6,8,10,11 | fall back to BMP |

⚠️ `Z_IMAGE_PNG = 8` (a real enum constant) is **NOT** honored by `ExportImage`
here — it writes BMP. Use **3 (JPEG)**. Verified: an 800×600 JPEG rendered the
live model and was read back as an image successfully.

## 3. Active document metadata (P3)

```python
doc = base.ActiveDoc                        # -> IZDoc (None if nothing open)
doc.Name                                    # full path, e.g. C:\...\b150004_221.ics
scene = doc.QueryInterface(ICAPI.IZSceneDoc)   # the rich scene interface
```
Units metadata property: OPEN (positions are in **metres** — a 0.6 value = 600 mm).
`IZSceneDocUtility` / `IZSceneDocProperty` are on the coclass; unit call TBD.

## 4. Scene / part enumeration + read by name (P4)

On `IZSceneDoc`:
```python
scene.GetChildrenElementsCount()            # int (top-level count), e.g. 18
scene.GetTopElement()                       # -> IZElement (scene root)
c = scene.GetFirstChild(); c = scene.GetNextChild()   # iterate top-level
scene.GetChildElements()                    # array form
```
`IZElement`: `Type` (int; observed **1=part, 2=assembly**), `Id`, `Name` (get/set),
`OwningDoc`, `GetParent()`, `GetChildren()/GetChildrenCount()`, `GetOwnerPart()`,
`Remove()`.

## 5. Selection (P5)

```python
selmgr = scene.SelectionMgr                 # -> IZSelectionMgr  (enumerate current selection)
```
Member details OPEN (enumerate at build time).

## 6. Positioning / transform (P6) — VERIFIED reads

QI any scene element to `IZSceneElement`:
```python
se = element.QueryInterface(ICAPI.IZSceneElement)
se.GetPositionTransform()                   # -> IZMathMatrix
se.SetPositionTransform(matrix)             # move (write)
se.ApplyTransform(matrix)                   # relative
se.PositionTransformComponentValue[t]       # get component t (named property, get/set)
se.SetPositionTransform(...)                # ...
se.ParameterMgr                             # -> IZParameterMgr (per-element params)
```
Component index `t` (verified reads): **0,1,2 = translation X,Y,Z in metres**;
3,4,5 = orientation components (semantics TBD — build a matrix via `IZMathUtility`
for rotations rather than poking 3..5). Simple placement = set t=0,1,2.

Mating/constraints: `IZSceneDoc.RelationMgr`, `.ConstraintSolver`, `.Solve(...)`,
`.AssembleElements(...)` exist — defer to M4 and confirm before use.

## 7. Parameters (edit) — interface known

```python
pmgr = se.ParameterMgr                      # or scene.ParameterMgr for scene-level
pmgr.Count
p = pmgr.GetParameterByName("Length")       # -> IZParameter
p = pmgr.GetParameter(i)
# value get/set on IZParameter: confirm member names at build time (OPEN)
scene.RegenerateParts(True)                 # regen after edits
scene.Update()
```

## 8. Save (P7) — VERIFIED interface

On `IZSceneDoc`:
```python
scene.SaveAs(bstrFileName, eLinksSaveOptions, vbForceOverwriteExisting)      # HRESULT
scene.SaveAsCopy(bstrFileName, eLinksSaveOptions, vbForceOverwriteExisting)  # backups!
```
`SaveAsCopy` is ideal for backup-before-write. `.ics` is the scene file extension.

⚠️ **VERIFIED GOTCHAS with `SaveAsCopy`:**
- `eLinksSaveOptions` is enum `eZLinksSaveOptions`, valid members **1..5** only
  (`Z_LINKS_SAVE_ALL=1 … Z_LINKS_IGNORE=5`). Passing **0** → **E_POINTER
  (0x80004003)** + a **modal "error saving file" dialog** that **wedges the STA
  worker thread** until a human dismisses it.
- Even with a valid option, `SaveAsCopy` on a scene that **contains a linked
  catalog part** pops a modal dialog (also wedges the thread).

> **Design decision (backups): do NOT use `SaveAsCopy` for backups.** The MCP
> backup instead does a plain **filesystem copy** of the document's on-disk
> `.ics` file (`safety.backup_active_doc`) — no COM, no modal, cannot wedge. For
> a never-saved scene there is no file, so the write proceeds with no backup
> (nothing to lose). `SaveAs`/`SaveAsCopy` remain the calls for the explicit
> save tools (M4), where a modal is acceptable/expected.

> **General robustness rule:** any COM call that pops a modal dialog wedges the
> STA worker irrecoverably (try/except cannot catch a modal). Prefer non-modal
> APIs; validate enum/arg values; keep an eye out for save/link/overwrite prompts.

---

## Element/utility interfaces worth knowing
- `IZObjectCreator`, `IZModelQueryUtility`, `IZMathUtility` (build matrices/points) — on the app coclass.
- `IZSceneDoc` extras: `CameraMgr`, `LightMgr`, `WindowMgr`, `ConfigurationMgr`,
  `AnimationMgr`, undo (`StartUndoTransaction`/`End`/`Abort`), `CreatePart`,
  `CreateBlockPart/CylinderPart/...` (primitive creation), `ImportModel`.

## OPEN QUESTIONS
1. Live `InsertElement()` behavior (return, where it lands, default transform) — verify in M3 under read_write+backup.
2. Rotation semantics of `PositionTransformComponentValue[3..5]`; prefer `IZMathUtility` matrices.
3. `IZParameter` value get/set member names; parameter units.
4. Document unit metadata call.
5. `IZSelectionMgr` enumeration members.
6. `eLinksSaveOptions` enum values (using 0).

## Reproduce
`scripts/discover_api.py` performs the read-only live discovery above (QI path,
catalog/scene enumeration, JPEG export). Re-run after any IronCAD upgrade.
