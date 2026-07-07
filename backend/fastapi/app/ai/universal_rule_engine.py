# universal_rule_engine.py
#
# Universal Structural Rule Engine
#
# Every structure type has specific rules that determine:
#   - Which members are mandatory
#   - Where they must be placed
#   - What profiles/materials are required
#   - What connectivity is needed
#   - What constitutes a structurally valid model
#
# This engine is the "brain" behind pattern-based generation and validation.
# It does NOT generate coordinates — that is done by grid_engine.py +
# coordinate_solver.py. It defines WHAT must exist and validates whether it does.

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
import math


# ══════════════════════════════════════════════════════════════════════════════
# RULE DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class StructuralRule:
    rule_id:     str
    description: str
    severity:    str        # "error" | "warning" | "info"
    check:       Callable   # fn(members, params) -> list[str] (issues)


@dataclass
class StructureRuleset:
    structure_type: str
    rules:          List[StructuralRule] = field(default_factory=list)

    def validate(self, members: list, params: dict) -> dict:
        issues   = []
        warnings = []
        infos    = []
        passed   = []
        for rule in self.rules:
            msgs = rule.check(members, params)
            for msg in msgs:
                full = f"[{rule.rule_id}] {msg}"
                if rule.severity == "error":   issues.append(full)
                elif rule.severity == "warning":warnings.append(full)
                else:                          infos.append(full)
            if not msgs:
                passed.append(rule.rule_id)

        total = len(self.rules)
        score = round(len(passed) / total * 100) if total else 100
        return {
            "structure_type": self.structure_type,
            "score":          score,
            "rules_checked":  total,
            "passed":         len(passed),
            "errors":         issues,
            "warnings":       warnings,
            "info":           infos,
        }


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _count_type(members, mtype):
    return sum(1 for m in members if (m.get("Type") or m.get("Name","")).upper() == mtype.upper())

def _lengths(members, mtype):
    result = []
    for m in members:
        if (m.get("Type") or m.get("Name","")).upper() != mtype.upper():
            continue
        sp = m.get("StartPoint") or m.get("startPoint") or {}
        ep = m.get("EndPoint")   or m.get("endPoint")   or {}
        if sp and ep:
            dx=float(ep.get("X",0))-float(sp.get("X",0))
            dy=float(ep.get("Y",0))-float(sp.get("Y",0))
            dz=float(ep.get("Z",0))-float(sp.get("Z",0))
            result.append(math.sqrt(dx**2+dy**2+dz**2))
    return result

def _max_z(members):
    zs = []
    for m in members:
        for pk in ["StartPoint","EndPoint","startPoint","endPoint"]:
            pt = m.get(pk) or {}
            if pt: zs.append(float(pt.get("Z", pt.get("z", 0))))
    return max(zs) if zs else 0

def _unique_z_levels(members, snap=100):
    zs = set()
    for m in members:
        for pk in ["StartPoint","EndPoint","startPoint","endPoint"]:
            pt = m.get(pk) or {}
            if pt:
                z = float(pt.get("Z", pt.get("z", 0)))
                zs.add(round(z/snap)*snap)
    return sorted(zs)

def _diag_count(members):
    n = 0
    for m in members:
        sp = m.get("StartPoint") or m.get("startPoint") or {}
        ep = m.get("EndPoint")   or m.get("endPoint")   or {}
        if sp and ep:
            dz=abs(float(ep.get("Z",0))-float(sp.get("Z",0)))
            dx=abs(float(ep.get("X",0))-float(sp.get("X",0)))
            dy=abs(float(ep.get("Y",0))-float(sp.get("Y",0)))
            if dz > 100 and math.sqrt(dx**2+dy**2) > 100:
                n += 1
    return n

def _has_profile_family(members, families):
    for m in members:
        p = (m.get("Profile") or "").upper()
        if any(p.startswith(f.upper()) for f in families):
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# RULE SETS PER STRUCTURE TYPE
# ══════════════════════════════════════════════════════════════════════════════

def _building_rules() -> StructureRuleset:
    rs = StructureRuleset("building")

    rs.rules.append(StructuralRule("BLD-01","Minimum 4 columns required","error",
        lambda m,p: [f"Only {_count_type(m,'COLUMN')} column(s) — need ≥4"] if _count_type(m,"COLUMN") < 4 else []))

    rs.rules.append(StructuralRule("BLD-02","Must have beams at each storey level","error",
        lambda m,p: ["No beams found"] if _count_type(m,"BEAM") == 0 else []))

    rs.rules.append(StructuralRule("BLD-03","Beam:column ratio should be > 1","warning",
        lambda m,p: [f"Low ratio {round(_count_type(m,'BEAM')/_count_type(m,'COLUMN'),2)} — secondary members may be missing"]
        if _count_type(m,"COLUMN") > 0 and _count_type(m,"BEAM")/_count_type(m,"COLUMN") < 1 else []))

    rs.rules.append(StructuralRule("BLD-04","Perimeter bracing required for lateral stability","warning",
        lambda m,p: ["No secondary bracing members found — add X-bracing on perimeter bays"]
        if _count_type(m,"SECONDARY") == 0 and not p.get("moment_frame") else []))

    rs.rules.append(StructuralRule("BLD-05","Column profiles must be HEA/HEB/UC/CHS family","warning",
        lambda m,p: ["Column profiles should be H-section or CHS — check profile selection"]
        if not _has_profile_family([x for x in m if (x.get("Type","")).upper()=="COLUMN"],
                                    ["HEA","HEB","UC","CHS","RHS","W"]) else []))

    rs.rules.append(StructuralRule("BLD-06","Beam profiles must be IPE/UB family","warning",
        lambda m,p: ["Beam profiles should be I-section — check profile selection"]
        if not _has_profile_family([x for x in m if (x.get("Type","")).upper()=="BEAM"],
                                    ["IPE","UB","UBP","W","HEA"]) else []))

    rs.rules.append(StructuralRule("BLD-07","Story height should be 2800-5000mm","info",
        lambda m,p: [f"Unusual story height: {round(p.get('story_height',3500))}mm"]
        if not (2800 <= p.get("story_height", 3500) <= 5000) else []))

    rs.rules.append(StructuralRule("BLD-08","Column height consistency","warning",
        lambda m,p: ["Columns have inconsistent heights — check alignment"]
        if len(set(round(l/500)*500 for l in _lengths(m,"COLUMN"))) > 3 else []))

    return rs


def _warehouse_rules() -> StructureRuleset:
    rs = StructureRuleset("warehouse")

    rs.rules.append(StructuralRule("WH-01","Minimum 4 columns required","error",
        lambda m,p: [f"Only {_count_type(m,'COLUMN')} columns"] if _count_type(m,"COLUMN") < 4 else []))

    rs.rules.append(StructuralRule("WH-02","Must have roof rafters/secondary members","error",
        lambda m,p: ["No secondary roof members (rafters/purlins)"] if _count_type(m,"SECONDARY") == 0 else []))

    rs.rules.append(StructuralRule("WH-03","Span should be 12-30m for portal warehouse","warning",
        lambda m,p: [f"Unusual span: {round(p.get('spacing_y',18000))}mm"]
        if not (8000 <= p.get("spacing_y", 18000) <= 40000) else []))

    rs.rules.append(StructuralRule("WH-04","Girts on perimeter walls recommended","info",
        lambda m,p: ["Consider adding girts (wall rails) for cladding support"]
        if _count_type(m,"SECONDARY") < _count_type(m,"COLUMN") else []))

    rs.rules.append(StructuralRule("WH-05","Eave height 4-12m recommended","warning",
        lambda m,p: [f"Unusual eave height: {round(p.get('story_height',6000))}mm"]
        if not (3000 <= p.get("story_height", 6000) <= 15000) else []))

    return rs


def _tower_lattice_rules() -> StructureRuleset:
    rs = StructureRuleset("tower_lattice")

    rs.rules.append(StructuralRule("TWR-01","Must have ≥4 leg members (one per face corner)","error",
        lambda m,p: [f"Only {_count_type(m,'COLUMN')} legs — 4-leg tower needs ≥ n_panels×4"]
        if _count_type(m,"COLUMN") < 4 else []))

    rs.rules.append(StructuralRule("TWR-02","Must have diagonal bracing in every panel","error",
        lambda m,p: ["No diagonal bracing found — tower is unstable without diagonals"]
        if _diag_count(m) == 0 else []))

    rs.rules.append(StructuralRule("TWR-03","Diagonals:legs ratio should be ≥ 2","warning",
        lambda m,p: [f"Low diagonal density: {_diag_count(m)} diagonals for {_count_type(m,'COLUMN')} legs"]
        if _count_type(m,"COLUMN") > 0 and _diag_count(m) < _count_type(m,"COLUMN") * 1.5 else []))

    rs.rules.append(StructuralRule("TWR-04","Horizontals must exist at every panel level","error",
        lambda m,p: ["Missing horizontal members — required at every panel transition"]
        if _count_type(m,"SECONDARY") < len(_unique_z_levels(m)) - 1 else []))

    rs.rules.append(StructuralRule("TWR-05","Tower height should be >5m for lattice type","warning",
        lambda m,p: [f"Low tower height: {round(_max_z(m)/1000,1)}m — lattice towers typically 10-100m"]
        if _max_z(m) < 5000 else []))

    rs.rules.append(StructuralRule("TWR-06","Leg profiles should be CHS/RHS/angle family","warning",
        lambda m,p: ["Leg profiles unexpected — CHS/RHS/L section recommended for tower legs"]
        if not _has_profile_family([x for x in m if (x.get("Type","")).upper()=="COLUMN"],
                                    ["CHS","RHS","SHS","HEA","L","UC"]) else []))

    rs.rules.append(StructuralRule("TWR-07","Taper ratio base:top should be 3:1 to 6:1","info",
        lambda m,p: [f"Check taper ratio: base={round(p.get('base_width',4000))} top={round(p.get('top_width',1200))}"]
        if p.get("base_width") and p.get("top_width") and
           not (2 <= p.get("base_width",4000) / max(p.get("top_width",1200),1) <= 8) else []))

    return rs


def _tower_telecom_rules() -> StructureRuleset:
    rs = StructureRuleset("tower_telecom")

    rs.rules.append(StructuralRule("TEL-01","3-legged or 4-legged tower required","error",
        lambda m,p: [f"Telecom tower has {_count_type(m,'COLUMN')} legs — need 3×panels or 4×panels"]
        if _count_type(m,"COLUMN") < 3 else []))

    rs.rules.append(StructuralRule("TEL-02","Platform cross arms required","warning",
        lambda m,p: ["No cross-arm (platform) beams found — antenna attachment points missing"]
        if _count_type(m,"BEAM") == 0 else []))

    rs.rules.append(StructuralRule("TEL-03","Diagonal bracing required in all panels","error",
        lambda m,p: ["Missing diagonal bracing"]
        if _diag_count(m) < 2 else []))

    rs.rules.append(StructuralRule("TEL-04","Tower height 20-100m for telecom","info",
        lambda m,p: [f"Height {round(_max_z(m)/1000,1)}m is outside typical telecom range (20-100m)"]
        if not (15000 <= _max_z(m) <= 120000) else []))

    return rs


def _tower_transmission_rules() -> StructureRuleset:
    rs = StructureRuleset("tower_transmission")

    rs.rules.append(StructuralRule("TRN-01","4 main legs required","error",
        lambda m,p: [f"Only {_count_type(m,'COLUMN')} legs — transmission tower needs 4"]
        if _count_type(m,"COLUMN") < 4 else []))

    rs.rules.append(StructuralRule("TRN-02","K-bracing or X-bracing required","error",
        lambda m,p: ["No diagonal bracing — transmission tower requires K or X bracing"]
        if _diag_count(m) == 0 else []))

    rs.rules.append(StructuralRule("TRN-03","Cross arms (conductor attachment) required","error",
        lambda m,p: ["No cross arms found — required for conductor attachment"]
        if _count_type(m,"BEAM") < 2 else []))

    rs.rules.append(StructuralRule("TRN-04","Height should be 20-80m","info",
        lambda m,p: [f"Transmission tower height {round(_max_z(m)/1000,1)}m — typical 20-80m"]
        if not (15000 <= _max_z(m) <= 100000) else []))

    rs.rules.append(StructuralRule("TRN-05","Waist narrowing required (catenary shape)","warning",
        lambda m,p: ["No waist detected — transmission towers should narrow at mid-height"]
        if not p.get("waist_width") else []))

    return rs


def _tower_guyed_rules() -> StructureRuleset:
    rs = StructureRuleset("tower_guyed")

    rs.rules.append(StructuralRule("GUY-01","Central mast (column) required","error",
        lambda m,p: ["No central mast member found"] if _count_type(m,"COLUMN") == 0 else []))

    rs.rules.append(StructuralRule("GUY-02","Guy cables required (≥3 per level)","error",
        lambda m,p: [f"Only {_count_type(m,'SECONDARY')} guy cables — need ≥3 per guy level"]
        if _count_type(m,"SECONDARY") < 3 else []))

    rs.rules.append(StructuralRule("GUY-03","Guy anchor radius ≥ 0.3×height","warning",
        lambda m,p: [f"Guy radius {p.get('guy_radius',0)}mm < 0.3×height — increase for stability"]
        if p.get("guy_radius") and p.get("height") and
           p.get("guy_radius") < p.get("height") * 0.3 else []))

    return rs


def _portal_frame_rules() -> StructureRuleset:
    rs = StructureRuleset("portal_frame")

    rs.rules.append(StructuralRule("PF-01","Left and right columns required","error",
        lambda m,p: [f"Only {_count_type(m,'COLUMN')} columns — portal frame needs 2 per bay"]
        if _count_type(m,"COLUMN") < 2 else []))

    rs.rules.append(StructuralRule("PF-02","Rafters required","error",
        lambda m,p: ["No rafter members found"] if _count_type(m,"SECONDARY") == 0 else []))

    rs.rules.append(StructuralRule("PF-03","Haunch recommended for spans >10m","info",
        lambda m,p: ["Consider haunches at eave connections for spans >10m"]
        if p.get("spacing_y", 0) > 10000 and not p.get("has_haunch") else []))

    return rs


def _pipe_rack_rules() -> StructureRuleset:
    rs = StructureRuleset("pipe_rack")

    rs.rules.append(StructuralRule("PR-01","Two rows of columns required","error",
        lambda m,p: [f"Only {_count_type(m,'COLUMN')} columns — pipe rack needs 2 rows"]
        if _count_type(m,"COLUMN") < 4 else []))

    rs.rules.append(StructuralRule("PR-02","Pipe beams (transverse) required at each level","error",
        lambda m,p: ["No pipe support beams found"] if _count_type(m,"BEAM") == 0 else []))

    rs.rules.append(StructuralRule("PR-03","X-bracing in end bays required","warning",
        lambda m,p: ["No end-bay bracing — add X-bracing for wind resistance"]
        if _count_type(m,"SECONDARY") < 2 else []))

    rs.rules.append(StructuralRule("PR-04","Level height should be 1.5-5m","info",
        lambda m,p: [f"Pipe rack level height unusual: {round(p.get('story_height',3000))}mm"]
        if not (1500 <= p.get("story_height", 3000) <= 6000) else []))

    return rs


# ══════════════════════════════════════════════════════════════════════════════
# RULE ENGINE REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

_RULESETS: Dict[str, StructureRuleset] = {
    "building":              _building_rules(),
    "warehouse":             _warehouse_rules(),
    "shed":                  _warehouse_rules(),
    "portal_frame":          _portal_frame_rules(),
    "tower_lattice":         _tower_lattice_rules(),
    "tower_self_supporting": _tower_lattice_rules(),
    "tower_telecom":         _tower_telecom_rules(),
    "tower_transmission":    _tower_transmission_rules(),
    "tower_guyed":           _tower_guyed_rules(),
    "pipe_rack":             _pipe_rack_rules(),
    "industrial_frame":      _pipe_rack_rules(),
}


def get_ruleset(structure_type: str) -> Optional[StructureRuleset]:
    return _RULESETS.get(structure_type.lower())


def validate_structure(members: list, params: dict) -> dict:
    """
    Run the appropriate ruleset for the given structure_type.
    Falls back to building rules if type is unknown.
    """
    stype   = (params.get("structure_type") or "building").lower()
    ruleset = get_ruleset(stype)
    if not ruleset:
        # Generic minimum rules
        issues = []
        cols   = _count_type(members, "COLUMN")
        beams  = _count_type(members, "BEAM")
        if cols < 2:
            issues.append(f"Only {cols} column(s) — structure needs at least 2")
        return {"structure_type": stype, "score": 80 if not issues else 40,
                "rules_checked": 1, "passed": 0 if issues else 1,
                "errors": issues, "warnings": [], "info": []}
    return ruleset.validate(members, params)


def list_structure_types() -> List[str]:
    return list(_RULESETS.keys())


def get_generation_checklist(structure_type: str) -> List[str]:
    """
    Returns an ordered list of what must be created for a given structure type.
    Used by the universal generator to ensure completeness.
    """
    checklists = {
        "building": [
            "Columns at all grid intersections",
            "Primary beams in X direction at each storey",
            "Primary beams in Y direction at each storey",
            "X-bracing on perimeter bays at each storey",
            "Roof beams at top level",
            "Optional: floor slabs, staircases, cladding",
        ],
        "warehouse": [
            "Portal columns (left and right per frame)",
            "Rafters from eave to ridge (left and right slopes)",
            "Ridge beam connecting all frames",
            "Purlins along X direction",
            "Girts on perimeter walls",
            "Optional: crane beam, end-wall bracing",
        ],
        "tower_lattice": [
            "4 tapered legs (from base to apex)",
            "Horizontal members at every panel level",
            "Diagonal bracing in every panel face",
            "Cross arms at antenna levels",
            "Optional: ladder, safety cage, top mast",
        ],
        "tower_telecom": [
            "3 or 4 tapered legs",
            "Horizontal members at every panel level",
            "Diagonal bracing in every panel face",
            "Platform cross arms at each platform level",
            "Top mounting frame",
        ],
        "tower_transmission": [
            "4 legs (tapered with waist)",
            "K-bracing or X-bracing in all panels",
            "Horizontal members at every panel level",
            "Cross arms at each conductor attachment level",
            "Earth wire arm at top",
            "Optional: maintenance platforms",
        ],
        "tower_guyed": [
            "Central mast sections (full height)",
            "Guy cables (≥3 per level, 120° apart)",
            "Guy level horizontal struts",
        ],
        "pipe_rack": [
            "Two rows of columns",
            "Pipe support beams (transverse) at each level",
            "Longitudinal tie beams",
            "X-bracing in end bays",
            "Optional: access platforms, pipe guides",
        ],
        "tower_hexagonal": [
            "6 inclined main legs (tapered base to top)",
            "Horizontal ring members at every panel level",
            "Diagonal X-bracing on each face",
            "Base ring and crown at top",
            "Optional: ladder, platforms, antenna mast, cable support",
        ],
        "bridge": [
            "Support columns at abutments and piers",
            "Deck girders along span",
            "Cross bracing between girders",
            "Longitudinal edge beams",
        ],
        "stadium": [
            "Column grid on rectangular footprint",
            "Radial roof beams from centre",
            "Ring beams at perimeter",
            "Perimeter bracing",
        ],
        "substation": [
            "Equipment support columns",
            "Multi-level pipe-rack style beams",
            "Cross bracing in end bays",
            "Platform for switchgear",
        ],
        "parking": [
            "Columns at grid intersections",
            "Floor beams at each level",
            "Optional ramp beam",
            "Optional bracing on perimeter",
        ],
        "office_building": [
            "Columns at all grid intersections",
            "Primary beams in X and Y at each storey",
            "X-bracing on perimeter bays",
            "Roof beams at top level",
        ],
        "residential": [
            "Columns at all grid intersections",
            "Primary beams at each storey",
            "Perimeter bracing",
            "Roof framing",
        ],
        "portal_frame": [
            "Portal columns (left and right per frame)",
            "Rafters from eave to ridge",
            "Bracing in end bays",
            "Purlins along span",
        ],
        "industrial_frame": [
            "Columns at grid intersections",
            "Primary beams at each level",
            "Equipment support beams",
            "Cross bracing",
        ],
    }
    return checklists.get(structure_type, ["Columns", "Beams", "Bracing"])


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    # Test building validation
    building_members = [
        {"Type":"COLUMN","Profile":"HEA200","Material":"S275",
         "StartPoint":{"X":0,"Y":0,"Z":0},"EndPoint":{"X":0,"Y":0,"Z":3500}},
        {"Type":"COLUMN","Profile":"HEA200","Material":"S275",
         "StartPoint":{"X":6000,"Y":0,"Z":0},"EndPoint":{"X":6000,"Y":0,"Z":3500}},
        {"Type":"COLUMN","Profile":"HEA200","Material":"S275",
         "StartPoint":{"X":0,"Y":6000,"Z":0},"EndPoint":{"X":0,"Y":6000,"Z":3500}},
        {"Type":"COLUMN","Profile":"HEA200","Material":"S275",
         "StartPoint":{"X":6000,"Y":6000,"Z":0},"EndPoint":{"X":6000,"Y":6000,"Z":3500}},
        {"Type":"BEAM","Profile":"IPE300","Material":"S275",
         "StartPoint":{"X":0,"Y":0,"Z":3500},"EndPoint":{"X":6000,"Y":0,"Z":3500}},
        {"Type":"BEAM","Profile":"IPE300","Material":"S275",
         "StartPoint":{"X":0,"Y":6000,"Z":3500},"EndPoint":{"X":6000,"Y":6000,"Z":3500}},
    ]

    print("=== BUILDING VALIDATION ===")
    result = validate_structure(building_members, {"structure_type":"building","story_height":3500})
    print(json.dumps(result, indent=2))

    print("\n=== TOWER CHECKLIST ===")
    for item in get_generation_checklist("tower_lattice"):
        print(f"  ☐ {item}")