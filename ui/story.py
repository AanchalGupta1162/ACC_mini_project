"""Plain-English narration of the engine's decisions for the demo page. Pure: no Kubernetes, no Prometheus."""

ARMS = ("reactive", "predictive", "demand")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag(value):
    return str(value).strip().lower() in ("true", "1")


def capacity_rps(ready_pods, rps_per_pod):
    return ready_pods * rps_per_pod


def verdict(observed_rps, ready_pods, rps_per_pod):
    cap = capacity_rps(ready_pods, rps_per_pod)
    if observed_rps <= cap:
        return {"ok": True, "text": f"Capacity ({cap:.0f} rps) covers traffic ({observed_rps:.0f} rps)"}
    return {"ok": False,
            "text": f"Traffic ({observed_rps:.0f} rps) is ahead of ready capacity ({cap:.0f} rps) - new pods still warming"}


def winning_arms(row):
    """Arms whose target equals the final target. An abstaining arm (blank) never wins."""
    target = _num(row.get("target"))
    return [a for a in ARMS if _num(row.get(f"{a}_target")) is not None and _num(row.get(f"{a}_target")) == target]


def narrate(row):
    if not row:
        return {"phase": "waiting", "headline": "Engine not running",
                "detail": "Start it with: make engine"}
    rps = _num(row.get("observed_rps")) or 0.0
    current = int(_num(row.get("current_replicas")) or 0)
    target = int(_num(row.get("target")) or 0)
    arms = " + ".join(winning_arms(row)) or "no arm"
    if target > current:
        return {"phase": "scaling-up",
                "headline": f"Traffic {rps:.0f} rps - scaling UP {current} -> {target} pods",
                "detail": f"Decided by the {arms} arm (the arbiter takes the highest request). Scale-up is immediate."}
    if target < current:
        return {"phase": "scaling-down",
                "headline": f"Traffic {rps:.0f} rps - scaling DOWN {current} -> {target} pods",
                "detail": f"Traffic has eased ({arms} arm). Scale-down waits for a cooldown so we never shed capacity too fast."}
    return {"phase": "steady",
            "headline": f"Traffic {rps:.0f} rps - {current} pods, no change needed",
            "detail": f"All arms agree the current size is right ({arms} set the target)."}


def events_from_rows(rows):
    events = []
    for r in rows:
        t = _num(r.get("timestamp")) or 0.0
        if _flag(r.get("scaled")):
            events.append({"t": t, "kind": "scale",
                           "text": f"Scaled {r.get('current_replicas')} -> {r.get('target')} pods "
                                   f"({' + '.join(winning_arms(r)) or 'no arm'} arm)"})
        if (_num(r.get("uncordon")) or 0) > 0:
            events.append({"t": t, "kind": "node", "text": "Woke a warm reserve node (already has the image, ready in seconds)"})
        if (_num(r.get("cold_create")) or 0) > 0:
            events.append({"t": t, "kind": "node", "text": "Reserve pool exhausted - building a cold node"})
    return events

