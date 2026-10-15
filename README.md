# Predictive-Reactive Autoscaling Engine — JioHotstar match day

A Kubernetes autoscaler with **two arms running side by side**: a reactive arm that responds to
load that has already arrived, and a predictive arm that forecasts the next two minutes with
Holt-Winters. An arbiter takes `max(reactive, predictive)` and is the only component that ever
changes the cluster. Extra worker nodes sit **cordoned and pre-pulled** as a warm reserve pool, so
new capacity becomes schedulable in seconds instead of a node build.

Case study: a cricket match on a streaming platform — traffic with a *known shape* (toss, first
ball, innings break, final overs), compressed into a repeating 10-minute cycle.

```
k6 viewers ─► playback-api pods ─► Prometheus ──► reactive arm  (15s: unplaced pods, CPU vs request)
                                            └──► predictive arm (30s: Holt-Winters forecast of RPS at T+2min)
                                                        │ max()
                                                        ▼
                                              arbiter ──► Deployment replicas
                                                      └─► uncordon a warm reserve node (seconds)
                                                          or build a cold node (tens of seconds)
```

## Quick start

```bash
make setup      # python venv
make all        # k3d cluster + app + Prometheus/Grafana + dashboard
make engine     # terminal 1: the autoscaling engine
make ui         # terminal 2: live cluster map   -> http://localhost:8000
make spike      # terminal 3: a sudden rush
```

- Cluster map: <http://localhost:8000>
- Grafana: <http://localhost:3000> (admin / admin) → "JioHotstar · Predictive-Reactive Autoscaler"
- Prometheus: <http://localhost:9090>
- App: <http://localhost:8080/play>

Teardown: `make down`.

## Headline results (this laptop)

| Question | Answer |
|---|---|
| How fast is a warm reserve node? | **~4s** of infrastructure time to promote (uncordon), vs **~20s** to build a cold node and pull the image, vs ~1.7s to use a node that is already live. Every path then pays the same 20s app warm-up. |
| Does the predictive arm actually lead? | Yes — on a 20 → 110 rps spike it asked for more capacity at t=61s, while the reactive arm was still asking for the minimum, and it triggered the reserve promotion at t=123s. |
| What does the forecaster get wrong? | A sharp, never-seen spike: the trend model overshot to ~296 rps against an actual 110 (MAPE ~165%). This is the reactive arm's job, and the reason both arms exist. |

Full numbers and method: [`results/summary.md`](results/summary.md).

## Requirements

Rancher Desktop with the **dockerd (moby)** engine and its **built-in Kubernetes disabled**
(the engine needs several nodes; Rancher's own k3s is a single node). VM sized 6 CPU / 8 GB:

```bash
rdctl set --kubernetes.enabled=false --virtual-machine.memory-in-gb=8 --virtual-machine.number-cpus=6
```

Plus `k3d`, `kubectl`, `helm`, `k6` (`brew install k3d kubectl helm k6`) and Python 3.10+.

## How it works

**Reactive arm** (`engine/reactive.py`) — reproduces the HPA's own decision: replicas scale in
proportion to CPU against the pods' CPU *request*, with the HPA's ±10% tolerance band. It also
watches something the HPA cannot see: pods the scheduler could not place.

**Predictive arm** (`engine/predictive.py`) — pulls ~30 minutes of request-rate history from
Prometheus and refits Holt-Winters every 30 seconds. No training, no saved model, no GPU: it is
exponential smoothing over level, trend and season. It forecasts 8 steps of 15s (T+2 min), takes
the peak of that forecast and converts it to replicas using the measured capacity of one pod.
Below 40 samples it abstains and the reactive arm carries the system (the cold-start argument).

**Arbiter** (`engine/arbiter.py`, `engine/controller.py`) — `target = clamp(max(arms), 2, 12)`,
with a 20-second cooldown on replica changes. Capacity is reconciled every cycle, even inside the
cooldown, so unplaced pods get a node immediately. On the way down it hands a reserve node back.
If **both** arms abstain it changes nothing at all and says so.

**Warm reserve pool** (`engine/reserve_pool.py`) — reserve nodes are created with the cluster,
already have the image, and are cordoned. Promotion is one `uncordon`. When the pool is empty the
engine falls back to the honest slow path: `k3d node create` plus an image import.

**Why "slots"** — every k3d node reports the whole VM's CPU, so 150m pods would never fill a node
and the reserve pool would never be exercised. Each node therefore advertises an extended resource
`autoscaler.demo/slot: 4` and each pod requests one, which fixes capacity at 4 pods per node.

## Capacity calibration (measured on this laptop)

One `playback-api` pod (`WORK_ITERATIONS=400000`, request 150m, limit 300m), load from k6:

| offered rps | p95 latency | CPU (% of request) |
|---|---|---|
| 10 | 19 ms | 81% |
| 15 | 16 ms | 86% |
| 20 | 27 ms | 111% |
| 25 | 194 ms | 106% |
| 30 | 144 ms | 142% |

So one pod serves ~10 rps at the engine's 80%-of-request CPU target and starts hurting past 20 rps.
`RPS_PER_POD=12` with `SAFETY_MARGIN=1.2` (≈10 effective rps per pod) keeps both arms consistent:
at 110 rps the predictive arm asks for ~11 pods, which is also where the CPU rule lands.
Re-run the sweep with `loadtest/calibrate.js` on different hardware.

## Experiments

| Experiment | Command | Measures |
|---|---|---|
| Scaling lag + variance | `make trials` | pending pod-seconds, time-to-capacity, p95, failure rate for HPA / reactive / predictive / both over N trials |
| Promotion latency | `make latency` | active node vs warm uncordon vs cold node build |
| Forecast accuracy | `.venv/bin/python experiments/analyze_log.py mape results/live-decisions.csv` | MAPE at T+2 min (only scored where traffic > 5 rps) |
| Stability / churn | `.venv/bin/python experiments/analyze_log.py churn results/*.csv` | scale and node events per run |
| Fallback safety | `touch engine/faults/fault-predictive` (and `fault-reactive`) | each arm dies independently; both dead = no change |

Results land in `results/`.

**Baseline fairness.** The HPA baseline (`k8s/hpa.yaml`) uses the same bounds (2–12) and the
same 80% CPU target as the engine's reactive arm, and otherwise default settings. The
comparison therefore isolates what this project adds — forecasting and warm node promotion —
rather than a difference in thresholds. The HPA and the engine must never run at the same
time: they would fight over the same Deployment.

## Known limitations (stated openly)

- **The reserve pool is finite.** Past the last reserve the engine builds a cold node, which is an
  order of magnitude slower. That trade-off is the point, not a bug.
- **The arbiter is a single process.** If it dies, nothing scales; the cluster simply freezes where
  it is. Leader election is future work.
- **The predictive arm needs history** — about 10 minutes for a trend, ~20 minutes for the seasonal
  pattern. The reactive arm covers that window.
- **Holt-Winters only predicts shapes it has seen.** A genuinely novel spike is the reactive arm's
  job, which is exactly why both arms exist. On a sharp one-off ramp the trend model overshoots.
- **Traffic is synthetic.** It is a model of match-day shape, not real JioHotstar data.
- **Scaled-down model.** 2 active + 2 reserve nodes and at most 12 pods on one laptop.
- **Cold-built nodes are not torn down automatically.** The engine adds them when the pool is empty
  but never removes them; `make reset` deletes them (container *and* the stale Node object).
- **Local nodes share the laptop's CPU**, so a promoted node adds scheduling room rather than new
  silicon. Promotion latency and scheduler behaviour are real; raw capacity gain is not.

## Repo layout

```
cluster/     k3d lifecycle, labelling/cordoning, monitoring + dashboard install, reset
k8s/         playback-api, mock services, HPA baseline, ServiceMonitor, Helm values
app/         the autoscaled service (CPU-bound endpoint, 20s warm-up, Prometheus metrics)
engine/      config, metrics source, reactive arm, predictive arm, arbiter, reserve pool, CLI
loadtest/    k6: matchday, spike, steady, calibrate
ui/          FastAPI live cluster map
dashboards/  Grafana dashboard JSON
experiments/ trial runner, promotion latency, log analysis
tests/       78 unit tests, no cluster required (`make test`)
docs/        the plan this was built from, and the 6-minute demo script
```
