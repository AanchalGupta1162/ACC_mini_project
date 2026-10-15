"""Run the predictive-reactive autoscaling engine (the only writer to the cluster)."""
import argparse
import time

import metrics_source
import reactive
from config import CYCLE_SECONDS, ENABLE_COLD_PATH, EXPORTER_PORT
from controller import FAULT_DIR_DEFAULT, Controller
from decision_log import DecisionLog
from exporter import Exporter
from predictive import PredictiveArm
from reserve_pool import ClusterAdapter


def build_controller(mode, log_path=None, allow_cold=ENABLE_COLD_PATH, with_exporter=True):
    return Controller(
        ClusterAdapter(),
        reactive_fn=reactive.compute if mode in ("both", "reactive") else None,
        predictive_arm=PredictiveArm() if mode in ("both", "predictive") else None,
        log=DecisionLog(log_path) if log_path else None,
        exporter=Exporter() if with_exporter else None,
        allow_cold=allow_cold,
        rps_fn=metrics_source.current_rps,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Predictive-reactive autoscaling engine")
    parser.add_argument("--mode", choices=["both", "reactive", "predictive"], default="both",
                        help="which arm(s) to run (the variance experiment uses all three)")
    parser.add_argument("--log-csv", help="append one row per cycle to this CSV")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--no-cold", action="store_true",
                        help="never build a cold node; let pods stay Pending when reserves run out")
    parser.add_argument("--no-exporter", action="store_true", help="do not open the Prometheus endpoint")
    args = parser.parse_args(argv)

    FAULT_DIR_DEFAULT.mkdir(exist_ok=True)
    controller = build_controller(args.mode, args.log_csv, not args.no_cold, not args.no_exporter)
    print(f"[engine] mode={args.mode} cycle={CYCLE_SECONDS}s "
          f"{'' if args.no_exporter else f'metrics on :{EXPORTER_PORT} '}"
          f"kill an arm with: touch {FAULT_DIR_DEFAULT}/fault-reactive|fault-predictive", flush=True)
    while True:
        try:
            controller.run_cycle()
        except Exception as e:
            # e.g. API server blip: nothing is retried mid-cycle, the cluster is left as it was
            print(f"[engine] cycle failed, cluster unchanged: {e}", flush=True)
        if args.once:
            break
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
