#!/usr/bin/env python3
"""Forecast accuracy (MAPE at T+2min) and scaling churn, from the engine's decision log."""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from exp_metrics import (actuals_from_rows, align_forecasts, count_events,  # noqa: E402
                         forecasts_from_rows, mape)
from metrics_source import RPS_QUERY, range_query  # noqa: E402


def load_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def cmd_mape(args):
    rows = load_rows(args.log)
    forecasts = forecasts_from_rows(rows)
    if not forecasts:
        sys.exit("no forecasts in this log (was the predictive arm still warming up?)")
    # the log records the traffic the engine saw, so accuracy can be scored long after the
    # run; Prometheus is only the fallback for logs written before that column existed
    actuals = actuals_from_rows(rows)
    source = "decision log"
    if not actuals:
        start, end = forecasts[0][0], forecasts[-1][0] + 150
        actuals = range_query(RPS_QUERY, (end - start) / 60, 15, end=end)
        source = "prometheus"
    pairs = align_forecasts(forecasts, actuals, horizon_seconds=args.horizon)
    if not pairs:
        sys.exit("no forecast could be matched to an actual value; run this after the load test finishes")
    print(f"actuals from {source}: {len(actuals)} samples")
    print(f"forecasts={len(forecasts)} matched={len(pairs)} "
          f"MAPE@T+{args.horizon}s={mape(pairs, min_actual=args.min_actual):.1f}% "
          f"(scored only where actual traffic > {args.min_actual} rps)")


def cmd_churn(args):
    for path in args.logs:
        rows = load_rows(path)
        minutes = (float(rows[-1]["timestamp"]) - float(rows[0]["timestamp"])) / 60 if rows else 0
        print(f"{path}: {count_events(rows)} over {minutes:.1f} min")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(required=True)
    p_mape = sub.add_parser("mape", help="forecast accuracy against what actually happened")
    p_mape.add_argument("log")
    p_mape.add_argument("--horizon", type=int, default=120)
    p_mape.add_argument("--min-actual", type=float, default=5.0,
                        help="ignore samples below this request rate: percentage error on near-idle traffic is meaningless")
    p_mape.set_defaults(func=cmd_mape)
    p_churn = sub.add_parser("churn", help="count scale and node events")
    p_churn.add_argument("logs", nargs="+")
    p_churn.set_defaults(func=cmd_churn)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
