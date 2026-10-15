"""Turn raw node/pod records into the shape the cluster map draws. No Kubernetes imports here."""


def _node_state(node):
    role = node.get("role")
    if role == "system":
        return "system"
    if role == "reserve":
        return "warm-reserve" if node.get("unschedulable") else "promoted"
    if "cold" in node["name"]:
        return "cold"
    return "active"


def build_state(nodes, pods):
    placed = {}
    pending = []
    for p in pods:
        if p.get("node"):
            placed.setdefault(p["node"], []).append(p)
        else:
            pending.append(p)

    rendered = []
    for n in nodes:
        rendered.append({
            "name": n["name"],
            "role": n.get("role"),
            "state": _node_state(n),
            "pods": placed.get(n["name"], []),
        })
    schedulable = sum(1 for n in nodes if n.get("role") in ("active", "reserve") and not n.get("unschedulable"))
    return {
        "nodes": rendered,
        "pending": pending,
        "summary": {
            "pods": len(pods),
            "ready": sum(1 for p in pods if p.get("ready")),
            "schedulable_nodes": schedulable,
        },
    }
