# structure_detector.py
#
# Stage 0+1: Model Understanding + Structure Type Detection
#
# Uses LLM to parse natural language → structured JSON params.
# All geometry generation happens in grid_engine.py — LLM only does intent parsing.
#
# Supported structures: building, warehouse, shed, portal_frame,
#   tower_lattice, tower_guyed, tower_telecom, tower_transmission,
#   tower_self_supporting, pipe_rack, industrial_frame

import json
import re
import os
from groq import Groq

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """
You are a structural BIM interpreter. Convert the user's description into a JSON object.
Return ONLY valid JSON — no markdown, no explanation, no extra text.

STRUCTURE TYPES (use exactly one):
  building, warehouse, shed, portal_frame,
  tower_lattice, tower_guyed, tower_telecom, tower_transmission, tower_self_supporting,
  pipe_rack, industrial_frame

FIELD RULES:
- All dimensions in millimeters (mm).
- If user says "6m" → 6000, "30m" → 30000, "100ft" → 30480.
- Default material: S355 for towers, S275 for buildings.

SCHEMA BY TYPE:

building / warehouse / shed / portal_frame:
{
  "structure_type": "building",
  "bays_x": 4,         // number of bays in X direction
  "bays_y": 3,         // number of bays in Y direction
  "stories": 2,        // number of floors (1 for single storey)
  "spacing_x": 6000,   // bay spacing in X (mm)
  "spacing_y": 6000,   // bay spacing in Y (mm)
  "story_height": 3500,// floor-to-floor height (mm)
  "has_bracing": true,
  "roof_type": "flat", // "flat" | "gable" | "hip"
  "shape": "rect",     // "rect" | "L" | "U"
  "has_crane_beam": false,
  "has_haunch": false   // for portal_frame only
}

tower_lattice / tower_self_supporting:
{
  "structure_type": "tower_lattice",
  "height": 30000,       // total height (mm)
  "base_width": 4000,    // base footprint width (mm)
  "top_width": 1200,     // top footprint width (mm)
  "panel_height": 3000,  // panel/section height (mm)
  "cross_arms": [        // optional antenna/conductor arms
    {"z": 28000, "length": 3000, "profile": "CHS114x5", "material": "S355"}
  ]
}

tower_telecom:
{
  "structure_type": "tower_telecom",
  "height": 40000,
  "base_width": 3600,
  "top_width": 900,
  "panel_height": 2500,
  "platforms": [
    {"z": 24000, "arm_length": 2000},
    {"z": 32000, "arm_length": 1800},
    {"z": 40000, "arm_length": 1500}
  ]
}

tower_transmission:
{
  "structure_type": "tower_transmission",
  "height": 45000,
  "base_width": 8000,
  "waist_width": 2200,
  "panel_height": 3500,
  "arm_levels": [
    {"z": 20000, "length": 9000, "label": "bottom"},
    {"z": 29000, "length": 8000, "label": "middle"},
    {"z": 38000, "length": 7000, "label": "top"}
  ]
}

tower_guyed:
{
  "structure_type": "tower_guyed",
  "height": 60000,
  "mast_diameter": 900,
  "panel_height": 3000,
  "guy_levels": [20000, 40000, 60000],
  "guy_radius": 20000
}

pipe_rack / industrial_frame:
{
  "structure_type": "pipe_rack",
  "bays_x": 5,
  "stories": 3,
  "spacing_x": 6000,
  "spacing_y": 5000,
  "story_height": 3000
}

RULES:
- If user says "tower" without specifying type, default to tower_lattice.
- If height is mentioned without base_width for towers, estimate base_width = height * 0.12.
- If user says "telecom", "mobile", "cell" → tower_telecom.
- If user says "transmission", "pylon", "electricity", "power line" → tower_transmission.
- If user says "guyed", "radio mast", "broadcast" → tower_guyed.
- If user says "warehouse", "shed", "industrial" → warehouse.
- If user says "portal", "haunch" → portal_frame with has_haunch: true.
- Extract all numbers from the description carefully.
"""


def detect_structure(user_input: str) -> dict:
    """
    Parse natural language description → structured params dict.
    Falls back to rule-based heuristics if LLM fails.
    """
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system",  "content": SYSTEM_PROMPT},
                {"role": "user",    "content": user_input},
            ],
            temperature=0,
            max_tokens=600,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip any accidental markdown
        raw = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()
        params = json.loads(raw)
        return _validate_and_fix(params)

    except json.JSONDecodeError:
        return _rule_based_fallback(user_input)
    except Exception:
        return _rule_based_fallback(user_input)


def _validate_and_fix(p: dict) -> dict:
    """Ensure all required fields have valid values."""
    stype = p.get("structure_type", "building").lower()

    if "tower" in stype:
        p.setdefault("height", 30000)
        p.setdefault("panel_height", max(2500, p["height"] // 12))
        if stype in ("tower_lattice", "tower_self_supporting"):
            p.setdefault("base_width",  p["height"] * 0.13)
            p.setdefault("top_width",   p.get("base_width", 4000) * 0.25)
        if stype == "tower_transmission":
            p.setdefault("base_width",  8000)
            p.setdefault("waist_width", 2200)
        if stype == "tower_guyed":
            p.setdefault("mast_diameter", 900)
            p.setdefault("guy_radius", p["height"] * 0.33)
            h = p["height"]
            p.setdefault("guy_levels", [h*0.33, h*0.66, h])
    else:
        p.setdefault("bays_x",      4)
        p.setdefault("bays_y",      3)
        p.setdefault("stories",     1 if stype in ("warehouse","shed","portal_frame") else 2)
        p.setdefault("spacing_x",   6000)
        p.setdefault("spacing_y",   6000)
        p.setdefault("story_height",3500 if stype == "building" else 5500)
        p.setdefault("has_bracing", True)
        p.setdefault("roof_type",   "gable" if stype in ("warehouse","shed","portal_frame") else "flat")

    return p


def _rule_based_fallback(text: str) -> dict:
    """Keyword-based fallback when LLM fails."""
    t = text.lower()

    # Detect structure type
    if any(w in t for w in ["transmission", "pylon", "power line", "overhead line", "electricity tower"]):
        stype = "tower_transmission"
    elif any(w in t for w in ["telecom", "mobile", "cell tower", "antenna", "5g", "4g"]):
        stype = "tower_telecom"
    elif any(w in t for w in ["guyed", "radio mast", "broadcast", "radio tower"]):
        stype = "tower_guyed"
    elif any(w in t for w in ["lattice tower", "self-supporting", "self supporting"]):
        stype = "tower_lattice"
    elif "tower" in t:
        stype = "tower_lattice"
    elif any(w in t for w in ["warehouse", "industrial shed", "factory"]):
        stype = "warehouse"
    elif any(w in t for w in ["portal", "haunch"]):
        stype = "portal_frame"
    elif any(w in t for w in ["shed", "hangar"]):
        stype = "shed"
    elif any(w in t for w in ["pipe rack", "process", "petrochemical"]):
        stype = "pipe_rack"
    else:
        stype = "building"

    # Extract numbers
    nums = [float(n) for n in re.findall(r"\b\d+(?:\.\d+)?\b", t)]

    if "tower" in stype:
        # Convert to mm if numbers look like meters
        heights = [n*1000 if n < 500 else n for n in nums if 5 <= n <= 600 or 5000 <= n <= 600000]
        height  = heights[0] if heights else 30000
        return _validate_and_fix({"structure_type": stype, "height": height})

    # Building defaults
    big_nums = [n for n in nums if n >= 3]
    return _validate_and_fix({
        "structure_type": stype,
        "bays_x":      int(big_nums[0]) if len(big_nums) > 0 else 4,
        "bays_y":      int(big_nums[1]) if len(big_nums) > 1 else 3,
        "stories":     int(big_nums[2]) if len(big_nums) > 2 else 2,
        "spacing_x":   big_nums[3]*1000 if len(big_nums) > 3 and big_nums[3] < 30 else 6000,
        "story_height":big_nums[4]*1000 if len(big_nums) > 4 and big_nums[4] < 20 else 3500,
    })


# ── CLI test ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        "Create a 4x3 bay building, 2 stories, 6m spacing, 3.5m height",
        "Build a 30m lattice tower, 4m base, 1.2m top, cross arms at 28m",
        "Telecom tower 40 meters with 3 platforms",
        "132kV transmission pylon 45 meters high",
        "Guyed radio mast 60m with 900mm tube",
        "5 bay warehouse with crane beam 18m span 7m height",
        "Portal frame shed with haunch 12m span 5 bays",
    ]
    for t in tests:
        p = detect_structure(t)
        print(f"\nInput: {t}")
        print(f"Parsed: {json.dumps(p, indent=2)}")