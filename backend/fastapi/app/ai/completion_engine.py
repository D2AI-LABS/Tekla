# completion_engine.py  (v2 — Universal BIM)
#
# Workflow 2: Universal BIM Completion
#
# Analyzes an existing Tekla model → finds structural gaps →
# generates ONLY the missing members (not the whole structure again).
#
# Pipeline:
#   existing_model
#     → build_graph()           (graph_builder.py)
#     → detect_grid_pattern()   (infer bay spacing)
#     → detect_story_pattern()  (infer floor heights)
#     → find_missing_columns()  (grid intersections without columns)
#     → find_missing_beams()    (connected column pairs without beams)
#     → find_missing_bracing()  (perimeter bays without X-bracing)
#     → find_missing_roof()     (top level beams without roof members)
#     → assemble_missing()      (deduplicate, format as BimElement list)

import math
from collections import defaultdict, Counter
from app.knowledge_graph.graph_builder import build_graph
from app.models.role_classifier import classify_role

SNAP_MM = 100.0   # two points are "the same" if closer than this


# ── GEOMETRY UTILITIES ───────────────────────────────────────────────────────

def _dist(a: dict, b: dict) -> float:
    return math.sqrt(
        (a["x"]-b["x"])**2 + (a["y"]-b["y"])**2 + (a["z"]-b["z"])**2
    )


def _pt_key(pt: dict, snap: float = SNAP_MM) -> tuple:
    """Round point to snap grid so nearby points compare equal."""
    return (
        round(pt["x"] / snap),
        round(pt["y"] / snap),
        round(pt["z"] / snap),
    )


def _length(node: dict) -> float:
    s, e = node["start"], node["end"]
    return math.sqrt((s["x"]-e["x"])**2+(s["y"]-e["y"])**2+(s["z"]-e["z"])**2)


# ── GRID PATTERN DETECTOR ────────────────────────────────────────────────────

def detect_grid_pattern(nodes: list) -> dict:
    """
    Infers the XY grid from column base points.
    Returns {x_coords, y_coords, spacing_x, spacing_y, z_levels, story_height}.
    """
    col_bases_x, col_bases_y, z_vals = [], [], []

    for n in nodes:
        if n["role"] == "COLUMN":
            base_z = min(n["start"]["z"], n["end"]["z"])
            top_z  = max(n["start"]["z"], n["end"]["z"])
            col_bases_x.append(n["start"]["x"])
            col_bases_y.append(n["start"]["y"])
            z_vals.append(base_z)
            z_vals.append(top_z)

    def cluster(vals, snap=SNAP_MM*2):
        """Group close values and return cluster centres."""
        sorted_v = sorted(set(round(v/snap)*snap for v in vals))
        return sorted_v

    x_coords  = cluster(col_bases_x)
    y_coords  = cluster(col_bases_y)
    z_levels  = cluster(z_vals)

    def modal_gap(coords):
        if len(coords) < 2:
            return 6000.0
        gaps = [coords[i+1]-coords[i] for i in range(len(coords)-1) if coords[i+1]-coords[i] > 100]
        if not gaps:
            return 6000.0
        cnt = Counter(round(g/100)*100 for g in gaps)
        return float(cnt.most_common(1)[0][0])

    spacing_x  = modal_gap(x_coords)
    spacing_y  = modal_gap(y_coords)
    story_h    = modal_gap([z for z in z_levels if z > 0])

    return {
        "x_coords":    x_coords,
        "y_coords":    y_coords,
        "z_levels":    z_levels,
        "spacing_x":   spacing_x,
        "spacing_y":   spacing_y,
        "story_height":story_h,
        "n_cols_x":    len(x_coords),
        "n_cols_y":    len(y_coords),
    }


# ── EXISTING MEMBER INDEX ────────────────────────────────────────────────────

def _build_endpoint_index(nodes: list) -> set:
    """Set of (start_key, end_key) for quick duplicate detection."""
    idx = set()
    for n in nodes:
        sk = _pt_key(n["start"])
        ek = _pt_key(n["end"])
        idx.add((sk, ek))
        idx.add((ek, sk))
    return idx


def _member_exists(start: dict, end: dict, index: set) -> bool:
    sk = _pt_key(start)
    ek = _pt_key(end)
    return (sk, ek) in index


# ── PROFILE SELECTOR (simple version used here) ─────────────────────────────

def _col_profile(height_mm: float) -> tuple:
    if height_mm > 7000: return ("HEA300", "S355")
    if height_mm > 4500: return ("HEA240", "S355")
    return ("HEA200", "S275")


def _beam_profile(span_mm: float) -> tuple:
    if span_mm > 12000: return ("IPE500", "S355")
    if span_mm >  9000: return ("IPE400", "S355")
    if span_mm >  6000: return ("IPE360", "S355")
    if span_mm >  4000: return ("IPE300", "S275")
    return ("IPE240", "S275")


def _brace_profile(span_mm: float) -> tuple:
    if span_mm > 8000: return ("CHS88x4",  "S275")
    return              ("L60x60x5", "S235")


# ── MAKE ELEMENT HELPER ──────────────────────────────────────────────────────

def _el(etype, profile, material, sx, sy, sz, ex, ey, ez) -> dict:
    return {
        "Type":       etype,
        "Profile":    profile,
        "Material":   material,
        "StartPoint": {"X": round(sx,1), "Y": round(sy,1), "Z": round(sz,1)},
        "EndPoint":   {"X": round(ex,1), "Y": round(ey,1), "Z": round(ez,1)},
    }


# ── MISSING COLUMN FINDER ────────────────────────────────────────────────────

def find_missing_columns(grid: dict, existing_index: set) -> list:
    """
    Every (x, y) grid intersection should have a column from z=0 to top z.
    Returns list of missing column elements.
    """
    missing  = []
    z_base   = min(grid["z_levels"]) if grid["z_levels"] else 0.0
    z_top    = max(grid["z_levels"]) if grid["z_levels"] else 3500.0

    for x in grid["x_coords"]:
        for y in grid["y_coords"]:
            s = {"x": x, "y": y, "z": z_base}
            e = {"x": x, "y": y, "z": z_top}
            if not _member_exists(s, e, existing_index):
                prof, mat = _col_profile(z_top - z_base)
                missing.append(_el("COLUMN", prof, mat, x, y, z_base, x, y, z_top))

    return missing


# ── MISSING BEAM FINDER ──────────────────────────────────────────────────────

def find_missing_beams(grid: dict, existing_index: set) -> list:
    """
    Between every adjacent pair of grid columns at each Z level, there
    should be a beam. Checks both X-direction and Y-direction spans.
    """
    missing = []
    xs      = grid["x_coords"]
    ys      = grid["y_coords"]
    zs      = [z for z in grid["z_levels"] if z > 0]  # skip base level

    for z in zs:
        # X-direction beams
        for xi in range(len(xs) - 1):
            for y in ys:
                s = {"x": xs[xi],   "y": y, "z": z}
                e = {"x": xs[xi+1], "y": y, "z": z}
                if not _member_exists(s, e, existing_index):
                    span = abs(xs[xi+1] - xs[xi])
                    prof, mat = _beam_profile(span)
                    missing.append(_el("BEAM", prof, mat,
                                       xs[xi], y, z, xs[xi+1], y, z))

        # Y-direction beams
        for yi in range(len(ys) - 1):
            for x in xs:
                s = {"x": x, "y": ys[yi],   "z": z}
                e = {"x": x, "y": ys[yi+1], "z": z}
                if not _member_exists(s, e, existing_index):
                    span = abs(ys[yi+1] - ys[yi])
                    prof, mat = _beam_profile(span)
                    missing.append(_el("BEAM", prof, mat,
                                       x, ys[yi], z, x, ys[yi+1], z))

    return missing


# ── MISSING BRACING FINDER ───────────────────────────────────────────────────

def find_missing_bracing(grid: dict, existing_index: set) -> list:
    """
    Perimeter bays (first and last in X/Y) should have X-bracing.
    Each braced bay needs 2 diagonals per storey.
    """
    missing = []
    xs      = grid["x_coords"]
    ys      = grid["y_coords"]
    z_levs  = sorted([z for z in grid["z_levels"] if z >= 0])

    # Perimeter faces in X direction (front and back face) — all bays
    for face_y in [ys[0], ys[-1]]:
        for xi in range(len(xs) - 1):
            for zi in range(len(z_levs) - 1):
                z0, z1 = z_levs[zi], z_levs[zi + 1]
                x0, x1 = xs[xi], xs[xi + 1]
                s1 = {"x": x0, "y": face_y, "z": z0}
                e1 = {"x": x1, "y": face_y, "z": z1}
                s2 = {"x": x1, "y": face_y, "z": z0}
                e2 = {"x": x0, "y": face_y, "z": z1}
                span = math.sqrt((x1 - x0) ** 2 + (z1 - z0) ** 2)
                prof, mat = _brace_profile(span)
                if not _member_exists(s1, e1, existing_index):
                    missing.append(_el("SECONDARY", prof, mat, x0, face_y, z0, x1, face_y, z1))
                if not _member_exists(s2, e2, existing_index):
                    missing.append(_el("SECONDARY", prof, mat, x1, face_y, z0, x0, face_y, z1))

    # Perimeter faces in Y direction — all bays
    for face_x in [xs[0], xs[-1]]:
        for yi in range(len(ys) - 1):
            for zi in range(len(z_levs) - 1):
                z0, z1 = z_levs[zi], z_levs[zi + 1]
                y0, y1 = ys[yi], ys[yi + 1]
                s1 = {"x": face_x, "y": y0, "z": z0}
                e1 = {"x": face_x, "y": y1, "z": z1}
                s2 = {"x": face_x, "y": y1, "z": z0}
                e2 = {"x": face_x, "y": y0, "z": z1}
                span = math.sqrt((y1 - y0) ** 2 + (z1 - z0) ** 2)
                prof, mat = _brace_profile(span)
                if not _member_exists(s1, e1, existing_index):
                    missing.append(_el("SECONDARY", prof, mat, face_x, y0, z0, face_x, y1, z1))
                if not _member_exists(s2, e2, existing_index):
                    missing.append(_el("SECONDARY", prof, mat, face_x, y1, z0, face_x, y0, z1))

    return missing


# ── MISSING ROOF MEMBER FINDER ───────────────────────────────────────────────

def find_missing_roof(grid: dict, existing_index: set, roof_type: str = "flat") -> list:
    """
    Checks for roof-level members at the top Z level.
    For flat roofs: same as floor beams at top Z.
    For gable: ridge beam + rafters.
    """
    if not grid["z_levels"]:
        return []

    missing = []
    z_roof  = max(grid["z_levels"])
    xs      = grid["x_coords"]
    ys      = grid["y_coords"]

    if roof_type == "flat":
        # Roof beams in X
        for xi in range(len(xs)-1):
            for y in ys:
                s = {"x": xs[xi], "y": y, "z": z_roof}
                e = {"x": xs[xi+1], "y": y, "z": z_roof}
                if not _member_exists(s, e, existing_index):
                    span = abs(xs[xi+1]-xs[xi])
                    prof, mat = _beam_profile(span)
                    missing.append(_el("BEAM", prof, mat, xs[xi], y, z_roof, xs[xi+1], y, z_roof))
        # Roof beams in Y
        for yi in range(len(ys)-1):
            for x in xs:
                s = {"x": x, "y": ys[yi], "z": z_roof}
                e = {"x": x, "y": ys[yi+1], "z": z_roof}
                if not _member_exists(s, e, existing_index):
                    span = abs(ys[yi+1]-ys[yi])
                    prof, mat = _beam_profile(span)
                    missing.append(_el("BEAM", prof, mat, x, ys[yi], z_roof, x, ys[yi+1], z_roof))

    elif roof_type == "gable":
        total_span = max(ys) - min(ys) if ys else 6000
        ridge_z    = z_roof + total_span * 0.15
        ridge_y    = (min(ys) + max(ys)) / 2

        # Ridge beam
        for xi in range(len(xs)-1):
            s = {"x": xs[xi], "y": ridge_y, "z": ridge_z}
            e = {"x": xs[xi+1], "y": ridge_y, "z": ridge_z}
            if not _member_exists(s, e, existing_index):
                missing.append(_el("BEAM", "IPE200", "S235",
                                   xs[xi], ridge_y, ridge_z,
                                   xs[xi+1], ridge_y, ridge_z))

        # Rafters from eave to ridge
        for x in xs:
            for y_eave in [min(ys), max(ys)]:
                s = {"x": x, "y": y_eave,  "z": z_roof}
                e = {"x": x, "y": ridge_y, "z": ridge_z}
                if not _member_exists(s, e, existing_index):
                    missing.append(_el("SECONDARY", "IPE200", "S235",
                                       x, y_eave, z_roof, x, ridge_y, ridge_z))

    return missing


# ── SYMMETRY MIRROR ──────────────────────────────────────────────────────────

def mirror_missing_region(
    nodes:       list,
    missing_els: list,
    axis:        str = "X",  # "X" or "Y"
) -> list:
    """
    If a model is ~50% complete, mirror the existing half to generate
    the missing half. Only adds members that don't already exist.

    axis = "X" → mirror about the YZ plane at mid-X
    axis = "Y" → mirror about the XZ plane at mid-Y
    """
    if not nodes:
        return missing_els

    xs = [n["start"]["x"] for n in nodes] + [n["end"]["x"] for n in nodes]
    ys = [n["start"]["y"] for n in nodes] + [n["end"]["y"] for n in nodes]
    mid_x = (min(xs) + max(xs)) / 2
    mid_y = (min(ys) + max(ys)) / 2

    mirrored = []
    existing_index = _build_endpoint_index(nodes)

    for n in nodes:
        s, e = n["start"], n["end"]
        if axis == "X":
            ns = {"x": 2*mid_x - s["x"], "y": s["y"], "z": s["z"]}
            ne = {"x": 2*mid_x - e["x"], "y": e["y"], "z": e["z"]}
        else:
            ns = {"x": s["x"], "y": 2*mid_y - s["y"], "z": s["z"]}
            ne = {"x": e["x"], "y": 2*mid_y - e["y"], "z": e["z"]}

        if not _member_exists(ns, ne, existing_index):
            mirrored.append(_el(
                n["role"].replace("PRIMARY_", ""),
                n.get("profile", "IPE300"),
                n.get("material", "S275"),
                ns["x"], ns["y"], ns["z"],
                ne["x"], ne["y"], ne["z"],
            ))

    return missing_els + mirrored


# ── COMPLETENESS ASSESSMENT ──────────────────────────────────────────────────

def assess_completeness(nodes: list, grid: dict) -> dict:
    """
    Returns a 0-100 completeness score and identifies which components
    are present vs missing at a high level.
    """
    expected_cols   = len(grid["x_coords"]) * len(grid["y_coords"])
    expected_z      = len([z for z in grid["z_levels"] if z > 0])
    expected_x_beams= (len(grid["x_coords"])-1) * len(grid["y_coords"]) * expected_z if expected_z else 0
    expected_y_beams= len(grid["x_coords"]) * (len(grid["y_coords"])-1) * expected_z if expected_z else 0
    expected_beams  = expected_x_beams + expected_y_beams

    actual_cols  = sum(1 for n in nodes if n["role"] == "COLUMN")
    actual_beams = sum(1 for n in nodes if n["role"] == "BEAM")
    actual_sec   = sum(1 for n in nodes if n["role"] == "SECONDARY")

    col_pct  = min(100, round(actual_cols  / expected_cols  * 100)) if expected_cols  else 100
    beam_pct = min(100, round(actual_beams / expected_beams * 100)) if expected_beams else 100

    overall  = round((col_pct + beam_pct) / 2)

    return {
        "completeness_pct": overall,
        "columns":  {"expected": expected_cols,  "actual": actual_cols,  "pct": col_pct},
        "beams":    {"expected": expected_beams, "actual": actual_beams, "pct": beam_pct},
        "secondary":{"actual": actual_sec},
        "strategy": "mirror" if overall < 60 else "fill_gaps",
    }


# ════════════════════════════════════════════════════════════════════════════
#  MASTER COMPLETION FUNCTION
# ════════════════════════════════════════════════════════════════════════════

def complete_model(data: list, options: dict = None) -> dict:
    """
    Main entry point for Workflow 2: Existing Model Completion.

    Steps:
      1. Build graph from existing data
      2. Detect grid pattern
      3. Assess completeness
      4. Find missing columns, beams, bracing, roof
      5. Apply mirror if < 60% complete
      6. Return missing elements only

    Args:
      data    — list of raw Tekla member dicts (from output.json)
      options — {roof_type, use_mirror, mirror_axis}

    Returns dict with missing elements + analysis report.
    """
    opts       = options or {}
    roof_type  = opts.get("roof_type", "flat")
    use_mirror = opts.get("use_mirror", True)
    mirror_ax  = opts.get("mirror_axis", "X")

    if not data:
        return {
            "status":   "empty_model",
            "missing":  [],
            "analysis": {},
            "count":    0,
        }

    # Step 1: Build graph
    graph = build_graph(data)
    nodes = graph["nodes"]

    if not nodes:
        return {"status": "no_nodes", "missing": [], "analysis": {}, "count": 0}

    # Step 2: Detect grid
    grid = detect_grid_pattern(nodes)

    if not grid.get("x_coords") or not grid.get("y_coords"):
        from topology_engine import detect_bays_xy, detect_levels
        xs, ys = detect_bays_xy(data)
        z_levels = detect_levels(data)
        if xs and ys:
            grid = {
                **grid,
                "x_coords": xs,
                "y_coords": ys,
                "z_levels": z_levels or grid.get("z_levels", []),
                "spacing_x": grid.get("spacing_x", 6000),
                "spacing_y": grid.get("spacing_y", 6000),
                "story_height": grid.get("story_height", 3500),
            }

    # Step 3: Assess completeness
    assessment = assess_completeness(nodes, grid)

    # Step 4: Build existing-member index
    existing_index = _build_endpoint_index(nodes)

    # Step 5: Find missing members
    missing = []

    if grid["x_coords"] and grid["y_coords"]:
        miss_cols   = find_missing_columns(grid, existing_index)
        miss_beams  = find_missing_beams(grid, existing_index)
        miss_brace  = find_missing_bracing(grid, existing_index)
        miss_roof   = find_missing_roof(grid, existing_index, roof_type)

        missing = miss_cols + miss_beams + miss_brace + miss_roof
    else:
        miss_cols  = []
        miss_beams = []
        miss_brace = []
        miss_roof  = []

    # Step 6: Mirror if less than 60% complete
    if use_mirror and assessment["completeness_pct"] < 60:
        missing = mirror_missing_region(nodes, missing, mirror_ax)

    # Deduplicate within the missing list itself
    seen  = set()
    dedup = []
    for e in missing:
        s = e["StartPoint"]; ep = e["EndPoint"]
        k = (
            round(s["X"]/50), round(s["Y"]/50), round(s["Z"]/50),
            round(ep["X"]/50), round(ep["Y"]/50), round(ep["Z"]/50),
        )
        if k not in seen:
            seen.add(k)
            dedup.append(e)

    missing = dedup

    return {
        "status":       "ok",
        "missing":      missing,
        "count":        len(missing),
        "breakdown": {
            "columns":   len(miss_cols)  if grid["x_coords"] else 0,
            "beams":     len(miss_beams) if grid["x_coords"] else 0,
            "bracing":   len(miss_brace) if grid["x_coords"] else 0,
            "roof":      len(miss_roof)  if grid["x_coords"] else 0,
            "mirrored":  len(missing) - len(miss_cols) - len(miss_beams)
                         - len(miss_brace) - len(miss_roof),
        },
        "grid":        grid,
        "assessment":  assessment,
    }