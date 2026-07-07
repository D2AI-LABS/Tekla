# main.py  — Universal AI BIM Engine  v5.0
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from typing import Any, List
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq

import json, os, uuid, math, time, csv, hashlib

from app.services.structure_engine      import build_structural_model, classify_role
from app.knowledge_graph.graph_builder         import build_graph
from app.services.connection_engine     import build_connections
from app.services.grid_engine           import generate_grid, STRUCTURE_TYPES
from app.extractor.structure_detector    import detect_structure, placement_for_type, is_create_command, extract_primary_command, is_modify_command
from app.services.placement_engine      import place_structure, detect_active_grid, validate_structure_geometry
from app.ai.completion_engine     import complete_model
from app.ai.topology_engine       import analyse_topology, infer_structure_type
from app.ai.coordinate_solver     import solve_coordinates, GridConstraints
from app.ai.universal_rule_engine import validate_structure, get_generation_checklist, list_structure_types
from app.ai.agent_engine import run_agent_command, infer_agent_placement, AGENT_COMMANDS, cleanup_model
from app.extractor.structure_editor import execute_edit, plan_edit
from app.planner.structure_planner import plan_structure, verify_components, STRUCTURE_TAXONOMY

load_dotenv()
app    = FastAPI(title="Universal AI BIM Engine v5.0")
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON   = os.path.join(BASE_DIR, "output.json")
OUTPUT_CSV    = os.path.join(BASE_DIR, "model_export.csv")
MANIFEST_FILE = os.path.join(BASE_DIR, "extraction_manifest.json")
PENDING_PROMPT= os.path.join(BASE_DIR, "pending_prompt.txt")
ELEMENTS_JSON = os.path.join(BASE_DIR, "bim_elements.json")
CLEAR_TEKLA_JSON = os.path.join(BASE_DIR, "clear_tekla.json")

# ── File helpers ──────────────────────────────────────────────────────────────
def load_json():
    if not os.path.exists(OUTPUT_JSON): return []
    try:
        with open(OUTPUT_JSON, "r", encoding="utf-8-sig") as f: return json.load(f)
    except: return []

PENDING_INSERT= os.path.join(BASE_DIR, "pending_insert.json")

def save_json(data):
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)

def write_tekla(payload, clear_first: bool = False):
    body = dict(payload)
    if clear_first:
        body["ClearModelFirst"] = True
    with open(ELEMENTS_JSON, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
    n = len(body.get("Elements") or [])
    with open(PENDING_INSERT, "w", encoding="utf-8") as f:
        json.dump({"expected": n, "structure_type": body.get("StructureType", ""),
                   "prompt": body.get("Prompt", ""), "started": time.time(),
                   "clear_first": bool(body.get("ClearModelFirst"))}, f)

def request_tekla_clear(reason: str = "manual"):
    """Signal dotnet poller to delete all Tekla members before next insert."""
    with open(CLEAR_TEKLA_JSON, "w", encoding="utf-8") as f:
        json.dump({"reason": reason, "requested": time.time()}, f)
    save_json([])

def _clear_pending_insert():
    if os.path.exists(PENDING_INSERT):
        try: os.remove(PENDING_INSERT)
        except OSError: pass

def _pending_insert() -> dict:
    if not os.path.exists(PENDING_INSERT):
        return {}
    try:
        with open(PENDING_INSERT, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _normalize_tekla_profile(profile: str) -> str:
    """Tekla catalog uses uppercase X — L60x60x5 → L60X60X5."""
    if not profile:
        return "HEA200"
    p = profile.upper().replace("×", "X").replace("*", "X")
    return p


def _normalize_tekla_material(material: str) -> str:
    if not material:
        return "S275"
    m = material.upper().strip()
    if m in ("S235", "S235JR", "S235J0", "S235J2"):
        return "S275"
    return m


def _tekla_status() -> dict:
    """Compare Python output.json with last Tekla extraction manifest."""
    members = load_json()
    manifest = {}
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            pass
    tekla_count = manifest.get("member_count")
    py_count = len(members)
    pending = _pending_insert()
    expected = pending.get("expected")
    sync_pct = None
    if expected and tekla_count is not None and expected > 0:
        sync_pct = round(min(100, tekla_count / expected * 100))
    in_sync = tekla_count is not None and tekla_count == py_count
    if pending and tekla_count is not None and expected:
        in_sync = in_sync and tekla_count >= max(1, int(expected * 0.9))
    return {
        "tekla_member_count": tekla_count,
        "output_json_count": py_count,
        "last_tekla_export": manifest.get("export_timestamp"),
        "model_name": manifest.get("model_name"),
        "in_sync": in_sync,
        "tekla_plugin_required": True,
        "pending_expected": expected,
        "pending_prompt": pending.get("prompt"),
        "sync_pct": sync_pct,
    }


def _hash(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(8192), b""):
            h.update(c)
    return h.hexdigest()

def _csv_count():
    if not os.path.exists(OUTPUT_CSV): return -1
    try:
        with open(OUTPUT_CSV, newline="", encoding="utf-8-sig") as f:
            return max(0, len(list(csv.reader(f))) - 1)
    except: return -1

def _summary(data, structured):
    try: cc = len(build_connections(data)["relationships"])
    except: cc = 0
    return {"total":len(data),"columns":len(structured["PRIMARY_COLUMN"]),
            "beams":len(structured["PRIMARY_BEAM"]),"secondary":len(structured["SECONDARY"]),
            "unknown":len(structured["UNKNOWN"]),"connections":cc}

# ── Request models ────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):   message: str
class AgentRequest(BaseModel):   command: str
class BuildRequest(BaseModel):   prompt: str

class BIMGenerateRequest(BaseModel):
    prompt:        str
    save_to_model: bool  = True
    send_to_tekla: bool  = True
    placement:     str   = "auto"
    rotation_deg:  float = 0.0

class BIMCompleteRequest(BaseModel):
    roof_type:     str  = "flat"
    use_mirror:    bool = True
    mirror_axis:   str  = "X"
    send_to_tekla: bool = True

class BIMAgentRequest(BaseModel):
    command:       str
    send_to_tekla: bool = True

# ── Info routes ───────────────────────────────────────────────────────────────
@app.get("/")
def root(): return {"status":"running","version":"5.0-universal-bim","docs":"/docs"}

@app.get("/health")
def health():
    st = _tekla_status()
    return {"status": "ok", "model_members": st["output_json_count"], **st}


@app.get("/bim/tekla-status")
def bim_tekla_status():
    return _tekla_status()


@app.post("/bim/sync-from-tekla")
def sync_from_tekla():
    """Re-read output.json after Tekla extract — call after 'refresh' in dotnet terminal."""
    st = _tekla_status()
    return {"status": "ok", **st, "members": load_json()}


@app.post("/bim/clear-tekla")
def bim_clear_tekla():
    """Delete all members in Tekla model and reset dashboard cache."""
    request_tekla_clear("api")
    deadline = time.time() + 30
    while time.time() < deadline:
        if not os.path.exists(CLEAR_TEKLA_JSON):
            st = _tekla_status()
            if (st.get("output_json_count") or 0) == 0:
                return {"status": "cleared", **st, "members": []}
        time.sleep(1)
    st = _tekla_status()
    return {"status": "pending", "message": "Clear signal sent — ensure TeklaExtractor.exe is running", **st}


@app.post("/bim/regenerate")
def bim_regenerate(req: BuildRequest):
    """Clear Tekla model then run full generate pipeline (Create Structure shortcut)."""
    prompt = (req.prompt or "create warehouse width 12m depth 12m").strip()
    request_tekla_clear("regenerate")
    deadline = time.time() + 25
    while time.time() < deadline and os.path.exists(CLEAR_TEKLA_JSON):
        time.sleep(1)
    return bim_generate(BIMGenerateRequest(
        prompt=prompt,
        save_to_model=False,
        send_to_tekla=True,
        placement="origin",
    ))

@app.get("/bim/structure-types")
def structure_types():
    return {
        "types": list_structure_types(),
        "taxonomy": STRUCTURE_TAXONOMY,
        "checklists": {t: get_generation_checklist(t) for t in list_structure_types()},
    }


@app.post("/bim/plan")
def bim_plan(req: BuildRequest):
    """Preview structure plan without generating geometry."""
    if not req.prompt.strip():
        raise HTTPException(400, "prompt required")
    return plan_structure(req.prompt.strip())

# ── Upload model (fixes 404) ──────────────────────────────────────────────────
@app.post("/upload-model")
async def upload_model(file: UploadFile = File(...)):
    try:
        content = await file.read()
        data = json.loads(content)
        if not isinstance(data, list):
            raise HTTPException(400, "JSON must be a list of members")
        save_json(data)
        return {"status": "ok", "members": len(data)}
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/upload-model-json")
async def upload_model_json(request: Request):
    """Tekla plugin posts extracted model as JSON array (may be empty [])."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")
    if not isinstance(body, list):
        raise HTTPException(400, "JSON must be a list of members")
    save_json(body)
    st = _tekla_status()
    pending = _pending_insert()
    exp = pending.get("expected")
    if exp and len(body) >= max(1, int(exp * 0.85)):
        _clear_pending_insert()
    st = _tekla_status()
    return {"status": "ok", "members": len(body), "in_sync": st.get("in_sync"), **st}

@app.post("/bim/await-sync")
@app.get("/bim/await-sync")
def bim_await_sync(expected: int = 0, timeout_sec: int = 90):
    """Poll until Tekla extract matches dashboard (same view in both)."""
    deadline = time.time() + min(max(timeout_sec, 10), 180)
    target = expected or _pending_insert().get("expected") or 0
    last = {}
    while time.time() < deadline:
        st = _tekla_status()
        last = st
        tekla = st.get("tekla_member_count") or 0
        if st.get("in_sync") and tekla > 0:
            if not target or tekla >= max(1, int(target * 0.9)):
                return {"status": "synced", **st, "members": load_json()}
        time.sleep(2)
    return {"status": "timeout", "message": "Tekla still not synced — run refresh in dotnet terminal", **last}
@app.post("/bim/generate")
def bim_generate(req: BIMGenerateRequest):
    prompt = req.prompt.strip()
    if not prompt: raise HTTPException(400, "prompt required")
    result = {"workflow":"creation","prompt":prompt,"stages":{}}

    # Stage 0: existing model context
    existing = load_json()
    grid_ctx = detect_active_grid(existing)
    result["stages"]["0_model_understanding"] = {
        "existing_members": len(existing),
        "grid_detected":    grid_ctx.get("has_model", False),
        "grid_extent_x":    grid_ctx.get("width_x", 0),
        "grid_extent_y":    grid_ctx.get("width_y", 0),
    }

    # Stage 1: Structure planner (detect + classify + checklist + knowledge graph)
    prompt = extract_primary_command(prompt)
    result["prompt_used"] = prompt

    # Modify commands belong in Build Agent — not full structure generator
    if is_modify_command(prompt):
        agent_result = run_agent_command(
            prompt, existing, {"send_to_tekla": req.send_to_tekla}
        )
        if agent_result.get("status") == "error":
            raise HTTPException(400, agent_result.get("message", "Use Build Agent tab for this command"))
        if agent_result.get("status") == "cleanup":
            save_json(agent_result.get("members") or [])
            return {
                "workflow": "modify",
                "prompt": prompt,
                "status": "ok",
                "action": "cleanup",
                "generated_count": 0,
                "added": 0,
                "removed": agent_result.get("removed", 0),
                "total_in_model": len(agent_result.get("members") or []),
                "message": agent_result.get("message"),
                "redirected_to": "build_agent",
            }
        elements = agent_result.get("elements") or []
        if not elements:
            raise HTTPException(
                400,
                f"No new members added for: '{prompt}'. "
                "Try Build Agent tab, or use 'Create ...' for a new structure.",
            )
        model_members = _to_members(elements)
        if req.send_to_tekla:
            write_tekla({
                "StructureType": agent_result.get("action", "modify"),
                "Prompt": prompt,
                "Elements": [_bim_el(m) for m in model_members],
            })
        return {
            "workflow": "modify",
            "prompt": prompt,
            "status": "success",
            "redirected_to": "build_agent",
            "action": agent_result.get("action"),
            "generated_count": len(model_members),
            "added": agent_result.get("added", len(model_members)),
            "total_in_model": len(load_json()) if req.send_to_tekla else len(existing) + len(model_members),
            "member_counts": {
                "columns": sum(1 for m in model_members if m.get("Type") == "COLUMN"),
                "beams": sum(1 for m in model_members if m.get("Type") == "BEAM"),
                "secondary": sum(1 for m in model_members if m.get("Type") == "SECONDARY"),
            },
            "sent_to_tekla": req.send_to_tekla and len(model_members) > 0,
            "elements": model_members,
            "message": f"Routed to Build Agent — {agent_result.get('action', 'modify')}",
        }

    try:
        plan = plan_structure(prompt)
        params = plan["params"]
    except Exception as e:
        raise HTTPException(500, f"Stage1 planner: {e}")
    result["stages"]["1_structure_detection"] = {
        "status": "ok",
        "structure_type": plan["structure_type"],
        "structure_label": plan["structure_label"],
        "generator": plan["generator"],
        "params": params,
        "feature_flags": plan["feature_flags"],
        "component_checklist": plan["component_checklist"],
    }
    result["stages"]["1b_knowledge_graph"] = {
        "status": "ok",
        "nodes": len(plan["knowledge_graph"]["nodes"]),
        "edges": len(plan["knowledge_graph"]["edges"]),
        "graph": plan["knowledge_graph"],
        "pipeline_plan": plan["pipeline_plan"],
    }
    result["plan"] = plan

    # Auto placement from planner
    placement_strategy = req.placement
    if req.placement == "auto":
        placement_strategy = plan.get("placement", placement_for_type(params.get("structure_type", "building")))
        if existing and infer_agent_placement(prompt, True) == "append":
            placement_strategy = "append"

    # Stage 2: Grid/geometry generation
    try:
        raw_elements = generate_grid(params)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Stage2: {e}")
    result["stages"]["2_grid_generation"] = {
        "raw_elements": len(raw_elements),
        "columns":      sum(1 for e in raw_elements if e["Type"]=="COLUMN"),
        "beams":        sum(1 for e in raw_elements if e["Type"]=="BEAM"),
        "secondary":    sum(1 for e in raw_elements if e["Type"]=="SECONDARY"),
    }

    # Stage 2b: Pre-placement geometry validation (grid nodes, face-local bracing)
    raw_before = len(raw_elements)
    raw_elements, geo_pre = validate_structure_geometry(raw_elements, params)
    result["stages"]["2b_geometry_validation"] = geo_pre
    if raw_before > 0 and len(raw_elements) == 0:
        raise HTTPException(
            400,
            f"All {raw_before} members rejected in geometry validation: {geo_pre.get('reasons', {})}. "
            f"Check structure footprint for type '{params.get('structure_type')}'.",
        )

    # Stage 3: Full placement pipeline (origin, rotation, dedup, bounds)
    is_create = is_create_command(prompt)
    pl = place_structure(
        raw_elements, params, existing,
        strategy=placement_strategy,
        rotation_deg=req.rotation_deg,
        skip_existing_dedup=is_create,
    )
    elements = pl["elements"]
    origin = pl.get("placement_origin", {"x": 0, "y": 0, "z": 0})
    result["stages"]["3_placement"] = {
        "origin":             [origin.get("x", 0), origin.get("y", 0), origin.get("z", 0)],
        "duplicates_removed": pl.get("n_duplicates_removed", 0),
        "validation":         pl.get("validation", {}),
        "total_placed":       pl.get("total_placed", len(elements)),
        "input_members":      len(raw_elements),
        "grid_context":       {
            "has_model": pl.get("grid_context", {}).get("has_model", False),
            "width_x":   pl.get("grid_context", {}).get("width_x", 0),
            "width_y":   pl.get("grid_context", {}).get("width_y", 0),
        },
    }
    if len(raw_elements) > 0 and len(elements) == 0:
        raise HTTPException(
            400,
            f"Placement removed all {len(raw_elements)} members "
            f"({pl.get('n_duplicates_removed', 0)} duplicates vs existing {len(existing)} members). "
            "For edits use Build Agent (Add purlins / Complete missing). "
            "For new structures use an empty Tekla model or 'Append beside' placement.",
        )

    # Stage 4: Coordinate solver — snap to nodes, enforce footprint bounds
    gc = GridConstraints.from_existing_model(existing, padding=8000) if existing else GridConstraints()
    solved = solve_coordinates(
        elements, existing, gc,
        apply_node_snap=not is_create,
        check_existing_duplicates=not is_create,
    )
    elements = solved["elements"]
    result["stages"]["4_coordinate_solving"] = solved["stats"]

    if len(raw_elements) > 0 and len(elements) == 0:
        raise HTTPException(
            400,
            f"Coordinate solver rejected all {solved['stats'].get('input', 0)} members: "
            f"{solved['stats']}. Try 'World origin' placement or cleanup existing model.",
        )

    # Stage 5: Boundary & measurements
    bounds = _bounds(elements)
    result["stages"]["5_boundary"] = {**bounds, **_measurements(elements, params)}

    # Stage 6: Rule validation
    rule_result = validate_structure(elements, params)
    result["stages"]["6_rule_validation"] = rule_result

    # Stage 7: Topology
    topo = analyse_topology(elements)
    result["stages"]["7_topology"] = {
        "inferred_type":    topo.get("inferred_type",{}),
        "n_panels":         topo.get("n_panels",0),
        "bracing_pattern":  topo.get("bracing",{}).get("dominant","unknown"),
        "symmetry":         topo.get("symmetry",{}).get("type","unknown"),
        "completeness_pct": topo.get("completeness_pct",0),
    }

    # Stage 8: Clash detection
    clashes = _detect_clashes(elements)
    result["stages"]["8_clashes"] = {"count":len(clashes),"sample":clashes[:5]}

    # Stage 9: Convert to model schema
    model_members = _to_members(elements)
    result["stages"]["9_bim_output"] = {"element_count":len(model_members)}

    # Stage 10: Final validation + component checklist
    comp_check = verify_components(elements, plan)
    val = _validate(model_members, params)
    val["component_check"] = comp_check
    result["stages"]["10_validation"] = val

    # Save & send
    max_id = max((m.get("Id",0) for m in existing), default=0)
    for i, m in enumerate(model_members):
        m["Id"]=max_id+i+1; m["Guid"]=str(uuid.uuid4())

    if req.save_to_model and not req.send_to_tekla:
        existing.extend(model_members)
        save_json(existing)
    elif req.send_to_tekla:
        # Dashboard shows Tekla extract only — clear stale cache for new create
        if is_create_command(prompt) and placement_strategy not in ("append", "inline"):
            save_json([])
    elif req.save_to_model:
        existing.extend(model_members)
        save_json(existing)

    if req.send_to_tekla and model_members:
        clear_first = is_create_command(prompt) and placement_strategy not in ("append", "inline")
        write_tekla({"StructureType": params.get("structure_type", "unknown"),
                     "Prompt": prompt, "Elements": [_bim_el(m) for m in model_members]},
                    clear_first=clear_first)

    final_status = "success" if len(model_members) > 0 else "failed"

    mc = {
        "columns":   sum(1 for m in model_members if m.get("Type") == "COLUMN"),
        "beams":     sum(1 for m in model_members if m.get("Type") == "BEAM"),
        "secondary": sum(1 for m in model_members if m.get("Type") == "SECONDARY"),
    }
    result["pipeline"] = _pipeline_summary(result["stages"])
    result.update({
        "elements": model_members,
        "saved": req.save_to_model and not req.send_to_tekla,
        "sent_to_tekla": req.send_to_tekla and len(model_members) > 0,
        "status": final_status,
        "generated_count": len(model_members),
        "total_in_model": len(load_json()) if req.send_to_tekla else len(existing),
        "pending_tekla_insert": req.send_to_tekla,
        "member_counts": mc,
        "tower_features": params.get("tower_features"),
        "plan": {
            "structure_type": plan.get("structure_type"),
            "structure_label": plan.get("structure_label"),
            "generator": plan.get("generator"),
            "feature_flags": plan.get("feature_flags"),
            "component_checklist": plan.get("component_checklist"),
            "component_completeness_pct": comp_check.get("completeness_pct"),
            "component_verification": comp_check,
        },
    })
    return result

# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW 2 — COMPLETION
# ══════════════════════════════════════════════════════════════════════════════
MAX_COMPLETE_INSERT = 400  # per Tekla batch — run Complete again if more gaps remain


def _dedupe_elements(elements: list) -> list:
    seen_keys = set()
    out = []
    for e in elements:
        s, ep = e.get("StartPoint", {}), e.get("EndPoint", {})
        k = (
            round(s.get("X", 0) / 50), round(s.get("Y", 0) / 50), round(s.get("Z", 0) / 50),
            round(ep.get("X", 0) / 50), round(ep.get("Y", 0) / 50), round(ep.get("Z", 0) / 50),
        )
        if k not in seen_keys:
            seen_keys.add(k)
            out.append(e)
    return out


@app.post("/bim/complete")
def bim_complete(req: BIMCompleteRequest):
    existing = load_json()
    if not existing:
        raise HTTPException(400, "No model. Use Create Structure tab first, then Complete.")

    inferred = infer_structure_type(existing)
    itype = inferred.get("type", "building") if isinstance(inferred, dict) else str(inferred)
    is_tower = "tower" in itype

    topo = analyse_topology(existing)
    completion = None if is_tower else complete_model(existing, {
        "roof_type": req.roof_type,
        "use_mirror": req.use_mirror,
        "mirror_axis": req.mirror_axis,
    })

    pattern_missing = topo.get("missing_by_pattern", [])
    engine_missing = (completion.get("missing") or []) if completion else []
    tower_missing = []
    if is_tower:
        edit_result = execute_edit("Complete missing members", existing, {"send_to_tekla": False})
        tower_missing = edit_result.get("elements") or []

    all_missing = _dedupe_elements(pattern_missing + engine_missing + tower_missing)

    gc = GridConstraints.from_existing_model(existing, padding=10000)
    solved = solve_coordinates(all_missing, existing, gc)
    missing = solved["elements"]
    truncated = len(missing) > MAX_COMPLETE_INSERT
    if truncated:
        missing = missing[:MAX_COMPLETE_INSERT]

    if not missing:
        return {
            "status": "complete",
            "message": "Model complete — no missing members detected.",
            "topology": {
                "inferred_type": topo.get("inferred_type"),
                "completeness_pct": topo.get("completeness_pct"),
                "bracing": topo.get("bracing", {}).get("dominant", "unknown"),
            },
            "assessment": completion.get("assessment") if completion else {},
            "breakdown": completion.get("breakdown") if completion else {},
        }

    new_members = _to_members(missing)
    max_id = max((m.get("Id", 0) for m in existing), default=0)
    for i, m in enumerate(new_members):
        m["Id"] = max_id + i + 1
        m["Guid"] = str(uuid.uuid4())

    if req.send_to_tekla:
        write_tekla({
            "StructureType": "completion",
            "Prompt": f"Complete model — {len(new_members)} missing members",
            "Elements": [_bim_el(m) for m in new_members],
        })
    else:
        existing.extend(new_members)
        save_json(existing)

    return {
        "status":         "ok",
        "added":          len(new_members),
        "total_in_model": len(existing) if not req.send_to_tekla else len(existing) + len(new_members),
        "pending_tekla_insert": req.send_to_tekla,
        "truncated":      truncated,
        "remaining_est":  max(0, len(all_missing) - len(new_members)) if truncated else 0,
        "message":        (
            f"Added {len(new_members)} members — run Complete again for remaining ~{len(all_missing) - len(new_members)} gaps."
            if truncated else None
        ),
        "coordinate_solver_stats": solved["stats"],
        "assessment":     completion.get("assessment") if completion else {},
        "breakdown":      completion.get("breakdown") if completion else {},
        "topology": {
            "inferred_type":       topo.get("inferred_type"),
            "bracing_pattern":     topo.get("bracing", {}).get("dominant", "unknown"),
            "symmetry":            topo.get("symmetry", {}).get("type", "unknown"),
            "completeness_before": topo.get("completeness_pct"),
        },
        "sent_to_tekla": req.send_to_tekla,
        "members":       new_members,
    }

# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW 2b — BUILD AGENT (additive — stays on existing model)
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/bim/agent")
def bim_agent(req: BIMAgentRequest):
    existing = load_json()
    agent_result = run_agent_command(req.command, existing, {"send_to_tekla": req.send_to_tekla})

    if agent_result.get("status") == "error":
        raise HTTPException(400, agent_result.get("message", "Agent command failed"))

    # Model cleanup — replace output.json with filtered members
    if agent_result.get("status") == "cleanup":
        cleaned = agent_result.get("members") or []
        save_json(cleaned)
        return {
            "status":    "ok",
            "action":    "cleanup",
            "removed":   agent_result.get("removed", 0),
            "kept":      agent_result.get("kept", len(cleaned)),
            "added":     0,
            "total_in_model": len(cleaned),
            "breakdown": agent_result.get("breakdown"),
            "message":   agent_result.get("message"),
        }

    # Delegate to full generate pipeline (with inline placement)
    if agent_result.get("status") == "delegate_generate":
        gen = bim_generate(BIMGenerateRequest(
            prompt=agent_result["prompt"],
            save_to_model=not req.send_to_tekla,
            send_to_tekla=req.send_to_tekla,
            placement=agent_result.get("placement", "inline"),
        ))
        gen["agent_action"] = agent_result.get("action")
        return gen

    elements = agent_result.get("elements") or []
    if not elements:
        return {
            "status":  "ok",
            "action":  agent_result.get("action"),
            "added":   0,
            "message": agent_result.get("message", "Nothing to add."),
            "topology": agent_result.get("topology"),
        }

    new_members = _to_members(elements)
    max_id = max((m.get("Id", 0) for m in existing), default=0)
    for i, m in enumerate(new_members):
        m["Id"] = max_id + i + 1
        m["Guid"] = str(uuid.uuid4())

    if req.send_to_tekla:
        write_tekla({
            "StructureType": f"agent_{agent_result.get('action', 'edit')}",
            "Prompt": req.command,
            "Elements": [_bim_el(m) for m in new_members],
        })
    else:
        existing.extend(new_members)
        save_json(existing)

    return {
        "status":        "ok",
        "action":        agent_result.get("action"),
        "added":         len(new_members),
        "total_in_model": len(load_json()) if req.send_to_tekla else len(existing),
        "elements":      new_members,
        "params":        agent_result.get("params"),
        "topology":      agent_result.get("topology"),
        "coordinate_solver_stats": agent_result.get("coordinate_solver_stats"),
        "sent_to_tekla": req.send_to_tekla,
    }

@app.get("/bim/agent/commands")
def agent_commands():
    return AGENT_COMMANDS


@app.get("/bim/edit/plan")
def bim_edit_plan(prompt: str = ""):
    """Preview edit plan without modifying the model."""
    existing = load_json()
    if not prompt.strip():
        raise HTTPException(400, "prompt required")
    return plan_edit(prompt.strip(), existing)


@app.post("/bim/edit")
def bim_edit(req: BIMAgentRequest):
    """Universal structure editor — modify any loaded BIM structure."""
    existing = load_json()
    if not existing:
        raise HTTPException(400, "No model loaded. Export from Tekla or create a structure first.")
    result = execute_edit(req.command.strip(), existing, {"send_to_tekla": req.send_to_tekla})
    if result.get("status") == "error":
        raise HTTPException(400, result.get("message", "Edit failed"))
    if result.get("status") == "cleanup":
        save_json(result.get("members") or [])
        return {**result, "total_in_model": len(result.get("members") or [])}
    if result.get("status") == "delegate_create":
        return bim_agent(req)
    elements = result.get("elements") or []
    if not elements:
        return {**result, "added": 0, "total_in_model": len(existing)}
    new_members = _to_members(elements)
    max_id = max((m.get("Id", 0) for m in existing), default=0)
    for i, m in enumerate(new_members):
        m["Id"] = max_id + i + 1
        m["Guid"] = str(uuid.uuid4())
    if req.send_to_tekla:
        write_tekla({
            "StructureType": f"edit_{result.get('action', 'modify')}",
            "Prompt": req.command,
            "Elements": [_bim_el(m) for m in new_members],
        })
    return {
        **result,
        "status": "success",
        "added": len(new_members),
        "elements": new_members,
        "member_counts": {
            "columns": sum(1 for m in new_members if m.get("Type") == "COLUMN"),
            "beams": sum(1 for m in new_members if m.get("Type") == "BEAM"),
            "secondary": sum(1 for m in new_members if m.get("Type") == "SECONDARY"),
        },
        "sent_to_tekla": req.send_to_tekla and len(new_members) > 0,
        "total_in_model": len(existing),
    }

@app.post("/bim/cleanup")
def bim_cleanup():
    existing = load_json()
    if not existing:
        raise HTTPException(400, "No model to clean.")
    result = cleanup_model(existing)
    save_json(result["members"])
    return {"status": "ok", **result}

# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW 3 — TOPOLOGY ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/bim/analyse")
def bim_analyse():
    existing = load_json()
    if not existing:
        return {
            "status": "empty",
            "message": "No members yet. Use Create Structure or export from Tekla, then refresh.",
            "inferred_type": {"type": "unknown", "confidence": 0},
            "completeness_pct": 0,
            "missing_count": 0,
            "levels": [], "x_coords": [], "y_coords": [],
            "symmetry": {}, "bracing": {}, "panel_status": [], "faces": [],
            "member_places": [], "rule_validation": {"score": 0},
            "generation_checklist": get_generation_checklist("building"),
        }

    topo     = analyse_topology(existing)
    inferred = infer_structure_type(existing)
    # Fix: infer_structure_type may return str or dict
    if isinstance(inferred, str):
        inferred = {"type": inferred, "confidence": 1.0}
    rule_res = validate_structure(existing, {"structure_type": inferred.get("type","building")})

    return {
        "status":           "ok",
        "inferred_type":    inferred,
        "levels":           topo.get("levels",[]),
        "x_coords":         topo.get("x_coords",[]),
        "y_coords":         topo.get("y_coords",[]),
        "symmetry":         topo.get("symmetry",{}),
        "bracing":          topo.get("bracing",{}),
        "panel_status":     topo.get("panel_status",[]),
        "faces":            topo.get("faces",[]),
        "missing_count":    topo.get("missing_count",0),
        "completeness_pct": topo.get("completeness_pct",0),
        "member_places":    topo.get("member_places",[])[:50],
        "rule_validation":  rule_res,
        "generation_checklist": get_generation_checklist(inferred.get("type","building")),
    }

# ── Validate ──────────────────────────────────────────────────────────────────
@app.get("/bim/validate")
def bim_validate():
    existing = load_json()
    if not existing: return {"status":"empty","score":0}
    inferred = infer_structure_type(existing)
    if isinstance(inferred, str):
        inferred = {"type": inferred, "confidence": 1.0}
    rule_res = validate_structure(existing, {"structure_type":inferred.get("type","building")})
    clashes  = _detect_clashes([{**m,"StartPoint":m.get("StartPoint",{}),"EndPoint":m.get("EndPoint",{})} for m in existing])
    conn     = build_connections(existing)
    isolated = [mid for mid, c in conn["by_member"].items() if not c]
    score = max(0, rule_res["score"] - len(clashes)*3 - min(20, len(isolated)*2))
    return {"status":"ok","validation_score":score,"total_members":len(existing),
            "inferred_type":inferred,"rule_validation":rule_res,
            "clashes":{"count":len(clashes),"sample":clashes[:5]},
            "isolated_members":isolated[:10]}

# ── Send to Tekla ─────────────────────────────────────────────────────────────
@app.post("/bim/send-to-tekla")
def send_to_tekla(req: BuildRequest):
    existing = load_json()
    if not existing: raise HTTPException(400, "No model data.")
    write_tekla({"StructureType":"full_model","Prompt":req.prompt or "Full model",
                 "Elements":[_bim_el(m) for m in existing]})
    return {"status":"queued","elements":len(existing)}

# ── Standard routes ───────────────────────────────────────────────────────────
@app.get("/verify")
def verify():
    now=time.time(); issues=[]; details={}
    je=os.path.exists(OUTPUT_JSON); jc=-1; jm=None; jh=None
    if je:
        data=load_json(); jc=len(data); stat=os.stat(OUTPUT_JSON); jm=stat.st_mtime; jh=_hash(OUTPUT_JSON)
        details.update({"json_members":jc,"export_age_seconds":round(now-jm,1),"json_hash":jh,"stale":(now-jm)>3600})
    else:
        issues.append("output.json not found"); details["json_members"]=None; details["stale"]=False
    if os.path.exists(OUTPUT_CSV): details["csv_members"]=_csv_count()
    model_name="Unknown"
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE,"r",encoding="utf-8-sig") as f: mf=json.load(f)
            model_name=mf.get("model_name","Unknown"); details["manifest_model"]=model_name
        except: pass
    return {"ok":je and not issues,"issues":issues,"model_name":model_name,
            "member_count":jc if jc>=0 else None,"details":details,
            "suggestion":"Run POST /bim/generate" if not je else None}

@app.get("/model-data")
def model_data():
    data=load_json()
    if not data:
        return {"status":"success","members":[],"edges":[],"relationships":[],"summary":{"total":0,"columns":0,"beams":0,"secondary":0,"connections":0,"unknown":0}}
    structured=build_structural_model(data)
    relationships=[]; edge_counts={}
    try:
        conn=build_connections(data)
        relationships=conn.get("relationships",[])
        edge_counts=conn.get("counts",{})
    except: pass
    try: graph=build_graph(data); edges=graph.get("edges",[])
    except: edges=[]
    members_out=[]
    for m in data:
        sp=m.get("StartPoint",{}); ep=m.get("EndPoint",{}); geo=m.get("Geometry",{})
        members_out.append({"id":str(m.get("Id","")),"role":classify_role(m).replace("PRIMARY_",""),
            "name":m.get("Name",""),"profile":m.get("Profile","???"),"material":m.get("Material",""),
            "length":round(geo.get("Length",0),1),"weight":m.get("Weight",0),"drawing":m.get("Drawing",""),
            "x":sp.get("X",0),"y":sp.get("Y",0),"z":sp.get("Z",0),
            "x2":ep.get("X",0),"y2":ep.get("Y",0),"z2":ep.get("Z",0)})
    summary=_summary(data,structured)
    summary["connections"]=sum(edge_counts.values()) if edge_counts else len(relationships)
    summary["type_counts"]={
        "COLUMN": sum(1 for m in data if str(m.get("Type","")).upper()=="COLUMN"),
        "BEAM": sum(1 for m in data if str(m.get("Type","")).upper()=="BEAM"),
        "SECONDARY": sum(1 for m in data if str(m.get("Type","")).upper()=="SECONDARY"),
    }
    return {"status":"success","members":members_out,"edges":edges,"relationships":relationships,
            "relationship_counts":edge_counts,"summary":summary}

@app.get("/bim/clashes")
def bim_clashes():
    data=load_json()
    if not data: return {"status":"ok","count":0,"clashes":[]}
    clashes=_detect_clashes(data, max_results=100, full_scan=True)
    return {"status":"ok","count":len(clashes),"clashes":clashes}

@app.get("/bim/defects")
def bim_defects():
    data=load_json()
    if not data: return {"status":"ok","defects":[]}
    defects=[]
    for m in data:
        mid=str(m.get("Id","")); role=classify_role(m).replace("PRIMARY_","")
        geo=m.get("Geometry",{}); sp=m.get("StartPoint",{}); ep=m.get("EndPoint",{})
        length=geo.get("Length",0) or 0
        if role not in ("CONNECTION","UNKNOWN") and length < 1:
            defects.append({"id":mid,"label":"Zero-length member","severity":"HIGH","role":role})
        if not m.get("Material") and role not in ("CONNECTION","UNKNOWN"):
            defects.append({"id":mid,"label":"Missing material","severity":"HIGH","role":role})
        if not m.get("Profile") or m.get("Profile")=="???":
            defects.append({"id":mid,"label":"Missing profile","severity":"LOW","role":role})
        if role=="UNKNOWN":
            defects.append({"id":mid,"label":"Unclassified member","severity":"HIGH","role":role})
    return {"status":"ok","defects":defects,"total":len(defects)}

@app.get("/graph")
def graph_route():
    data=load_json()
    if not data: return {"status":"success","nodes":[],"edges":[],"meta":{"total":0}}
    try: g=build_graph(data); return {"status":"success",**g}
    except Exception as e: raise HTTPException(500,str(e))

@app.get("/connections")
def connections():
    data=load_json()
    if not data: return {"status":"success","by_member":{},"relationships":[],"counts":{}}
    try: conn=build_connections(data); return {"status":"success",**conn}
    except Exception as e: raise HTTPException(500,str(e))

@app.get("/drawings")
def drawings():
    data=load_json(); dwg={}
    for m in data:
        d=m.get("Drawing","")
        if d and d not in dwg: dwg[d]={"number":d,"name":d,"status":"Approved","updated":"","type":"GA"}
    return {"status":"success","drawings":list(dwg.values())}

@app.post("/query")
def query(req: QueryRequest):
    data=load_json()
    structured=build_structural_model(data) if data else {"PRIMARY_COLUMN":[],"PRIMARY_BEAM":[],"SECONDARY":[],"UNKNOWN":[]}
    r=client.chat.completions.create(model="llama-3.3-70b-versatile",messages=[{"role":"user",
        "content":f"BIM: {len(data)} members, {len(structured['PRIMARY_COLUMN'])} cols, {len(structured['PRIMARY_BEAM'])} beams. Q: {req.message}"}])
    return {"status":"success","response":r.choices[0].message.content}

@app.post("/build-structure")
def build_structure(req: BuildRequest):
    with open(PENDING_PROMPT,"w") as f: f.write(req.prompt)
    return {"status":"queued","prompt":req.prompt}

@app.get("/build-status")
def build_status(): return {"pending":os.path.exists(PENDING_PROMPT)}

@app.post("/agent")
def agent(req: AgentRequest):
    data=load_json()
    structured=build_structural_model(data) if data else {"PRIMARY_COLUMN":[],"PRIMARY_BEAM":[],"SECONDARY":[],"UNKNOWN":[]}
    cmd=req.command.strip().lower()
    if cmd.startswith("list"):
        return {"status":"success","action":"list",
                "profiles":sorted({m.get("Profile","") for m in data if m.get("Profile")}),
                "materials":sorted({m.get("Material","") for m in data if m.get("Material")}),
                "summary":{"total":len(data),"columns":len(structured["PRIMARY_COLUMN"]),"beams":len(structured["PRIMARY_BEAM"])}}
    if cmd.startswith("delete"):
        rm={"unknown":"UNKNOWN","secondary":"SECONDARY","beam":"PRIMARY_BEAM","beams":"PRIMARY_BEAM","column":"PRIMARY_COLUMN","columns":"PRIMARY_COLUMN"}
        target=next((v for k,v in rm.items() if k in cmd),None)
        if not target: return {"status":"error","message":"Cannot determine target role."}
        before=len(data); data=[m for m in data if classify_role(m)!=target]; save_json(data)
        return {"status":"success","action":"delete","removed":before-len(data),"remaining":len(data)}
    if cmd.startswith("create"):
        sys_p="Return ONLY JSON: {count:int,type:'BEAM'|'COLUMN',profile:str,material:str}"
        try:
            resp=client.chat.completions.create(model="llama-3.3-70b-versatile",max_tokens=100,
                messages=[{"role":"system","content":sys_p},{"role":"user","content":req.command}])
            parsed=json.loads(resp.choices[0].message.content.strip().replace("```json","").replace("```","").strip())
        except Exception as ex: return {"status":"error","message":str(ex)}
        count=min(max(int(parsed.get("count",1)),1),100); mtype=parsed.get("type","BEAM").upper()
        prof=parsed.get("profile","IPE300").upper(); mat=parsed.get("material","S275").upper()
        is_col=mtype=="COLUMN"; max_id=max((m.get("Id",0) for m in data),default=0); new_ms=[]
        for i in range(count):
            nid=max_id+i+1; s={"X":float(i*1000),"Y":0.0,"Z":0.0}
            e={"X":float(i*1000),"Y":0.0,"Z":3000.0} if is_col else {"X":float((i+1)*1000),"Y":0.0,"Z":0.0}
            dx,dy,dz=e["X"]-s["X"],e["Y"]-s["Y"],e["Z"]-s["Z"]
            new_ms.append({"Id":nid,"Guid":str(uuid.uuid4()),"Type":mtype,"Direction":"VERTICAL" if is_col else "HORIZONTAL",
                "Name":mtype,"Profile":prof,"Material":mat,"Class":_tekla_class(mtype),"Finish":"","StartPoint":s,"EndPoint":e,
                "Geometry":{"DeltaX":dx,"DeltaY":dy,"DeltaZ":dz,"Length":(dx**2+dy**2+dz**2)**0.5}})
        data.extend(new_ms); save_json(data)
        return {"status":"success","action":"create","created":count,"type":mtype,"profile":prof,"material":mat,
                "message":f"Created {count} {mtype}(s)","new_ids":[m["Id"] for m in new_ms]}
    r=client.chat.completions.create(model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":f"Model: {len(data)} members. Command: {req.command}"}])
    return {"status":"success","action":"ai_response","response":r.choices[0].message.content}

# ── Helpers ───────────────────────────────────────────────────────────────────
def _bounds(elements):
    xs,ys,zs=[],[],[]
    for e in elements:
        for pt in [e.get("StartPoint",{}),e.get("EndPoint",{})]:
            xs.append(pt.get("X",0)); ys.append(pt.get("Y",0)); zs.append(pt.get("Z",0))
    if not xs: return {}
    return {"min_x":min(xs),"max_x":max(xs),"min_y":min(ys),"max_y":max(ys),"min_z":min(zs),"max_z":max(zs),
            "width_x":max(xs)-min(xs),"width_y":max(ys)-min(ys),"height":max(zs)-min(zs)}

def _measurements(elements, params):
    cols=[e for e in elements if e.get("Type")=="COLUMN"]; beams=[e for e in elements if e.get("Type")=="BEAM"]
    def L(e):
        s,ep=e.get("StartPoint",{}),e.get("EndPoint",{})
        return math.sqrt((s.get("X",0)-ep.get("X",0))**2+(s.get("Y",0)-ep.get("Y",0))**2+(s.get("Z",0)-ep.get("Z",0))**2)
    cl=[L(c) for c in cols]; bl=[L(b) for b in beams]
    return {"column_count":len(cols),"beam_count":len(beams),
            "avg_col_h":round(sum(cl)/len(cl),0) if cl else 0,
            "avg_beam_span":round(sum(bl)/len(bl),0) if bl else 0,
            "max_beam_span":round(max(bl),0) if bl else 0}

def _detect_clashes(elements, max_results=20, full_scan=False):
    PAD=75.0; SNAP=50.0; clashes=[]
    def bb(e):
        s,ep=e.get("StartPoint",{}),e.get("EndPoint",{})
        if not s or not ep: return None
        return {"x0":min(s.get("X",0),ep.get("X",0))-PAD,"x1":max(s.get("X",0),ep.get("X",0))+PAD,
                "y0":min(s.get("Y",0),ep.get("Y",0))-PAD,"y1":max(s.get("Y",0),ep.get("Y",0))+PAD,
                "z0":min(s.get("Z",0),ep.get("Z",0))-PAD,"z1":max(s.get("Z",0),ep.get("Z",0))+PAD}
    def ov(a,b): return(a["x0"]<b["x1"] and a["x1"]>b["x0"] and a["y0"]<b["y1"] and a["y1"]>b["y0"] and a["z0"]<b["z1"] and a["z1"]>b["z0"])
    def sh(e1,e2):
        for p1 in [e1.get("StartPoint",{}),e1.get("EndPoint",{})]:
            for p2 in [e2.get("StartPoint",{}),e2.get("EndPoint",{})]:
                if p1 and p2 and math.sqrt((p1.get("X",0)-p2.get("X",0))**2+(p1.get("Y",0)-p2.get("Y",0))**2+(p1.get("Z",0)-p2.get("Z",0))**2)<=SNAP: return True
        return False
    boxes=[(e,bb(e)) for e in elements if bb(e)]
    for i in range(len(boxes)):
        j_limit = len(boxes) if full_scan else min(i + 40, len(boxes))
        for j in range(i+1, j_limit):
            e1,b1=boxes[i]; e2,b2=boxes[j]
            if not sh(e1,e2) and ov(b1,b2):
                ovl=min(b1["x1"]-b2["x0"],b2["x1"]-b1["x0"],b1["y1"]-b2["y0"],b2["y1"]-b1["y0"],b1["z1"]-b2["z0"],b2["z1"]-b1["z0"])
                if ovl>PAD*0.4:
                    clashes.append({"a":str(e1.get("Id", e1.get('Type',''))),"b":str(e2.get("Id", e2.get('Type',''))),
                        "aRole":e1.get("Type",""),"bRole":e2.get("Type",""),
                        "aProfile":e1.get("Profile",""),"bProfile":e2.get("Profile",""),
                        "overlap_mm":round(ovl,0),"severity":"high" if ovl>PAD else "medium"})
        if len(clashes)>=max_results: break
    return clashes

def _tekla_class(etype: str) -> str:
    t = (etype or "BEAM").upper()
    if t == "COLUMN":
        return "1"
    if t == "BEAM":
        return "2"
    if t in ("SECONDARY", "BRACE"):
        return "3"
    return "0"


def _infer_direction(dx, dy, dz) -> str:
    adx, ady, adz = abs(dx), abs(dy), abs(dz)
    if adz > adx * 1.2 and adz > ady * 1.2:
        return "VERTICAL"
    if adz < adx * 0.35 and adz < ady * 0.35:
        return "HORIZONTAL"
    if adz > 80 and (adx > 80 or ady > 80):
        return "DIAGONAL"
    return "VERTICAL" if adz >= adx and adz >= ady else "HORIZONTAL"


def _to_members(elements):
    ms=[]
    for e in elements:
        s=e.get("StartPoint",{}); ep=e.get("EndPoint",{})
        dx=ep.get("X",0)-s.get("X",0); dy=ep.get("Y",0)-s.get("Y",0); dz=ep.get("Z",0)-s.get("Z",0)
        L=math.sqrt(dx**2+dy**2+dz**2)
        direction = _infer_direction(dx, dy, dz)
        ms.append({"Id":0,"Guid":"","Type":e.get("Type","BEAM"),"Direction":direction,
            "Name": e.get("Name") or e.get("Type", "BEAM"),"Profile":e.get("Profile","IPE300"),"Material":e.get("Material","S275"),
            "Class":_tekla_class(e.get("Type","BEAM")),"Finish":"","StartPoint":s,"EndPoint":ep,
            "Geometry":{"DeltaX":dx,"DeltaY":dy,"DeltaZ":dz,"Length":round(L,1)},"Weight":0,"Drawing":""})
    return ms

def _bim_el(m):
    return {
        "Type": m.get("Type", "BEAM"),
        "Profile": _normalize_tekla_profile(m.get("Profile", "HEA200")),
        "Material": _normalize_tekla_material(m.get("Material", "S275")),
        "Name": m.get("Name", "") or m.get("Type", "BEAM"),
        "Class": _tekla_class(m.get("Type", "BEAM")),
        "StartPoint": m.get("StartPoint", {}),
        "EndPoint": m.get("EndPoint", {}),
    }

def _validate(members, params):
    issues=[]; passed=[]
    cols=sum(1 for m in members if m.get("Type")=="COLUMN"); beams=sum(1 for m in members if m.get("Type")=="BEAM")
    sec=sum(1 for m in members if m.get("Type")=="SECONDARY")
    is_tower="tower" in params.get("structure_type","")
    if cols==0: issues.append("No columns/legs.")
    else: passed.append(f"{cols} columns OK.")
    if beams==0 and not is_tower: issues.append("No beams.")
    elif beams: passed.append(f"{beams} beams OK.")
    if sec==0 and is_tower: issues.append("No bracing/secondary members.")
    elif sec: passed.append(f"{sec} secondary OK.")
    no_p=sum(1 for m in members if not m.get("Profile"))
    if no_p: issues.append(f"{no_p} missing profiles.")
    else: passed.append("All profiles assigned.")
    total=len(issues)+len(passed); score=round(len(passed)/total*100) if total else 100
    return {"score":score,"issues":issues,"passed":passed,"total_members":len(members),
            "columns":cols,"beams":beams,"secondary":sec}


PIPELINE_STAGE_META = [
    ("0_model_understanding",   "Stage 0 — Model understanding"),
    ("1_structure_detection",   "Stage 1 — Structure detection & planning"),
    ("1b_knowledge_graph",      "Stage 2 — Knowledge graph & checklist"),
    ("2_grid_generation",       "Stage 3 — Geometry generation"),
    ("2b_geometry_validation",  "Stage 4 — Geometry validation"),
    ("3_placement",             "Stage 5 — Placement"),
    ("4_coordinate_solving",    "Stage 6 — Coordinate solving"),
    ("5_boundary",              "Stage 7 — Boundary & measurements"),
    ("6_rule_validation",       "Stage 8 — Rule validation"),
    ("7_topology",              "Stage 9 — Topology analysis"),
    ("8_clashes",               "Stage 10 — Clash detection"),
    ("9_bim_output",            "Stage 11 — Tekla-ready output"),
    ("10_validation",           "Stage 12 — Final validation"),
]


def _stage_summary(key: str, data: dict) -> str:
    if not data:
        return "Not run"
    if key == "0_model_understanding":
        return f"{data.get('existing_members', 0)} existing · grid {'yes' if data.get('grid_detected') else 'no'}"
    if key == "1_structure_detection":
        label = data.get("structure_label") or data.get("structure_type", "unknown")
        gen = data.get("generator", "")
        return f"{label} · generator: {gen}" if gen else str(label)
    if key == "1b_knowledge_graph":
        g = data.get("graph") or {}
        parts = [f"{data.get('nodes', len(g.get('nodes', [])))} components"]
        if g.get("structure_type"):
            parts.append(g["structure_type"].replace("_", " "))
        return " · ".join(parts)
    if key == "2_grid_generation":
        return f"{data.get('raw_elements', 0)} raw · C{data.get('columns',0)} B{data.get('beams',0)} S{data.get('secondary',0)}"
    if key == "2b_geometry_validation":
        return f"{data.get('kept', '?')} kept · {data.get('rejected', 0)} rejected"
    if key == "3_placement":
        return f"{data.get('total_placed', 0)} placed · origin {data.get('origin', [])}"
    if key == "4_coordinate_solving":
        return f"{data.get('solved', '?')} solved · {data.get('rejected', 0)} rejected"
    if key == "5_boundary":
        return f"W×D×H {data.get('width_x',0):.0f}×{data.get('width_y',0):.0f}×{data.get('height',0):.0f} mm"
    if key == "6_rule_validation":
        return f"score {data.get('score', '?')} · {len(data.get('errors', []))} errors"
    if key == "7_topology":
        t = data.get("inferred_type") or {}
        return f"{t.get('type', '?')} · {data.get('completeness_pct', 0)}% complete"
    if key == "8_clashes":
        return f"{data.get('count', 0)} clashes"
    if key == "9_bim_output":
        return f"{data.get('element_count', 0)} members"
    if key == "10_validation":
        comp = data.get("component_check", {})
        pct = comp.get("completeness_pct")
        extra = f" · components {pct}%" if pct is not None else ""
        return f"score {data.get('score', '?')}/100 · {data.get('total_members', 0)} members{extra}"
    return "OK"


def _pipeline_summary(stages: dict) -> list:
    rows = []
    for key, label in PIPELINE_STAGE_META:
        data = stages.get(key)
        if data is None:
            status = "pending"
        elif isinstance(data, dict) and data.get("status") == "error":
            status = "error"
        elif key == "3_placement" and data.get("input_members", 0) > 0 and data.get("total_placed", 0) == 0:
            status = "error"
        elif key == "4_coordinate_solving" and data.get("input", 0) > 0 and data.get("solved", 0) == 0:
            status = "error"
        else:
            status = "ok"
        rows.append({
            "key": key,
            "label": label,
            "status": status,
            "summary": _stage_summary(key, data),
        })
    return rows