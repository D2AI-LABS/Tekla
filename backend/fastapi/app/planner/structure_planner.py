# structure_planner.py — Universal AI BIM Structure Planner
#
# Converts natural language into an executable plan:
#   classify type → extract params → component checklist → generator routing → validation targets

from typing import Dict, List, Any
import re

from app.extractor.structure_detector import detect_structure, placement_for_type, is_create_command, extract_primary_command
from app.ai.universal_rule_engine import get_generation_checklist

# ── Supported structure taxonomy (maps to grid_engine dispatch keys) ──────────
STRUCTURE_TAXONOMY = {
    "building":           {"label": "Building", "generator": "building"},
    "office_building":    {"label": "Office building", "generator": "building"},
    "residential":        {"label": "Residential building", "generator": "building"},
    "warehouse":          {"label": "Warehouse", "generator": "warehouse"},
    "portal_frame":       {"label": "Portal shed", "generator": "portal_frame"},
    "industrial_frame":   {"label": "Industrial frame", "generator": "industrial_frame"},
    "pipe_rack":          {"label": "Pipe rack", "generator": "pipe_rack"},
    "tower_lattice":      {"label": "Telecom / lattice tower", "generator": "tower_lattice"},
    "tower_hexagonal":    {"label": "Hexagonal lattice tower", "generator": "tower_hexagonal"},
    "tower_transmission": {"label": "Transmission tower", "generator": "tower_transmission"},
    "tower_telecom":      {"label": "Telecom tower", "generator": "tower_telecom"},
    "tower_guyed":        {"label": "Guyed mast", "generator": "tower_guyed"},
    "bridge":             {"label": "Bridge", "generator": "bridge"},
    "stadium":            {"label": "Stadium", "generator": "stadium"},
    "substation":         {"label": "Substation", "generator": "substation"},
    "parking":            {"label": "Parking structure", "generator": "parking"},
}

# Required BIM components per structure family (planning checklist)
COMPONENT_CATALOG: Dict[str, List[str]] = {
    "building": [
        "columns", "beams", "bracing", "floor_beams", "roof_beams", "roof_framing", "connections",
    ],
    "warehouse": [
        "portal_columns", "rafters", "ridge_beam", "purlins", "girts", "bracing",
    ],
    "portal_frame": ["portal_columns", "rafters", "bracing", "purlins"],
    "pipe_rack": ["columns", "pipe_support_beams", "longitudinal_ties", "cross_bracing"],
    "industrial_frame": ["columns", "beams", "equipment_supports", "bracing"],
    "tower_lattice": [
        "main_legs", "horizontal_members", "diagonal_bracing", "base_ring",
        "ladder", "platforms", "handrails", "antenna_mast", "antenna_frame", "cable_support",
    ],
    "tower_hexagonal": [
        "main_legs", "horizontal_members", "diagonal_bracing", "base_ring", "crown",
        "ladder", "platforms", "handrails", "antenna_mast", "antenna_frame",
    ],
    "tower_transmission": ["main_legs", "k_bracing", "cross_arms", "horizontal_members"],
    "tower_telecom": ["main_legs", "horizontal_members", "diagonal_bracing", "platform_arms"],
    "tower_guyed": ["mast", "guy_cables", "struts"],
    "bridge": ["deck_girders", "cross_members", "bracing", "supports", "abutments"],
    "stadium": ["columns", "radial_beams", "ring_beams", "bracing", "roof_trusses"],
    "substation": ["columns", "equipment_beams", "bracing", "platform"],
    "parking": ["columns", "beams", "ramps", "bracing"],
}

# Prompt keywords → param flags
FEATURE_KEYWORDS = {
    "has_ladder":       ["ladder", "staircase", "stairs"],
    "has_platform":     ["platform", "platforms", "handrail", "handrails"],
    "has_antenna":      ["antenna", "mast", "mounting frame", "equipment mount", "cable support"],
    "has_foundation":   ["foundation", "base plate", "base plates"],
    "has_bracing":      ["bracing", "x-bracing", "cross brac", "diagonal"],
    "has_roof":         ["roof", "purlin", "purlins", "gable"],
    "has_crane":        ["crane", "crane beam"],
    "complete_model":   ["complete", "automatically generate", "full engineering", "repair missing"],
}


def _normalize_prompt(prompt: str) -> str:
    return re.sub(r"\s+", " ", extract_primary_command(prompt).strip().lower())


def _infer_feature_flags(text: str, stype: str) -> Dict[str, bool]:
    """Map prompt keywords to generator feature flags."""
    flags = {}
    for flag, keywords in FEATURE_KEYWORDS.items():
        flags[flag] = any(k in text for k in keywords)

    # "Complete" prompts enable all standard extras for the structure family
    if flags.get("complete_model") or "complete bim" in text or "complete structure" in text:
        if "tower" in stype:
            flags["has_ladder"] = True
            flags["has_platform"] = True
            flags["has_antenna"] = True
            flags["has_bracing"] = True
        elif stype in ("building", "office_building", "residential"):
            flags["has_bracing"] = True
            flags["has_roof"] = True
        elif stype == "warehouse":
            flags["has_bracing"] = True

    return flags


def _merge_params(base: dict, flags: dict) -> dict:
    """Apply planner flags onto grid_engine params."""
    p = dict(base)
    if "tower" in p.get("structure_type", ""):
        p["has_ladder"] = flags.get("has_ladder", p.get("has_ladder", False))
        p["has_platform"] = flags.get("has_platform", p.get("has_platform", False))
        p["has_antenna"] = flags.get("has_antenna", p.get("has_antenna", False))
        p["tower_features"] = {
            "ladder": p["has_ladder"],
            "platform": p["has_platform"],
            "antenna": p["has_antenna"],
        }
    if flags.get("has_bracing"):
        p["has_bracing"] = True
    if flags.get("has_crane"):
        p["has_crane"] = True
    if flags.get("has_roof") and "roof_type" not in p:
        p["roof_type"] = "gable" if p.get("structure_type") in ("warehouse", "portal_frame") else "flat"
    p["planner_flags"] = flags
    return p


def build_component_checklist(stype: str, flags: dict) -> List[dict]:
    """Required components with planned inclusion based on flags."""
    base_key = stype
    if stype in ("office_building", "residential"):
        base_key = "building"
    items = COMPONENT_CATALOG.get(base_key) or COMPONENT_CATALOG.get(stype, ["primary_members"])

    checklist = []
    for name in items:
        planned = True
        if name in ("ladder",) and not flags.get("has_ladder"):
            planned = False
        if name in ("platforms", "handrails") and not flags.get("has_platform"):
            planned = False
        if name in ("antenna_mast", "antenna_frame", "cable_support") and not flags.get("has_antenna"):
            planned = False
        checklist.append({"component": name, "planned": planned, "status": "pending"})
    return checklist


def build_knowledge_graph(stype: str, params: dict, checklist: List[dict]) -> dict:
    """Semantic BIM graph — member categories and relationships to generate."""
    nodes = []
    edges = []
    if "tower" in stype:
        levels = params.get("levels") or params.get("bays") or 5
        nodes = [
            {"id": "legs", "role": "PRIMARY_COLUMN", "category": "main_legs",
             "label": "Main legs", "count_hint": params.get("faces", 6)},
            {"id": "horizontals", "role": "SECONDARY", "category": "horizontal_members",
             "label": "Ring horizontals", "count_hint": levels},
            {"id": "diagonals", "role": "SECONDARY", "category": "diagonal_bracing",
             "label": "Face bracing", "count_hint": levels * 2},
        ]
        edges = [
            {"from": "legs", "to": "horizontals", "relation": "SUPPORTS"},
            {"from": "horizontals", "to": "diagonals", "relation": "FRAMES_INTO"},
        ]
        if params.get("has_ladder"):
            nodes.append({"id": "ladder", "role": "SECONDARY", "category": "ladder", "label": "Ladder cage"})
            edges.append({"from": "legs", "to": "ladder", "relation": "ATTACHED_TO"})
        if params.get("has_platform"):
            nodes.append({"id": "platform", "role": "BEAM", "category": "platforms", "label": "Work platform"})
            edges.append({"from": "horizontals", "to": "platform", "relation": "SUPPORTS"})
        if params.get("has_antenna"):
            nodes.append({"id": "antenna", "role": "BEAM", "category": "antenna_mast", "label": "Antenna mast"})
            edges.append({"from": "legs", "to": "antenna", "relation": "MOUNTED_ON"})
    elif stype in ("building", "office_building", "residential", "warehouse", "portal_frame"):
        nodes = [
            {"id": "columns", "role": "PRIMARY_COLUMN", "category": "columns", "label": "Columns"},
            {"id": "beams", "role": "PRIMARY_BEAM", "category": "beams", "label": "Beams"},
            {"id": "bracing", "role": "SECONDARY", "category": "bracing", "label": "Bracing"},
        ]
        edges = [
            {"from": "columns", "to": "beams", "relation": "SUPPORTS"},
            {"from": "beams", "to": "bracing", "relation": "BRACED_BY"},
        ]
    elif stype == "bridge":
        nodes = [
            {"id": "girders", "role": "PRIMARY_BEAM", "category": "deck_girders", "label": "Deck girders"},
            {"id": "supports", "role": "PRIMARY_COLUMN", "category": "supports", "label": "Piers / supports"},
            {"id": "bracing", "role": "SECONDARY", "category": "bracing", "label": "Cross bracing"},
        ]
        edges = [
            {"from": "supports", "to": "girders", "relation": "SUPPORTS"},
            {"from": "girders", "to": "bracing", "relation": "BRACED_BY"},
        ]
    else:
        nodes = [{"id": "primary", "role": "PRIMARY", "category": "structural_frame", "label": "Primary frame"}]

    if not edges and len(nodes) > 1:
        for i in range(len(nodes) - 1):
            edges.append({"from": nodes[i]["id"], "to": nodes[i + 1]["id"], "relation": "FRAMES_INTO"})

    return {
        "structure_type": stype,
        "nodes": nodes,
        "edges": edges,
        "checklist": checklist,
        "rule_checklist": get_generation_checklist(
            params.get("structure_type", stype).replace("office_building", "building").replace("residential", "building")
        ),
    }


def plan_structure(prompt: str) -> dict:
    """
    Master planner — call before grid generation.
    Returns executable plan consumed by main.py / agent_engine.
    """
    text = _normalize_prompt(prompt)
    params = detect_structure(prompt)
    stype = params.get("structure_type", "building")
    flags = _infer_feature_flags(text, stype)
    params = _merge_params(params, flags)

    taxonomy = STRUCTURE_TAXONOMY.get(stype, {"label": stype, "generator": stype})
    checklist = build_component_checklist(stype, flags)
    graph = build_knowledge_graph(stype, params, checklist)

    placement = placement_for_type(stype)
    if any(k in text for k in ["beside", "adjacent", "next to", "append"]):
        placement = "append"

    stages = [
        {"stage": 1, "name": "Structure detection", "action": "classify_type", "output": stype},
        {"stage": 2, "name": "Parameter extraction", "action": "parse_dimensions", "output": "params"},
        {"stage": 3, "name": "Knowledge graph", "action": "build_graph", "output": f"{len(graph['nodes'])} nodes"},
        {"stage": 4, "name": "Topology plan", "action": "component_checklist", "output": f"{len(checklist)} components"},
        {"stage": 5, "name": "Geometry generation", "action": "call_generator", "output": taxonomy["generator"]},
        {"stage": 6, "name": "Geometry validation", "action": "validate_footprint"},
        {"stage": 7, "name": "Structure optimization", "action": "repair_gaps"},
        {"stage": 8, "name": "Tekla-ready output", "action": "assign_profiles"},
        {"stage": 9, "name": "Tekla insert", "action": "write_bim_elements"},
        {"stage": 10, "name": "Final validation", "action": "report_counts"},
    ]

    return {
        "prompt": prompt,
        "structure_type": stype,
        "structure_label": taxonomy["label"],
        "generator": taxonomy["generator"],
        "params": params,
        "placement": placement,
        "feature_flags": flags,
        "component_checklist": checklist,
        "knowledge_graph": graph,
        "pipeline_plan": stages,
        "is_create": is_create_command(prompt),
        "command_pattern": "Create [structure type] [dimensions] [options]",
    }


def verify_components(elements: list, plan: dict) -> dict:
    """Post-generation checklist verification (Stage 10 helper)."""
    stype = plan.get("structure_type", "")
    cols = sum(1 for e in elements if e.get("Type") == "COLUMN")
    beams = sum(1 for e in elements if e.get("Type") == "BEAM")
    sec = sum(1 for e in elements if e.get("Type") == "SECONDARY")

    results = []
    for item in plan.get("component_checklist", []):
        if not item.get("planned"):
            item["status"] = "skipped"
            results.append(item)
            continue
        ok = True
        name = item["component"]
        if name in ("main_legs", "columns", "portal_columns", "supports") and cols == 0:
            ok = False
        if name in ("horizontal_members", "beams", "rafters", "deck_girders", "platforms") and beams == 0 and sec == 0:
            ok = False
        if name in ("diagonal_bracing", "bracing", "k_bracing") and sec == 0:
            ok = False
        item["status"] = "ok" if ok else "missing"
        results.append(item)

    return {
        "columns": cols,
        "beams": beams,
        "secondary": sec,
        "total": len(elements),
        "checklist": results,
        "completeness_pct": round(100 * sum(1 for r in results if r["status"] == "ok") / max(1, sum(1 for r in results if r["planned"])), 1),
    }
