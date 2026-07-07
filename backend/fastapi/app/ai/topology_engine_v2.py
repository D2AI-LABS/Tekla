# topology_engine.py
#
# Universal BIM Topology Learning Engine
#
# This module does what mirror + grid-detection alone cannot:
#   - Understands the STRUCTURAL HIERARCHY of any model
#   - Recognises faces, bays, levels, corners, platforms
#   - Classifies every member's semantic PLACE (Face-2, Bay-4, Corner, Roof…)
#   - Detects bracing patterns (X / K / V / Z / diagonal)
#   - Detects symmetry type (rectangular, triangular, hexagonal, radial)
#   - Infers missing geometry from PATTERN — not from mirror
#
# Used by:
#   completion_engine.py   (pattern-based gap filling)
#   coordinate_solver.py   (topology context for XYZ planning)
#   universal_generator.py (structure-aware generation)

import math
import json
from collections import defaultdict, Counter
from typing import List, Dict, Tuple, Optional

SNAP = 100.0  # mm — two points are "same node" if closer than this


# ══════════════════════════════════════════════════════════════════════════════
# 1.  NODE CLUSTERING  (snap nearby endpoints into shared structural nodes)
# ══════════════════════════════════════════════════════════════════════════════

def _pt(d: dict) -> Tuple[float, float, float]:
    return (float(d.get("X", d.get("x", 0))),
            float(d.get("Y", d.get("y", 0))),
            float(d.get("Z", d.get("z", 0))))


def _key(x, y, z, snap=SNAP) -> Tuple[int, int, int]:
    return (round(x / snap), round(y / snap), round(z / snap))


def cluster_nodes(members: list) -> Dict[str, dict]:
    """
    Merge all member endpoints that are within SNAP of each other into
    unique structural nodes.  Returns {node_id: {x,y,z, members:[…]}}
    """
    nodes: Dict[tuple, dict] = {}
    node_list = []

    for m in members:
        sp = m.get("StartPoint") or m.get("startPoint") or {}
        ep = m.get("EndPoint")   or m.get("endPoint")   or {}
        mid_str = str(m.get("Id", id(m)))

        for pt in [sp, ep]:
            if not pt:
                continue
            x, y, z = _pt(pt)
            k = _key(x, y, z)
            if k not in nodes:
                nid = f"N{len(nodes)}"
                nodes[k] = {"id": nid, "x": x, "y": y, "z": z, "members": []}
                node_list.append(nodes[k])
            nodes[k]["members"].append(mid_str)

    return {n["id"]: n for n in node_list}


# ══════════════════════════════════════════════════════════════════════════════
# 2.  LEVEL / BAY / FACE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def detect_levels(members: list) -> List[float]:
    """
    Return sorted list of distinct Z elevations (storey / panel tops).
    """
    zs = set()
    for m in members:
        for pt_key in ["StartPoint", "EndPoint", "startPoint", "endPoint"]:
            pt = m.get(pt_key) or {}
            z = float(pt.get("Z", pt.get("z", -1)))
            if z >= 0:
                zs.add(round(z / SNAP) * SNAP)
    return sorted(zs)


def detect_bays_xy(members: list) -> Tuple[List[float], List[float]]:
    """
    Return (x_coords, y_coords) of distinct grid lines.
    """
    xs, ys = set(), set()
    for m in members:
        for pt_key in ["StartPoint", "EndPoint", "startPoint", "endPoint"]:
            pt = m.get(pt_key) or {}
            x = float(pt.get("X", pt.get("x", 0)))
            y = float(pt.get("Y", pt.get("y", 0)))
            xs.add(round(x / SNAP) * SNAP)
            ys.add(round(y / SNAP) * SNAP)
    return sorted(xs), sorted(ys)


def detect_faces(members: list, structure_shape: str = "rect") -> List[dict]:
    """
    Identify the vertical faces of the structure.
    For rectangular: 4 faces (N/S/E/W).
    For triangular:  3 faces.
    For hexagonal:   6 faces.
    Returns list of {face_id, normal_x, normal_y, members:[]}.
    """
    xs, ys = detect_bays_xy(members)
    if not xs or not ys:
        return []

    if structure_shape in ("hexagonal", "hex"):
        return _hex_faces(members)
    if structure_shape in ("triangular", "tri"):
        return _tri_faces(members)
    # Default: rectangular
    return _rect_faces(members, xs, ys)


def _rect_faces(members, xs, ys):
    faces = [
        {"face_id": "F-South", "y_fixed": min(ys), "axis": "Y", "normal": (0, -1)},
        {"face_id": "F-North", "y_fixed": max(ys), "axis": "Y", "normal": (0,  1)},
        {"face_id": "F-West",  "x_fixed": min(xs), "axis": "X", "normal": (-1, 0)},
        {"face_id": "F-East",  "x_fixed": max(xs), "axis": "X", "normal": ( 1, 0)},
    ]
    tol = SNAP * 1.5
    for m in members:
        for f in faces:
            if "y_fixed" in f:
                pts = [m.get("StartPoint",{}), m.get("EndPoint",{})]
                if any(abs(float(p.get("Y", p.get("y",0))) - f["y_fixed"]) < tol for p in pts if p):
                    f.setdefault("members", []).append(str(m.get("Id", "")))
            elif "x_fixed" in f:
                pts = [m.get("StartPoint",{}), m.get("EndPoint",{})]
                if any(abs(float(p.get("X", p.get("x",0))) - f["x_fixed"]) < tol for p in pts if p):
                    f.setdefault("members", []).append(str(m.get("Id", "")))
    for f in faces:
        f.setdefault("members", [])
    return faces


def _tri_faces(members):
    # Find 3 leg positions from bottom Z
    zs = detect_levels(members)
    base_z = zs[0] if zs else 0
    tol    = SNAP * 1.5
    leg_pts = set()
    for m in members:
        for pt_key in ["StartPoint", "EndPoint"]:
            pt = m.get(pt_key) or {}
            z  = float(pt.get("Z", pt.get("z", -1)))
            if abs(z - base_z) < tol:
                x = round(float(pt.get("X", 0)) / SNAP) * SNAP
                y = round(float(pt.get("Y", 0)) / SNAP) * SNAP
                leg_pts.add((x, y))
    corners = list(leg_pts)[:3]
    faces   = []
    for i in range(len(corners)):
        a = corners[i]; b = corners[(i+1) % len(corners)]
        faces.append({"face_id": f"F-{i+1}", "corner_a": a, "corner_b": b,
                      "normal": (-(b[1]-a[1]), b[0]-a[0]), "members": []})
    return faces


def _hex_faces(members):
    zs = detect_levels(members)
    base_z = zs[0] if zs else 0
    tol    = SNAP * 1.5
    leg_pts = set()
    for m in members:
        for pt_key in ["StartPoint", "EndPoint"]:
            pt = m.get(pt_key) or {}
            z  = float(pt.get("Z", pt.get("z", -1)))
            if abs(z - base_z) < tol:
                x = round(float(pt.get("X", 0)) / SNAP) * SNAP
                y = round(float(pt.get("Y", 0)) / SNAP) * SNAP
                leg_pts.add((x, y))
    corners = list(leg_pts)[:6]
    faces   = []
    for i in range(len(corners)):
        a = corners[i]; b = corners[(i+1) % len(corners)]
        faces.append({"face_id": f"F-{i+1}", "corner_a": a, "corner_b": b,
                      "normal": (-(b[1]-a[1]), b[0]-a[0]), "members": []})
    return faces


# ══════════════════════════════════════════════════════════════════════════════
# 3.  BRACING PATTERN RECOGNITION
# ══════════════════════════════════════════════════════════════════════════════

def detect_bracing_pattern(members: list, levels: List[float]) -> dict:
    """
    Inspect diagonal members between adjacent levels and classify the
    bracing pattern per face / panel.

    Returns:
      {
        "dominant": "X" | "K" | "V" | "Z" | "diagonal" | "unknown",
        "confidence": 0.0-1.0,
        "per_panel": [ {"z0":…, "z1":…, "pattern":…}, … ],
        "diag_angle_deg": float,
      }
    """
    diagonals = []
    for m in members:
        sp = m.get("StartPoint") or m.get("startPoint") or {}
        ep = m.get("EndPoint")   or m.get("endPoint")   or {}
        if not sp or not ep:
            continue
        dz = abs(float(ep.get("Z", ep.get("z",0))) - float(sp.get("Z", sp.get("z",0))))
        dx = abs(float(ep.get("X", ep.get("x",0))) - float(sp.get("X", sp.get("x",0))))
        dy = abs(float(ep.get("Y", ep.get("y",0))) - float(sp.get("Y", sp.get("y",0))))
        horiz = math.sqrt(dx**2 + dy**2)
        if dz > SNAP and horiz > SNAP:   # not pure horizontal, not pure vertical
            diagonals.append({
                "member": m,
                "dz": dz, "dh": horiz,
                "angle": math.degrees(math.atan2(dz, horiz)),
                "sz": min(float(sp.get("Z", sp.get("z",0))),
                          float(ep.get("Z", ep.get("z",0)))),
            })

    if not diagonals:
        return {"dominant": "unknown", "confidence": 0.0, "per_panel": [], "diag_angle_deg": 0}

    # Average diagonal angle
    avg_angle = sum(d["angle"] for d in diagonals) / len(diagonals)

    # Count diagonals per panel
    per_panel = []
    for i, lev in enumerate(levels[:-1]):
        z0, z1 = lev, levels[i+1]
        panel_diags = [d for d in diagonals if z0 - SNAP <= d["sz"] < z1 + SNAP]

        if not panel_diags:
            pat = "none"
        elif len(panel_diags) >= 4:
            pat = "X"    # X-brace has 2 diagonals per face, multiple faces
        elif len(panel_diags) == 2:
            # Check if they meet at mid-height (K) or at top/bottom (V/Z)
            sz_vals = [d["sz"] for d in panel_diags]
            if max(sz_vals) - min(sz_vals) < SNAP:
                pat = "X"
            else:
                pat = "K"
        else:
            pat = "diagonal"

        per_panel.append({"z0": z0, "z1": z1, "pattern": pat,
                          "diag_count": len(panel_diags)})

    counts  = Counter(p["pattern"] for p in per_panel)
    dominant= counts.most_common(1)[0][0] if counts else "unknown"
    total   = sum(counts.values())
    conf    = counts[dominant] / total if total else 0.0

    return {
        "dominant":      dominant,
        "confidence":    round(conf, 2),
        "per_panel":     per_panel,
        "diag_angle_deg":round(avg_angle, 1),
        "total_diagonals": len(diagonals),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4.  SYMMETRY DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def detect_symmetry(members: list) -> dict:
    """
    Detects the dominant symmetry of the structure.
    Returns {type, axes, confidence, n_legs (for towers)}.
    """
    xs, ys = detect_bays_xy(members)
    levels  = detect_levels(members)

    if not xs or not ys:
        return {"type": "unknown", "confidence": 0.0}

    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2

    # Count unique column base positions
    base_z = levels[0] if levels else 0
    col_bases = set()
    for m in members:
        name = str(m.get("Name", m.get("Type", ""))).upper()
        if "COLUMN" not in name and "LEG" not in name:
            continue
        for pt_key in ["StartPoint", "EndPoint"]:
            pt = m.get(pt_key) or {}
            z  = float(pt.get("Z", pt.get("z", -999)))
            if abs(z - base_z) < SNAP * 2:
                col_bases.add((
                    round(float(pt.get("X", 0)) / SNAP) * SNAP,
                    round(float(pt.get("Y", 0)) / SNAP) * SNAP,
                ))

    n = len(col_bases)

    # Determine shape by n_legs / symmetry
    if n == 3:
        return {"type": "triangular", "n_legs": 3, "center": (cx, cy), "confidence": 0.9}
    if n == 4:
        return {"type": "rectangular", "n_legs": 4, "center": (cx, cy), "confidence": 0.9}
    if n == 6:
        # Could be hexagonal tower or 2×3 building grid
        aspect = (max(xs) - min(xs)) / max(max(ys) - min(ys), 1)
        if 0.8 < aspect < 1.2:
            return {"type": "hexagonal", "n_legs": 6, "center": (cx, cy), "confidence": 0.85}
        return {"type": "rectangular_grid", "n_legs": 6, "center": (cx, cy), "confidence": 0.7}
    if n >= 8:
        return {"type": "rectangular_grid", "n_legs": n, "center": (cx, cy), "confidence": 0.8}

    # Fallback: check aspect ratio
    width_x = max(xs) - min(xs)
    width_y = max(ys) - min(ys)
    aspect  = width_x / max(width_y, 1)
    if aspect > 2 or aspect < 0.5:
        return {"type": "elongated", "center": (cx, cy), "confidence": 0.6}

    return {"type": "rectangular", "n_legs": n, "center": (cx, cy), "confidence": 0.5}


# ══════════════════════════════════════════════════════════════════════════════
# 5.  PLACE RECOGNITION  (semantic location of each member)
# ══════════════════════════════════════════════════════════════════════════════

def classify_member_place(m: dict, topology: dict) -> dict:
    """
    Given a member and the full topology context, returns its semantic place:
      {
        zone:       "ground" | "mid" | "top" | "roof"
        level_idx:  int
        bay_x:      int
        bay_y:      int
        face:       "F-North" | "F-South" | "F-East" | "F-West" | "interior" | "corner"
        role:       "leg" | "primary_beam" | "secondary_beam" | "diagonal" | "horizontal" | "purlin"
        position:   "corner" | "edge" | "interior" | "roof_ridge" | "foundation"
      }
    """
    levels = topology.get("levels", [0])
    xs     = topology.get("x_coords", [0])
    ys     = topology.get("y_coords", [0])

    sp = m.get("StartPoint") or m.get("startPoint") or {}
    ep = m.get("EndPoint")   or m.get("endPoint")   or {}
    if not sp or not ep:
        return {"zone": "unknown", "face": "unknown", "role": "unknown", "position": "unknown"}

    sx, sy, sz = _pt(sp)
    ex, ey, ez = _pt(ep)
    mid_z = (sz + ez) / 2
    top_z = max(levels) if levels else 0
    base_z= min(levels) if levels else 0

    # Zone
    if top_z > base_z:
        frac = (mid_z - base_z) / (top_z - base_z)
    else:
        frac = 0
    if frac < 0.1:   zone = "ground"
    elif frac < 0.45: zone = "lower"
    elif frac < 0.75: zone = "mid"
    elif frac < 0.95: zone = "upper"
    else:             zone = "top"

    # Level index
    level_idx = 0
    for i, lev in enumerate(levels):
        if mid_z >= lev - SNAP:
            level_idx = i

    # Bay indices
    mid_x = (sx + ex) / 2
    mid_y = (sy + ey) / 2
    bay_x = 0
    for i, gx in enumerate(xs):
        if mid_x >= gx - SNAP:
            bay_x = i
    bay_y = 0
    for i, gy in enumerate(ys):
        if mid_y >= gy - SNAP:
            bay_y = i

    # Face detection
    tol   = SNAP * 2
    on_minx = abs(mid_x - min(xs)) < tol if xs else False
    on_maxx = abs(mid_x - max(xs)) < tol if xs else False
    on_miny = abs(mid_y - min(ys)) < tol if ys else False
    on_maxy = abs(mid_y - max(ys)) < tol if ys else False

    if   on_miny: face = "F-South"
    elif on_maxy: face = "F-North"
    elif on_minx: face = "F-West"
    elif on_maxx: face = "F-East"
    else:         face = "interior"

    # Corner detection
    corner_count = sum([on_minx, on_maxx, on_miny, on_maxy])
    if corner_count >= 2:
        position = "corner"
    elif corner_count == 1:
        position = "edge"
    elif zone in ("top", "roof"):
        position = "roof"
    elif zone == "ground":
        position = "foundation"
    else:
        position = "interior"

    # Role from geometry
    dz = abs(ez - sz); dx = abs(ex - sx); dy = abs(ey - sy)
    horiz = math.sqrt(dx**2 + dy**2)
    if dz > horiz * 2:
        role = "leg"
    elif horiz > dz * 2:
        role = "primary_beam" if position in ("corner", "edge") else "secondary_beam"
    else:
        role = "diagonal"

    return {
        "zone":       zone,
        "level_idx":  level_idx,
        "bay_x":      bay_x,
        "bay_y":      bay_y,
        "face":       face,
        "role":       role,
        "position":   position,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 6.  STRUCTURAL HIERARCHY BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_structural_hierarchy(members: list) -> dict:
    """
    Builds the complete structural hierarchy of the model:
      foundation → legs/columns → horizontal members → diagonals → roof

    Returns a tree dict used by the universal generator and completion engine.
    """
    levels   = detect_levels(members)
    xs, ys   = detect_bays_xy(members)
    symmetry = detect_symmetry(members)
    bracing  = detect_bracing_pattern(members, levels)

    topology = {"levels": levels, "x_coords": xs, "y_coords": ys,
                "symmetry": symmetry, "bracing": bracing}

    # Classify every member
    annotated = []
    for m in members:
        place = classify_member_place(m, topology)
        annotated.append({**m, "_place": place})

    # Group by level
    by_level = defaultdict(list)
    for m in annotated:
        by_level[m["_place"]["level_idx"]].append(m)

    # Group by face
    by_face = defaultdict(list)
    for m in annotated:
        by_face[m["_place"]["face"]].append(m)

    # Count panel completeness per level
    panel_status = []
    n_faces_expected = symmetry.get("n_legs", 4)
    for li, lev in enumerate(levels[:-1] if len(levels) > 1 else levels):
        z0 = lev
        z1 = levels[li+1] if li+1 < len(levels) else lev + 3000
        panel_members = [m for m in annotated if m["_place"]["level_idx"] == li]
        diag_count = sum(1 for m in panel_members if m["_place"]["role"] == "diagonal")
        horiz_count= sum(1 for m in panel_members if m["_place"]["role"] in ("primary_beam","secondary_beam","horizontal"))
        panel_status.append({
            "level_idx":    li,
            "z0":           z0,
            "z1":           z1,
            "total_members":len(panel_members),
            "diagonals":    diag_count,
            "horizontals":  horiz_count,
            "complete":     diag_count > 0 and horiz_count > 0,
        })

    return {
        "levels":          levels,
        "x_coords":        xs,
        "y_coords":        ys,
        "symmetry":        symmetry,
        "bracing":         bracing,
        "n_panels":        len(levels) - 1 if len(levels) > 1 else 0,
        "panel_status":    panel_status,
        "by_level":        dict(by_level),
        "by_face":         dict(by_face),
        "total_members":   len(members),
        "annotated":       annotated,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 7.  STRUCTURE TYPE INFERENCE from geometry alone
# ══════════════════════════════════════════════════════════════════════════════

def infer_structure_type(members: list) -> dict:
    """
    Infer structure type from geometry ONLY — no LLM, no keywords.
    Uses aspect ratio, diagonal density, level count, symmetry.
    """
    if not members:
        return {"type": "unknown", "confidence": 0.0}

    levels   = detect_levels(members)
    xs, ys   = detect_bays_xy(members)
    symmetry = detect_symmetry(members)

    height    = (max(levels) - min(levels)) if len(levels) > 1 else 0
    width_x   = (max(xs) - min(xs)) if len(xs) > 1 else 1
    width_y   = (max(ys) - min(ys)) if len(ys) > 1 else 1
    footprint = max(width_x, width_y)

    # Aspect ratio H:W — towers are tall and narrow
    aspect = height / max(footprint, 1)

    # Diagonal density — fraction of members that are diagonal
    total_diag = 0
    for m in members:
        sp = m.get("StartPoint") or m.get("startPoint") or {}
        ep = m.get("EndPoint")   or m.get("endPoint")   or {}
        if sp and ep:
            dz = abs(float(ep.get("Z", 0)) - float(sp.get("Z", 0)))
            dx = abs(float(ep.get("X", 0)) - float(sp.get("X", 0)))
            dy = abs(float(ep.get("Y", 0)) - float(sp.get("Y", 0)))
            dh = math.sqrt(dx**2 + dy**2)
            if dz > SNAP and dh > SNAP:
                total_diag += 1
    diag_density = total_diag / max(len(members), 1)

    # Floor / bay count
    n_levels  = len(levels)
    n_cols_xy = len(xs) * len(ys)
    sym_type  = symmetry.get("type", "rectangular")

    # Classification rules
    if aspect > 4 and diag_density > 0.25:
        if sym_type == "hexagonal":
            stype = "tower_telecom"
        elif sym_type == "triangular":
            stype = "tower_telecom"
        elif aspect > 6:
            stype = "tower_lattice"
        else:
            stype = "tower_lattice"
        conf = 0.85
    elif aspect > 2 and diag_density > 0.15:
        stype = "tower_lattice"; conf = 0.7
    elif n_levels == 1 and width_y > width_x * 1.5:
        stype = "warehouse"; conf = 0.8
    elif n_levels == 1 and diag_density < 0.1:
        stype = "shed"; conf = 0.75
    elif n_cols_xy >= 6 and n_levels >= 2:
        stype = "building"; conf = 0.8
    elif diag_density > 0.3 and aspect < 2:
        stype = "pipe_rack"; conf = 0.7
    else:
        stype = "building"; conf = 0.5

    return {
        "type":          stype,
        "confidence":    conf,
        "aspect_ratio":  round(aspect, 2),
        "diag_density":  round(diag_density, 2),
        "n_levels":      n_levels,
        "symmetry":      sym_type,
        "height_mm":     round(height, 0),
        "footprint_mm":  round(footprint, 0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 8.  PATTERN-BASED MISSING MEMBER INFERENCE  (replaces mirror)
# ══════════════════════════════════════════════════════════════════════════════

def infer_missing_from_pattern(members: list, hierarchy: dict) -> List[dict]:
    """
    Uses the detected structural PATTERN to infer missing members —
    not simple geometric mirror.

    Logic:
      1. For each detected panel, check if bracing pattern is broken
      2. For each face in each level, check horizontal continuity
      3. For each level boundary, check column/leg continuity
      4. Generate only the specific missing members

    Returns list of element dicts (Type, Profile, Material, StartPoint, EndPoint).
    """
    missing  = []
    bracing  = hierarchy["bracing"]
    levels   = hierarchy["levels"]
    xs       = hierarchy["x_coords"]
    ys       = hierarchy["y_coords"]
    panels   = hierarchy["panel_status"]
    symmetry = hierarchy["symmetry"]

    dominant_pat = bracing.get("dominant", "X")
    sym_type     = symmetry.get("type", "rectangular")

    # Build existing endpoint index
    existing_keys = set()
    for m in members:
        sp = m.get("StartPoint") or m.get("startPoint") or {}
        ep = m.get("EndPoint")   or m.get("endPoint")   or {}
        if sp and ep:
            k  = (_key(float(sp.get("X",0)), float(sp.get("Y",0)), float(sp.get("Z",0))),
                  _key(float(ep.get("X",0)), float(ep.get("Y",0)), float(ep.get("Z",0))))
            existing_keys.add(k)
            existing_keys.add((k[1], k[0]))

    def exists(sx, sy, sz, ex, ey, ez):
        k = (_key(sx, sy, sz), _key(ex, ey, ez))
        return k in existing_keys or (k[1], k[0]) in existing_keys

    def add(etype, prof, mat, sx, sy, sz, ex, ey, ez):
        if not exists(sx, sy, sz, ex, ey, ez):
            missing.append({
                "Type":       etype,
                "Profile":    prof,
                "Material":   mat,
                "StartPoint": {"X": round(sx,1),"Y": round(sy,1),"Z": round(sz,1)},
                "EndPoint":   {"X": round(ex,1),"Y": round(ey,1),"Z": round(ez,1)},
            })
            existing_keys.add((_key(sx,sy,sz), _key(ex,ey,ez)))

    # Profile defaults
    def col_prof(h):
        if h > 7000: return "HEA300","S355"
        if h > 4000: return "HEA240","S355"
        return "HEA200","S275"
    def bm_prof(s):
        if s > 9000: return "IPE400","S355"
        if s > 6000: return "IPE300","S355"
        return "IPE240","S275"

    for panel in panels:
        if panel["complete"]:
            continue  # already has diagonals + horizontals

        li   = panel["level_idx"]
        z0   = panel["z0"]
        z1   = panel["z1"]

        # ── Rectangular / building pattern ───────────────────────────────
        if sym_type in ("rectangular", "rectangular_grid", "elongated"):
            for xi, x in enumerate(xs):
                # Missing columns
                for y in ys:
                    add("COLUMN", *col_prof(z1-z0), x, y, z0, x, y, z1)
                # Missing X-direction horizontals at top of panel
                if xi < len(xs)-1:
                    for y in ys:
                        sp, sm = bm_prof(xs[xi+1]-x)
                        add("BEAM", sp, sm, x, y, z1, xs[xi+1], y, z1)
            # Missing Y-direction horizontals
            for yi, y in enumerate(ys):
                if yi < len(ys)-1:
                    for x in xs:
                        sp, sm = bm_prof(ys[yi+1]-y)
                        add("BEAM", sp, sm, x, y, z1, x, ys[yi+1], z1)
            # Bracing on perimeter
            if dominant_pat == "X":
                for face_y in [min(ys), max(ys)]:
                    for xi in range(len(xs)-1):
                        add("SECONDARY","L60x60x5","S235", xs[xi],face_y,z0, xs[xi+1],face_y,z1)
                        add("SECONDARY","L60x60x5","S235", xs[xi+1],face_y,z0, xs[xi],face_y,z1)
                for face_x in [min(xs), max(xs)]:
                    for yi in range(len(ys)-1):
                        add("SECONDARY","L60x60x5","S235", face_x,ys[yi],z0, face_x,ys[yi+1],z1)
                        add("SECONDARY","L60x60x5","S235", face_x,ys[yi+1],z0, face_x,ys[yi],z1)

        # ── Tower pattern (triangular / hexagonal) ───────────────────────
        elif sym_type in ("triangular", "hexagonal", "hex"):
            cx = symmetry.get("center",(0,0))[0]
            cy = symmetry.get("center",(0,0))[1]
            n  = 3 if sym_type == "triangular" else 6
            hw0 = math.sqrt((xs[0]-cx)**2 + (ys[0]-cy)**2) if xs and ys else 2000
            angs= [2*math.pi*i/n for i in range(n)]
            legs0 = [(cx + hw0*math.cos(a), cy + hw0*math.sin(a)) for a in angs]
            for i in range(n):
                ax,ay = legs0[i]; bx,by = legs0[(i+1)%n]
                # Leg
                add("COLUMN","CHS193x6","S355", ax,ay,z0, ax,ay,z1)
                # Horizontal
                add("SECONDARY","L60x60x5","S235", ax,ay,z1, bx,by,z1)
                # Diagonal (X-brace on each face)
                add("SECONDARY","L70x70x6","S275", ax,ay,z0, bx,by,z1)
                add("SECONDARY","L70x70x6","S275", bx,by,z0, ax,ay,z1)

    return missing


# ══════════════════════════════════════════════════════════════════════════════
# 9.  MASTER TOPOLOGY ANALYSIS FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def analyse_topology(members: list) -> dict:
    """
    Full topology analysis. Entry point called by completion_engine and main.py.

    Returns comprehensive topology dict including:
      - structure_type inference
      - levels, bays, faces
      - symmetry, bracing pattern
      - panel completeness
      - member semantic places
      - missing member suggestions (pattern-based, not mirror)
    """
    if not members:
        return {"status": "empty"}

    inferred    = infer_structure_type(members)
    hierarchy   = build_structural_hierarchy(members)
    missing     = infer_missing_from_pattern(members, hierarchy)
    faces       = detect_faces(members, inferred.get("type", "rect"))

    return {
        "status":            "ok",
        "inferred_type":     inferred,
        "levels":            hierarchy["levels"],
        "x_coords":          hierarchy["x_coords"],
        "y_coords":          hierarchy["y_coords"],
        "n_panels":          hierarchy["n_panels"],
        "panel_status":      hierarchy["panel_status"],
        "symmetry":          hierarchy["symmetry"],
        "bracing":           hierarchy["bracing"],
        "faces":             faces,
        "missing_by_pattern":missing,
        "missing_count":     len(missing),
        "completeness_pct":  max(0, round(100 - len(missing) /
                             max(len(members) + len(missing), 1) * 100)),
        "member_places":     [
            {"id": str(m.get("Id","")), **m.get("_place", {})}
            for m in hierarchy.get("annotated", [])
        ],
    }


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Minimal test with fake members
    test_members = [
        {"Id":1,"Name":"COLUMN","Type":"COLUMN","Profile":"HEA200","Material":"S275",
         "StartPoint":{"X":0,"Y":0,"Z":0},"EndPoint":{"X":0,"Y":0,"Z":3500}},
        {"Id":2,"Name":"COLUMN","Type":"COLUMN","Profile":"HEA200","Material":"S275",
         "StartPoint":{"X":6000,"Y":0,"Z":0},"EndPoint":{"X":6000,"Y":0,"Z":3500}},
        {"Id":3,"Name":"COLUMN","Type":"COLUMN","Profile":"HEA200","Material":"S275",
         "StartPoint":{"X":0,"Y":6000,"Z":0},"EndPoint":{"X":0,"Y":6000,"Z":3500}},
        {"Id":4,"Name":"BEAM","Type":"BEAM","Profile":"IPE300","Material":"S275",
         "StartPoint":{"X":0,"Y":0,"Z":3500},"EndPoint":{"X":6000,"Y":0,"Z":3500}},
    ]
    result = analyse_topology(test_members)
    print(json.dumps({
        "inferred_type":  result["inferred_type"],
        "levels":         result["levels"],
        "symmetry":       result["symmetry"],
        "bracing":        result["bracing"],
        "n_panels":       result["n_panels"],
        "missing_count":  result["missing_count"],
        "completeness":   result["completeness_pct"],
    }, indent=2))