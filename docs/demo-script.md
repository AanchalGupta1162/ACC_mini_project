# 6-minute demo script

Before the panel arrives:

```bash
make all                     # cluster, app, monitoring, dashboard (~10 min)
make engine                  # terminal 1
make ui                      # terminal 2  -> http://localhost:8000
k6 run loadtest/matchday.js  # terminal 3, start ~30 min early so the forecaster has 2+ cycles
```
Pre-flight, every time (node labels and the per-node slot capacity do not survive a VM restart):

```bash
cluster/label-nodes.sh && cluster/verify-cluster.sh   # must print PASS
cluster/verify-monitoring.sh                          # must print PASS
```

Open: the live view (http://localhost:8000) as the main screen, Grafana (http://localhost:3000, admin/admin) as backup.

**How to read the live view (say this to the panel):** the blue line is incoming traffic, the green line is
what the ready pods can serve (ready pods x 12 req/s). If green stays above blue, users are fine; red
shading would mean traffic outran capacity. The banner narrates each decision in plain English, the four
boxes show which arm asked for how many pods (the arbiter takes the highest; "abstained" = arm is down),
and the event list shows reserve nodes being woken. Open the page *before* starting k6 so the chart
shows the whole spike.

| Time | What you do | What to point at |
|---|---|---|
| 0:00–0:45 | The problem: JioHotstar match day. The HPA only reacts *after* the queue forms; pods then take ~20s to warm up, and that window is where viewers see buffering. | Architecture diagram |
| 0:45–1:45 | Baseline: `kubectl apply -f k8s/hpa.yaml`, run `k6 run loadtest/spike.js`. Kubernetes' own autoscaler reacts late. | Grafana: p95 latency climbing, unplaced pods > 0 |
| 1:45–3:00 | Engine on the same spike. The predictive line rises *before* the traffic; the arbiter takes the biggest of the three arms (reactive, predictive, demand). | Grafana panel 1 (forecast leads) and panel 2 (both arms + arbiter) |
| 3:00–4:00 | A reserve node lights up on the cluster map and pods fill it within seconds. | Cluster map: dashed grey node turns green |
| 4:00–4:45 | Kill an arm live: `touch engine/faults/fault-predictive`. The engine keeps scaling on the reactive and demand arms. Then `make kill-demand` and `make kill-reactive` → "ALL arms abstained", nothing changes. `rm engine/faults/fault-*` to recover. | Engine log + Grafana "arm health" panel |
| 4:45–5:30 | Results: variance across trials, warm promotion vs cold node build, forecast MAPE. | `results/summary.md` charts |
| 5:30–6:00 | Honest limits: finite reserve pool, single arbiter, forecaster needs ~20 min of history, synthetic traffic, scaled-down model. | Limitations slide |

Reset between runs: `make reset`.
