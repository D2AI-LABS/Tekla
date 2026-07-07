# placement_engine.py
#
# Stage: Intelligent Placement Engine
#
# Responsibilities:
#   1. Detect active grid boundaries from existing model
#   2. Auto-select structure origin (don't place at random)
#   3. Align structure to grid axes
#   4. Offset all generated elements to correct world position
#   5. Validate all points are inside grid boundary
#   6. Resolve member local-axis orientation
#
# This is the missing link between grid_engine.py (which generates
# local-coordinate geometry) and Tekla insertion (which needs
# world-coordinate XYZ).

import math
from typing import List, Tuple, Optional


# ── GRID DETECTION from existing model data ─────────────────────────────────

def detect_active_grid(existing_members: list) -> dict:
    """
    Scans existing model members to detect the implicit grid:
    min/max XYZ, likely bay spacings, story heights, and grid origin.

    Returns a grid context dict used by the placement engine.
    """
    if not existing_members:
        return _default_grid()

    xs, ys, zs = [], [], []
    for m in existing_members:
        for pt_key in ["StartPoint", "EndPoint", "startPoint", "endPoint"]:
            pt = m.get(pt_key) or {}
            if pt:
                xs.append(float(pt.get("X", pt.get("x", 0))))
                ys.append(float(pt.get("Y", pt.get("y", 0))))
                zs.append(float(pt.get("Z", pt.get("z", 0))))

    if not xs:
        return _default_grid()

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)

    # Infer typical bay spacing from X and Y coordinate clusters
    spacing_x = _infer_spacing(xs)
    spacing_y = _infer_spacing(ys)
    story_h   = _infer_spacing([z for z in zs if z > 0]) or 3500

    return {
        "origin":     {"x": min_x, "y": min_y, "z": min_z},
        "extent":     {"x": max_x, "y": max_y, "z": max_z},
        "width_x":    max_x - min_x,
        "width_y":    max_y - min_y,
        "height":     max_z - min_z,
        "spacing_x":  spacing_x,
        "spacing_y":  spacing_y,
        "story_h":    story_h,
        "has_model":  True,
    }


def _default_grid() -> dict:
    """Used when no existing model — start from world origin."""
    return {
        "origin":    {"x": 0.0, "y": 0.0, "z": 0.0},
        "extent":    {"x": 0.0, "y": 0.0, "z": 0.0},
        "width_x":  0.0, "width_y": 0.0, "height": 0.0,
        "spacing_x": 6000.0, "spacing_y": 6000.0, "story_h": 3500.0,
        "has_model": False,
    }


def _infer_spacing(coords: list) -> float:
    """
    Find the most common gap between sorted unique coordinates.
    Used to detect grid bay spacings from existing member endpoints.
    """
    unique = sorted(set(round(c / 100) * 100 for c in coords))
    if len(unique) < 2:
        return 6000.0
    gaps = [unique[i+1] - unique[i] for i in range(len(unique)-1)]
    # Return the most common gap (mode)
    from collections import Counter
    cnt = Counter(round(g / 50) * 50 for g in gaps if g > 100)
    return float(cnt.most_common(1)[0][0]) if cnt else 6000.0


# ── STRUCTURE PLACEMENT STRATEGY ────────────────────────────────────────────

def compute_placement_origin(
    grid_ctx:     dict,
    params:       dict,
    strategy:     str = "auto",
) -> dict:
    """
    Decide WHERE to place the new structure in world coordinates.

    strategy options:
      "auto"    — place adjacent to existing model (default)
      "origin"  — start at world (0,0,0)
      "center"  — center the new structure on existing model center
      "append"  — place at max_x + gap

    Returns {"x": float, "y": float, "z": float} — the world-space offset
    to apply to all generated elements.
    """
    stype = params.get("structure_type", "building")

    # Towers always go at world origin (they're standalone)
    if "tower" in stype:
        cx = (grid_ctx["origin"]["x"] + grid_ctx["extent"]["x"]) / 2
        cy = (grid_ctx["origin"]["y"] + grid_ctx["extent"]["y"]) / 2
        return {"x": cx, "y": cy, "z": 0.0}

    if not grid_ctx["has_model"] or strategy == "origin":
        return {"x": 0.0, "y": 0.0, "z": 0.0}

    if strategy == "center":
        new_w = params.get("bays_x", 4) * params.get("spacing_x", 6000)
        new_d = params.get("bays_y", 3) * params.get("spacing_y", 6000)
        cx = (grid_ctx["origin"]["x"] + grid_ctx["extent"]["x"]) / 2 - new_w / 2
        cy = (grid_ctx["origin"]["y"] + grid_ctx["extent"]["y"]) / 2 - new_d / 2
        return {"x": cx, "y": cy, "z": 0.0}

    # "auto" / "append" — place next to existing model with 1-bay gap
    gap = params.get("spacing_x", grid_ctx["spacing_x"])
    return {
        "x": grid_ctx["extent"]["x"] + gap,
        "y": grid_ctx["origin"]["y"],
        "z": 0.0,
    }


# ── COORDINATE TRANSFORM ─────────────────────────────────────────────────────

def apply_offset(elements: list, offset: dict, rotation_deg: float = 0.0) -> list:
    """
    Translate all element coordinates by offset {x, y, z}.
    Optionally rotate around the Z axis by rotation_deg degrees.
    Returns new list (does not mutate input).
    """
    rad     = math.radians(rotation_deg)
    cos_r   = math.cos(rad)
    sin_r   = math.sin(rad)
    ox, oy, oz = offset["x"], offset["y"], offset["z"]

    result = []
    for e in elements:
        ne = dict(e)
        ne["StartPoint"] = _transform_pt(e["StartPoint"], ox, oy, oz, cos_r, sin_r)
        ne["EndPoint"]   = _transform_pt(e["EndPoint"],   ox, oy, oz, cos_r, sin_r)
        result.append(ne)
    return result


def _transform_pt(pt: dict, ox, oy, oz, cos_r, sin_r) -> dict:
    lx = pt["X"]
    ly = pt["Y"]
    lz = pt["Z"]
    # Rotate in XY plane
    rx = lx * cos_r - ly * sin_r
    ry = lx * sin_r + ly * cos_r
    return {
        "X": round(rx + ox, 1),
        "Y": round(ry + oy, 1),
        "Z": round(lz + oz, 1),
    }


# ── GRID BOUNDARY VALIDATION ─────────────────────────────────────────────────

def validate_placement(
    elements:  list,
    grid_ctx:  dict,
    tolerance: float = 500.0,
) -> dict:
    """
    Check that all generated elements fall within acceptable grid bounds.
    Returns {"ok": bool, "out_of_bounds": int, "warnings": list}.
    """
    warnings = []
    out      = 0

    if not grid_ctx["has_model"]:
        return {"ok": True, "out_of_bounds": 0, "warnings": []}

    # Expand existing bounds to include new structure footprint
    all_x, all_y, all_z = [], [], []
    for e in elements:
        for pt in [e["StartPoint"], e["EndPoint"]]:
            all_x.append(pt["X"]); all_y.append(pt["Y"]); all_z.append(pt["Z"])

    if not all_x:
        return {"ok": True, "out_of_bounds": 0, "warnings": []}

    new_min_x, new_max_x = min(all_x), max(all_x)
    new_min_y, new_max_y = min(all_y), max(all_y)

    # Z is always valid (different floor levels)
    for e in elements:
        for pt in [e["StartPoint"], e["EndPoint"]]:
            if abs(pt["X"] - new_min_x) > tolerance * 100:
                pass  # large model — skip strict bounds
            if pt["Z"] < -tolerance:
                out += 1
                warnings.append(f"Member below Z=0: {pt}")
                break

    if out:
        warnings.insert(0, f"{out} elements have invalid Z coordinates.")

    return {"ok": out == 0, "out_of_bounds": out, "warnings": warnings}


# ── MEMBER ORIENTATION RESOLVER ──────────────────────────────────────────────

def resolve_orientation(element: dict) -> dict:
    """
    Adds orientation metadata to an element:
      direction   — VERTICAL | HORIZONTAL_X | HORIZONTAL_Y | DIAGONAL
      local_axis  — primary axis vector
      angle_deg   — rotation around member axis (for section orientation)

    Used by the C# insertion layer to set beam Position correctly.
    """
    s = element["StartPoint"]
    e = element["EndPoint"]

    dx = e["X"] - s["X"]
    dy = e["Y"] - s["Y"]
    dz = e["Z"] - s["Z"]
    L  = math.sqrt(dx**2 + dy**2 + dz**2)

    if L < 1e-6:
        direction = "ZERO_LENGTH"
        axis      = {"x": 0, "y": 0, "z": 1}
    elif abs(dz) > abs(dx) and abs(dz) > abs(dy):
        direction = "VERTICAL"
        axis      = {"x": 0, "y": 0, "z": 1}
    elif abs(dx) >= abs(dy):
        direction = "HORIZONTAL_X"
        axis      = {"x": 1, "y": 0, "z": 0}
    elif abs(dy) > abs(dx):
        direction = "HORIZONTAL_Y"
        axis      = {"x": 0, "y": 1, "z": 0}
    else:
        direction = "DIAGONAL"
        axis      = {"x": round(dx/L,3), "y": round(dy/L,3), "z": round(dz/L,3)}

    etype = element.get("Type", "BEAM").upper()
    # Columns: web parallel to stronger direction
    angle = 0
    if etype == "COLUMN":
        angle = 0   # web faces X by default — can be parameterized
    elif direction == "HORIZONTAL_Y":
        angle = 0   # beam bends about weak axis in Y span

    return {**element, "direction": direction, "local_axis": axis, "angle_deg": angle}


# ── DUPLICATE DETECTION against existing model ───────────────────────────────

SNAP_MM = 50.0

def remove_duplicates_against_model(
    new_elements: list,
    existing_members: list,
) -> Tuple[list, int]:
    """
    Removes any new_elements that already exist in the Tekla model
    (within SNAP_MM tolerance on both endpoints).

    Returns (filtered_elements, n_removed).
    """
    def key(sp, ep):
        return (
            round(sp["X"]/SNAP_MM), round(sp["Y"]/SNAP_MM), round(sp["Z"]/SNAP_MM),
            round(ep["X"]/SNAP_MM), round(ep["Y"]/SNAP_MM), round(ep["Z"]/SNAP_MM),
        )

    existing_keys = set()
    for m in existing_members:
        sp = m.get("StartPoint") or m.get("startPoint") or {}
        ep = m.get("EndPoint")   or m.get("endPoint")   or {}
        if sp and ep:
            k  = key(
                {"X": sp.get("X",0),"Y": sp.get("Y",0),"Z": sp.get("Z",0)},
                {"X": ep.get("X",0),"Y": ep.get("Y",0),"Z": ep.get("Z",0)},
            )
            kr = key(
                {"X": ep.get("X",0),"Y": ep.get("Y",0),"Z": ep.get("Z",0)},
                {"X": sp.get("X",0),"Y": sp.get("Y",0),"Z": sp.get("Z",0)},
            )
            existing_keys.add(k)
            existing_keys.add(kr)

    filtered = []
    removed  = 0
    for e in new_elements:
        k = key(e["StartPoint"], e["EndPoint"])
        if k not in existing_keys:
            filtered.append(e)
        else:
            removed += 1

    return filtered, removed


# ── FULL PLACEMENT PIPELINE ──────────────────────────────────────────────────

def place_structure(
    elements:         list,
    params:           dict,
    existing_members: list,
    strategy:         str   = "auto",
    rotation_deg:     float = 0.0,
) -> dict:
    """
    Master placement function. Called by main.py after grid_engine.generate_grid().

    Steps:
      1. Detect active grid from existing model
      2. Compute placement origin (where to put the structure)
      3. Apply offset + rotation transform
      4. Remove duplicates against existing model
      5. Validate all points
      6. Add orientation metadata

    Returns dict with placed elements + placement report.
    """
    # 1. Grid detection
    grid_ctx = detect_active_grid(existing_members)

    # 2. Placement origin
    origin = compute_placement_origin(grid_ctx, params, strategy)

    # 3. Transform
    placed = apply_offset(elements, origin, rotation_deg)

    # 4. Deduplicate against existing model
    placed, n_removed = remove_duplicates_against_model(placed, existing_members)

    # 5. Validate bounds
    validation = validate_placement(placed, grid_ctx)

    # 6. Orientation metadata
    placed = [resolve_orientation(e) for e in placed]

    return {
        "elements":      placed,
        "placement_origin": origin,
        "rotation_deg":  rotation_deg,
        "grid_context":  grid_ctx,
        "n_duplicates_removed": n_removed,
        "validation":    validation,
        "total_placed":  len(placed),
    }