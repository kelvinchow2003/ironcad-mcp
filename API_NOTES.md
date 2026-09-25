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

### 🔑 SilentMode — the master robustness fix (VERIFIED)

`IZBaseAppSetups.SilentMode = True` suppresses modal dialogs during automation.
A modal otherwise **irrecoverably wedges the STA COM worker** (try/except cannot
catch it). Enabled on every attach:

```python
setups = app.QueryInterface(ICAPI.IZBaseAppSetups)
setups.SilentMode = True
```

Verified: with SilentMode on, `SaveAsCopy` on a scene with linked catalog parts
(previously popped a modal and hung) **succeeds instantly**. Implemented in
`connection._enable_silent_mode()`; reported by `ironcad_status().silent_mode`.

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

## 6b. Move — VERIFIED (matrix path, non-modal)

```python
se = element.QueryInterface(ICAPI.IZSceneElement)
m  = se.GetPositionTransform()              # IZMathMatrix (keeps orientation)
m.SetTranslation(x, y, z)                   # absolute translation, metres
m.SetRotation(nAxis, degrees, vbApplyToCurrent)   # nAxis 0/1/2 = X/Y/Z
se.SetPositionTransform(m)
scene.Update()
```
Verified live: moved a placed part to [0.3,0,0] then [0,0.3,0]. `IZMathUtility`
(`CreateTranslationMathMatrix`, `CreateRotationMathMatrixAboutAxis`, ...) is
available if a fresh matrix is preferred.

## 7. Parameters (edit) — VERIFIED

```python
pmgr = se.ParameterMgr                      # or scene.ParameterMgr for scene-level
pmgr.Count
p = pmgr.GetParameterByName("Abdeckleiste") # -> IZParameter (raises if absent)
p = pmgr.GetParameter(i)
p.Value            # get/set FLOAT  (driving numeric value) -- VERIFIED
p.Expression       # get/set STR    (driving expression)
p.TextParameterValue  # get/set STR (text parameters)
p.TryToSetExpression(expr, out, refs)
scene.RegenerateParts(True)                 # regen after edits -- NOT modal (verified)
scene.Update()
```
Verified live: read+set `IZParameter.Value` on a placed PIL part; RegenerateParts
did not pop a dialog.

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

> **Update:** with **SilentMode** on (see §0), `SaveAs`/`SaveAsCopy` on
> linked-part scenes **no longer wedge** — VERIFIED. The file-copy backup is
> still preferred (zero COM), but the save tools (M4) now work reliably. The MCP
> save tools use `Z_LINKS_IGNORE (5)`.

> **General robustness rule:** any modal dialog would wedge the STA worker, so we
> keep SilentMode on for the whole session; still prefer non-modal APIs and
> validate enum/arg values.

---

## Element/utility interfaces worth knowing
- `IZObjectCreator`, `IZModelQueryUtility`, `IZMathUtility` (build matrices/points) — on the app coclass.
- `IZSceneDoc` extras: `CameraMgr`, `LightMgr`, `WindowMgr`, `ConfigurationMgr`,
  `AnimationMgr`, undo (`StartUndoTransaction`/`End`/`Abort`), `CreatePart`,
  `CreateBlockPart/CylinderPart/...` (primitive creation), `ImportModel`.

## 9. Connect / attach parts (the blue/white spheres) — STAND-IN + OPEN

**What the user does by hand:** drags a part so its **anchor** snaps onto another
part's highlighted **attachment point** (the blue/white spheres); the parts then
link and move together. This is *relative/attachment* positioning, not absolute
world coordinates.

**Current MCP approach (VERIFIED, efficient stand-in):** place a part at an
already-placed part's translation **+ an offset**, using only the verified
transform calls (§6/§6b):

```python
base = anchor_se.PositionTransformComponentValue[0..2]   # read anchor xyz (m)
target = [base[t] + offset[t] for t in range(3)]
m = se.GetPositionTransform(); m.SetTranslation(*target); se.SetPositionTransform(m)
```

Exposed as `relative_to` + `offset` on `ironcad_add_catalog_part` /
`ironcad_move_part`, and as the batch tool **`ironcad_build_parts`** (one backup,
N parts, one regen). This removes the credit-burning "guess an absolute
coordinate → capture JPEG → nudge → recapture" loop: only the first part needs an
absolute position; everything else is placed relative to it in a single call,
verified with ONE capture at the end.

> Limitation: this positions by the part **anchor** only. Edge/face alignment
> ("flush to the end of a beam") needs a **bounding box** read, which is NOT yet
> verified (no confirmed bbox call in §4). Add it to the spike before relying on
> edge alignment.

### 9b. Connect API — MEMBERS + SIGNATURES found (spike 2026-09-18)

`scripts/discover_connect.py` run live (scene with two placed parts). Signatures
below are read from the generated typelib
(`comtypes/gen/_0C40AF17...26_0.py`) — **exact, not guessed.** Live *behavior*
(esp. VARIANT marshaling) is still UNVERIFIED until a gated scratch-scene test.

**The blue/white sphere == the part ANCHOR.** On `IZSceneElement` (every part).
Anchor read/write below is ✅ **VERIFIED LIVE 2026-09-18** (scratch scene, two PIL
parts, no modal, SilentMode on — `scripts/test_anchor_write.py`):
```python
se.GetAnchorTransform()          # -> IZMathMatrix (anchor in LOCAL coords)   ✅
se.SetAnchorTransform(matrix)    # move the anchor on the part                ✅ (HRESULT 0)
se.AnchorTransformComponentValue[t]        # get/set anchor component 0..2 = X,Y,Z m ✅
se.AnchorBehavior                # get/set enum eZAnchorBehavior:             ✅ (get 1 -> set 2 -> get 2)
    #   0 Z_ANCHOR_BEHAVIOR_MOVE_FREELY
    #   1 Z_ANCHOR_BEHAVIOR_FIXED_POSITION        (default for these catalog parts)
    #   2 Z_ANCHOR_BEHAVIOR_ATTACHED_TO_SURFACE   <- "attach to a face"
    #   3 Z_ANCHOR_BEHAVIOR_SLIDE_ALONG_SURFACE   <- "slide on a face"
se.CreateLinks(lLinks:int, piIncrementalMatrix:IZMathMatrix) -> VARIANT[linked els]  # signature only
se.HasInternalLinks(); se.GetInternallyLinkedElements()/Count(); se.Unlink(); se.IsOKToUnlinkInternal()
se.GetPositionTransform()/SetPositionTransform(m)   # anchor in PARENT coords (already used)
```
> ⚠️ `InsertElement()` does NOT drop at origin — verified part landed at
> `[0.599,-0.496,-0.850]`. Always set position after insert; never assume [0,0,0].

**Assembly / constraint solve.** On `IZSceneDoc`.
`AssembleElements` is ✅ **VERIFIED LIVE 2026-09-18** (`scripts/test_link_write.py`):
```python
scene.AssembleElements([elA, elB]) -> IZAssembly    # ✅ plain PYTHON LIST marshals fine
    # created 'Assembly2' with GetChildrenCount()==2; elA.GetParent()==elB.GetParent()=='Assembly2'
    # => parts are grouped and MOVE TOGETHER. QI the returned assembly to IZElement to
    #    rename (.Name) / enumerate; QI to IZSceneElement to move the whole group.
    # NOTE: despite python-ironcad's "VARIANT methods sometimes fail" warning, a Python
    #    list of IZElement pointers works here (comtypes builds the SAFEARRAY VARIANT).
scene.Solve(lSolveSetSize, pSolvingSet, lFixedSetSize, pFixedSet)  # arrays of IZElement* — NOT yet tested
scene.ConstraintSolver        # get/set bool
scene.NeedConstraintSolve     # get/set bool
scene.RelationMgr             # -> IZRelationMgr: GetShapeRelations()->VARIANT, GetShapeRelationsCount()->int (was 0 before/after assemble)
scene.MoveChild(piChild, vbKeepLink)   # object it's called ON is the NEW parent — re-parent; NOT yet tested
scene.UnlinkSceneElements(...); scene.GetLinksInfo(...); scene.GetDirectLinksInfo(...)
```

**Selection is now fully mapped (resolves §5).** On `IZSceneDoc.SelectionMgr`
(`IZSelectionMgr`): `GetSelectedElements()`, `GetTrueSelectedElements()`,
`GetSelectedElementsZArray()`, `AddElementToSelection(el)`,
`AddElementsToSelection(...)`, `RemoveAllFromSelection()`, `SelectionsAvailable`,
`SelectAllTopLevelElements()`, `GetSelectedFaces/Edges/Vertices/Curves`(+`...Count`),
`SelectionFilterType`. Enables `ironcad_get_selection` + a "connect the two
SELECTED parts" workflow.

⚠️ **VARIANT-array caution (§1):** `AssembleElements` takes a VARIANT array and
`Solve` takes `POINTER(POINTER(IZElement))` arrays — the exact class python-ironcad
warns "sometimes don't work." The VARIANT-free primitives (`SetAnchorTransform`,
`AnchorBehavior`, single-element `CreateLinks`) are the lower-risk first target.

**STATUS 2026-09-18:** anchor primitives + `AssembleElements` both VERIFIED live
(non-modal, SilentMode on, nothing saved). Wired into tools: `ironcad_set_anchor`
/ `ironcad_get_anchor` (anchor) and **`ironcad_connect_parts`** (assembly grouping
via AssembleElements). `relative_to`+`offset` (§9) remains the positioning
stand-in; `AssembleElements` is now the true "move together" link. Still untested:
`Solve`/constraints and `MoveChild` re-parenting (not needed for basic grouping).
**Never fabricate a method name.**

## 10. Interference / joint awareness (calculate, don't screenshot) — VERIFIED

Real parts have a cross-section, so members whose axes meet at a point
INTERPENETRATE (verified: naive 40×40 cube had 4 corner clashes of 40×40×40 mm).
Build by CALCULATION, not screenshots:

- `ironcad_get_part_bbox(name)` → global `{dims_m, min_m, max_m, center_m}`
  (IZPart.GetBoundingBox). Read a profile's section (40×40 → dims ~[0.04,0.04,L])
  and extents, then compute placements.
- `ironcad_check_interference(tolerance_mm=0.5)` → pairwise AABB overlap of every
  leaf part (recurses assemblies), `{clash_count, clashes:[{a,b,overlap_mm}]}`.
  Aim for clash_count 0 (touching faces are not clashes). AABB is exact for
  axis-aligned extrusion frames; conservative for non-box/rotated parts.

Clash-free frame recipe (equal fixed-length bars, section t): put the 4 posts in
the corner columns spanning z[0,L]; stack the horizontal bars in thin z-layers
OUTSIDE [0,L] with the two horizontal axes on separate layers (bottom X z[-t,0],
bottom Y z[-2t,-t], top X z[L,L+t], top Y z[L+t,L+2t]) → members butt, never
interpenetrate. Verified via `ironcad_check_interference` → 0 clashes.

## RESOLVED (were open)
- ✅ `InsertElement()` — returns the new IZElement, lands in the active scene at a default transform; name auto-resolves parametric placeholders (e.g. `PIL4140SNN_` → `PIL4140SNN500`).
- ✅ `IZParameter` value get/set → `.Value` (float), `.Expression` (str).
- ✅ `eLinksSaveOptions` enum = 1..5 (`Z_LINKS_IGNORE=5`); 0 is invalid.
- ✅ Modal-wedge class → eliminated by SilentMode + file-copy backups.

## STILL OPEN (non-blocking)
1. Rotation semantics of `PositionTransformComponentValue[3..5]` — we use matrix `SetRotation` instead.
2. Document unit metadata call (positions are metres; no explicit units read yet).
3. ~~`IZSelectionMgr` enumeration members~~ ✅ RESOLVED §9b (GetSelectedElements etc.).
4. Full catalog-item parameter/config introspection before instantiation (partial).
5. **Real attach/mate/anchor API** — members + signatures now FOUND (§9b: anchor
   transform/behavior, CreateLinks, AssembleElements, Solve). Still need ONE gated
   live write on a scratch scene to verify behavior/VARIANT marshaling, then wire
   `ironcad_connect_parts`. Stand-in until then: `relative_to`+`offset` (§9).
6. ~~Bounding-box read~~ ✅ RESOLVED 2026-09-18: `el.QueryInterface(ICAPI.IZPart).GetBoundingBox(vbInLocalSpace)`
   → VARIANT[6] = [minx,miny,minz,maxx,maxy,maxz] (also on IZAssembly/IZBody/IZFeature).
7. **Catalog profile length control** — for `PIL4040SNN_` (40x40 T-slot, 500mm
   default) the length param 'Abdeckleiste' is expression-driven; Value/Expression/
   TryToSetExpression all return "Not implemented". Length not settable via API for
   this part type; open whether other catalog parts differ.
8. **Rotation axes**: `SetRotation(nAxis,deg,applyToCurrent)` uses the part's LOCAL
   frame. Default extrusion length = global +Y on insert. Recipes (rotation_deg
   [rx,ry,rz], applied 0,1,2): +Z=[0,-90,0]; +X=[0,-90,-90]. Now exposed as
   `rotation_deg` on add_catalog_part / build_parts.

## Reproduce
`scripts/discover_api.py` performs the read-only live discovery above (QI path,
catalog/scene enumeration, JPEG export). Re-run after any IronCAD upgrade.

## Camera (verified live 2026-09-24)

`scene.CameraMgr.ActiveCamera` -> `IZCamera`. Getters return tuples.
Setters (`Position`, `Direction`, `Up`, `CenterOfInterest`) take a VARIANT
that must be a SAFEARRAY of doubles: pass `array.array('d', [x, y, z])`. A
plain tuple or list marshals as a VARIANT array and fails with E_INVALIDARG.
`AdjustCameraToFitShapeInRect` keeps the direction you set, so set the
direction, then fit, then `ExportImage`. Used by `ironcad_capture_view(view=...)`.
