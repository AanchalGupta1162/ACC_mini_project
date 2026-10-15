# AGENTS.md

Working notes for AI coding agents on this repo. Human-facing docs: [`README.md`](README.md),
[`docs/demo-script.md`](docs/demo-script.md).

This is an academic mini-project (Advanced Cloud Computing & DevOps) that is also *demonstrated
live to an evaluator panel*. That changes the priorities: the numbers in `results/` must be
reproducible and honestly reported, and the demo must not break. Prefer boring and verifiable
over clever.

## What this is

A Kubernetes autoscaler with two arms that run side by side:

- **reactive arm** (`engine/reactive.py`, every 15s) — reproduces HPA logic on CPU-vs-request,
  plus a term for pods the scheduler cannot place.
- **predictive arm** (`engine/predictive.py`, every 30s) — Holt-Winters forecast of request rate
  at T+2min, converted to replicas through a calibrated RPS-per-pod figure.
- **arbiter** (`engine/arbiter.py`) — takes `max()` of the two and decides replicas, node
  promotions and cold-node builds.

Extra k3d worker nodes sit cordoned with the image pre-pulled (a warm reserve pool), so capacity
becomes schedulable in seconds rather than a node build.

## Commands

```bash
make setup     # venv + deps
make test      # unit tests, no cluster needed  -> run this before every commit
make all       # k3d cluster + app + Prometheus/Grafana + dashboard (~10 min)
make engine    # the engine (both arms)
make ui        # live cluster map -> http://localhost:8000
make reset     # back to the demo baseline between runs
```

Tests are plain `pytest` (`pytest.ini` puts `engine experiments app ui` on the path, so modules
import by bare name — `from arbiter import decide`, not `from engine.arbiter import decide`).

## Invariants — do not break these

1. **`ClusterAdapter` is the only thing that writes to the cluster.** Everything else is pure or
   read-only. If you need a new cluster mutation, add it there, not inline.
2. **An arm that cannot compute returns `None` and abstains.** It never guesses, never returns a
   stale value, and never raises into the control loop. A cycle that fails leaves the cluster
   untouched and says so. This is a demo feature (arms get killed live), not just hygiene.
3. **Decision logic stays pure.** `arbiter.py`, `reactive.py`, `experiments/exp_metrics.py` and
   `ui/cluster_state.py` take values in and return values out, with no Prometheus or Kubernetes
   imports. That is what makes them testable without a cluster, and most of the test suite depends
   on it.
4. **All Prometheus reads live in `engine/metrics_source.py`.** No PromQL anywhere else.
5. **Config goes in `engine/config.py`**, env-overridable, never hard-coded at a call site. Tests
   that care about a tunable must derive expectations *from* it — a test that hard-codes
   `RPS_PER_POD` breaks the moment the app is recalibrated.
6. **The exporter publishes `-1` for an abstaining arm**, and the Grafana dashboard filters
   `> -1`. Do not publish `0` — zero is a real target the arbiter can mean.
7. **The decision log is self-contained.** Each row records the traffic actually observed, so
   forecast accuracy can be scored from the CSV alone. Prometheus keeps no history across a
   restart; we learned this by losing a 40-minute run to it.

## Testing

Test-first, and watch the test fail before writing the code. This matters more than usual here
because the control loop is hard to exercise by hand: a full match-day run takes ~43 minutes, so a
bug caught by a unit test saves most of an evening.

Unit tests must not need a cluster or Prometheus. Inject fakes — `Controller` takes `cluster`,
`reactive_fn`, `predictive_arm`, `log`, `exporter`, `clock` and `rps_fn` for exactly this reason.

## Environment quirks (all of these have cost time)

- **Rancher Desktop with the `moby` engine and its built-in Kubernetes disabled.** The cluster is
  k3d, created by `cluster/create-cluster.sh`. Do not enable Rancher's own Kubernetes.
- **`host.k3d.internal` does not reach macOS** — it resolves to the Rancher VM. The engine's
  exporter is scraped via **`host.docker.internal:9900`**.
- **Node labels and the `autoscaler.demo/slot` extended resource do not survive a VM restart.**
  Run `cluster/label-nodes.sh && cluster/verify-cluster.sh` (must print PASS) before any run.
- **Every k3d node reports the whole VM's CPU**, so per-node pod capacity is modelled with the
  extended resource `autoscaler.demo/slot: 4` and one slot requested per pod. Without this the
  reserve pool is never exercised, because everything fits on one node.
- **Cold nodes leave a stale `Node` object behind** when their container is deleted.
  `cluster/reset.sh` deletes both.
- **This workload is memory-hungry on a 16 GB host.** A 12-pod CPU-burn run with an 8 GB VM
  exhausted swap and took the container engine down mid-experiment. Check `vm.swapusage` before a
  long run, and close other heavy apps.
- Writing shell edits with `sed` over k6 files containing `||` fails; use a small Python script.
- Do not pipe a long-running background command through `tail` — it buffers until EOF and looks
  hung.

## Reporting results

`results/` is experiment evidence for the report, not scratch. When a number changes, update
`results/summary.md` *and* the README's headline table together.

State what was actually measured. The forecaster overshooting a never-seen spike by 165% MAPE is a
real finding that motivates the two-arm design — it belongs in the report, not hidden. Same for
the missing graceful shutdown that makes scale-down drop in-flight requests.
