# Design: demand arm, asymmetric scaling, fair evaluation

## Why (evidence, not opinion)

First HPA comparison (`results/hpa-comparison-20261015/`, n=1 per mode): HPA 5.25% failed / p95 1.44 s,
engine ("both") 11.73% failed / p95 2.40 s. Reading the decision CSVs:

1. `predictive_target` is empty in every row. The arm needs 40 samples (10 min) and the spike test lasts
   4.5 min, so "both" was effectively the reactive arm. That test could not show a prediction benefit.
2. A 20 s cooldown gated **scale-up**: at 99.6 rps / 5 replicas the arbiter wanted 7 but `scaled=False`.
   HPA has no scale-up delay by default.
3. The CPU-ratio rule cannot see demand beyond the pod CPU limit (300m = 200% of the 150m request), and
   warming pods dilute it. The engine peaked at 7 replicas for 110 rps (about 9-10 needed at the calibrated 12 rps/pod).
4. n=1, and the second pair aborted after 8 rows: not a stable result either way.

Background (kubernetes.io HPA algorithm): on scale-up the HPA assumes not-yet-ready pods use 0% CPU, i.e.
it is also blind to demand while pods warm up. Production predictive scalers pair a forecast with a
reactive fallback and size for startup lead time.

## Changes

1. **Demand arm** (`engine/demand.py`, new, third arm). Sizes to *observed request rate*, projected
   forward by the pod startup lead time using a least-squares slope over the last few samples:
   `projected = latest + max(0, slope) * DEMAND_LEAD_SECONDS`, `target = ceil(projected * SAFETY_MARGIN / RPS_PER_POD)`.
   Needs only `DEMAND_MIN_POINTS` (3) samples, so it works from the first minute. Abstains (`None`) when it
   cannot compute. Not capped by the CPU limit, but it reads *served* throughput (the app increments its counter on completion), so timed-out requests are invisible to it. It makes no seasonality claim: it is a lead-time-aware
   rate rule, not a forecaster.
2. **Asymmetric scaling in the arbiter.** Scale-up is immediate. Scale-down is allowed only
   `SCALE_DOWN_COOLDOWN_SECONDS` (default 60) after the last change. Node capacity reconciliation is unchanged.
3. **Warm-node pre-promotion.** Capacity is computed for `target + PREWARM_HEADROOM_PODS` (default 2), so a
   reserve node is uncordoned before pods go Pending. The same headroom guards the "return a node" rule to prevent
   promote/return flapping.
4. **Modes.** `full` = reactive + predictive + demand. `both` keeps its old meaning (reactive + predictive), so
   `reactive` -> `both` -> `full` is an ablation and old results stay comparable.
5. **Fair evaluation.** `run_trials.py` gains `--workload spike|matchday` and `full` in `MODES`. Protocol:
   >= 3 trials per mode, interleaved, reported mean +/- sd; match-day workload lets the forecaster warm up.
   If HPA still wins on a metric, `results/summary.md` says so.

## Invariants kept (AGENTS.md)

Decision logic pure; all PromQL in `metrics_source.py`; config in `config.py`; abstaining arm publishes -1;
decision log self-contained (new `demand_target` column); `ClusterAdapter` untouched.

## Non-goals

No ML forecaster (no training data, high demo risk). No change to the predictive arm. No claims about results
until trials are run on the cluster.
