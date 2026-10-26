"""Pure analysis helpers for the experiments. No Prometheus or Kubernetes imports."""
import statistics


def pod_seconds(points, step_seconds):
    """Integrate a pod-count series [(ts, value)] sampled every step_seconds."""
    return sum(v for _, v in points) * step_seconds


def time_to_capacity(available_points, required, start_ts):
    for ts, v in available_points:
        if ts >= start_ts and v >= required:
            return ts - start_ts
    return None


def mean_std(values):
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return statistics.fmean(values), statistics.stdev(values) if len(values) > 1 else 0.0


def forecasts_from_rows(rows):
    """The engine logs its cached forecast every cycle; keep one entry per real computation."""
    by_time = {}
    for r in rows:
        if r.get("forecast_rps") and r.get("forecast_made_at"):
            by_time[float(r["forecast_made_at"])] = float(r["forecast_rps"])
    return sorted(by_time.items())


def actuals_from_rows(rows):
    """The traffic the engine actually saw, [(ts, rps)] — the log is self-contained."""
    return [(float(r["timestamp"]), float(r["observed_rps"]))
            for r in rows if r.get("observed_rps")]


def align_forecasts(forecasts, actuals, horizon_seconds=120, tolerance_seconds=15):
    pairs = []
    for made_at, forecast in forecasts:
        want = made_at + horizon_seconds
        nearest = min(actuals, key=lambda p: abs(p[0] - want), default=None)
        if nearest is not None and abs(nearest[0] - want) <= tolerance_seconds:
            pairs.append((forecast, nearest[1]))
    return pairs


def mape(pairs, min_actual=0.01):
    usable = [(f, a) for f, a in pairs if a > min_actual]
    if not usable:
        raise ValueError("no actual values above min_actual")
    return 100 * statistics.fmean(abs(f - a) / a for f, a in usable)


def count_events(rows):
    events = {"scale_up": 0, "scale_down": 0, "uncordon": 0, "cordon": 0, "cold_create": 0}
    for r in rows:
        if r["scaled"] == "True":
            events["scale_up" if int(r["target"]) > int(r["current_replicas"]) else "scale_down"] += 1
        for key in ("uncordon", "cordon", "cold_create"):
            events[key] += int(r.get(key) or 0)
    return events


def summary_rows(rows, keys):
    """Per-mode mean/std of the numeric columns in trials.csv rows (strings, '' = missing)."""
    out = []
    for mode in dict.fromkeys(r["mode"] for r in rows):
        mine = [r for r in rows if r["mode"] == mode]
        row = {"mode": mode, "n": len(mine)}
        for k in keys:
            row[k] = mean_std([float(r[k]) if r.get(k) not in (None, "") else None for r in mine])
        out.append(row)
    return out
