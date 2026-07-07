# agent_engine.py — Build Agent: additive edits on EXISTING model (no offset placement)

from typing import List, Optional

from app.knowledge_graph.graph_builder import build_graph
from app.ai.completion_engine import (
    complete_model,
    detect_grid_pattern,
    find_missing_bracing,
    find_missing_beams,
    find_missing_columns,
    find_missing_roof,
    mirror_missing_region,
    _build_endpoint_index,
)
from app.ai.topology_engine import detect_bays_xy, detect_levels, analyse_topology, infer_missing_from_pattern
from app.ai.coordinate_solver import solve_coordinates, GridConstraints
from app.extractor.structure_detector import detect_structure, is_create_command, is_full_structure_create, placement_for_type
from app.planner.structure_planner import plan_structure
from app.extractor.structure_editor import execute_edit, plan_edit
from app.services.grid_engine import generate_grid
from app.services.placement_engine import detect_active_grid, apply_offset, validate_structure_geometry, place_structure


# ── Safe limits (prevent 500+ member spam on messy models) ───────────────────
MAX_AGENT_INSERT = 150
MAX_PATTERN_FILL = 80


def _grid_from_members(members: list) -> dict:
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
        "x_coords": xs,
        "y_coords": ys,
        "z_levels": z_levels,
        "spacing_x": modal_gap(xs),
        "spacing_y": modal_gap(ys),
        "story_height": modal_gap([z for z in z_levels if z > 0]) if z_levels else 3500,
        "n_cols_x": len(xs),
        "n_cols_y": len(ys),
    }


def _resolve_grid(members: list) -> tuple:
    graph = build_graph(members)
    grid = detect_grid_pattern(graph["nodes"]) if graph["nodes"] else {}
    if grid.get("x_coords") and grid.get("y_coords"):
        return grid, graph["nodes"]
    return _grid_from_members(members), graph["nodes"]


def _model_anchor(members: list, mode: str = "corner") -> dict:
    """World-space anchor for new geometry on existing Tekla grid."""
    ctx = detect_active_grid(members)
    if not ctx.get("has_model"):
        return {"x": 0.0, "y": 0.0, "z": 0.0}
    ox, oy = ctx["origin"]["x"], ctx["origin"]["y"]
    ex, ey = ctx["extent"]["x"], ctx["extent"]["y"]
    if mode == "center":
        return {"x": (ox + ex) / 2, "y": (oy + ey) / 2, "z": 0.0}
    return {"x": ox, "y": oy, "z": max(0.0, ctx["origin"].get("z", 0))}


def _insert_elements(existing: list, raw_elements: list, label: str) -> dict:
    if not raw_elements:
        return {
            "status": "ok",
            "action": label,
            "added": 0,
            "message": f"No additional {label} members needed — model already has them.",
            "elements": [],
        }

    if len(raw_elements) > MAX_AGENT_INSERT:
        raw_elements = raw_elements[:MAX_AGENT_INSERT]

    gc = GridConstraints.from_existing_model(existing, padding=5000)
    # Do NOT snap to nearest node — that caused random cross-model connections
    solved = solve_coordinates(raw_elements, existing, gc, apply_node_snap=False)
    elements = solved["elements"]

    return {
        "status": "ok",
        "action": label,
        "added": len(elements),
        "elements": elements,
        "coordinate_solver_stats": solved["stats"],
        "rejected": len(solved.get("rejected", [])),
    }


def _generate_on_site(command: str, existing: list, placement: str = None) -> dict:
    """
    Generate a full structure anchored inside the Tekla grid.
    Uses Structure Planner to classify, extract params, and route generators.
    """
    plan = plan_structure(command)
    params = plan["params"]
    stype = params.get("structure_type", "building")
    if placement is None:
        placement = infer_agent_placement(command, bool(existing))
        if placement not in ("append", "center") and is_create_command(command):
            placement = plan.get("placement") or placement_for_type(stype)

    raw = generate_grid(params)
    validated, _ = validate_structure_geometry(raw, params)

    pl = place_structure(
        validated, params, existing,
        strategy=placement, rotation_deg=0.0,
        skip_existing_dedup=True,
    )
    elements = pl["elements"]

    gc = GridConstraints.from_existing_model(existing, padding=8000)
    solved = solve_coordinates(
        elements, existing, gc,
        apply_node_snap=False,
        check_existing_duplicates=False,
    )

    return {
        "status": "ok",
        "action": params.get("structure_type", "generate"),
        "added": len(solved["elements"]),
        "elements": solved["elements"],
        "structure_type": params.get("structure_type"),
        "structure_label": plan.get("structure_label"),
        "generator": plan.get("generator"),
        "placement_origin": pl.get("placement_origin"),
        "placement_strategy": placement,
        "params": params,
        "plan": plan,
        "coordinate_solver_stats": solved["stats"],
        "grid_alignment": pl.get("validation"),
    }


def infer_agent_placement(command: str, has_existing: bool) -> str:
    cmd = command.strip().lower()
    if not has_existing:
        return "origin"
    # ONLY append when user explicitly wants a separate structure
    if any(k in cmd for k in ["beside", "adjacent", "next to", "separate structure", "append x", "to the right of"]):
        return "append"
    return "inline"


def run_agent_command(command: str, existing: list, options: dict = None) -> dict:
    opts = options or {}
    cmd = command.strip().lower()
    send_tekla = opts.get("send_to_tekla", True)

    if not existing:
        return {
            "status": "error",
            "action": "none",
            "message": "No model in output.json. Use Create Structure tab first, or export from Tekla.",
        }

    # Full structure create must run the complete generator — not accessory-only edits
    if is_create_command(command) or is_full_structure_create(command):
        plan = plan_structure(command)
        placement = infer_agent_placement(command, True)
        if placement not in ("append",):
            placement = plan.get("placement") or placement_for_type(plan["structure_type"])
        result = _generate_on_site(command, existing, placement)
        result["send_to_tekla"] = send_tekla
        result["params"] = plan["params"]
        result["plan"] = plan
        return result

    # ── Universal structure editor (all BIM types) ─────────────────────────
    edit_result = execute_edit(command, existing, opts)
    if edit_result.get("status") == "delegate_create":
        pass  # fall through to create handler below
    elif edit_result.get("status") in ("ok", "cleanup"):
        edit_result["send_to_tekla"] = send_tekla and edit_result.get("status") != "cleanup"
        return edit_result
    elif edit_result.get("status") == "error":
        return edit_result

    grid, nodes = _resolve_grid(existing)
    existing_index = _build_endpoint_index(nodes) if nodes else set()
    roof_type = "gable" if "gable" in cmd else "flat"

    # ── Cleanup (small edits only — not embedded in full create specs) ────
    if (
        not is_full_structure_create(command)
        and any(k in cmd for k in ["cleanup", "clean up", "remove duplicate", "remove out of grid", "fix model"])
    ):
        cleaned = cleanup_model(existing)
        return {
            "status": "cleanup",
            "action": "cleanup",
            "removed": cleaned["removed"],
            "kept": cleaned["kept"],
            "members": cleaned["members"],
            "breakdown": cleaned["breakdown"],
            "message": f"Removed {cleaned['removed']} bad members, kept {cleaned['kept']}.",
            "send_to_tekla": False,
        }

    # ── Complete / fill gaps ─────────────────────────────────────────────
    if any(k in cmd for k in ["complete", "missing", "fill gap", "finish model", "analyse and complete"]):
        topo = analyse_topology(existing)
        comp = complete_model(existing, {
            "roof_type": roof_type,
            "use_mirror": opts.get("use_mirror", False),
            "mirror_axis": "Y" if " y" in cmd else "X",
        })
        pattern = topo.get("missing_by_pattern", [])
        merged = _dedupe_elements(pattern + (comp.get("missing") or []))[:MAX_AGENT_INSERT]
        result = _insert_elements(existing, merged, "complete")
        result["topology"] = {
            "completeness_before": topo.get("completeness_pct"),
            "breakdown": comp.get("breakdown"),
        }
        result["send_to_tekla"] = send_tekla
        return result

    # ── Bracing only ───────────────────────────────────────────────────
    if any(k in cmd for k in ["brac", "brace", "bracing", "cross brac", "x-brac", "x brac"]):
        missing = []
        if grid.get("x_coords") and grid.get("y_coords"):
            missing = find_missing_bracing(grid, existing_index)
        if not missing:
            topo = analyse_topology(existing)
            missing = [m for m in topo.get("missing_by_pattern", []) if m.get("Type") == "SECONDARY"]
        result = _insert_elements(existing, missing, "bracing")
        result["send_to_tekla"] = send_tekla
        return result

    # ── Mirror ─────────────────────────────────────────────────────────
    if "mirror" in cmd and "mirror if" not in cmd:
        axis = "Y" if (" y" in cmd or "along y" in cmd) else "X"
        mirrored = mirror_missing_region(nodes, [], axis)
        result = _insert_elements(existing, mirrored, f"mirror_{axis.lower()}")
        result["send_to_tekla"] = send_tekla
        return result

    # ── Targeted fills ─────────────────────────────────────────────────
    if any(k in cmd for k in ["purlin", "roof purlin"]):
        missing = find_missing_roof(grid, existing_index, roof_type) if grid.get("x_coords") else []
        result = _insert_elements(existing, missing, "purlins")
        result["send_to_tekla"] = send_tekla
        return result

    if any(k in cmd for k in ["beam", "column", "roof", "floor", "mezzanine"]):
        missing = []
        if grid.get("x_coords"):
            if "column" in cmd:
                missing.extend(find_missing_columns(grid, existing_index))
            if any(k in cmd for k in ["beam", "floor", "mezzanine"]):
                missing.extend(find_missing_beams(grid, existing_index))
            if "roof" in cmd:
                missing.extend(find_missing_roof(grid, existing_index, roof_type))
        result = _insert_elements(existing, missing, "structural_fill")
        result["send_to_tekla"] = send_tekla
        return result

    if any(k in cmd for k in ["extend", "add bay", "more bay", "extra bay"]):
        result = _generate_on_site(command, existing, "inline")
        result["send_to_tekla"] = send_tekla
        return result

    # ── Pattern fill (capped) ──────────────────────────────────────────
    topo = analyse_topology(existing)
    if topo.get("missing_count", 0) > 0 and topo.get("completeness_pct", 100) < 85:
        missing = infer_missing_from_pattern(existing, {
            "levels": topo.get("levels", []),
            "x_coords": topo.get("x_coords", []),
            "y_coords": topo.get("y_coords", []),
            "panel_status": topo.get("panel_status", []),
            "bracing": topo.get("bracing", {}),
            "symmetry": topo.get("symmetry", {}),
        })[:MAX_PATTERN_FILL]
        if missing:
            result = _insert_elements(existing, missing, "pattern_fill")
            result["send_to_tekla"] = send_tekla
            return result

    # Last resort — generate on site, never append far away
    result = _generate_on_site(command, existing, infer_agent_placement(command, True))
    result["send_to_tekla"] = send_tekla
    return result


def _member_points(m: dict) -> tuple:
    s = m.get("StartPoint") or m.get("startPoint") or {}
    e = m.get("EndPoint") or m.get("endPoint") or {}
    return (
        float(s.get("X", s.get("x", 0))), float(s.get("Y", s.get("y", 0))), float(s.get("Z", s.get("z", 0))),
        float(e.get("X", e.get("x", 0))), float(e.get("Y", e.get("y", 0))), float(e.get("Z", e.get("z", 0))),
    )


def cleanup_model(members: list, grid_padding: float = 12000) -> dict:
    """Remove zero-length, duplicate, and far off-grid members from a corrupted model."""
    if not members:
        return {"members": [], "removed": 0, "kept": 0, "breakdown": {}}

    # Ignore far-away junk when detecting the real Tekla grid footprint
    core = []
    for m in members:
        sx, sy, _, ex, ey, _ = _member_points(m)
        if max(abs(sx), abs(sy), abs(ex), abs(ey)) < 150000:
            core.append(m)
    ref = core if core else members

    xs, ys = detect_bays_xy(ref)
    z_levels = detect_levels(ref)
    if xs and ys:
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        ctx = detect_active_grid(ref)
        min_x = ctx["origin"]["x"]
        min_y = ctx["origin"]["y"]
        max_x = ctx["extent"]["x"]
        max_y = ctx["extent"]["y"]

    max_z = (max(z_levels) if z_levels else 0) + 80000
    pad = grid_padding

    def in_footprint(x, y, z):
        return (min_x - pad <= x <= max_x + pad and
                min_y - pad <= y <= max_y + pad and
                -500 <= z <= max_z)

    seen, kept, breakdown = set(), [], {"zero_length": 0, "duplicate": 0, "off_grid": 0}
    for m in members:
        sx, sy, sz, ex, ey, ez = _member_points(m)
        length = ((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2) ** 0.5
        if length < 50:
            breakdown["zero_length"] += 1
            continue

        key = (
            round(sx / 50), round(sy / 50), round(sz / 50),
            round(ex / 50), round(ey / 50), round(ez / 50),
            m.get("Profile", m.get("profile", "")),
        )
        if key in seen:
            breakdown["duplicate"] += 1
            continue

        if not (in_footprint(sx, sy, sz) or in_footprint(ex, ey, ez)):
            breakdown["off_grid"] += 1
            continue

        seen.add(key)
        kept.append(m)

    return {
        "members": kept,
        "kept": len(kept),
        "removed": len(members) - len(kept),
        "breakdown": breakdown,
    }


def _dedupe_elements(elements: list) -> list:
    seen, out = set(), []
    for e in elements:
        s, ep = e.get("StartPoint", {}), e.get("EndPoint", {})
        k = (
            round(s.get("X", 0) / 50), round(s.get("Y", 0) / 50), round(s.get("Z", 0) / 50),
            round(ep.get("X", 0) / 50), round(ep.get("Y", 0) / 50), round(ep.get("Z", 0) / 50),
        )
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


# Presets for UI — all supported BIM structure types
AGENT_COMMANDS = {
    "edit": [
        {"cmd": "Add ladder to tower", "desc": "Install internal ladder + rungs on existing tower"},
        {"cmd": "Add platform and handrails to tower", "desc": "Equipment platform ring on tower"},
        {"cmd": "Add antenna mast to tower", "desc": "Top mast + mounting frame"},
        {"cmd": "Complete missing members", "desc": "Auto-fill gaps (building or tower)"},
        {"cmd": "Add cross bracing to all faces", "desc": "Missing X-braces on perimeter"},
        {"cmd": "Add roof purlins at 1.5m spacing", "desc": "Roof-level beams on detected grid"},
        {"cmd": "Extend height to 50m", "desc": "Add panels above current top (towers/buildings)"},
        {"cmd": "Clean up model remove duplicates and out of grid members", "desc": "Fix corrupted model"},
    ],
    "additive": [
        {"cmd": "Add cross bracing to all faces", "desc": "Missing X-braces on perimeter bays"},
        {"cmd": "Complete missing members", "desc": "Fill columns, beams, bracing, roof gaps"},
        {"cmd": "Add roof purlins at 1.5m spacing", "desc": "Roof-level beams on detected grid"},
        {"cmd": "Mirror the structure along X axis", "desc": "Mirror incomplete half"},
        {"cmd": "Clean up model remove duplicates and out of grid members", "desc": "Fix corrupted model"},
    ],
    "buildings": [
        {"cmd": "Create 4x3 bay building 2 storeys 6m spacing 3.5m height with bracing", "desc": "Multi-storey office/industrial frame"},
        {"cmd": "Create 5x4 bay building 3 storeys 6m spacing with bracing", "desc": "Large rectangular building"},
        {"cmd": "Create L-shaped building 5x4 bays 2 storeys 6m spacing with bracing", "desc": "L-shape footprint"},
        {"cmd": "Create 6x2 bay building 4 storeys 7m spacing 3m height flat roof", "desc": "High-rise style frame"},
    ],
    "industrial": [
        {"cmd": "Create warehouse 6 bays 18m span 7m height with crane beam", "desc": "Wide-span portal warehouse"},
        {"cmd": "Create portal frame shed 12m span 5 bays with haunch", "desc": "Clear-span shed at grid corner"},
        {"cmd": "Create 5-bay pipe rack 3 levels 6m spacing", "desc": "Multi-level pipe support rack"},
        {"cmd": "Create industrial frame 4 bays 12m span 3 levels", "desc": "Process equipment support"},
    ],
    "towers": [
        {"cmd": "Create lattice tower 40m height base 4m top 1.2m panel 3m 5 bays with ladder platform and antenna", "desc": "4-leg telecom tower — zig-zag bracing, platform, antennas (reference)"},
        {"cmd": "Create lattice tower 30m base 4m top 1.2m panel height", "desc": "Square tapered lattice tower"},
        {"cmd": "Create telecom tower 45m height 4 platforms", "desc": "3-leg self-supporting tower"},
        {"cmd": "Create hexagonal tower 24m height 5 levels", "desc": "6-leg tapered tower"},
        {"cmd": "Create transmission pylon 45m high", "desc": "HV transmission tower with cross-arms"},
        {"cmd": "Create guyed mast 60m height", "desc": "Guyed radio mast with cable anchors"},
    ],
}
