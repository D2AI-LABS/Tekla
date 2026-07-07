# connection_engine.py
#
# Converts raw geometric edges from graph_builder.build_graph() into
# labeled ENGINEERING relationships.
#
# Relationship types:
#   SUPPORTS      — column holds up a beam at column's TOP end
#   SUPPORTED_BY  — inverse of SUPPORTS
#   FRAMES_INTO   — beam end meets another beam (beam-to-beam)
#   BRACES        — secondary member connecting to column/beam
#   BRACED_BY     — inverse of BRACES
#   CONNECTED_TO  — fallback (ambiguous geometry)

from app.knowledge_graph.graph_builder import build_graph

SNAP_MM = 50.0


def _is_top_end(node: dict, point: dict) -> bool:
    z_start = node["start"]["z"]
    z_end   = node["end"]["z"]
    return abs(point["z"] - max(z_start, z_end)) < 1e-6


def _dist(a: dict, b: dict) -> float:
    return (
        (a["x"]-b["x"])**2 + (a["y"]-b["y"])**2 + (a["z"]-b["z"])**2
    ) ** 0.5


def _classify_edge(a: dict, b: dict) -> tuple:
    a_role, b_role = a["role"], b["role"]

    if a_role == "COLUMN" and b_role == "BEAM":
        meets_at_top = _is_top_end(a, a["end"]) or _is_top_end(a, a["start"])
        if meets_at_top:
            return ("SUPPORTS", "SUPPORTED_BY")
        return ("CONNECTED_TO", "CONNECTED_TO")

    if b_role == "COLUMN" and a_role == "BEAM":
        rel = _classify_edge(b, a)
        return (rel[1], rel[0])

    if a_role == "BEAM" and b_role == "BEAM":
        return ("FRAMES_INTO", "FRAMES_INTO")

    if a_role == "SECONDARY" and b_role in ("BEAM", "COLUMN"):
        return ("BRACES", "BRACED_BY")
    if b_role == "SECONDARY" and a_role in ("BEAM", "COLUMN"):
        rel = _classify_edge(b, a)
        return (rel[1], rel[0])

    return ("CONNECTED_TO", "CONNECTED_TO")


def build_connections(data: list) -> dict:
    graph        = build_graph(data)
    nodes_by_id  = {n["id"]: n for n in graph["nodes"]}
    by_member    = {n["id"]: [] for n in graph["nodes"]}
    relationships= []
    counts       = {}

    for e in graph["edges"]:
        a = nodes_by_id.get(e["source"])
        b = nodes_by_id.get(e["target"])
        if not a or not b:
            continue

        rel_a_to_b, rel_b_to_a = _classify_edge(a, b)

        by_member[a["id"]].append({
            "with": b["id"], "relationship": rel_a_to_b,
            "other_role": b["role"], "other_name": b["name"],
            "other_profile": b["profile"],
        })
        by_member[b["id"]].append({
            "with": a["id"], "relationship": rel_b_to_a,
            "other_role": a["role"], "other_name": a["name"],
            "other_profile": a["profile"],
        })

        relationships.append({
            "source": a["id"], "target": b["id"], "type": rel_a_to_b
        })
        counts[rel_a_to_b] = counts.get(rel_a_to_b, 0) + 1

    return {
        "by_member":     by_member,
        "relationships": relationships,
        "counts":        counts,
        "nodes_by_id":   nodes_by_id,
    }


def find_member(data: list, query: str):
    graph = build_graph(data)
    q = query.strip().upper()
    if not q:
        return None
    for n in graph["nodes"]:
        if n["id"] == q:
            return n
    for n in graph["nodes"]:
        if n["name"].upper() == q:
            return n
    for n in graph["nodes"]:
        if q in n["id"].upper() or q in n["name"].upper():
            return n
    return None


def describe_load_path(data: list, member_id: str, max_depth: int = 3) -> dict:
    conn        = build_connections(data)
    nodes_by_id = conn["nodes_by_id"]
    by_member   = conn["by_member"]

    if member_id not in nodes_by_id:
        return {"member": member_id, "path": [], "terminated": "not_found"}

    path    = [{"id": member_id, "role": nodes_by_id[member_id]["role"], "via": None}]
    current = member_id
    visited = {member_id}

    for _ in range(max_depth):
        supporters = [
            r for r in by_member.get(current, [])
            if r["relationship"] == "SUPPORTED_BY" and r["with"] not in visited
        ]
        if not supporters:
            return {"member": member_id, "path": path, "terminated": "no_support"}
        nxt = supporters[0]["with"]
        path.append({"id": nxt, "role": nodes_by_id[nxt]["role"], "via": "SUPPORTED_BY"})
        visited.add(nxt)
        current = nxt

    return {"member": member_id, "path": path, "terminated": "max_depth"}