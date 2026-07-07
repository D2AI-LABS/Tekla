# structure_detector.py — Universal natural-language → structure params
import re

# Keywords that mean "generate a full new structure" (not additive edit)
STRUCTURE_TYPE_KEYWORDS = {
    "tower_hexagonal":    ["hexagonal tower", "hex tower", "hexagon tower", "hexagonal", "6 sided", "6-sided"],
    "tower_transmission": ["transmission", "pylon", "power line", "132kv", "220kv", "hv tower"],
    "tower_lattice":      ["lattice", "4 leg", "4-leg", "4leg", "square tower", "telecom lattice"],
    "tower_telecom":      ["telecom", "mobile", "cell tower", "5g"],
    "tower_guyed":        ["guyed", "radio mast", "broadcast mast"],
    "pipe_rack":          ["pipe rack", "pipe support", "process rack", "pipe bridge"],
    "industrial_frame":   ["industrial frame", "process platform", "equipment support", "industrial plant"],
    "portal_frame":       ["portal frame", "portal shed", "shed with", "haunch", "clear span shed"],
    "warehouse":          ["warehouse", "factory", "industrial hall", "storage hall"],
    "bridge":             ["bridge", "viaduct", "overpass", "footbridge"],
    "stadium":            ["stadium", "arena", "sports hall"],
    "substation":         ["substation", "switchyard", "electrical substation"],
    "parking":            ["parking", "car park", "parking structure", "parking garage"],
    "office_building":    ["office building", "office block", "commercial building"],
    "residential":        ["residential", "apartment", "housing", "flat block"],
    "building":           ["building", "multi-storey", "multistorey"],
}

CREATE_PREFIXES = ("create ", "build ", "generate ", "make ", "design ", "add a ", "add an ")

FULL_STRUCTURE_HINTS = (
    "tower", "lattice", "hexagonal", "telecom", "transmission", "pylon",
    "building", "warehouse", "bridge", "stadium", "portal frame", "pipe rack",
    " height ", " panels", " panel height", " base width", " inclined leg",
)


def is_full_structure_create(user_input: str) -> bool:
    """True when prompt specifies a complete new structure (not a small edit)."""
    t = extract_primary_command(user_input).strip().lower()
    if not any(t.startswith(p) for p in CREATE_PREFIXES):
        return False
    return any(h in t for h in FULL_STRUCTURE_HINTS)


def extract_primary_command(user_input: str) -> str:
    """
    When the prompt textarea contains several example lines, use the first
    Create/Build line only — avoids matching 'bridge' from a later example line.
    """
    raw = (user_input or "").strip()
    if not raw:
        return raw
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) <= 1:
        return raw
    for ln in lines:
        low = ln.lower()
        if any(low.startswith(p) for p in CREATE_PREFIXES):
            return ln
    return lines[0]


def _to_mm(value: float) -> float:
    """Prompt numbers under 500 are metres; 500+ are already millimetres."""
    return value * 1000 if value < 500 else value


def _first_mm(t: str, patterns: list, default=None):
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            return _to_mm(float(m.group(1)))
    return default


def _all_nums(t: str) -> list:
    return [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)(?:\s*(?:m|mm|metre|meter|meters|metres))?", t)]


def _parse_bays(t: str) -> tuple:
    m = re.search(r"(\d+)\s*[x×]\s*(\d+)\s*(?:bay|bays)?", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)\s*bays?\s*[x×]\s*(\d+)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)\s*(?:bay|bays)\b", t)
    if m:
        return int(m.group(1)), 1
    return None, None


def _parse_stories(t: str, default: int = 2) -> int:
    for pat in [
        r"(\d+)\s*(?:storey|storeys|story|stories|floor|floors|level|levels)\b",
        r"(?:storey|story|floor)\s*(\d+)",
    ]:
        m = re.search(pat, t)
        if m:
            return max(1, int(m.group(1)))
    return default


def _parse_spacing(t: str, default: float = 6000) -> float:
    v = _first_mm(t, [
        r"(\d+(?:\.\d+)?)\s*m?\s*spacing",
        r"spacing\s*(?:x\s*)?(\d+(?:\.\d+)?)\s*m?",
        r"(\d+(?:\.\d+)?)\s*m?\s*grid",
    ])
    return v if v else default


def _parse_span(t: str, default: float = 12000) -> float:
    v = _first_mm(t, [
        r"(\d+(?:\.\d+)?)\s*m?\s*span",
        r"span\s*(\d+(?:\.\d+)?)\s*m?",
        r"(\d+(?:\.\d+)?)\s*m?\s*wide",
    ])
    return v if v else default


def _parse_height_mm(t: str, default: float = 12000) -> float:
    nums = _all_nums(t)
    heights = [_to_mm(n) for n in nums if 5 <= n <= 600 or 5000 <= n <= 600000]
    if heights:
        return heights[0]
    v = _first_mm(t, [
        r"height\s*(\d+(?:\.\d+)?)\s*m?",
        r"(\d+(?:\.\d+)?)\s*m?\s*high",
        r"(\d+(?:\.\d+)?)\s*m?\s*height",
    ])
    return v if v else default


def _parse_story_height(t: str, default: float = 3500) -> float:
    v = _first_mm(t, [
        r"(\d+(?:\.\d+)?)\s*m?\s*(?:storey|story|floor)\s*height",
        r"(?:storey|story|floor)\s*height\s*(\d+(?:\.\d+)?)\s*m?",
        r"(\d+(?:\.\d+)?)\s*m?\s*per\s*(?:storey|story|floor)",
    ])
    if v:
        return v
    nums = _all_nums(t)
    small = [_to_mm(n) for n in nums if 2.5 <= n <= 8]
    return small[0] if small else default


def _detect_type(t: str) -> str:
    if "tower" in t and not any(w in t for w in ["pipe rack", "building", "warehouse"]):
        for stype, keywords in STRUCTURE_TYPE_KEYWORDS.items():
            if "tower" in stype and any(w in t for w in keywords):
                return stype
        return "tower_lattice"

    for stype, keywords in STRUCTURE_TYPE_KEYWORDS.items():
        if "tower" in stype:
            continue
        if any(w in t for w in keywords):
            return stype

    if any(w in t for w in ["office"]):
        return "office_building"
    if any(w in t for w in ["shed", "canopy"]):
        return "portal_frame"
    if any(w in t for w in ["storey", "story", "bay building"]):
        return "building"
    return "building"


def _parse_wxL(t: str) -> tuple:
    m = re.search(r"(\d+(?:\.\d+)?)\s*m?\s*[x×]\s*(\d+(?:\.\d+)?)\s*m?", t)
    if m:
        return _to_mm(float(m.group(1))), _to_mm(float(m.group(2)))
    return None, None


def _bridge_params(t: str) -> dict:
    span = _parse_span(t, 120000)
    width = 12000
    wx, ly = _parse_wxL(t)
    if wx and ly:
        span, width = max(wx, ly), min(wx, ly)
    n_spans = 1
    m = re.search(r"(\d+)\s*span", t)
    if m:
        n_spans = int(m.group(1))
    h = _parse_height_mm(t, 0)
    deck_h = h if 500 < h < 50000 else 8000
    return {
        "structure_type": "bridge",
        "span": span,
        "width": width,
        "bays_x": n_spans,
        "deck_height": deck_h,
        "has_bracing": True,
    }


def _stadium_params(t: str) -> dict:
    wx, ly = _parse_wxL(t)
    radius = _first_mm(t, [r"radius\s*(\d+(?:\.\d+)?)\s*m?", r"(\d+(?:\.\d+)?)\s*m?\s*radius"], 60000)
    sx = (wx / 8) if wx else 7500
    sy = (ly / 8) if ly else 7500
    return _apply_footprint_dims(t, {
        "structure_type": "stadium",
        "radius": radius or 60000,
        "bays_x": 8,
        "bays_y": 8,
        "spacing_x": sx,
        "spacing_y": sy,
        "story_height": 12000,
        "stories": 1,
        "has_bracing": True,
        "roof_type": "gable",
    }, "building")


def _substation_params(t: str) -> dict:
    return {
        "structure_type": "substation",
        "bays_x": 4,
        "bays_y": 2,
        "rack_levels": 2,
        "spacing_x": 8000,
        "spacing_y": 6000,
        "story_height": 4000,
        "has_bracing": True,
    }


def _parking_params(t: str) -> dict:
    bx, by = _parse_bays(t)
    wx, ly = _parse_wxL(t)
    spacing = 8000
    if wx and ly:
        bx = bx or max(2, int(wx / spacing))
        by = by or max(2, int(ly / spacing))
    return {
        "structure_type": "parking",
        "bays_x": bx or 6,
        "bays_y": by or 4,
        "stories": _parse_stories(t, 1),
        "spacing_x": spacing,
        "spacing_y": spacing,
        "story_height": 3500,
        "has_bracing": "brac" in t,
        "roof_type": "flat",
    }


def _tower_params(t: str, stype: str) -> dict:
    height = _parse_height_mm(t, 30000)

    levels = None
    for m in re.finditer(r"(\d+)\s*(?:level|storey|story|panel)s?", t):
        levels = int(m.group(1))
        break
    platforms = None
    for m in re.finditer(r"(\d+)\s*platform", t):
        platforms = int(m.group(1))

    base_w = _first_mm(t, [
        r"base\s*(?:width\s*)?(\d+(?:\.\d+)?)\s*m?",
        r"(\d+(?:\.\d+)?)\s*m?\s*base",
    ], height * 0.13)
    top_w = _first_mm(t, [
        r"top\s*(?:width\s*)?(\d+(?:\.\d+)?)\s*m?",
        r"(\d+(?:\.\d+)?)\s*m?\s*top",
    ], height * 0.03)
    panel_h = _first_mm(t, [
        r"panel\s*(?:height\s*)?(\d+(?:\.\d+)?)\s*m?",
        r"(\d+(?:\.\d+)?)\s*m?\s*panel",
    ], max(2500, height // (levels or 12)))

    n_panels = levels or platforms or max(1, int(height // panel_h))
    cross_arms = []
    if platforms and stype in ("tower_telecom", "tower_lattice", "tower_hexagonal"):
        step = height / (platforms + 1)
        cross_arms = [{"z": step * (i + 1), "length": base_w * 0.35} for i in range(platforms)]

    platform_z = None
    for pat in [
        r"platform\s*(?:at\s*)?(\d+(?:\.\d+)?)\s*m",
        r"at\s*(\d+(?:\.\d+)?)\s*m?\s*platform",
    ]:
        m = re.search(pat, t)
        if m:
            platform_z = _to_mm(float(m.group(1)))
            break

    platform_heights = []
    every_m = re.search(r"(?:platforms?\s+)?every\s*(\d+(?:\.\d+)?)\s*m", t)
    if every_m and "platform" in t:
        step = _to_mm(float(every_m.group(1)))
        z = step
        while z < height - 1500:
            platform_heights.append(round(z, 1))
            z += step
    elif platforms and stype in ("tower_telecom", "tower_lattice", "tower_hexagonal"):
        step = height / (platforms + 1)
        platform_heights = [round(step * (i + 1), 1) for i in range(platforms)]

    return {
        "structure_type": stype,
        "height":         height,
        "base_width":     base_w,
        "top_width":      top_w,
        "panel_height":   panel_h,
        "levels":         n_panels,
        "cross_arms":     cross_arms,
        "platforms":      platforms or len(platform_heights),
        "platform_heights": platform_heights,
        "platform_z":     platform_z or (platform_heights[0] if platform_heights else height * 0.82),
        "has_platform":   "platform" in t and "no platform" not in t,
        "has_ladder":     "ladder" in t and "no ladder" not in t,
        "has_antenna":    ("antenna" in t or "equipment" in t or "mount" in t) and "no antenna" not in t,
        "has_cable_support": "cable" in t and "no cable" not in t,
        "has_internal_bracing": "internal brac" in t,
        "full_x_bracing": True,
        "has_bracing":    True,
        "tower_features": {
            "ladder":   "ladder" in t and "no ladder" not in t,
            "platform": "platform" in t and "no platform" not in t,
            "antenna":  ("antenna" in t or "equipment" in t or "mount" in t) and "no antenna" not in t,
        },
    }


def _apply_footprint_dims(t: str, p: dict, stype: str) -> dict:
    """Convert '30m x 60m' style prompts into bay counts instead of raw integers."""
    wx, ly = _parse_wxL(t)
    if not wx or not ly:
        return p
    length, width = max(wx, ly), min(wx, ly)
    sx = float(p.get("spacing_x", 7500 if stype in ("warehouse", "portal_frame", "shed") else 6000))
    if stype in ("warehouse", "portal_frame", "shed"):
        span = float(p.get("span", 18000))
        p["bays_x"] = max(2, int(round(length / sx)))
        p["bays_y"] = max(1, int(round(width / span)))
        p["spacing_x"] = sx
        p["spacing_y"] = span
        p["span"] = span
    else:
        sy = float(p.get("spacing_y", sx))
        p["bays_x"] = max(2, int(round(length / sx)))
        p["bays_y"] = max(2, int(round(width / sy)))
        p["spacing_x"] = sx
        p["spacing_y"] = sy
    return p


def _building_params(t: str, stype: str) -> dict:
    bx, by = _parse_bays(t)
    wx, ly = _parse_wxL(t)
    nums = _all_nums(t)
    # Ignore footprint dimensions when inferring bay counts from bare numbers
    if wx and ly:
        nums = [n for n in nums if abs(n - wx / 1000) > 0.01 and abs(n - ly / 1000) > 0.01]
    big = [n for n in nums if n >= 3 and n == int(n)]

    if bx is None:
        bx = int(big[0]) if len(big) > 0 else (6 if stype == "warehouse" else 4)
    if by is None:
        by = int(big[1]) if len(big) > 1 else (1 if stype in ("warehouse", "portal_frame") else 3)

    stories = 1 if stype in ("warehouse", "portal_frame", "shed") else _parse_stories(t, 2)
    spacing_x = _parse_spacing(t, 7500 if stype == "warehouse" else 6000)
    span = _parse_span(t, 18000 if stype == "warehouse" else spacing_x * max(bx, 1))
    story_h = _parse_story_height(t, 7000 if stype == "warehouse" else 3500)

    shape = "rect"
    if any(w in t for w in ["l-shape", "l shape", "l-shaped", "l shaped"]):
        shape = "L"
    elif any(w in t for w in ["u-shape", "u shape", "u-shaped"]):
        shape = "U"

    roof = "flat"
    if stype in ("warehouse", "portal_frame", "shed") or "gable" in t:
        roof = "gable"
    elif "hip" in t:
        roof = "hip"

    rack_levels = None
    m = re.search(r"(\d+)\s*(?:level|rack level|tier)", t)
    if m:
        rack_levels = int(m.group(1))

    return _apply_footprint_dims(t, {
        "structure_type": stype,
        "bays_x":         bx,
        "bays_y":         by,
        "stories":        stories,
        "spacing_x":      spacing_x,
        "spacing_y":      span if stype in ("warehouse", "portal_frame") else _parse_spacing(t, 6000),
        "story_height":   story_h,
        "span":           span,
        "rack_levels":    rack_levels or stories,
        "has_bracing":    "no bracing" not in t and ("brac" in t or stype in ("building", "pipe_rack", "industrial_frame")),
        "has_crane":      "crane" in t,
        "roof_type":      roof,
        "shape":          shape,
    }, stype)


def detect_structure(user_input: str) -> dict:
    """Parse any natural-language BIM prompt into grid_engine params."""
    primary = extract_primary_command(user_input)
    t = primary.lower().strip()
    stype = _detect_type(t)

    if stype == "bridge":
        return _bridge_params(t)
    if stype == "stadium":
        return _stadium_params(t)
    if stype == "substation":
        return _substation_params(t)
    if stype == "parking":
        return _parking_params(t)
    if stype in ("office_building", "residential"):
        p = _building_params(t, "building")
        p["structure_type"] = stype
        return p
    if "tower" in stype:
        return _tower_params(t, stype)
    return _building_params(t, stype)


def is_create_command(user_input: str) -> bool:
    """True when prompt asks for a full new structure (not additive edit)."""
    t = extract_primary_command(user_input).strip().lower()
    if is_modify_command(user_input):
        return False
    if any(t.startswith(p) for p in CREATE_PREFIXES):
        return True
    if any(w in t for w in ["tower", "lattice", "telecom", "pylon", "warehouse", "portal frame",
                             "pipe rack", "bridge", "stadium", "substation", "parking",
                             "storey building", "multi-storey", "office building"]):
        return True
    if "building" in t and any(t.startswith(p) for p in CREATE_PREFIXES):
        return True
    bx, _ = _parse_bays(t)
    if bx and any(w in t for w in ["bay", "storey", "story", "span", "warehouse", "shed"]):
        return True
    return False


def is_modify_command(user_input: str) -> bool:
    """True for additive edits — route to Build Agent, not full structure generator."""
    t = extract_primary_command(user_input).strip().lower()
    if is_full_structure_create(user_input):
        return False
    if any(t.startswith(p) for p in CREATE_PREFIXES):
        return False
    modify_starts = (
        "add ", "complete ", "fill ", "mirror ", "clean ", "clean up",
        "remove ", "fix ", "repair ", "analyse ", "analyze ",
    )
    if any(t.startswith(p) for p in modify_starts):
        return True
    modify_keywords = (
        "purlin", "cross bracing", "missing member", "duplicate",
        "out of grid", "fill gap", "finish model", "add bracing",
        "add beam", "add column", "add roof", "add floor",
        "add ladder", "add platform", "add antenna", "extend height",
        "edit ", "update ", "change ", "modify ", "increase height",
    )
    return any(k in t for k in modify_keywords)


def placement_for_type(structure_type: str) -> str:
    """Default placement — inline anchors to grid origin (A-1), stays inside grid."""
    if structure_type in ("portal_frame", "shed"):
        return "inline"
    if "tower" in structure_type:
        return "inline"
    return "inline"
