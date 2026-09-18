"""Discovery spike: the REAL "connect objects" API (anchor / attachment / mate).

Goal: resolve, on verified live calls, how ICAPI exposes what the user does by
hand with the blue/white attachment spheres — snapping one part's anchor onto
another part so they link and move together. Until this is run and its findings
recorded in API_NOTES.md, the MCP uses `relative_to` + `offset` (verified
transform math) as an efficient stand-in and must NOT fabricate a connect call
(spec §5 / §14: never invent a method name).

READ-ONLY: this script only introspects members (dir()/repr). It does NOT call
any snap/mate/attach method — those are scene mutations to be gated once verified.

    .venv\\Scripts\\python.exe scripts\\discover_connect.py

Run against a running IronCAD 2024 (API DLLs registered) with a scene that has at
least TWO placed parts, so relationship/selection members have something to show.
Output is a human report to stdout (safe — standalone script, not the MCP server).
"""

from __future__ import annotations

import traceback

# Members to look for, grouped by the mechanism they'd belong to. We only report
# presence + signature-ish repr; we never invoke them.
_SCENE_DOC_TARGETS = [
    "RelationMgr", "ConstraintMgr", "ConstraintSolver", "Solve",
    "AssembleElements", "AssemblyMgr", "AttachmentMgr", "AnchorMgr",
    "CreateConstraint", "CreateRelation", "AddConstraint", "Mate",
]
_SCENE_ELEMENT_TARGETS = [
    "Anchor", "GetAnchor", "SetAnchor", "AnchorPoint", "AttachTo", "Attach",
    "AttachmentPoints", "GetAttachmentPoint", "ConnectionPoints", "Reposition",
    "AttachToElement", "Link", "Constrain", "GetPositionTransform",
]


def _members(obj):
    """Return sorted dir() of a COM object, best-effort."""
    try:
        return sorted(m for m in dir(obj) if not m.startswith("__"))
    except Exception:  # noqa: BLE001
        return []


def _report_targets(obj, label, targets):
    present = _members(obj)
    print(f"\n-- {label}: probing for connect-related members --")
    for name in targets:
        hit = name in present
        mark = "OK  " if hit else "--  "
        detail = ""
        if hit:
            try:
                detail = f"  -> {type(getattr(obj, name)).__name__}"
            except Exception as exc:  # noqa: BLE001
                detail = f"  (present; getattr raised {type(exc).__name__})"
        print(f"   {mark}{name}{detail}")
    # Also dump anything else that looks relevant but wasn't on our list.
    extra = [m for m in present if any(
        kw in m.lower() for kw in
        ("anchor", "attach", "constrain", "relation", "mate", "assemble",
         "connect", "snap", "link", "align")
    ) and m not in targets]
    if extra:
        print(f"   (other candidates on {label}): {', '.join(extra)}")


def main() -> int:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        import comtypes.client
        import python_ironcad  # noqa: F401  (triggers typelib extraction; prints banner)
        import comtypes.gen.ICAPIIRONCADLib as ICAPI

        try:
            app = comtypes.client.GetActiveObject("IronCAD.Application")
        except Exception:
            print("Could not attach — is IronCAD 2024 running?")
            traceback.print_exc()
            return 2
        try:
            base = app.QueryInterface(ICAPI.IZBaseApp)
        except Exception:
            print("Attached, but IZBaseApp QI failed — run elevated "
                  "`python -m python_ironcad` to register the API DLLs.")
            traceback.print_exc()
            return 3

        doc = base.ActiveDoc
        if doc is None:
            print("No active document. Open a scene with two placed parts first.")
            return 4
        scene = doc.QueryInterface(ICAPI.IZSceneDoc)

        print("== IZSceneDoc: relationship / assembly / constraint API ==")
        _report_targets(scene, "IZSceneDoc", _SCENE_DOC_TARGETS)

        # If a RelationMgr / ConstraintMgr exists, enumerate ITS members too.
        for mgr_name in ("RelationMgr", "ConstraintMgr", "AssemblyMgr",
                         "AttachmentMgr", "AnchorMgr"):
            try:
                mgr = getattr(scene, mgr_name)
            except Exception:  # noqa: BLE001
                continue
            if mgr is None:
                continue
            print(f"\n-- {mgr_name} members --")
            print("   " + ", ".join(_members(mgr)))

        print("\n== IZSceneElement: per-part anchor / attach API ==")
        try:
            child = scene.GetFirstChild()
        except Exception as exc:  # noqa: BLE001
            print("   could not enumerate children:", exc)
            child = None
        idx = 0
        while child is not None and idx < 2:  # first two parts is enough
            try:
                se = child.QueryInterface(ICAPI.IZSceneElement)
                nm = getattr(child, "Name", "?")
                _report_targets(se, f"IZSceneElement[{idx}] {nm!r}",
                                _SCENE_ELEMENT_TARGETS)
            except Exception as exc:  # noqa: BLE001
                print(f"   element[{idx}] QI(IZSceneElement) FAIL: {exc}")
            idx += 1
            try:
                child = scene.GetNextChild()
            except Exception:  # noqa: BLE001
                break

        print("\n== Selection manager (for 'attach the two selected parts') ==")
        try:
            selmgr = scene.SelectionMgr
            print("   SelectionMgr members: " + ", ".join(_members(selmgr)))
        except Exception as exc:  # noqa: BLE001
            print("   SelectionMgr FAIL:", exc)

        print("\n== NEXT STEPS ==")
        print("   1. For every OK member above, record its repr/signature in "
              "API_NOTES.md under a new '## 9. Connect / attach (spheres)' section.")
        print("   2. In read_write on a SCRATCH scene, try ONE mutating call "
              "(e.g. attach part B to part A) behind a backup; confirm parts link "
              "and move together, and that it does not pop a modal (SilentMode on).")
        print("   3. Only then wire `ironcad_connect_parts` on the verified call.")
        print("\n== done ==")
        return 0
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
