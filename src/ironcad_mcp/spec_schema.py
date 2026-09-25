"""JSON Schema + validator for the two-stage drawing pipeline's spec file
(PRODUCTION_READINESS_PLAN.md Phase 5, agreed design in
feature-plan-two-stage-drawing-pipeline memory).

`interpret_drawing` (vision-only, zero MCP/IronCAD tool calls) writes a
`<drawing_name>.spec.json` conforming to `SPEC_SCHEMA`. The user reviews and
can hand-edit it. `build_from_sketch` then validates it with
:func:`validate_spec` before touching IronCAD — a structurally invalid or
incomplete spec is refused rather than built against, since fixing a spec.json
by hand is free and fixing a half-built IronCAD scene is not.
"""

from __future__ import annotations

from typing import Any

SPEC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ironcad-mcp drawing interpretation spec",
    "type": "object",
    "required": ["source_drawing", "overall_envelope_mm", "bom", "confidence"],
    "properties": {
        "source_drawing": {
            "type": "string",
            "description": "Path to the PDF/image this spec was interpreted from.",
        },
        "drawing_name": {
            "type": "string",
            "description": "Short identifier, e.g. the drawing number (B150004-173).",
        },
        "overall_envelope_mm": {
            "type": "object",
            "required": ["x", "y", "z"],
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"},
            },
            "description": "The drawing's stated/derived overall envelope, in mm. "
                            "This is a NOMINAL reference for planning, not necessarily "
                            "the literal target once catalog substitutions are applied.",
        },
        "bom": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["item_id", "role", "qty", "catalog_match"],
                "properties": {
                    "item_id": {"type": ["string", "integer"],
                                "description": "The drawing's own BOM item number/label."},
                    "role": {"type": "string",
                             "description": "What this item IS structurally, e.g. "
                                             "'W-rail', 'H-post', 'side panel', 'corner bracket'."},
                    "nominal_size": {"type": "string",
                                      "description": "The drawing's stated dimension(s) for this item, as text (e.g. '42.00in' or '20.9x42.7in')."},
                    "qty": {"type": "integer", "minimum": 1},
                    "catalog_match": {
                        "type": "object",
                        "required": ["status"],
                        "properties": {
                            "status": {"enum": ["resolved", "unresolved"]},
                            "catalog": {"type": "string"},
                            "entry_name": {"type": "string",
                                            "description": "The EXACT catalog part name, verified against ironcad_list_catalog_parts / ironcad_get_catalog_manifest — never invented."},
                            "substitution_note": {"type": "string",
                                                    "description": "If this substitutes for the drawing's literal spec (e.g. 41mm profile drawn -> 40mm available), say so explicitly."},
                            "why_unresolved": {"type": "string",
                                                 "description": "Required when status=unresolved: why no catalog match was found."},
                        },
                    },
                },
            },
        },
        "panel_relationships": {
            "type": "array",
            "description": "Which framing members border each panel, and on which side — needed for the T-slot mid-channel panel-fit sizing rule.",
            "items": {
                "type": "object",
                "properties": {
                    "panel_item_id": {"type": ["string", "integer"]},
                    "borders": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "side": {"enum": ["top", "bottom", "left", "right", "front", "back"]},
                                "member_item_id": {"type": ["string", "integer"]},
                                "channel_faces_panel": {"type": "boolean",
                                                          "description": "True if this member's T-slot channel opening faces the panel (mid-channel inset applies); false if it's a non-channel-facing edge (flush/daylight, zero inset)."},
                            },
                        },
                    },
                },
            },
        },
        "joint_connector_family": {
            "type": "string",
            "description": "Which connector family the drawing's hardware belongs to (e.g. FAS40xx for a 40mm profile) — informational; NOT built by this MCP per the no-fasteners standing rule, but recorded so a human adding hardware afterward knows which family to pick.",
        },
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Every inference made that wasn't explicitly stated on the drawing (e.g. 'assumed open on 4th side per FRONT/BACK subassembly naming convention').",
        },
        "gaps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "BOM items or drawing details this interpretation could NOT resolve (e.g. 'no clean catalog match for the corner bracket, item 7').",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "Overall confidence in this interpretation. ALWAYS shown to the user for review regardless of this value (per the agreed always-on review gate) — this field is informational, not a gate itself.",
        },
    },
}


def validate_spec(spec: dict) -> list[str]:
    """Return a list of human-readable error strings (empty = valid).

    Uses `jsonschema` for structural validation, then a couple of semantic
    checks jsonschema itself can't express (e.g. `why_unresolved` required
    exactly when status=unresolved)."""
    import jsonschema

    errors: list[str] = []
    validator = jsonschema.Draft202012Validator(SPEC_SCHEMA)
    for err in sorted(validator.iter_errors(spec), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{loc}: {err.message}")

    for item in spec.get("bom", []) if isinstance(spec.get("bom"), list) else []:
        match = item.get("catalog_match") if isinstance(item, dict) else None
        if isinstance(match, dict) and match.get("status") == "unresolved" and not match.get("why_unresolved"):
            errors.append(
                f"bom item {item.get('item_id', '?')}: catalog_match.status is "
                f"'unresolved' but 'why_unresolved' is missing"
            )
        if isinstance(match, dict) and match.get("status") == "resolved" and not match.get("entry_name"):
            errors.append(
                f"bom item {item.get('item_id', '?')}: catalog_match.status is "
                f"'resolved' but 'entry_name' is missing"
            )
    return errors
