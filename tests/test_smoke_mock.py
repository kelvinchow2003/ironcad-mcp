"""Automated tests that need NO IronCAD (spec Section 10).

Covers tool-independent logic: name resolution, mode gating, registration-error
detection, and the stdout-redirect guard. We deliberately do not deep-mock ICAPI.
"""

from __future__ import annotations

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
        "ironcad_get_anchor",
        "ironcad_capture_view", "ironcad_list_catalogs", "ironcad_list_catalog_parts",
        "ironcad_get_catalog_part_info", "ironcad_add_catalog_part",
        "ironcad_build_parts",
        "ironcad_set_part_parameter", "ironcad_move_part", "ironcad_set_anchor",
        "ironcad_connect_parts",
        "ironcad_save", "ironcad_save_copy", "ironcad_execute_api_script",
    }
    assert expected <= tools, f"missing tools: {expected - tools}"
    prompts = {p.name for p in asyncio.run(mcp.list_prompts())}
    assert "build_from_sketch" in prompts


def test_redirect_stdout_to_stderr_object_fallback(monkeypatch, capsys):
    # Under pytest capture, fds may be unavailable; the object-level fallback
    # must still keep writes off stdout.
    with redirect_stdout_to_stderr():
        print("this must not land on stdout")
    out = capsys.readouterr()
    assert "this must not land on stdout" not in out.out
