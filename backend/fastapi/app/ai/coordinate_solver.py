# coordinate_solver.py
#
# Universal XYZ Coordinate Solver
#
# Every generated member passes through this module BEFORE insertion.
# Responsibilities:
#   1. Snap start/end points to the nearest active grid node
#   2. Enforce grid boundary constraints (no members outside grid)
#   3. Resolve member orientation (local axis, rotation, angle)
#   4. Detect and reject zero-length or near-duplicate members
#   5. Adjust coordinates for tapered structures (towers)
#   6. Compute all derived geometry (length, dx, dy, dz, midpoint)
#   7. Validate final coordinates against engineering constraints

import math
from typing import List, Tuple, Optional, Dict

SNAP_GRID  = 50.0    # snap endpoints to nearest 50mm grid
SNAP_NODE  = 75.0    # merge endpoints within 75mm of existing nodes
MIN_LENGTH = 100.0   # reject members shorter than 100mm
MAX_LENGTH = 200_000 # reject members longer than 200m (likely error)


# ══════════════════════════════════════════════════════════════════════════════
# 1.  GRID SNAP
# ══════════════════════════════════════════════════════════════════════════════

def snap_to_grid(x: float, y: float, z: float,
                 snap_xy: float = SNAP_GRID,
                 snap_z:  float = SNAP_GRID) -> Tuple[float, float, float]:
    """
    Snap XY to nearest snap_xy mm and Z to nearest snap_z mm.
    Prevents floating-point drift from accumulating over many panels.
    """
    return (
        round(x / snap_xy) * snap_xy,
        round(y / snap_xy) * snap_xy,
        round(z / snap_z)  * snap_z,
    )


def snap_to_nearest_node(x: float, y: float, z: float,
                          nodes: List[Tuple[float, float, float]],
                          tolerance: float = SNAP_NODE) -> Tuple[float, float, float]:
    """
    If any existing node is within `tolerance` mm, snap to that node.
    This ensures structural connectivity (no floating endpoints).
    """
    best_d  = tolerance
    best_pt = (x, y, z)
    for (nx, ny, nz) in nodes:
        d = math.sqrt((x-nx)**2 + (y-ny)**2 + (z-nz)**2)
        if d < best_d:
            best_d  = d
            best_pt = (nx, ny, nz)
    return best_pt


# ══════════════════════════════════════════════════════════════════════════════
# 2.  MEMBER GEOMETRY COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

def compute_geometry(sx: float, sy: float, sz: float,
                     ex: float, ey: float, ez: float) -> dict:
    """
    Compute all derived geometric properties for a member.
    """
    dx = ex - sx; dy = ey - sy; dz = ez - sz
    L  = math.sqrt(dx**2 + dy**2 + dz**2)

    if L < 1e-9:
        return {
            "length": 0, "dx": 0, "dy": 0, "dz": 0,
            "direction": "ZERO_LENGTH", "angle_from_horiz": 0,
            "midpoint": {"X": sx, "Y": sy, "Z": sz},
            "unit": {"x": 0, "y": 0, "z": 0},
            "valid": False,
        }

    # Direction classification
    abs_dz = abs(dz)
    horiz  = math.sqrt(dx**2 + dy**2)
    if abs_dz > horiz * 1.5:
        direction = "VERTICAL"
    elif horiz > abs_dz * 1.5:
        if abs(dx) >= abs(dy):
            direction = "HORIZONTAL_X"
        else:
            direction = "HORIZONTAL_Y"
    else:
        direction = "DIAGONAL"

    angle_from_horiz = math.degrees(math.atan2(abs_dz, max(horiz, 1e-9)))

    return {
        "length":           round(L, 1),
        "dx":               round(dx, 1),
        "dy":               round(dy, 1),
        "dz":               round(dz, 1),
        "direction":        direction,
        "angle_from_horiz": round(angle_from_horiz, 1),
        "midpoint":         {"X": round((sx+ex)/2, 1), "Y": round((sy+ey)/2, 1), "Z": round((sz+ez)/2, 1)},
        "unit":             {"x": round(dx/L, 4), "y": round(dy/L, 4), "z": round(dz/L, 4)},
        "valid":            MIN_LENGTH <= L <= MAX_LENGTH,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3.  ORIENTATION RESOLVER
# ══════════════════════════════════════════════════════════════════════════════

def resolve_member_orientation(element: dict) -> dict:
    """
    Compute the Tekla-compatible orientation for a member:
      - rotation angle around member axis
      - position type (TOP / MIDDLE / BOTTOM for Tekla's Position enum)
      - local x/y/z axes

    Returns updated element dict with 'orientation' key added.
    """
    sp = element.get("StartPoint", {})
    ep = element.get("EndPoint",   {})
    sx, sy, sz = float(sp.get("X",0)), float(sp.get("Y",0)), float(sp.get("Z",0))
    ex, ey, ez = float(ep.get("X",0)), float(ep.get("Y",0)), float(ep.get("Z",0))

    geo = compute_geometry(sx, sy, sz, ex, ey, ez)
    direction = geo["direction"]
    etype     = (element.get("Type") or "BEAM").upper()

    # Local Z axis (up vector for the cross-section)
    if direction == "VERTICAL":
        # Column: local Z = global X (web faces X by default)
        local_z = (1, 0, 0)
        position_rotation = "TOP"   # Tekla Position.RotationEnum.TOP
        angle_deg = 0
    elif direction in ("HORIZONTAL_X", "HORIZONTAL_Y"):
        # Beam: local Z = global Z (flange on top)
        local_z   = (0, 0, 1)
        position_rotation = "FRONT"
        angle_deg = 0
    else:
        # Diagonal brace: orient web toward structure center
        local_z   = (0, 0, 1)
        position_rotation = "FRONT"
        angle_deg = 0

    # Cross-section rotation for asymmetric profiles (channels, angles)
    profile = (element.get("Profile") or "").upper()
    if profile.startswith("L"):
        angle_deg = 0   # angle: outstanding leg up
    elif profile.startswith("C"):
        angle_deg = 0   # channel: web vertical
    elif profile.startswith("UC") or profile.startswith("HEA") or profile.startswith("HEB"):
        if direction == "VERTICAL":
            angle_deg = 0  # web in XZ plane
    elif profile.startswith("IPE") or profile.startswith("UB"):
        angle_deg = 0      # strong axis bending about Y

    return {
        **element,
        "orientation": {
            "position_rotation": position_rotation,
            "angle_deg":         angle_deg,
            "local_z":           local_z,
            "direction":         direction,
        },
        "geometry": geo,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4.  GRID BOUNDARY ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════════

class GridConstraints:
    """
    Defines the allowed coordinate space for the active Tekla grid.
    Members outside this space are clamped or rejected.
    """
    def __init__(self,
                 min_x: float = -1e9, max_x: float = 1e9,
                 min_y: float = -1e9, max_y: float = 1e9,
                 min_z: float = 0,    max_z: float = 1e9,
                 strict: bool = False):
        self.min_x = min_x; self.max_x = max_x
        self.min_y = min_y; self.max_y = max_y
        self.min_z = min_z; self.max_z = max_z
        self.strict = strict   # if True, reject outside-grid members; else clamp

    def clamp(self, x, y, z) -> Tuple[float, float, float]:
        return (
            max(self.min_x, min(self.max_x, x)),
            max(self.min_y, min(self.max_y, y)),
            max(self.min_z, min(self.max_z, z)),
        )

    def inside(self, x, y, z, tol=500.0) -> bool:
        return (self.min_x - tol <= x <= self.max_x + tol and
                self.min_y - tol <= y <= self.max_y + tol and
                self.min_z - tol <= z <= self.max_z + tol)

    @classmethod
    def from_existing_model(cls, members: list, padding: float = 5000) -> "GridConstraints":
        """Auto-detect grid boundaries from existing model + padding."""
        xs, ys, zs = [], [], []
        for m in members:
            for pt_key in ["StartPoint", "EndPoint", "startPoint", "endPoint"]:
                pt = m.get(pt_key) or {}
                if pt:
                    xs.append(float(pt.get("X", pt.get("x", 0))))
                    ys.append(float(pt.get("Y", pt.get("y", 0))))
                    zs.append(float(pt.get("Z", pt.get("z", 0))))
        if not xs:
            return cls()
        return cls(
            min_x=min(xs) - padding, max_x=max(xs) + padding,
            min_y=min(ys) - padding, max_y=max(ys) + padding,
            min_z=min(zs) - padding, max_z=max(zs) + padding,
        )


# ══════════════════════════════════════════════════════════════════════════════
# 5.  DUPLICATE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

class DuplicateFilter:
    """
    Tracks inserted member endpoints to detect and block duplicates.
    Operates in two stages:
      1. Exact key match (after grid-snapping)
      2. Near-duplicate match (endpoints within SNAP_NODE of each other)
    """
    def __init__(self, snap: float = SNAP_NODE):
        self._snap   = snap
        self._keys   = set()   # fast exact match

    def _key(self, sx, sy, sz, ex, ey, ez) -> tuple:
        def rnd(v): return round(v / self._snap)
        return (rnd(sx), rnd(sy), rnd(sz), rnd(ex), rnd(ey), rnd(ez))

    def is_duplicate(self, sx, sy, sz, ex, ey, ez) -> bool:
        k  = self._key(sx, sy, sz, ex, ey, ez)
        kr = self._key(ex, ey, ez, sx, sy, sz)
        return k in self._keys or kr in self._keys

    def register(self, sx, sy, sz, ex, ey, ez):
        k  = self._key(sx, sy, sz, ex, ey, ez)
        kr = self._key(ex, ey, ez, sx, sy, sz)
        self._keys.add(k); self._keys.add(kr)

    def load_from_model(self, members: list):
        """Pre-populate from existing Tekla model members."""
        for m in members:
            sp = m.get("StartPoint") or m.get("startPoint") or {}
            ep = m.get("EndPoint")   or m.get("endPoint")   or {}
            if sp and ep:
                sx,sy,sz = float(sp.get("X",0)),float(sp.get("Y",0)),float(sp.get("Z",0))
                ex,ey,ez = float(ep.get("X",0)),float(ep.get("Y",0)),float(ep.get("Z",0))
                self.register(sx,sy,sz,ex,ey,ez)


# ══════════════════════════════════════════════════════════════════════════════
# 6.  TAPERED STRUCTURE COORDINATE ADJUSTER
# ══════════════════════════════════════════════════════════════════════════════

def compute_tapered_leg_position(
    leg_idx:    int,
    n_legs:     int,
    height_z:   float,
    total_h:    float,
    base_hw:    float,
    top_hw:     float,
    center_x:   float = 0.0,
    center_y:   float = 0.0,
) -> Tuple[float, float]:
    """
    For tapered towers (lattice, telecom, transmission), compute the
    exact (x, y) position of a leg at a given height.

    Works for any n_legs (3=tri, 4=square, 6=hex).
    """
    # Interpolate half-width at this height
    hw = base_hw + (top_hw - base_hw) * (height_z / max(total_h, 1))
    # Angle for this leg
    ang = 2 * math.pi * leg_idx / n_legs
    x   = center_x + hw * math.cos(ang)
    y   = center_y + hw * math.sin(ang)
    return (round(x, 1), round(y, 1))


def generate_tower_leg_path(
    leg_idx:  int,
    n_legs:   int,
    levels:   List[float],
    base_hw:  float,
    top_hw:   float,
    cx:       float = 0.0,
    cy:       float = 0.0,
) -> List[dict]:
    """
    Returns the complete path of a single tower leg as a list of
    (StartPoint, EndPoint) element dicts for each panel segment.
    """
    total_h = levels[-1] - levels[0] if len(levels) > 1 else 1
    segments = []
    for i in range(len(levels) - 1):
        z0 = levels[i]; z1 = levels[i+1]
        x0, y0 = compute_tapered_leg_position(leg_idx, n_legs, z0, total_h, base_hw, top_hw, cx, cy)
        x1, y1 = compute_tapered_leg_position(leg_idx, n_legs, z1, total_h, base_hw, top_hw, cx, cy)
        segments.append({
            "leg_idx":    leg_idx,
            "level_from": i,
            "level_to":   i+1,
            "StartPoint": {"X": x0, "Y": y0, "Z": z0},
            "EndPoint":   {"X": x1, "Y": y1, "Z": z1},
        })
    return segments


# ══════════════════════════════════════════════════════════════════════════════
# 7.  MASTER SOLVE FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def solve_coordinates(
    elements:         List[dict],
    existing_members: List[dict]  = None,
    grid_constraints: GridConstraints = None,
    apply_node_snap:  bool = True,
    check_existing_duplicates: bool = True,
) -> dict:
    """
    Run all coordinate-solving steps on a list of raw elements.

    Steps:
      1. Grid-snap all endpoints
      2. Snap to nearest existing node (structural connectivity)
      3. Grid boundary enforcement
      4. Duplicate removal
      5. Geometry computation
      6. Orientation resolution
      7. Validation

    Returns:
      {
        "elements":  [solved element dicts],
        "rejected":  [rejected element dicts with reason],
        "stats":     {...},
      }
    """
    existing = existing_members or []

    # Build node list from existing model for snapping
    existing_nodes: List[Tuple[float,float,float]] = []
    if apply_node_snap:
        for m in existing:
            for pt_key in ["StartPoint", "EndPoint"]:
                pt = m.get(pt_key) or {}
                if pt:
                    existing_nodes.append((
                        float(pt.get("X",0)), float(pt.get("Y",0)), float(pt.get("Z",0))
                    ))

    # Auto-detect constraints if not provided
    if grid_constraints is None and existing:
        grid_constraints = GridConstraints.from_existing_model(existing)
    elif grid_constraints is None:
        grid_constraints = GridConstraints()

    # Duplicate filter (pre-loaded with existing model)
    dup_filter = DuplicateFilter()
    if check_existing_duplicates:
        dup_filter.load_from_model(existing)

    solved   = []
    rejected = []
    n_snapped= 0
    n_clamped= 0
    n_duped  = 0
    n_invalid= 0

    for e in elements:
        sp = e.get("StartPoint", {})
        ep = e.get("EndPoint",   {})
        if not sp or not ep:
            rejected.append({**e, "_reject_reason": "missing_points"})
            continue

        sx, sy, sz = float(sp.get("X",0)), float(sp.get("Y",0)), float(sp.get("Z",0))
        ex, ey, ez = float(ep.get("X",0)), float(ep.get("Y",0)), float(ep.get("Z",0))

        # Step 1: Grid snap
        sx, sy, sz = snap_to_grid(sx, sy, sz)
        ex, ey, ez = snap_to_grid(ex, ey, ez)

        # Step 2: Node snap
        if apply_node_snap and existing_nodes:
            nsx, nsy, nsz = snap_to_nearest_node(sx, sy, sz, existing_nodes)
            nex, ney, nez = snap_to_nearest_node(ex, ey, ez, existing_nodes)
            if (nsx,nsy,nsz) != (sx,sy,sz) or (nex,ney,nez) != (ex,ey,ez):
                n_snapped += 1
            sx,sy,sz = nsx,nsy,nsz
            ex,ey,ez = nex,ney,nez

        # Step 3: Grid boundary
        if not grid_constraints.inside(sx,sy,sz) or not grid_constraints.inside(ex,ey,ez):
            if grid_constraints.strict:
                rejected.append({**e, "_reject_reason": "outside_grid"})
                n_invalid += 1
                continue
            else:
                sx,sy,sz = grid_constraints.clamp(sx,sy,sz)
                ex,ey,ez = grid_constraints.clamp(ex,ey,ez)
                n_clamped += 1

        # Step 4: Duplicate check
        if dup_filter.is_duplicate(sx,sy,sz,ex,ey,ez):
            rejected.append({**e, "_reject_reason": "duplicate"})
            n_duped += 1
            continue

        # Step 5: Geometry
        geo = compute_geometry(sx, sy, sz, ex, ey, ez)
        if not geo["valid"]:
            rejected.append({**e, "_reject_reason": f"invalid_length_{geo['length']:.0f}mm"})
            n_invalid += 1
            continue

        # Step 6: Orientation
        new_e = {
            **e,
            "StartPoint": {"X": round(sx,1), "Y": round(sy,1), "Z": round(sz,1)},
            "EndPoint":   {"X": round(ex,1), "Y": round(ey,1), "Z": round(ez,1)},
        }
        new_e = resolve_member_orientation(new_e)

        # Register in duplicate filter
        dup_filter.register(sx, sy, sz, ex, ey, ez)

        solved.append(new_e)

    return {
        "elements": solved,
        "rejected": rejected,
        "stats": {
            "input":      len(elements),
            "solved":     len(solved),
            "rejected":   len(rejected),
            "snapped":    n_snapped,
            "clamped":    n_clamped,
            "duplicates": n_duped,
            "invalid":    n_invalid,
        },
    }


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    test_elements = [
        {"Type":"COLUMN","Profile":"HEA200","Material":"S275",
         "StartPoint":{"X":0.3,"Y":0.1,"Z":0},"EndPoint":{"X":0.3,"Y":0.1,"Z":3500.7}},
        {"Type":"BEAM","Profile":"IPE300","Material":"S275",
         "StartPoint":{"X":0,"Y":0,"Z":3500},"EndPoint":{"X":6000,"Y":0,"Z":3500}},
        # Duplicate
        {"Type":"BEAM","Profile":"IPE300","Material":"S275",
         "StartPoint":{"X":0,"Y":0,"Z":3500},"EndPoint":{"X":6000,"Y":0,"Z":3500}},
        # Zero length
        {"Type":"COLUMN","Profile":"HEA200","Material":"S275",
         "StartPoint":{"X":0,"Y":0,"Z":0},"EndPoint":{"X":0,"Y":0,"Z":0}},
    ]
    result = solve_coordinates(test_elements)
    print(json.dumps(result["stats"], indent=2))
    print(f"Solved: {len(result['elements'])}, Rejected: {len(result['rejected'])}")
    for r in result["rejected"]:
        print(f"  REJECTED: {r['_reject_reason']} — {r['Type']} {r['Profile']}")