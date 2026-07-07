# structure_editor.py — Universal BIM structure editor
#
# Edit any loaded structure via natural language:
#   Add ladder / platform / antenna to tower
#   Complete missing members (building or tower)
#   Add bracing, purlins, extend bays
#   Cleanup duplicates

from typing import Dict, List, Any, Optional
import re

from app.ai.topology_engine import analyse_topology, infer_structure_type, detect_levels
from app.services.placement_engine import detect_active_grid
from app.extractor.structure_detector import extract_primary_command, is_create_command, is_full_structure_create
from app.planner.structure_planner import plan_structure, verify_components, build_component_checklist
from app.services.grid_engine import (
    _tower_ladder,
    _tower_platform,
    _tower_antenna,
    make_element,
    generate_grid,
)
from app.ai.completion_engine import (
    complete_model,
    find_missing_bracing,
    find_missing_beams,
    find_missing_columns,
    find_missing_roof,
    detect_grid_pattern,
    _build_endpoint_index,
)
from app.knowledge_graph.graph_builder import build_graph
from app.ai.coordinate_solver import solve_coordinates, GridConstraints
from app.ai.completion_engine import _build_endpoint_index


# ── Edit action taxonomy ──────────────────────────────────────────────────────
EDIT_PATTERNS = {
    "cleanup": [
        r"clean\s*up", r"remove duplicate", r"remove out of grid", r"fix model",
    ],
    "add_tower_feature": [
        r"add\s+(?:a\s+)?ladder", r"with\s+ladder", r"install\s+ladder",
        r"add\s+(?:a\s+)?platform", r"with\s+platform", r"add\s+platforms",
        r"add\s+(?:an?\s+)?antenna", r"with\s+antenna", r"add\s+mast",
        r"add\s+handrail", r"add\s+cable support",
    ],
    "complete": [
        r"complete", r"missing member", r"fill gap", r"finish model",
        r"repair missing", r"fix missing", r"analyse and complete",
    ],
    "add_bracing": [
        r"add\s+(?:cross\s+)?brac", r"add\s+x.?brac", r"missing brac",
    ],
    "add_purlins": [
        r"purlin", r"roof beam", r"roof framing",
    ],
    "add_structural": [
        r"add\s+column", r"add\s+beam", r"add\s+floor", r"add\s+roof",
    ],
    "extend": [
        r"extend", r"add\s+bay", r"more\s+bay", r"extra\s+bay", r"increase\s+height",
        r"extend\s+height", r"add\s+\d+\s*m\s+to",
    ],
    "mirror": [r"mirror"],
}


def _bounds(members: list) -> dict:
    xs, ys, zs = [], [], []
    for m in members:
        for k in ("StartPoint", "EndPoint", "startPoint", "endPoint"):
            pt = m.get(k) or {}
            if pt:
                xs.append(float(pt.get("X", pt.get("x", 0))))
                ys.append(float(pt.get("Y", pt.get("y", 0))))
                zs.append(float(pt.get("Z", pt.get("z", 0))))
    if not xs:
        return {"min_x": 0, "max_x": 0, "min_y": 0, "max_y": 0, "min_z": 0, "max_z": 0}
    return {
        "min_x": min(xs), "max_x": max(xs),
        "min_y": min(ys), "max_y": max(ys),
        "min_z": min(zs), "max_z": max(zs),
    }


def infer_model_context(members: list) -> dict:
    """Read structure type and dimensions from the loaded Tekla model."""
    if not members:
        return {"structure_type": "unknown", "member_count": 0}

    inferred = infer_structure_type(members)
    topo = analyse_topology(members)
    bb = _bounds(members)
    grid_ctx = detect_active_grid(members)
    height = bb["max_z"] - bb["min_z"]
    width_x = bb["max_x"] - bb["min_x"]
    width_y = bb["max_y"] - bb["min_y"]
    footprint = max(width_x, width_y, 1000)

    stype = inferred.get("type", "building")
    if "tower" in stype or inferred.get("confidence", 0) > 0.65 and height / footprint > 3:
        family = "tower"
    elif stype in ("warehouse", "shed", "portal_frame"):
        family = "industrial"
    elif stype in ("bridge", "stadium", "pipe_rack"):
        family = stype
    else:
        family = "building"

    return {
        "structure_type": stype,
        "family": family,
        "confidence": inferred.get("confidence", 0),
        "member_count": len(members),
        "height": height,
        "max_z": bb["max_z"],
        "min_z": bb["min_z"],
        "base_width": footprint,
        "top_width": footprint * 0.35,
        "footprint_x": width_x,
        "footprint_y": width_y,
        "origin": grid_ctx.get("origin", {"x": 0, "y": 0, "z": 0}),
        "completeness_pct": topo.get("completeness_pct", 0),
        "topology": topo,
    }


def _dedupe_elements(elements: list) -> list:
    seen, out = set(), []
    for e in elements:
        s, ep = e.get("StartPoint", {}), e.get("EndPoint", {})
        k = _endpoint_key(s, ep)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def _resolve_grid(members: list) -> tuple:
    from topology_engine import detect_bays_xy, detect_levels
    graph = build_graph(members)
    grid = detect_grid_pattern(graph["nodes"]) if graph["nodes"] else {}
    if grid.get("x_coords") and grid.get("y_coords"):
        return grid, graph["nodes"]
    xs, ys = detect_bays_xy(members)
    z_levels = detect_levels(members)
    if len(z_levels) < 2 and z_levels:
        z_levels = sorted(set(z_levels + [z_levels[0] + 3500]))
    def modal_gap(coords):
        if len(coords) < 2:
            return 6000.0
        gaps = [coords[i + 1] - coords[i] for i in range(len(coords) - 1) if coords[i + 1] - coords[i] > 100]
        if not gaps:
            return 6000.0
        from collections import Counter
        c = Counter(round(g / 100) * 100 for g in gaps)
        return float(c.most_common(1)[0][0]) if c else 6000.0
    return {
        "x_coords": xs, "y_coords": ys, "z_levels": z_levels,
        "spacing_x": modal_gap(xs), "spacing_y": modal_gap(ys),
        "story_height": modal_gap([z for z in z_levels if z > 0]) if z_levels else 3500,
    }, graph["nodes"]


def _insert_elements(existing: list, raw_elements: list, label: str) -> dict:
    if not raw_elements:
        return {"status": "ok", "action": label, "added": 0, "elements": []}
    gc = GridConstraints.from_existing_model(existing, padding=5000)
    solved = solve_coordinates(
        raw_elements, existing, gc,
        apply_node_snap=False,
        check_existing_duplicates=True,
    )
    elements = solved["elements"]
    return {
        "status": "ok",
        "action": label,
        "added": len(elements),
        "elements": elements,
        "coordinate_solver_stats": solved["stats"],
        "rejected": len(solved.get("rejected", [])),
    }


def parse_edit_intent(command: str, context: dict) -> dict:
    """Map natural language to an edit action + flags."""
    t = extract_primary_command(command).strip().lower()

    # "Create complete hexagonal tower 45m..." is a NEW structure, not edit-complete
    if is_full_structure_create(command):
        return {"action": "create", "command": command, "flags": {}, "context": context}

    if is_create_command(command) and not any(
        k in t for k in ["add ", "extend ", "complete missing", "repair missing", "fill gap"]
    ):
        return {"action": "create", "command": command, "flags": {}, "context": context}

    action = "auto_complete"
    for action_name, patterns in EDIT_PATTERNS.items():
        if any(re.search(p, t) for p in patterns):
            action = action_name
            break

    # Cleanup keywords inside a full tower spec must not hijack create
    if action == "cleanup" and is_create_command(command):
        action = "auto_complete"

    flags = {
        "ladder":   any(k in t for k in ["ladder", "stair", "stairs"]),
        "platform": any(k in t for k in ["platform", "handrail"]),
        "antenna":  any(k in t for k in ["antenna", "mast", "mount", "cable support"]),
        "bracing":  "brac" in t,
        "purlins":  "purlin" in t or "roof beam" in t,
        "columns":  "column" in t,
        "beams":    "beam" in t and "roof" not in t,
        "roof":     "roof" in t,
        "mirror_x": "mirror" in t and " y" not in t and "along y" not in t,
        "mirror_y": "mirror" in t and (" y" in t or "along y" in t),
    }

    extend_h = _first_mm(t, [
        r"extend\s+(?:height\s+)?(?:to\s+)?(\d+(?:\.\d+)?)\s*m",
        r"increase\s+height\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*m",
        r"add\s+(\d+(?:\.\d+)?)\s*m\s+to\s+(?:the\s+)?(?:tower|structure|height)",
    ])

    return {
        "action": action,
        "command": command,
        "flags": flags,
        "extend_height_mm": extend_h,
        "context": context,
    }


def _first_mm(t: str, patterns: list) -> Optional[float]:
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            v = float(m.group(1))
            return v * 1000 if v < 500 else v
    return None


def _endpoint_key(sp: dict, ep: dict, tol: float = 50) -> tuple:
    return (
        round(float(sp.get("X", 0)) / tol), round(float(sp.get("Y", 0)) / tol),
        round(float(sp.get("Z", 0)) / tol),
        round(float(ep.get("X", 0)) / tol), round(float(ep.get("Y", 0)) / tol),
        round(float(ep.get("Z", 0)) / tol),
    )


def filter_new_elements(existing: list, candidates: list) -> list:
    """Keep only elements whose endpoints are not already in the model."""
    existing_keys = set()
    for m in existing:
        sp = m.get("StartPoint") or m.get("startPoint") or {}
        ep = m.get("EndPoint") or m.get("endPoint") or {}
        if sp and ep:
            existing_keys.add(_endpoint_key(sp, ep))
            existing_keys.add(_endpoint_key(ep, sp))

    out = []
    for e in candidates:
        sp, ep = e.get("StartPoint", {}), e.get("EndPoint", {})
        k = _endpoint_key(sp, ep)
        if k not in existing_keys:
            out.append(e)
    return out


def _tower_feature_edit(existing: list, intent: dict) -> list:
    ctx = intent["context"]
    flags = intent["flags"]
    height = float(ctx.get("max_z", ctx.get("height", 30000)))
    base_w = float(ctx.get("base_width", 4000))
    top_w = float(ctx.get("top_width", base_w * 0.3))

    p = {
        "has_ladder": flags.get("ladder"),
        "has_platform": flags.get("platform"),
        "has_antenna": flags.get("antenna"),
        "platform_z": height * 0.82,
    }
    raw = []
    if p["has_platform"]:
        raw.extend(_tower_platform(p, height, base_w, top_w, "CHS139x5", "S355", "L60x60x5", "S275"))
    if p["has_ladder"]:
        raw.extend(_tower_ladder(p, height, base_w))
    if p["has_antenna"]:
        raw.extend(_tower_antenna(p, height, base_w))

    if not any([p["has_ladder"], p["has_platform"], p["has_antenna"]]):
        p["has_ladder"] = "ladder" in intent["command"].lower()
        p["has_platform"] = "platform" in intent["command"].lower()
        p["has_antenna"] = "antenna" in intent["command"].lower()
        if p["has_platform"]:
            raw.extend(_tower_platform(p, height, base_w, top_w, "CHS139x5", "S355", "L60x60x5", "S275"))
        if p["has_ladder"]:
            raw.extend(_tower_ladder(p, height, base_w))
        if p["has_antenna"]:
            raw.extend(_tower_antenna(p, height, base_w))

    return filter_new_elements(existing, raw)


def _building_edit(existing: list, intent: dict) -> list:
    grid, nodes = _resolve_grid(existing)
    existing_index = _build_endpoint_index(nodes) if nodes else set()
    roof_type = "gable" if "gable" in intent["command"].lower() else "flat"
    action = intent["action"]
    flags = intent["flags"]
    missing = []

    if action in ("complete", "auto_complete"):
        comp = complete_model(existing, {"roof_type": roof_type, "use_mirror": False})
        missing.extend(comp.get("missing") or [])
        topo = intent["context"].get("topology") or analyse_topology(existing)
        missing.extend(topo.get("missing_by_pattern") or [])

    if action == "add_bracing" or flags.get("bracing"):
        if grid.get("x_coords"):
            missing.extend(find_missing_bracing(grid, existing_index))

    if action == "add_purlins" or flags.get("purlins"):
        if grid.get("x_coords"):
            missing.extend(find_missing_roof(grid, existing_index, roof_type))

    if action == "add_structural" or flags.get("columns") or flags.get("beams") or flags.get("roof"):
        if grid.get("x_coords"):
            if flags.get("columns") or action == "complete":
                missing.extend(find_missing_columns(grid, existing_index))
            if flags.get("beams") or action == "complete":
                missing.extend(find_missing_beams(grid, existing_index))
            if flags.get("roof") or flags.get("purlins"):
                missing.extend(find_missing_roof(grid, existing_index, roof_type))

    return _dedupe_elements(missing)


def plan_edit(command: str, existing: list) -> dict:
    """Build an executable edit plan from NL command + current model."""
    ctx = infer_model_context(existing)
    intent = parse_edit_intent(command, ctx)

    checklist = []
    if ctx["family"] == "tower":
        st = ctx["structure_type"] if "tower" in ctx["structure_type"] else "tower_lattice"
        flags_map = {
            "has_ladder": intent["flags"].get("ladder"),
            "has_platform": intent["flags"].get("platform"),
            "has_antenna": intent["flags"].get("antenna"),
        }
        checklist = build_component_checklist(st, flags_map)

    return {
        "intent": intent["action"],
        "command": command,
        "model_context": ctx,
        "flags": intent["flags"],
        "structure_type": ctx["structure_type"],
        "structure_label": ctx["structure_type"].replace("_", " ").title(),
        "editable": ctx["member_count"] > 0,
        "component_checklist": checklist,
        "suggested_tab": "build_agent" if intent["action"] != "create" else "create",
    }


def execute_edit(command: str, existing: list, options: dict = None) -> dict:
    """
    Universal edit executor — returns same shape as run_agent_command.
    """
    opts = options or {}
    if not existing:
        return {
            "status": "error",
            "action": "none",
            "message": "No model loaded. Export from Tekla or create a structure first.",
        }

    ctx = infer_model_context(existing)
    intent = parse_edit_intent(command, ctx)
    action = intent["action"]

    if action == "cleanup":
        from agent_engine import cleanup_model  # lazy — avoid import cycle at load
        cleaned = cleanup_model(existing)
        return {
            "status": "cleanup",
            "action": "cleanup",
            "removed": cleaned["removed"],
            "kept": cleaned["kept"],
            "members": cleaned["members"],
            "breakdown": cleaned["breakdown"],
            "message": f"Removed {cleaned['removed']} bad members, kept {cleaned['kept']}.",
            "model_context": ctx,
        }

    if action == "create" or is_full_structure_create(command):
        return {"status": "delegate_create", "command": command, "model_context": ctx}

    raw_elements = []

    if action == "add_tower_feature" or (
        ctx["family"] == "tower"
        and any(intent["flags"].get(k) for k in ("ladder", "platform", "antenna"))
        and not is_full_structure_create(command)
    ):
        raw_elements = _tower_feature_edit(existing, intent)
        label = "tower_features"

    elif ctx["family"] == "tower" and action in ("complete", "auto_complete"):
        plan = plan_structure(
            f"Create tower complete with ladder platform antenna height {int(ctx['max_z'])}mm"
        )
        full = generate_grid(plan["params"])
        raw_elements = filter_new_elements(existing, full)
        label = "tower_complete"

    elif action == "extend" and intent.get("extend_height_mm"):
        target = intent["extend_height_mm"]
        current = ctx["max_z"]
        if target > current:
            delta_cmd = (
                f"Create {ctx['structure_type']} height {int(target)}m "
                f"base {int(ctx['base_width']/1000)}m with bracing"
            )
            plan = plan_structure(delta_cmd)
            plan["params"]["height"] = target
            full = generate_grid(plan["params"])
            raw_elements = [
                e for e in full
                if float(e["StartPoint"].get("Z", 0)) >= current - 500
                or float(e["EndPoint"].get("Z", 0)) >= current - 500
            ]
            raw_elements = filter_new_elements(existing, raw_elements)
        label = "extend_height"
    else:
        raw_elements = _building_edit(existing, intent)
        label = action if action != "auto_complete" else "complete"

    if not raw_elements:
        return {
            "status": "ok",
            "action": label,
            "added": 0,
            "message": (
                f"No new members needed for '{command}' on {ctx['structure_type']} "
                f"({ctx['member_count']} members, {ctx['completeness_pct']:.0f}% complete)."
            ),
            "elements": [],
            "model_context": ctx,
            "plan": plan_edit(command, existing),
        }

    result = _insert_elements(existing, raw_elements, label)
    result["model_context"] = ctx
    result["plan"] = plan_edit(command, existing)
    result["edit_intent"] = action
    return result
