# grid_engine.py
#
# Universal BIM Grid Engine
# Generates full 3D coordinate grids for any structure type:
#   - Buildings (multi-storey rectangular / L-shape / U-shape)
#   - Warehouses (single storey, wide-span)
#   - Towers (lattice, guyed, telecom, transmission, self-supporting)
#   - Sheds / portal frames
#   - Industrial frames (pipe racks, process platforms)
#
# Output: list of Element dicts that map directly to generated_model.cs schema.
# Each element has: type, profile, material, start {X,Y,Z}, end {X,Y,Z}
#
# Usage:
#   from grid_engine import generate_grid
#   elements = generate_grid(params)

import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# ── Element schema (matches generated_model.cs) ─────────────────────────────

def make_element(etype: str, profile: str, material: str,
                 sx, sy, sz, ex, ey, ez, name: str = "") -> dict:
    return {
        "Type":      etype,
        "Name":      name or etype,
        "Profile":   profile,
        "Material":  material,
        "StartPoint": {"X": round(sx, 1), "Y": round(sy, 1), "Z": round(sz, 1)},
        "EndPoint":   {"X": round(ex, 1), "Y": round(ey, 1), "Z": round(ez, 1)},
    }


# ── Structure-type registry ──────────────────────────────────────────────────

STRUCTURE_TYPES = [
    "building", "warehouse", "shed", "portal_frame",
    "tower_lattice", "tower_guyed", "tower_telecom",
    "tower_transmission", "tower_self_supporting", "tower_hexagonal",
    "pipe_rack", "industrial_frame",
    "bridge", "stadium", "substation", "parking",
    "office_building", "residential",
]


# ── Profile auto-selector (Ratio Engine — Stage 5) ──────────────────────────

def select_profile(role: str, span_mm: float, height_mm: float,
                   structure_type: str) -> Tuple[str, str]:
    """
    Returns (profile_string, material_grade) based on structural heuristics.
    This is the Ratio Learning Module — keeps LLM out of section selection.
    """
    if "tower" in structure_type:
        # Tekla catalog — standard profiles that insert reliably
        if role == "LEG":
            if height_mm > 60_000:   return ("CHS273.1X6.3", "S355")
            if height_mm > 30_000:   return ("CHS168.3X6.3",  "S355")
            return                          ("CHS114.3X5",   "S355")
        if role == "DIAGONAL":       return ("L60X60X5",  "S275")
        if role == "HORIZONTAL":     return ("L60X60X5",  "S275")
        if role == "REDUNDANT":      return ("L60X60X5",  "S275")
        if role == "CROSS_ARM":      return ("CHS114.3X5", "S355")

    # Standard building / warehouse profiles
    if role == "COLUMN":
        if span_mm > 9_000 or height_mm > 7_000: return ("HEA300", "S355")
        if span_mm > 6_000 or height_mm > 5_000: return ("HEA240", "S355")
        return                                          ("HEA200", "S275")

    if role == "BEAM":
        if span_mm > 12_000: return ("IPE500", "S355")
        if span_mm > 9_000:  return ("IPE400", "S355")
        if span_mm > 6_000:  return ("IPE360", "S355")
        if span_mm > 4_000:  return ("IPE300", "S275")
        return                      ("IPE240", "S275")

    if role == "SECONDARY":
        if span_mm > 6_000:  return ("IPE200", "S235")
        return                      ("L80x80x6","S235")

    if role == "BRACING":
        if span_mm > 8_000:  return ("CHS88x4",  "S275")
        return                      ("L60x60x5", "S235")

    if role == "PURLIN":     return ("C150x65",  "S235")
    if role == "GIRT":       return ("C120x55",  "S235")
    if role == "RAFTER":     return ("IPE300",   "S275")
    if role == "HAUNCH":     return ("IPE360",   "S355")

    return ("IPE300", "S275")


# ════════════════════════════════════════════════════════════════════════════
#  BUILDING / WAREHOUSE / SHED / PORTAL
# ════════════════════════════════════════════════════════════════════════════

def generate_building(p: dict) -> List[dict]:
    """
    Rectangular multi-storey building with full grid.
    Params:
      bays_x, bays_y, stories
      spacing_x, spacing_y  (mm, default 6000)
      story_height           (mm, default 3500)
      has_bracing            (bool, default True)
      roof_type              "flat" | "gable" | "hip"
      shape                  "rect" | "L" | "U"
    """
    bays_x       = int(p.get("bays_x", 4))
    bays_y       = int(p.get("bays_y", 3))
    stories      = int(p.get("stories", 2))
    sx           = float(p.get("spacing_x", 6000))
    sy           = float(p.get("spacing_y", 6000))
    sh           = float(p.get("story_height", 3500))
    has_bracing  = p.get("has_bracing", True)
    roof_type    = p.get("roof_type", "flat")
    shape        = p.get("shape", "rect")

    total_h = sh * stories
    elements = []

    # ── Excluded zones for L/U shapes ────────────────────────
    def excluded(ix, iy):
        if shape == "L":
            # Remove top-right quadrant
            return ix >= bays_x // 2 and iy >= bays_y // 2
        if shape == "U":
            # Remove middle strip in Y
            mid_lo = bays_y // 3
            mid_hi = (2 * bays_y) // 3
            return ix > 0 and ix < bays_x and mid_lo <= iy <= mid_hi
        return False

    # Grid intersection points
    grid = [(ix * sx, iy * sy) for ix in range(bays_x + 1)
                                 for iy in range(bays_y + 1)
                                 if not excluded(ix, iy)]

    col_profile, col_mat = select_profile("COLUMN", max(sx, sy), total_h, "building")
    bm_x_prof,  bm_x_mat = select_profile("BEAM", sx, sh, "building")
    bm_y_prof,  bm_y_mat = select_profile("BEAM", sy, sh, "building")
    br_prof,    br_mat    = select_profile("BRACING", max(sx, sy), sh, "building")

    # ── COLUMNS ──────────────────────────────────────────────
    for (x, y) in grid:
        elements.append(make_element(
            "COLUMN", col_profile, col_mat,
            x, y, 0, x, y, total_h
        ))

    # ── BEAMS per storey ──────────────────────────────────────
    for story in range(stories):
        z = sh * (story + 1)
        ix_vals = sorted(set(int(round(pt[0] / sx)) for pt in grid if sx > 0))
        iy_vals = sorted(set(int(round(pt[1] / sy)) for pt in grid if sy > 0))

        for iy in iy_vals:
            for ix in ix_vals[:-1]:
                x1, x2 = ix * sx, (ix + 1) * sx
                y = iy * sy
                if (x1, y) in set(grid) and (x2, y) in set(grid):
                    elements.append(make_element(
                        "BEAM", bm_x_prof, bm_x_mat,
                        x1, y, z, x2, y, z
                    ))

        for ix in ix_vals:
            for iy in iy_vals[:-1]:
                y1, y2 = iy * sy, (iy + 1) * sy
                x = ix * sx
                if (x, y1) in set(grid) and (x, y2) in set(grid):
                    elements.append(make_element(
                        "BEAM", bm_y_prof, bm_y_mat,
                        x, y1, z, x, y2, z
                    ))

    # ── X-BRACING on all perimeter bays ──────────────────────────
    if has_bracing:
        for story in range(stories):
            z0, z1 = sh * story, sh * (story + 1)
            # Y-fixed faces (south iy=0, north iy=bays_y)
            for iy in [0, bays_y]:
                y = iy * sy
                for xi in range(bays_x):
                    x0, x1 = xi * sx, (xi + 1) * sx
                    if excluded(xi, iy) or excluded(xi + 1, iy):
                        continue
                    if (x0, y) not in set(grid) or (x1, y) not in set(grid):
                        continue
                    elements.append(make_element(
                        "SECONDARY", br_prof, br_mat,
                        x0, y, z0, x1, y, z1
                    ))
                    elements.append(make_element(
                        "SECONDARY", br_prof, br_mat,
                        x1, y, z0, x0, y, z1
                    ))
            # X-fixed faces (west ix=0, east ix=bays_x)
            for ix in [0, bays_x]:
                x = ix * sx
                for yi in range(bays_y):
                    y0, y1 = yi * sy, (yi + 1) * sy
                    if excluded(ix, yi) or excluded(ix, yi + 1):
                        continue
                    if (x, y0) not in set(grid) or (x, y1) not in set(grid):
                        continue
                    elements.append(make_element(
                        "SECONDARY", br_prof, br_mat,
                        x, y0, z0, x, y1, z1
                    ))
                    elements.append(make_element(
                        "SECONDARY", br_prof, br_mat,
                        x, y1, z0, x, y0, z1
                    ))

    # ── ROOF ─────────────────────────────────────────────────
    roof_z = total_h
    if roof_type == "gable":
        ridge_x = (bays_x * sx) / 2
        ridge_z = roof_z + min(sy * bays_y * 0.25, 2500)
        # Rafters from eave to ridge
        for iy in range(bays_y + 1):
            y = iy * sy
            for ix in range(bays_x + 1):
                x = ix * sx
                if not excluded(ix, iy):
                    elements.append(make_element(
                        "SECONDARY", "IPE200", "S235",
                        x, y, roof_z, ridge_x, y, ridge_z
                    ))
        # Ridge beam
        for ix in range(bays_x):
            elements.append(make_element(
                "BEAM", "IPE200", "S235",
                ridge_x, ix * sy, ridge_z, ridge_x, (ix + 1) * sy, ridge_z
            ))
    # Flat roof: beams already generated at top storey level

    return elements


def generate_warehouse(p: dict) -> List[dict]:
    """Wide-span warehouse — single storey, portal frames + purlins + girts."""
    bays_x    = int(p.get("bays_x", 6))
    bays_y    = int(p.get("bays_y", 1))
    sx        = float(p.get("spacing_x", 7500))
    sy        = float(p.get("spacing_y", 18000))
    height    = float(p.get("story_height", 6000))
    has_crane = p.get("has_crane_beam", False)

    elements = []
    col_p, col_m = select_profile("COLUMN", sy, height, "warehouse")
    rft_p, rft_m = select_profile("RAFTER", sy, height, "warehouse")
    pur_p, pur_m = select_profile("PURLIN", sx, height, "warehouse")

    for ix in range(bays_x + 1):
        x = ix * sx
        for iy in range(bays_y + 1):
            y = iy * sy
            # Columns
            elements.append(make_element("COLUMN", col_p, col_m, x, y, 0, x, y, height))

    # Portal rafters (gable roof)
    ridge_z = height + sy * 0.15  # 15% pitch
    for ix in range(bays_x + 1):
        x = ix * sx
        for iy in range(bays_y):
            y0 = iy * sy
            y_mid = y0 + sy / 2
            y1 = y0 + sy
            elements.append(make_element("SECONDARY", rft_p, rft_m, x, y0, height, x, y_mid, ridge_z))
            elements.append(make_element("SECONDARY", rft_p, rft_m, x, y_mid, ridge_z, x, y1, height))

    # Purlins along X (connecting rafters)
    for iy in range(bays_y):
        y_mid = iy * sy + sy / 2
        for ix in range(bays_x):
            elements.append(make_element("SECONDARY", pur_p, pur_m, ix * sx, y_mid, ridge_z, (ix+1)*sx, y_mid, ridge_z))

    # Girts (wall rails)
    girt_p, girt_m = select_profile("GIRT", sx, height, "warehouse")
    mid_z = height / 2
    for iy in range(bays_y + 1):
        y = iy * sy
        for ix in range(bays_x):
            elements.append(make_element("SECONDARY", girt_p, girt_m, ix*sx, y, mid_z, (ix+1)*sx, y, mid_z))

    # Crane beam (optional)
    if has_crane:
        crane_z = height - 800
        for ix in range(bays_x):
            elements.append(make_element("BEAM", "IPE450", "S355", ix*sx, sy*0.25, crane_z, (ix+1)*sx, sy*0.25, crane_z))
            elements.append(make_element("BEAM", "IPE450", "S355", ix*sx, sy*0.75, crane_z, (ix+1)*sx, sy*0.75, crane_z))

    return elements


def generate_portal_frame(p: dict) -> List[dict]:
    """Simple portal frame with or without haunch."""
    span     = float(p.get("spacing_y", 12000))
    height   = float(p.get("story_height", 5500))
    bays_x   = int(p.get("bays_x", 5))
    sx       = float(p.get("spacing_x", 6000))
    haunch   = p.get("has_haunch", True)

    elements = []
    col_p, col_m = select_profile("COLUMN", span, height, "shed")
    rft_p, rft_m = select_profile("RAFTER", span/2, height, "shed")

    for ix in range(bays_x + 1):
        x = ix * sx
        ridge_z = height + span * 0.15
        mid_y   = span / 2

        # Left column
        elements.append(make_element("COLUMN", col_p, col_m, x, 0, 0, x, 0, height))
        # Right column
        elements.append(make_element("COLUMN", col_p, col_m, x, span, 0, x, span, height))

        # Rafters
        if haunch:
            hlen = span * 0.08
            hn_p, hn_m = select_profile("HAUNCH", span, height, "shed")
            # Left haunch segment
            elements.append(make_element("SECONDARY", hn_p, hn_m, x, 0, height, x, hlen, height + span*0.08))
            elements.append(make_element("SECONDARY", rft_p, rft_m, x, hlen, height + span*0.08, x, mid_y, ridge_z))
            # Right haunch segment
            elements.append(make_element("SECONDARY", hn_p, hn_m, x, span, height, x, span-hlen, height + span*0.08))
            elements.append(make_element("SECONDARY", rft_p, rft_m, x, span-hlen, height + span*0.08, x, mid_y, ridge_z))
        else:
            elements.append(make_element("SECONDARY", rft_p, rft_m, x, 0, height, x, mid_y, ridge_z))
            elements.append(make_element("SECONDARY", rft_p, rft_m, x, mid_y, ridge_z, x, span, height))

    # Purlins
    pur_p, pur_m = select_profile("PURLIN", sx, height, "shed")
    ridge_z = height + span * 0.15
    mid_y   = span / 2
    for ix in range(bays_x):
        elements.append(make_element("SECONDARY", pur_p, pur_m, ix*sx, mid_y, ridge_z, (ix+1)*sx, mid_y, ridge_z))

    return elements


# ════════════════════════════════════════════════════════════════════════════
#  TOWER GENERATORS  (NEW — your main request)
# ════════════════════════════════════════════════════════════════════════════

def _tower_panels(leg_coords: List[Tuple[float,float]],
                  heights: List[float],
                  diag_profile: str, diag_mat: str,
                  horiz_profile: str, horiz_mat: str,
                  leg_profile: str, leg_mat: str,
                  cross_arms: List[dict] = None) -> List[dict]:
    """
    Shared panel builder for all tower types.
    leg_coords: list of (x,y) positions for legs (3 or 4 points)
    heights: list of Z levels (panel tops), ascending
    """
    elements = []
    n_legs = len(leg_coords)

    # ── LEG MEMBERS (continuous segments between height levels) ──
    z_levels = [0.0] + heights
    for k in range(len(z_levels) - 1):
        z0, z1 = z_levels[k], z_levels[k + 1]
        # Taper: leg coords may change per panel — for simplicity use same footprint
        for (lx, ly) in leg_coords:
            elements.append(make_element("COLUMN", leg_profile, leg_mat, lx, ly, z0, lx, ly, z1))

    # ── HORIZONTAL MEMBERS (at each level) ───────────────────────
    for z in heights:
        for i in range(n_legs):
            ax, ay = leg_coords[i]
            bx, by = leg_coords[(i + 1) % n_legs]
            elements.append(make_element("SECONDARY", horiz_profile, horiz_mat, ax, ay, z, bx, by, z))

    # ── DIAGONAL MEMBERS (K-brace or X-brace in each face) ───────
    for k in range(len(z_levels) - 1):
        z0, z1 = z_levels[k], z_levels[k + 1]
        zm = (z0 + z1) / 2
        for i in range(n_legs):
            ax, ay = leg_coords[i]
            bx, by = leg_coords[(i + 1) % n_legs]
            # Single diagonal per face per panel (K-brace style)
            elements.append(make_element("SECONDARY", diag_profile, diag_mat, ax, ay, z0, bx, by, z1))
            elements.append(make_element("SECONDARY", diag_profile, diag_mat, bx, by, z0, ax, ay, z1))

    # ── CROSS ARMS ───────────────────────────────────────────────
    if cross_arms:
        for arm in cross_arms:
            z_arm  = arm.get("z", heights[-1])
            length = arm.get("length", 3000)
            for ang in [0, 90, 180, 270]:
                rad = math.radians(ang)
                cx, cy = 0.0, 0.0  # center
                elements.append(make_element(
                    "BEAM", arm.get("profile", "CHS114x5"), arm.get("material", "S355"),
                    cx, cy, z_arm,
                    cx + math.cos(rad) * length, cy + math.sin(rad) * length, z_arm
                ))

    return elements


# ════════════════════════════════════════════════════════════════════════════
#  TOWER ACCESSORIES (ladder, antenna mast, mounting frame)
# ════════════════════════════════════════════════════════════════════════════

def _tower_platform(p: dict, height: float, base_w: float, top_w: float,
                    leg_p: str, leg_m: str, hz_p: str, hz_m: str) -> List[dict]:
    """Equipment platform ring + railing + antenna mounts (telecom reference layout)."""
    elements = []
    platform_z = float(p.get("platform_z", height * 0.82))
    frac = min(0.95, platform_z / height)
    half = (base_w + (top_w - base_w) * frac) / 2
    legs = [(-half, -half), (half, -half), (half, half), (-half, half)]

    for i in range(4):
        ax, ay = legs[i]
        bx, by = legs[(i + 1) % 4]
        elements.append(make_element("BEAM", hz_p, hz_m, ax, ay, platform_z, bx, by, platform_z, name="PLATFORM_BEAM"))

    rail_h = 1200
    for ax, ay in legs:
        elements.append(make_element("SECONDARY", "L50x50x5", "S235", ax, ay, platform_z, ax, ay, platform_z + rail_h))

    arm = half * 0.55
    for i, (ax, ay) in enumerate(legs):
        ox, oy = ax * 0.15, ay * 0.15
        elements.append(make_element(
            "BEAM", "CHS89x4", "S355", ox, oy, platform_z, ax, ay, platform_z, name="PLATFORM_BEAM"
        ))
        if i < 3:
            rad = math.radians(i * 90 + 45)
            elements.append(make_element(
                "BEAM", "CHS76x4", "S355",
                ox, oy, platform_z,
                ox + math.cos(rad) * arm, oy + math.sin(rad) * arm, platform_z,
                name="PLATFORM_BEAM",
            ))
            elements.append(make_element(
                "SECONDARY", "L60x60x5", "S275",
                ox + math.cos(rad) * arm, oy + math.sin(rad) * arm, platform_z,
                ox + math.cos(rad) * arm, oy + math.sin(rad) * arm, platform_z + 1800,
            ))
    return elements


def _tower_ladder(p: dict, height: float, base_w: float) -> List[dict]:
    """Internal ladder — only when has_ladder is True."""
    elements = []
    rung_h = 800
    ladder_x = -min(400, base_w * 0.08)
    rail_p, rail_m = "L50x50x5", "S235"
    rung_p, rung_m = "L40x40x4", "S235"
    elements.append(make_element("SECONDARY", rail_p, rail_m, ladder_x, -150, 0, ladder_x, -150, height))
    elements.append(make_element("SECONDARY", rail_p, rail_m, ladder_x, 150, 0, ladder_x, 150, height))
    z = rung_h
    while z <= height:
        elements.append(make_element("SECONDARY", rung_p, rung_m, ladder_x, -150, z, ladder_x, 150, z))
        z += rung_h
    return elements


def _tower_antenna(p: dict, height: float, base_w: float) -> List[dict]:
    """Top mast + antenna mounting frame — only when has_antenna is True."""
    elements = []
    mast_h = float(p.get("mast_height", 2500))
    elements.append(make_element("COLUMN", "CHS89x4", "S355", 0, 0, height, 0, 0, height + mast_h))
    frame_z = height + mast_h * 0.55
    arm = base_w * 0.22
    for ang in [0, 90, 180, 270]:
        rad = math.radians(ang)
        elements.append(make_element(
            "BEAM", "CHS76x4", "S355",
            0, 0, frame_z,
            math.cos(rad) * arm, math.sin(rad) * arm, frame_z,
            name="ANTENNA_BEAM",
        ))
        elements.append(make_element(
            "SECONDARY", "L60x60x5", "S275",
            math.cos(rad) * arm, math.sin(rad) * arm, frame_z,
            math.cos(rad) * arm, math.sin(rad) * arm, frame_z + 1200,
        ))
    return elements


def _tower_cable_support(p: dict, height: float, base_w: float, mast_h: float) -> List[dict]:
    """Cable support frame below antenna — radial BEAM arms + vertical guides."""
    elements = []
    top_z = height + mast_h * 0.35
    arm = base_w * 0.18
    for ang in [45, 135, 225, 315]:
        rad = math.radians(ang)
        elements.append(make_element(
            "BEAM", "CHS60x4", "S355",
            0, 0, top_z,
            math.cos(rad) * arm, math.sin(rad) * arm, top_z,
            name="CABLE_BEAM",
        ))
        elements.append(make_element(
            "SECONDARY", "L50x50x5", "S235",
            math.cos(rad) * arm, math.sin(rad) * arm, top_z,
            math.cos(rad) * arm, math.sin(rad) * arm, top_z + 800,
            name="CABLE_GUIDE",
        ))
    return elements


def _apply_tower_features(
    elements: List[dict], p: dict, height: float, base_w: float, top_w: float,
    hz_p: str, hz_m: str,
) -> List[dict]:
    """Route optional tower modules based on explicit prompt flags."""
    out = list(elements)
    if p.get("has_platform"):
        heights = p.get("platform_heights") or [float(p.get("platform_z", height * 0.82))]
        for pz in heights:
            pp = dict(p, platform_z=pz)
            out.extend(_tower_platform(pp, height, base_w, top_w, "CHS139x5", "S355", hz_p, hz_m))
    if p.get("has_ladder"):
        out.extend(_tower_ladder(p, height, base_w))
    if p.get("has_antenna"):
        out.extend(_tower_antenna(p, height, base_w))
        if p.get("has_cable_support"):
            mast_h = float(p.get("mast_height", 2500))
            out.extend(_tower_cable_support(p, height, base_w, mast_h))
    return out


def _tower_accessories(p: dict, height: float, base_w: float) -> List[dict]:
    """Legacy wrapper — delegates to explicit feature flags."""
    return _apply_tower_features([], p, height, base_w, p.get("top_width", base_w * 0.3), "L60x60x5", "S275")


def generate_tower_lattice(p: dict) -> List[dict]:
    """
    4-legged square-base lattice tower.
    Params: height, base_width, top_width, panel_height, cross_arms
    """
    height       = float(p.get("height", 30000))
    base_w       = float(p.get("base_width", 4000))
    top_w        = float(p.get("top_width", 1200))
    panel_h      = float(p.get("panel_height", 3000))
    cross_arms   = p.get("cross_arms", [])

    leg_p, leg_m   = select_profile("LEG", base_w, height, "tower_lattice")
    diag_p, diag_m = select_profile("DIAGONAL", base_w, height, "tower_lattice")
    hz_p, hz_m     = select_profile("HORIZONTAL", base_w, height, "tower_lattice")

    n_panels = max(1, int(height / panel_h))
    heights  = [panel_h * (k + 1) for k in range(n_panels)]

    elements = []
    z_levels = [0.0] + heights

    def leg_corners(half: float):
        return [(-half, -half), (half, -half), (half, half), (-half, half)]

    # Base ring at ground level
    half_base = base_w / 2
    base_legs = leg_corners(half_base)
    for i in range(4):
        ax, ay = base_legs[i]
        bx, by = base_legs[(i + 1) % 4]
        elements.append(make_element("SECONDARY", hz_p, hz_m, ax, ay, 0, bx, by, 0))

    # Tapered leg segments + zig-zag bracing per bay
    for k in range(len(z_levels) - 1):
        z0, z1   = z_levels[k], z_levels[k + 1]
        frac0    = z0 / height
        frac1    = z1 / height
        half0    = (base_w + (top_w - base_w) * frac0) / 2
        half1    = (base_w + (top_w - base_w) * frac1) / 2

        legs0 = leg_corners(half0)
        legs1 = leg_corners(half1)

        for (ax, ay), (bx, by) in zip(legs0, legs1):
            elements.append(make_element("COLUMN", leg_p, leg_m, ax, ay, z0, bx, by, z1))

        for i in range(4):
            ax, ay = legs1[i]
            bx, by = legs1[(i + 1) % 4]
            elements.append(make_element("SECONDARY", hz_p, hz_m, ax, ay, z1, bx, by, z1))

        # Zig-zag X-bracing — alternates direction each bay (reference image pattern)
        for i in range(4):
            if k % 2 == 0:
                ax, ay = legs0[i]
                bx, by = legs1[(i + 1) % 4]
            else:
                ax, ay = legs0[(i + 1) % 4]
                bx, by = legs1[i]
            elements.append(make_element("SECONDARY", diag_p, diag_m, ax, ay, z0, bx, by, z1))

    # Cross arms (optional extra mounts from platform count in prompt)
    for arm in cross_arms:
        z_arm  = float(arm.get("z", height))
        length = float(arm.get("length", 3000))
        arm_p  = arm.get("profile", "CHS114x5")
        arm_m  = arm.get("material", "S355")
        for ang in [0, 90, 180, 270]:
            rad = math.radians(ang)
            elements.append(make_element(
                "BEAM", arm_p, arm_m,
                0, 0, z_arm,
                math.cos(rad)*length, math.sin(rad)*length, z_arm
            ))

    return _apply_tower_features(elements, p, height, base_w, top_w, hz_p, hz_m)


def generate_tower_telecom(p: dict) -> List[dict]:
    """
    Telecom monopole / triangular self-supporting tower.
    3 legs, multiple platform levels with cross arms.
    """
    height     = float(p.get("height", 40000))
    base_w     = float(p.get("base_width", 3600))
    top_w      = float(p.get("top_width", 900))
    panel_h    = float(p.get("panel_height", 2500))
    platforms  = p.get("platforms", [{"z": height * 0.6}, {"z": height * 0.8}, {"z": height}])

    leg_p, leg_m   = select_profile("LEG", base_w, height, "tower_telecom")
    diag_p, diag_m = select_profile("DIAGONAL", base_w, height, "tower_telecom")
    hz_p, hz_m     = select_profile("HORIZONTAL", base_w, height, "tower_telecom")

    n_panels = max(1, int(height / panel_h))
    z_levels = [0.0] + [panel_h * (k+1) for k in range(n_panels)]

    elements = []

    def tri_legs(half_w):
        """Equilateral triangle vertices."""
        return [
            (0, half_w * 2 / math.sqrt(3)),
            (-half_w, -half_w / math.sqrt(3)),
            (half_w,  -half_w / math.sqrt(3)),
        ]

    for k in range(len(z_levels) - 1):
        z0, z1 = z_levels[k], z_levels[k+1]
        f0 = z0 / height
        f1 = z1 / height
        hw0 = (base_w + (top_w - base_w) * f0) / 2
        hw1 = (base_w + (top_w - base_w) * f1) / 2

        l0 = tri_legs(hw0)
        l1 = tri_legs(hw1)

        for (ax,ay),(bx,by) in zip(l0, l1):
            elements.append(make_element("COLUMN", leg_p, leg_m, ax, ay, z0, bx, by, z1))

        for i in range(3):
            ax,ay = l1[i]
            bx,by = l1[(i+1)%3]
            elements.append(make_element("SECONDARY", hz_p, hz_m, ax, ay, z1, bx, by, z1))

        for i in range(3):
            ax,ay = l0[i]
            bx,by = l1[(i+1)%3]
            elements.append(make_element("SECONDARY", diag_p, diag_m, ax, ay, z0, bx, by, z1))

    # Platform cross arms
    arm_p, arm_m = select_profile("CROSS_ARM", base_w, height, "tower_telecom")
    for plt in platforms:
        z_arm  = float(plt.get("z", height))
        length = float(plt.get("arm_length", 2000))
        for ang in [0, 120, 240]:
            rad = math.radians(ang)
            elements.append(make_element(
                "BEAM", arm_p, arm_m,
                0, 0, z_arm,
                math.cos(rad)*length, math.sin(rad)*length, z_arm
            ))

    elements.extend(_apply_tower_features(elements, p, height, base_w, top_w, hz_p, hz_m))
    return elements


def generate_tower_transmission(p: dict) -> List[dict]:
    """
    High-voltage transmission tower (double-circuit, X-frame or cat's cradle).
    4 main legs, K-bracing, multiple cross-arm levels.
    """
    height      = float(p.get("height", 45000))
    base_w      = float(p.get("base_width", 8000))
    waist_w     = float(p.get("waist_width", 2200))
    panel_h     = float(p.get("panel_height", 3500))
    arm_levels  = p.get("arm_levels", [
        {"z": height * 0.45, "length": 9000, "label": "bottom"},
        {"z": height * 0.65, "length": 8000, "label": "middle"},
        {"z": height * 0.85, "length": 7000, "label": "top"},
    ])

    leg_p, leg_m   = select_profile("LEG", waist_w, height, "tower_transmission")
    diag_p, diag_m = select_profile("DIAGONAL", base_w, height, "tower_transmission")
    hz_p, hz_m     = select_profile("HORIZONTAL", base_w, height, "tower_transmission")
    arm_p, arm_m   = select_profile("CROSS_ARM", base_w, height, "tower_transmission")

    n_panels = max(1, int(height / panel_h))
    z_levels = [0.0] + [panel_h*(k+1) for k in range(n_panels)]
    elements = []

    waist_z = height * 0.35  # where tower narrows

    for k in range(len(z_levels) - 1):
        z0, z1 = z_levels[k], z_levels[k+1]

        def half_w_at(z):
            if z <= waist_z:
                return base_w/2 + (waist_w/2 - base_w/2) * (z / waist_z)
            else:
                return waist_w/2

        hw0 = half_w_at(z0)
        hw1 = half_w_at(z1)

        legs0 = [(-hw0,-hw0),(hw0,-hw0),(hw0,hw0),(-hw0,hw0)]
        legs1 = [(-hw1,-hw1),(hw1,-hw1),(hw1,hw1),(-hw1,hw1)]

        for (ax,ay),(bx,by) in zip(legs0, legs1):
            elements.append(make_element("COLUMN", leg_p, leg_m, ax, ay, z0, bx, by, z1))

        for i in range(4):
            ax,ay = legs1[i]; bx,by = legs1[(i+1)%4]
            elements.append(make_element("SECONDARY", hz_p, hz_m, ax, ay, z1, bx, by, z1))

        # K-brace (mid-point bracing)
        zm = (z0+z1)/2
        for i in range(4):
            ax,ay = legs0[i]; bx,by = legs0[(i+1)%4]
            mid_top_x = (legs1[i][0]+legs1[(i+1)%4][0])/2
            mid_top_y = (legs1[i][1]+legs1[(i+1)%4][1])/2
            elements.append(make_element("SECONDARY", diag_p, diag_m, ax, ay, z0, mid_top_x, mid_top_y, z1))
            elements.append(make_element("SECONDARY", diag_p, diag_m, bx, by, z0, mid_top_x, mid_top_y, z1))

    # Cross arms (conductor attachment)
    for arm in arm_levels:
        z_arm  = float(arm["z"])
        length = float(arm["length"])
        for sign in [-1, 1]:
            elements.append(make_element("BEAM", arm_p, arm_m, 0, 0, z_arm, sign*length, 0, z_arm))
            # Earth wire arm
        elements.append(make_element("BEAM", "L60x60x5", "S275", 0, 0, z_arm, 0, length*0.6, z_arm+2000))

    elements.extend(_apply_tower_features(elements, p, height, base_w, base_w * 0.3, hz_p, hz_m))
    return elements


def generate_tower_guyed(p: dict) -> List[dict]:
    """
    Guyed mast: single central tube/lattice with guy cables at intervals.
    (Cables represented as SECONDARY members — Tekla will treat as rods.)
    """
    height    = float(p.get("height", 60000))
    mast_d    = float(p.get("mast_diameter", 900))
    guy_levels = p.get("guy_levels", [height*0.33, height*0.66, height])
    guy_radius = float(p.get("guy_radius", 20000))  # anchor radius
    panel_h   = float(p.get("panel_height", 3000))

    mast_prof = f"CHS{int(mast_d)}x{max(8, int(mast_d/80))}"
    elements  = []

    # Mast sections
    n = max(1, int(height / panel_h))
    for k in range(n):
        z0 = k * panel_h
        z1 = min((k+1)*panel_h, height)
        elements.append(make_element("COLUMN", mast_prof, "S355", 0, 0, z0, 0, 0, z1))

    # Guy cables (3 per level, 120° apart)
    for z_guy in guy_levels:
        for ang in [0, 120, 240]:
            rad = math.radians(ang)
            ax = math.cos(rad) * guy_radius
            ay = math.sin(rad) * guy_radius
            elements.append(make_element(
                "SECONDARY", "CHS26x3", "S355",
                0, 0, z_guy, ax, ay, 0
            ))

    return elements


def generate_tower_hexagonal(p: dict) -> List[dict]:
    """
    Deterministic hexagonal telecom tower builder.
    6 inclined legs, ring horizontals every panel, full X-bracing per face,
    optional internal bracing, then accessories (platforms, ladder, mast).
    """
    height    = float(p.get("height", 24000))
    base_w    = float(p.get("base_width", 3600))
    top_w     = float(p.get("top_width", 900))
    panel_h   = float(p.get("panel_height", 3000))
    levels_n  = int(p.get("levels", max(1, int(height / panel_h))))
    full_x    = p.get("full_x_bracing", True)
    internal  = p.get("has_internal_bracing", False)

    leg_p, leg_m   = select_profile("LEG", base_w, height, "tower_lattice")
    diag_p, diag_m = select_profile("DIAGONAL", base_w, height, "tower_lattice")
    hz_p, hz_m     = select_profile("HORIZONTAL", base_w, height, "tower_lattice")

    n_panels = max(1, levels_n)
    z_levels = [0.0] + [panel_h * (k + 1) for k in range(n_panels)]
    z_levels[-1] = min(z_levels[-1], height)
    elements = []

    def hex_legs(half_w: float):
        return [
            (half_w * math.cos(math.radians(60 * i - 30)),
             half_w * math.sin(math.radians(60 * i - 30)))
            for i in range(6)
        ]

    def half_w_at_z(z: float) -> float:
        f = min(1.0, max(0.0, z / height))
        return (base_w + (top_w - base_w) * f) / 2

    # Base ring + toe plate posts
    base_legs = hex_legs(base_w / 2)
    for i in range(6):
        ax, ay = base_legs[i]
        bx, by = base_legs[(i + 1) % 6]
        elements.append(make_element("SECONDARY", hz_p, hz_m, ax, ay, 0, bx, by, 0, name="BRACE"))
        elements.append(make_element("SECONDARY", "L50x50x5", "S235", ax, ay, 0, ax, ay, 150, name="BRACE"))

    for k in range(len(z_levels) - 1):
        z0, z1 = z_levels[k], z_levels[k + 1]
        hw0, hw1 = half_w_at_z(z0), half_w_at_z(z1)
        legs0 = hex_legs(hw0)
        legs1 = hex_legs(hw1)

        # 6 inclined main legs per panel
        for (ax, ay), (bx, by) in zip(legs0, legs1):
            elements.append(make_element("COLUMN", leg_p, leg_m, ax, ay, z0, bx, by, z1, name="COLUMN"))

        # 6 horizontal ring members + face X-bracing
        for i in range(6):
            ax, ay = legs1[i]
            bx, by = legs1[(i + 1) % 6]
            elements.append(make_element("SECONDARY", hz_p, hz_m, ax, ay, z1, bx, by, z1, name="BRACE"))

            ax0, ay0 = legs0[i]
            bx0, by0 = legs0[(i + 1) % 6]
            ax1, ay1 = legs1[i]
            bx1, by1 = legs1[(i + 1) % 6]

            # Primary diagonal (alternating direction per panel)
            if k % 2 == 0:
                elements.append(make_element("SECONDARY", diag_p, diag_m, ax0, ay0, z0, bx1, by1, z1, name="BRACE"))
            else:
                elements.append(make_element("SECONDARY", diag_p, diag_m, bx0, by0, z0, ax1, ay1, z1, name="BRACE"))

            # Second diagonal for full X-brace on every face
            if full_x:
                if k % 2 == 0:
                    elements.append(make_element("SECONDARY", diag_p, diag_m, bx0, by0, z0, ax1, ay1, z1, name="BRACE"))
                else:
                    elements.append(make_element("SECONDARY", diag_p, diag_m, ax0, ay0, z0, bx1, by1, z1, name="BRACE"))

        # Internal bracing — cross from tower centre to mid-panel on each leg
        if internal:
            cx, cy = 0.0, 0.0
            mid_z = (z0 + z1) / 2
            hw_mid = half_w_at_z(mid_z)
            for i in range(6):
                lx, ly = hex_legs(hw_mid)[i]
                elements.append(make_element("SECONDARY", diag_p, diag_m, cx, cy, mid_z, lx, ly, mid_z, name="BRACE"))
                opp = (i + 3) % 6
                ox, oy = hex_legs(hw_mid)[opp]
                elements.append(make_element("SECONDARY", diag_p, diag_m, lx, ly, mid_z, ox, oy, mid_z, name="BRACE"))

    # Crown ring at top
    half_top = top_w / 2
    crown = [(-half_top, -half_top), (half_top, -half_top), (half_top, half_top), (-half_top, half_top)]
    for i in range(4):
        ax, ay = crown[i]
        bx, by = crown[(i + 1) % 4]
        elements.append(make_element("SECONDARY", hz_p, hz_m, ax, ay, height, bx, by, height, name="BRACE"))

    return _apply_tower_features(elements, p, height, base_w, top_w, hz_p, hz_m)


def generate_tower_self_supporting(p: dict) -> List[dict]:
    """
    3-legged self-supporting tower (common for medium telecom).
    Slightly different geometry from lattice — equilateral triangle cross-section.
    """
    return generate_tower_telecom(p)


# ════════════════════════════════════════════════════════════════════════════
#  BRIDGE / STADIUM / SUBSTATION / PARKING
# ════════════════════════════════════════════════════════════════════════════

def generate_bridge(p: dict) -> List[dict]:
    """Simple girder bridge — supports, deck girders, cross bracing."""
    span = float(p.get("span", 120000))
    width = float(p.get("width", 12000))
    n_spans = int(p.get("bays_x", 1))
    deck_z = float(p.get("deck_height", 8000))
    span_each = span / max(1, n_spans)

    col_p, col_m = select_profile("COLUMN", span_each, deck_z, "building")
    bm_p, bm_m = select_profile("BEAM", span_each, deck_z, "building")
    br_p, br_m = select_profile("BRACING", width, deck_z, "building")
    elements = []

    for i in range(n_spans + 1):
        x = i * span_each
        for y in [0, width]:
            elements.append(make_element("COLUMN", col_p, col_m, x, y, 0, x, y, deck_z))

    for i in range(n_spans):
        x0, x1 = i * span_each, (i + 1) * span_each
        for y in [0, width]:
            elements.append(make_element("BEAM", bm_p, bm_m, x0, y, deck_z, x1, y, deck_z))
        for y in [0, width]:
            elements.append(make_element("SECONDARY", br_p, br_m, x0, 0, deck_z * 0.5, x1, width, deck_z * 0.5))

    for y in [0, width]:
        elements.append(make_element("BEAM", bm_p, bm_m, 0, y, deck_z, span, y, deck_z))

    return elements


def generate_stadium(p: dict) -> List[dict]:
    """Stadium frame — column ring + radial beams + roof ring (simplified)."""
    p = dict(p)
    p["structure_type"] = "building"
    p["stories"] = 1
    p["bays_x"] = int(p.get("bays_x", 8))
    p["bays_y"] = int(p.get("bays_y", 8))
    p["spacing_x"] = float(p.get("spacing_x", 7500))
    p["spacing_y"] = float(p.get("spacing_y", 7500))
    p["story_height"] = float(p.get("story_height", 12000))
    p["has_bracing"] = True
    base = generate_building(p)
    sx, sy = p["spacing_x"], p["spacing_y"]
    bx, by = p["bays_x"], p["bays_y"]
    roof_z = p["story_height"]
    bm_p, bm_m = select_profile("BEAM", sx * bx, roof_z, "building")
    cx, cy = (bx * sx) / 2, (by * sy) / 2
    for i in range(max(bx, by) + 1):
        ang = math.radians(i * 360 / max(bx, by))
        ex, ey = cx + math.cos(ang) * cx, cy + math.sin(ang) * cy
        base.append(make_element("BEAM", bm_p, bm_m, cx, cy, roof_z, ex, ey, roof_z))
    return base


def generate_substation(p: dict) -> List[dict]:
    """Electrical substation — pipe-rack style equipment supports."""
    p = dict(p)
    p["structure_type"] = "pipe_rack"
    p["bays_x"] = int(p.get("bays_x", 4))
    p["rack_levels"] = int(p.get("rack_levels", 2))
    p["spacing_x"] = float(p.get("spacing_x", 8000))
    p["spacing_y"] = float(p.get("spacing_y", 6000))
    p["story_height"] = float(p.get("story_height", 4000))
    return generate_pipe_rack(p)


def generate_parking(p: dict) -> List[dict]:
    """Open parking structure — columns + floor beams + optional ramp."""
    p = dict(p)
    p["structure_type"] = "building"
    p["has_bracing"] = p.get("has_bracing", False)
    p["roof_type"] = "flat"
    elements = generate_building(p)
    sx = float(p.get("spacing_x", 8000))
    by = int(p.get("bays_y", 4))
    ramp_z = float(p.get("story_height", 3500))
    bm_p, bm_m = select_profile("BEAM", sx * 2, ramp_z, "building")
    elements.append(make_element("BEAM", bm_p, bm_m, 0, 0, 0, sx * 2, by * sx * 0.5, ramp_z))
    return elements


# ════════════════════════════════════════════════════════════════════════════
#  INDUSTRIAL FRAME / PIPE RACK
# ════════════════════════════════════════════════════════════════════════════

def generate_pipe_rack(p: dict) -> List[dict]:
    """Multi-level pipe support rack."""
    bays     = int(p.get("bays_x", 5))
    levels   = int(p.get("rack_levels", p.get("stories", 3)))
    sx       = float(p.get("spacing_x", 6000))
    sy       = float(p.get("spacing_y", 5000))
    lh       = float(p.get("story_height", 3000))

    elements = []
    col_p, col_m = select_profile("COLUMN", sy, lh * levels, "pipe_rack")
    bm_p,  bm_m  = select_profile("BEAM",   sy, lh,          "pipe_rack")
    br_p,  br_m  = select_profile("BRACING", sx, lh,         "pipe_rack")

    # Columns
    for ix in range(bays + 1):
        for side in [0, 1]:
            y = side * sy
            x = ix * sx
            elements.append(make_element("COLUMN", col_p, col_m, x, y, 0, x, y, lh*levels))

    # Beams per level
    for lv in range(levels):
        z = lh * (lv + 1)
        for ix in range(bays + 1):
            x = ix * sx
            elements.append(make_element("BEAM", bm_p, bm_m, x, 0, z, x, sy, z))
        for ix in range(bays):
            for side in [0, 1]:
                y = side * sy
                elements.append(make_element("BEAM", bm_p, bm_m, ix*sx, y, z, (ix+1)*sx, y, z))

    # X-bracing in end bays
    for side in [0, 1]:
        y = side * sy
        for lv in range(levels):
            z0, z1 = lh*lv, lh*(lv+1)
            elements.append(make_element("SECONDARY", br_p, br_m, 0, y, z0, sx, y, z1))
            elements.append(make_element("SECONDARY", br_p, br_m, sx, y, z0, 0, y, z1))

    return elements


# ════════════════════════════════════════════════════════════════════════════
#  MAIN DISPATCHER
# ════════════════════════════════════════════════════════════════════════════

def generate_grid(params: dict) -> List[dict]:
    """
    Main entry point.
    params must contain 'structure_type' + type-specific params.
    Returns list of element dicts ready for generated_model.cs.
    """
    stype = str(params.get("structure_type", "building")).lower().strip()

    dispatch = {
        "building":              generate_building,
        "warehouse":             generate_warehouse,
        "shed":                  generate_portal_frame,
        "portal_frame":          generate_portal_frame,
        "tower_lattice":         generate_tower_lattice,
        "tower_guyed":           generate_tower_guyed,
        "tower_telecom":         generate_tower_telecom,
        "tower_transmission":    generate_tower_transmission,
        "tower_self_supporting": generate_tower_self_supporting,
        "tower_hexagonal":       generate_tower_hexagonal,
        "pipe_rack":             generate_pipe_rack,
        "industrial_frame":      generate_pipe_rack,
        "bridge":                generate_bridge,
        "stadium":               generate_stadium,
        "substation":            generate_substation,
        "parking":               generate_parking,
        "office_building":       generate_building,
        "residential":           generate_building,
    }

    fn = dispatch.get(stype)
    if fn is None:
        raise ValueError(f"Unknown structure_type: '{stype}'. Valid: {list(dispatch.keys())}")

    elements = fn(params)

    # Deduplicate exact-duplicate elements (same type + same coords)
    seen = set()
    unique = []
    for e in elements:
        sp = e["StartPoint"]
        ep = e["EndPoint"]
        key = (e["Type"], e["Profile"],
               sp["X"], sp["Y"], sp["Z"],
               ep["X"], ep["Y"], ep["Z"])
        if key not in seen:
            seen.add(key)
            unique.append(e)

    return unique


# ── CLI quick-test ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    tests = [
        {"structure_type": "building",     "bays_x": 4, "bays_y": 3, "stories": 3, "spacing_x": 6000, "spacing_y": 6000, "story_height": 3500},
        {"structure_type": "warehouse",    "bays_x": 6, "spacing_x": 7500, "spacing_y": 18000, "story_height": 7000},
        {"structure_type": "tower_lattice","height": 30000, "base_width": 4000, "top_width": 1200, "panel_height": 3000, "cross_arms": [{"z": 28000, "length": 3000}]},
        {"structure_type": "tower_telecom","height": 45000, "base_width": 3600, "top_width": 900},
        {"structure_type": "tower_transmission", "height": 45000, "base_width": 8000},
        {"structure_type": "tower_guyed",  "height": 60000, "mast_diameter": 900},
        {"structure_type": "pipe_rack",    "bays_x": 5, "stories": 3, "spacing_x": 6000},
    ]

    for t in tests:
        els = generate_grid(t)
        cols  = sum(1 for e in els if e["Type"] == "COLUMN")
        beams = sum(1 for e in els if e["Type"] == "BEAM")
        sec   = sum(1 for e in els if e["Type"] == "SECONDARY")
        print(f"[{t['structure_type']:25s}] total={len(els):4d}  COL={cols:3d}  BEAM={beams:3d}  SEC={sec:3d}")