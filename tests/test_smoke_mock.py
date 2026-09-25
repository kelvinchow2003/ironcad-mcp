"""Automated tests that need NO IronCAD (spec Section 10).

Covers tool-independent logic: name resolution, mode gating, registration-error
detection, and the stdout-redirect guard. We deliberately do not deep-mock ICAPI.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys

import pytest

from ironcad_mcp import safety
from ironcad_mcp.connection import _looks_like_registration_error
from ironcad_mcp.logging_setup import redirect_stdout_to_stderr
from ironcad_mcp.safety import NameResolutionError, NamedItem, WriteRefused


# ---- name resolution ----------------------------------------------------

def _items():
    return [
        NamedItem(0, "Bracket", object()),
        NamedItem(1, "Bolt", object()),
        NamedItem(2, "Bolt", object()),  # duplicate name
    ]


def test_resolve_exact_unique():
    it = safety.resolve_by_name(_items(), "Bracket")
    assert it.index == 0


def test_resolve_ambiguous_refuses():
    with pytest.raises(NameResolutionError):
        safety.resolve_by_name(_items(), "Bolt")


def test_resolve_missing_refuses_and_lists():
    with pytest.raises(NameResolutionError) as e:
        safety.resolve_by_name(_items(), "Washer")
    assert "Bracket" in str(e.value)  # lists available names


def test_resolve_index_fallback():
    it = safety.resolve_by_name(_items(), "Nope", index_fallback=1)
    assert it.index == 1


def test_resolve_case_insensitive_unique():
    it = safety.resolve_by_name(_items(), "bracket")
    assert it.index == 0


# ---- regression: ambiguous name + index (fixed 2026-09-24) --------------
# Previously `index_fallback` was only ever consulted when a name had ZERO
# matches, so it did nothing for the (common) case of a name shared by 2+
# stock catalog parts — exactly the scenario a symmetric T-slot frame hits
# for every group of identically-cut members. See PRODUCTION_READINESS_PLAN
# Phase 1.1 / MCP_TEST_REPORT_173.md.

def test_resolve_ambiguous_with_index_disambiguates():
    it = safety.resolve_by_name(_items(), "Bolt", index_fallback=2)
    assert it.index == 2 and it.name == "Bolt"
    it = safety.resolve_by_name(_items(), "Bolt", index_fallback=1)
    assert it.index == 1 and it.name == "Bolt"


def test_resolve_ambiguous_case_insensitive_with_index_disambiguates():
    it = safety.resolve_by_name(_items(), "bolt", index_fallback=2)
    assert it.index == 2 and it.name == "Bolt"


def test_resolve_ambiguous_bad_index_still_refuses():
    # index_fallback that doesn't correspond to any item at all -> still refuses.
    with pytest.raises(NameResolutionError):
        safety.resolve_by_name(_items(), "Bolt", index_fallback=99)


def test_resolve_unique_exact_match_wins_over_index():
    # A clean, unambiguous exact match takes priority over a (wrong) index —
    # index is only a fallback for missing/ambiguous names, never an override.
    it = safety.resolve_by_name(_items(), "Bracket", index_fallback=1)
    assert it.index == 0 and it.name == "Bracket"


# ---- mode gating --------------------------------------------------------

def test_read_only_refuses_writes(monkeypatch):
    monkeypatch.setenv("IRONCAD_MCP_MODE", "read_only")
    assert safety.current_mode() == "read_only"
    with pytest.raises(WriteRefused):
        safety.require_write_mode("ironcad_add_catalog_part")


def test_read_write_allows(monkeypatch):
    monkeypatch.setenv("IRONCAD_MCP_MODE", "read_write")
    assert safety.is_read_write()
    safety.require_write_mode("ironcad_add_catalog_part")  # no raise


def test_backup_none_for_unsaved(monkeypatch, tmp_path):
    # Unsaved / new scene: no on-disk file -> nothing to back up, returns None.
    monkeypatch.setenv("IRONCAD_MCP_BACKUP_DIR", str(tmp_path))
    assert safety.backup_active_doc(None) is None
    assert safety.backup_active_doc("Scene1") is None  # not a real path


def test_backup_requires_dir_for_saved_file(monkeypatch, tmp_path):
    src = tmp_path / "part.ics"
    src.write_bytes(b"ICS")
    monkeypatch.delenv("IRONCAD_MCP_BACKUP_DIR", raising=False)
    with pytest.raises(WriteRefused):
        safety.backup_active_doc(str(src))


def test_backup_copies_saved_file(monkeypatch, tmp_path):
    src = tmp_path / "b150004_221.ics"
    src.write_bytes(b"ICS-DATA")
    bdir = tmp_path / "bk"
    monkeypatch.setenv("IRONCAD_MCP_BACKUP_DIR", str(bdir))
    dst = safety.backup_active_doc(str(src))
    assert dst and os.path.isfile(dst) and os.path.dirname(dst) == str(bdir)
    with open(dst, "rb") as f:
        assert f.read() == b"ICS-DATA"
    assert "b150004_221" in os.path.basename(dst) and dst.endswith(".ics")


def test_backup_same_second_does_not_overwrite(monkeypatch, tmp_path):
    src = tmp_path / "part.ics"
    src.write_bytes(b"BEFORE-SAVE")
    monkeypatch.setenv("IRONCAD_MCP_BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setattr(safety, "_timestamp", lambda: "20260924_120000")
    first = safety.backup_active_doc(str(src))
    src.write_bytes(b"AFTER-SAVE")
    second = safety.backup_active_doc(str(src))
    assert first != second
    with open(first, "rb") as f:
        assert f.read() == b"BEFORE-SAVE"
    # Suffixed name still sorts after the unsuffixed one (prune order).
    assert sorted([second, first]) == [first, second]


# ---- backup retention + write-audit trail (Phase 7.2/7.3) ---------------

def test_backup_retention_prunes_oldest(monkeypatch, tmp_path):
    src = tmp_path / "part.ics"
    src.write_bytes(b"ICS-DATA")
    bdir = tmp_path / "bk"
    monkeypatch.setenv("IRONCAD_MCP_BACKUP_DIR", str(bdir))
    monkeypatch.setenv("IRONCAD_MCP_BACKUP_RETENTION_COUNT", "3")
    paths = []
    for i in range(5):
        # Force distinct timestamps so filenames (and thus prune ordering) differ.
        monkeypatch.setattr(
            safety, "_timestamp", lambda i=i: f"2026010{i}_000000"
        )
        paths.append(safety.backup_active_doc(str(src)))
    existing = [p for p in paths if os.path.isfile(p)]
    assert len(existing) == 3, f"expected exactly 3 backups retained, found {len(existing)}"
    # The 3 most recent (highest timestamp) must be the ones kept.
    assert set(existing) == set(paths[-3:])


def test_backup_retention_disabled_at_zero(monkeypatch, tmp_path):
    src = tmp_path / "part.ics"
    src.write_bytes(b"ICS-DATA")
    bdir = tmp_path / "bk"
    monkeypatch.setenv("IRONCAD_MCP_BACKUP_DIR", str(bdir))
    monkeypatch.setenv("IRONCAD_MCP_BACKUP_RETENTION_COUNT", "0")
    paths = []
    for i in range(4):
        monkeypatch.setattr(safety, "_timestamp", lambda i=i: f"2026020{i}_000000")
        paths.append(safety.backup_active_doc(str(src)))
    assert all(os.path.isfile(p) for p in paths), "retention=0 must disable pruning"


def test_write_audit_log_records_backup(monkeypatch, tmp_path):
    import json as _json

    src = tmp_path / "part.ics"
    src.write_bytes(b"ICS-DATA")
    bdir = tmp_path / "bk"
    monkeypatch.setenv("IRONCAD_MCP_BACKUP_DIR", str(bdir))
    dst = safety.backup_active_doc(str(src), action="ironcad_add_catalog_part")
    audit_path = bdir / "audit.log.jsonl"
    assert audit_path.is_file()
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    last = _json.loads(lines[-1])
    assert last["backup_path"] == dst
    assert last["action"] == "ironcad_add_catalog_part"
    assert last["source"] == str(src)


def test_write_audit_log_records_unsaved_scene(monkeypatch, tmp_path):
    import json as _json

    bdir = tmp_path / "bk"
    bdir.mkdir()
    monkeypatch.setenv("IRONCAD_MCP_BACKUP_DIR", str(bdir))
    result = safety.backup_active_doc("Scene1", action="ironcad_build_parts")
    assert result is None
    audit_path = bdir / "audit.log.jsonl"
    assert audit_path.is_file()
    last = _json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["backup_path"] is None
    assert last["action"] == "ironcad_build_parts"


# ---- new typed errors (Phase 4.1) ----------------------------------------

def test_new_error_types_are_runtime_errors():
    assert issubclass(safety.GeometryError, RuntimeError)
    assert issubclass(safety.ComUnavailableError, RuntimeError)
    assert issubclass(safety.UnsupportedCatalogPart, RuntimeError)


# ---- COM worker watchdog (Phase 4.3) -------------------------------------

def test_com_worker_timeout_raises_com_unavailable(monkeypatch):
    import time as _time

    from ironcad_mcp import com_worker

    monkeypatch.setenv("IRONCAD_MCP_COM_TIMEOUT_S", "0.2")
    worker = com_worker.ComWorker(name="test-timeout-worker")
    worker.start()
    try:
        def slow():
            _time.sleep(2.0)
            return "done"

        async def go():
            return await worker.run(slow)

        with pytest.raises(com_worker.ComWorkerError):
            asyncio.run(go())
    finally:
        # The worker thread is still blocked in time.sleep(2.0); give it a
        # moment to finish naturally before stopping (stop() joins with its
        # own timeout and won't hang the test suite either way).
        worker.stop(timeout=3.0)


def test_com_worker_timeout_disabled_at_zero(monkeypatch):
    from ironcad_mcp import com_worker

    monkeypatch.setenv("IRONCAD_MCP_COM_TIMEOUT_S", "0")
    worker = com_worker.ComWorker(name="test-notimeout-worker")
    worker.start()
    try:
        async def go():
            return await worker.run(lambda: "fast result")

        assert asyncio.run(go()) == "fast result"
    finally:
        worker.stop(timeout=3.0)


# ---- spec schema validation (Phase 5) ------------------------------------

def test_valid_spec_passes():
    from ironcad_mcp.spec_schema import validate_spec

    spec = {
        "source_drawing": "x.pdf",
        "overall_envelope_mm": {"x": 1, "y": 2, "z": 3},
        "bom": [{"item_id": 1, "role": "post", "qty": 4,
                  "catalog_match": {"status": "resolved", "catalog": "PIL_DIST",
                                     "entry_name": "PIL4040SNN_"}}],
        "confidence": "high",
    }
    assert validate_spec(spec) == []


def test_incomplete_spec_lists_missing_required_fields():
    from ironcad_mcp.spec_schema import validate_spec

    errors = validate_spec({"source_drawing": "x.pdf"})
    assert any("overall_envelope_mm" in e for e in errors)
    assert any("bom" in e for e in errors)
    assert any("confidence" in e for e in errors)


def test_unresolved_bom_item_requires_why():
    from ironcad_mcp.spec_schema import validate_spec

    spec = {
        "source_drawing": "x.pdf",
        "overall_envelope_mm": {"x": 1, "y": 2, "z": 3},
        "bom": [{"item_id": 1, "role": "bracket", "qty": 1,
                  "catalog_match": {"status": "unresolved"}}],
        "confidence": "low",
    }
    errors = validate_spec(spec)
    assert any("why_unresolved" in e for e in errors)


def test_validate_spec_tool_end_to_end(tmp_path):
    import json as _json

    from ironcad_mcp.server import mcp as _mcp

    spec = {
        "source_drawing": "x.pdf",
        "overall_envelope_mm": {"x": 1, "y": 2, "z": 3},
        "bom": [{"item_id": 1, "role": "post", "qty": 1,
                  "catalog_match": {"status": "resolved", "catalog": "PIL_DIST",
                                     "entry_name": "PIL4040SNN_"}}],
        "confidence": "high",
    }
    spec_path = tmp_path / "drawing.spec.json"
    spec_path.write_text(_json.dumps(spec), encoding="utf-8")

    tool = _mcp._tool_manager.get_tool("ironcad_validate_spec")
    result = asyncio.run(tool.run({"spec_path": str(spec_path)}, convert_result=False))
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["spec"]["bom"][0]["role"] == "post"


# ---- catalog manifest (Phase 6) ------------------------------------------

def test_catalog_manifest_loads_and_has_known_catalogs():
    from ironcad_mcp.catalog_manifest import get_catalog, load_manifest

    manifest = load_manifest()
    assert "PIL_DIST" in manifest["catalogs"]
    assert "PAN_DIST" in manifest["catalogs"]
    pan = get_catalog("pan_dist")  # case-insensitive
    assert pan is not None
    assert "entries" in pan


def test_catalog_manifest_tool_full_and_scoped(tmp_path):
    from ironcad_mcp.server import mcp as _mcp

    tool = _mcp._tool_manager.get_tool("ironcad_get_catalog_manifest")
    full = asyncio.run(tool.run({}, convert_result=False))
    assert "PIL_DIST" in full["catalogs"]

    scoped = asyncio.run(tool.run({"catalog": "PIL_DIST"}, convert_result=False))
    assert scoped["found"] is True
    assert scoped["data"]["purpose"]

    missing = asyncio.run(tool.run({"catalog": "NoSuchCatalog"}, convert_result=False))
    assert missing["found"] is False


# ---- registration-error detection --------------------------------------

def test_registration_error_detection():
    assert _looks_like_registration_error(Exception("...has no attribute 'ZIronCADApp'"))
    assert _looks_like_registration_error(Exception("API is not properly registered"))
    assert not _looks_like_registration_error(Exception("some other COM error"))


# ---- stdout guard -------------------------------------------------------

def test_all_tools_and_prompt_register():
    # Import-and-register smoke: no IronCAD needed. Guards against wiring breakage.
    import asyncio

    from ironcad_mcp.server import mcp

    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    expected = {
        "ironcad_status", "ironcad_attach", "ironcad_get_active_doc_info",
        "ironcad_list_parts", "ironcad_describe_part", "ironcad_get_selection",
        "ironcad_get_anchor", "ironcad_get_part_bbox", "ironcad_check_interference",
        "ironcad_capture_view", "ironcad_list_catalogs", "ironcad_list_catalog_parts",
        "ironcad_get_catalog_part_info", "ironcad_add_catalog_part",
        "ironcad_build_parts", "ironcad_list_children",
        "ironcad_add_catalog_assembly", "ironcad_build_panel",
        "ironcad_get_catalog_manifest", "ironcad_validate_spec",
        "ironcad_set_part_parameter", "ironcad_move_part", "ironcad_set_anchor",
        "ironcad_connect_parts",
        "ironcad_save", "ironcad_save_copy", "ironcad_execute_api_script",
    }
    assert expected <= tools, f"missing tools: {expected - tools}"
    prompts = {p.name for p in asyncio.run(mcp.list_prompts())}
    assert {"build_from_sketch", "interpret_drawing"} <= prompts


def test_redirect_stdout_to_stderr_object_fallback(monkeypatch, capsys):
    # Under pytest capture, fds may be unavailable; the object-level fallback
    # must still keep writes off stdout.
    with redirect_stdout_to_stderr():
        print("this must not land on stdout")
    out = capsys.readouterr()
    assert "this must not land on stdout" not in out.out
