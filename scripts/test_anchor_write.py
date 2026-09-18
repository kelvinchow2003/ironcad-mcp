"""GATED live WRITE test: anchor primitives (the blue/white sphere), VARIANT-free.

Verifies the §9b anchor API behaves as its signatures imply, on a SCRATCH scene
only. It NEVER touches a saved file: if the active document has an on-disk path,
it refuses and tells you to open a blank scene (File > New) first.

What it does (all reversible, nothing is saved):
  1. attach (verified QI path) + SilentMode on.
  2. SAFETY GATE: refuse if the active doc is a saved .ics file.
  3. place two catalog parts (fresh), separate them.
  4. READ each part's anchor: AnchorTransformComponentValue[0..2], AnchorBehavior.
  5. WRITE part B: SetAnchorTransform (nudge) then AnchorBehavior = 2
     (ATTACHED_TO_SURFACE); read both back to confirm the calls took.
  6. report; DO NOT save.

    .venv\\Scripts\\python.exe scripts\\test_anchor_write.py

Standalone script (safe stdout). Run with IronCAD 2024 open on a blank scene.
"""

from __future__ import annotations

import os
import traceback

Z_ATTACHED_TO_SURFACE = 2  # eZAnchorBehavior (API_NOTES §9b)


def _p(label, fn):
    try:
        v = fn()
        print(f"OK   {label}: {v!r}")
        return v
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {label}: {type(exc).__name__}: {exc}")
        return None


def _find_droppable_entry(cmgr, ICAPI):
    """Prefer PIL_DIST/PIL4140SNN_; else first non-group entry of any catalog."""
    want_cat, want_entry = "PIL_DIST", "PIL4140SNN_"
    for i in range(int(cmgr.Count)):
        cat = cmgr.Catalog[i]
        if str(cat.Name) != want_cat:
            continue
        for j in range(int(cat.EntryCount)):
            e = cat.Entry[j]
            if str(e.Name) == want_entry:
                return cat, e
    # fallback: first droppable entry anywhere
    for i in range(int(cmgr.Count)):
        cat = cmgr.Catalog[i]
        for j in range(int(cat.EntryCount)):
            e = cat.Entry[j]
            try:
                if not e.IsCatalogGroup():
                    return cat, e
            except Exception:  # noqa: BLE001
                return cat, e
    raise RuntimeError("No droppable catalog entry found.")


def _anchor_xyz(se):
    return [round(float(se.AnchorTransformComponentValue[t]), 5) for t in range(3)]


def _pos_xyz(se):
    return [round(float(se.PositionTransformComponentValue[t]), 5) for t in range(3)]


def main() -> int:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        import comtypes.client
        import python_ironcad  # noqa: F401  (typelib extraction; prints banner)
        import comtypes.gen.ICAPIIRONCADLib as ICAPI

        try:
            app = comtypes.client.GetActiveObject("IronCAD.Application")
            base = app.QueryInterface(ICAPI.IZBaseApp)
        except Exception:
            print("Could not attach / QI IZBaseApp — is IronCAD 2024 running and "
                  "registered?")
            traceback.print_exc()
            return 2

        # SilentMode ON (prevent modal wedge).
        try:
            app.QueryInterface(ICAPI.IZBaseAppSetups).SilentMode = True
            print("OK   SilentMode = True")
        except Exception as exc:  # noqa: BLE001
            print(f"WARN SilentMode could not be set: {exc}")

        doc = base.ActiveDoc
        if doc is None:
            print("No active document. Open a NEW blank scene (File > New) first.")
            return 4
        name = str(getattr(doc, "Name", "") or "")
        print(f"Active doc: {name!r}")

        # ---- SAFETY GATE: never write to a saved file --------------------
        if name and os.path.isfile(name):
            print("\nREFUSING: the active document is a SAVED file on disk:")
            print(f"   {name}")
            print("This write test only runs on an unsaved SCRATCH scene, so it can "
                  "never harm real work. Open a new blank scene (File > New), leave "
                  "it unsaved, and re-run.")
            return 5
        print("SAFE: active doc is an unsaved scratch scene — proceeding.\n")

        scene = doc.QueryInterface(ICAPI.IZSceneDoc)
        cmgr = base.CatalogMgr

        print("== place two fresh parts ==")
        cat, entry = _find_droppable_entry(cmgr, ICAPI)
        print(f"   using catalog {cat.Name!r} entry {entry.Name!r}")

        elA = entry.InsertElement()
        elB = entry.InsertElement()
        try:
            elA.Name = "claude_anchorA"
            elB.Name = "claude_anchorB"
        except Exception as exc:  # noqa: BLE001
            print(f"   (rename skipped: {exc})")
        seA = elA.QueryInterface(ICAPI.IZSceneElement)
        seB = elB.QueryInterface(ICAPI.IZSceneElement)

        # separate B by 0.4 m in X (verified matrix path)
        mB = seB.GetPositionTransform()
        mB.SetTranslation(0.4, 0.0, 0.0)
        seB.SetPositionTransform(mB)
        scene.Update()
        print(f"   A pos={_pos_xyz(seA)}  B pos={_pos_xyz(seB)}")

        print("\n== READ anchors (before) ==")
        print(f"   A anchor xyz={_anchor_xyz(seA)}  behavior={_p('A AnchorBehavior', lambda: int(seA.AnchorBehavior))}")
        print(f"   B anchor xyz={_anchor_xyz(seB)}  behavior={_p('B AnchorBehavior', lambda: int(seB.AnchorBehavior))}")

        print("\n== WRITE B anchor transform (nudge anchor +0.05 m local X) ==")
        mAnchor = _p("B GetAnchorTransform()", seB.GetAnchorTransform)
        if mAnchor is not None:
            _p("B anchor.SetTranslation(0.05,0,0)",
               lambda: mAnchor.SetTranslation(0.05, 0.0, 0.0))
            _p("B SetAnchorTransform(m)", lambda: seB.SetAnchorTransform(mAnchor))
            try:
                scene.Update()
            except Exception:  # noqa: BLE001
                pass
            print(f"   B anchor xyz AFTER = {_anchor_xyz(seB)}  (expect ~[0.05,0,0])")

        print("\n== WRITE B AnchorBehavior = 2 (ATTACHED_TO_SURFACE) ==")
        _p("B set AnchorBehavior=2",
           lambda: setattr(seB, "AnchorBehavior", Z_ATTACHED_TO_SURFACE))
        after = _p("B AnchorBehavior AFTER", lambda: int(seB.AnchorBehavior))
        print(f"   -> behavior now {after} (2 == attached-to-surface)")

        print("\n== RESULT ==")
        print("   Read/write of AnchorTransform + AnchorBehavior exercised above.")
        print("   NOTHING SAVED. Inspect IronCAD: two parts placed; B's anchor moved;")
        print("   B behavior set. Undo (Ctrl+Z) or just close-without-save to discard.")
        print("\n== done ==")
        return 0
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
