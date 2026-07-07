# role_classifier.py
#
# SINGLE SOURCE OF TRUTH for structural-member role classification.
# Imported by: structure_engine.py, graph_builder.py, topology_engine.py
#
# Classification priority (highest → lowest):
#   1. Tekla Class "3" / explicit SECONDARY Type
#   2. Name hints (SECONDARY, BRACE, PURLIN, …)
#   3. Diagonal geometry + L/C bracing profiles
#   4. Horizontal L/C ring members (tower horizontals)
#   5. Small-plate / embed override
#   6. Explicit COLUMN / BEAM Type from BIM export
#   7. Geometry + profile family
#   8. UNKNOWN
#
# Returns: "PRIMARY_COLUMN" | "PRIMARY_BEAM" | "SECONDARY" | "UNKNOWN"

import re


def _get_point(obj: dict, key: str) -> dict:
    return obj.get(key) or obj.get(key.lower()) or {}


def safe_str(x) -> str:
    return str(x or "").upper().strip()


def safe_length_mm(obj: dict):
    raw = obj.get("Length") or obj.get("length")
    if raw is None:
        geo = obj.get("Geometry") or {}
        raw = geo.get("Length") or geo.get("length")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    match = re.search(r"[\d.]+", str(raw))
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _deltas(obj: dict):
    start = _get_point(obj, "StartPoint")
    end   = _get_point(obj, "EndPoint")
    if not start or not end:
        return None
    dx = abs(start.get("X", 0) - end.get("X", 0))
    dy = abs(start.get("Y", 0) - end.get("Y", 0))
    dz = abs(start.get("Z", 0) - end.get("Z", 0))
    if dx == 0 and dy == 0 and dz == 0:
        return None
    return dx, dy, dz


def is_vertical_member(obj: dict):
    d = _deltas(obj)
    if d is None:
        return None
    dx, dy, dz = d
    return dz > dx and dz > dy


def is_diagonal_member(obj: dict) -> bool:
    """True when member has significant vertical AND horizontal run (typical bracing)."""
    d = _deltas(obj)
    if d is None:
        return False
    dx, dy, dz = d
    horiz = max(dx, dy)
    if horiz < 80 or dz < 80:
        return False
    if dz > horiz * 1.35:
        return False
    if horiz > dz * 1.35:
        return False
    return True


def _is_secondary_profile(profile: str) -> bool:
    return any(profile.startswith(p) for p in _SECONDARY_PROFILES)


_COLUMN_PROFILES   = {"PL", "HEB", "HEA", "UC", "CHS", "RHS", "SHS", "W"}
_BEAM_PROFILES     = {"UB", "IPE", "ISA", "IPN", "UBP", "D22"}
_SECONDARY_PROFILES= {"L", "C"}

_STRUCT_MATERIAL_ROOTS = (
    "STEEL", "CONCRETE", "M30", "M25", "M20", "A36", "S275", "S355"
)

_SECONDARY_NAME_HINTS = (
    "SECONDARY", "WALL", "ANCHOR", "SLEEVE", "BRACE", "PURLIN", "GIRT", "LADDER", "RUNG", "RAIL"
)

_PLATE_EMBED_MAX_LENGTH_MM = 300


def classify_role(obj: dict) -> str:
    if not isinstance(obj, dict):
        return "UNKNOWN"

    name     = safe_str(obj.get("Name"))
    profile  = safe_str(obj.get("Profile"))
    material = safe_str(obj.get("Material"))
    type_val = safe_str(obj.get("Type"))
    class_val= safe_str(obj.get("Class"))
    dir_val  = safe_str(obj.get("Direction"))
    length   = safe_length_mm(obj)
    vertical = is_vertical_member(obj)
    diagonal = is_diagonal_member(obj)

    # 0. Tekla Class → role (preserves insert intent on round-trip)
    if class_val == "3":
        return "SECONDARY"
    if class_val == "2":
        return "PRIMARY_BEAM"
    if class_val == "1" and type_val != "SECONDARY":
        if type_val == "BEAM":
            return "PRIMARY_BEAM"
        if type_val in ("COLUMN", "") or "COLUMN" in name:
            return "PRIMARY_COLUMN"

    # 1. Explicit BIM Type from grid_engine (before geometry overrides)
    if type_val == "SECONDARY":
        return "SECONDARY"
    if type_val == "BEAM":
        return "PRIMARY_BEAM"
    if type_val == "COLUMN":
        return "PRIMARY_COLUMN"

    # 2. Name-based SECONDARY override
    if any(h in name for h in _SECONDARY_NAME_HINTS):
        return "SECONDARY"

    # 3. Diagonal bracing geometry + angle profiles (not explicit BEAM/COLUMN)
    if diagonal and _is_secondary_profile(profile):
        return "SECONDARY"

    if dir_val == "DIAGONAL" and type_val not in ("BEAM", "COLUMN"):
        return "SECONDARY"

    # 4. Tower ring horizontals — L/C angles, only when not tagged as primary beam
    if vertical is False and _is_secondary_profile(profile):
        if type_val != "BEAM" and not any(p in profile for p in _BEAM_PROFILES):
            return "SECONDARY"

    # 5. Small-plate / embed override
    if (
        profile.startswith("PL")
        and length is not None
        and length < _PLATE_EMBED_MAX_LENGTH_MM
        and "COLUMN" not in name
    ):
        return "SECONDARY"

    # 7. Geometry indeterminate
    if vertical is None:
        if "COLUMN" in name or "COLUMN" in type_val or "VERTICAL" in dir_val:
            return "PRIMARY_COLUMN"
        if "BEAM" in name or "BEAM" in type_val or "HORIZONTAL" in dir_val:
            return "PRIMARY_BEAM"
        if any(p in profile for p in _COLUMN_PROFILES) and "COLUMN" in name:
            return "PRIMARY_COLUMN"
        if any(p in profile for p in _BEAM_PROFILES):
            return "PRIMARY_BEAM"
        return "UNKNOWN"

    # 8. Geometry says VERTICAL
    if vertical:
        if any(profile.startswith(p) for p in _SECONDARY_PROFILES):
            return "SECONDARY"
        if "BRACE" in name or "BRACING" in name:
            return "SECONDARY"
        if "COLUMN" in name or "COLUMN" in type_val:
            return "PRIMARY_COLUMN"
        if any(p in profile for p in _COLUMN_PROFILES):
            return "PRIMARY_COLUMN"
        if any(root in material for root in _STRUCT_MATERIAL_ROOTS):
            return "PRIMARY_COLUMN"

    # 9. Geometry says HORIZONTAL
    if not vertical:
        if "BEAM" in name or "GIRDER" in name or "BEAM" in type_val:
            return "PRIMARY_BEAM"
        if any(p in profile for p in _BEAM_PROFILES):
            return "PRIMARY_BEAM"

    # 10. Secondary by profile family
    if _is_secondary_profile(profile) and "COLUMN" not in name:
        return "SECONDARY"

    return "UNKNOWN"