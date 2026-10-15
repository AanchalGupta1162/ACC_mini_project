# Predictive-Reactive Kubernetes Autoscaler: Project Plan and Context

> Hand-off document for another agent. It captures everything decided so far in the planning conversation. **No implementation has been done yet.** The user wants the idea fully designed first, then implementation. Do not generate the repo until the user says so.

---

## 1. Who / what / when

- **Project type:** Mini project for an *Advanced Cloud Computing and DevOps* course.
- **Deadline:** Presentation in ~2 days from Wed 14 Oct 2026. Solo build.
- **Audience:** An evaluator/panel who must *visually understand* what is happening. Visual appeal is a primary goal.
- **Mentor's suggestion:** Build a use case. The user chose **JioHotstar** (live cricket streaming) as the case study.
- **User's own research addition:** Rancher Desktop as a local Kubernetes tool.
- **Hardware available:**
  - MacBook **M4 Pro**, 24 GB unified memory, 1 TB disk (intended *demo machine*)
  - MacBook **M4 Air**, 16 GB RAM, 512 GB disk (intended *backup / clone*)
  - Both Apple Silicon (arm64), so use multi-arch images.
- **Constraint:** Everything free and open source, runs locally and offline.

---

## 2. Project concept (from the user's feasibility analysis document)

A **Predictive-Reactive Autoscaling Engine for Kubernetes** running two independent scaling arms side by side:

- **Reactive Arm:** reacts once load has already risen (pending pods or high CPU). Loop every **15 s**.
- **Predictive Arm:** forecasts load a few minutes ahead with **Holt-Winters** (statsmodels) and recommends capacity before a spike. Loop every **30 s**, pulling ~30 min of history, forecasting about **T+2 min** (design range 2-5 min).
- **Arbiter:** `target = max(reactive_target, predictive_target)`, clamped to `[minReplicas, maxReplicas]`. It is the **only component that changes cluster state** (patches `Deployment.spec.replicas`, cordons/uncordons reserve nodes).
- **Warm reserve pool:** extra worker nodes that are created but **cordoned**, with the app image already pulled, so capacity is schedulable within seconds.
- **Per-arm safety fallback:** each arm wraps its computation in try/except. A failing arm abstains for that cycle. If both fail, the arbiter makes no change, logs a warning, and retries next cycle.

**Problem being solved:** The built-in Horizontal Pod Autoscaler (HPA) is purely reactive. Between a metric crossing a threshold and new pods being Ready there is lag (metrics averaging window 15-60 s, scheduling, image pull, app warm-up). During that window users see latency, timeouts, drops. Many workloads (daily peaks, scheduled events, match starts) are predictable, and a reactive-only controller wastes that.

**Reference paper:** *AgileSphere: Predictive-Reactive Kubernetes Cluster Autoscaling With Warm-Pool Node Promotion* (Habibi, Rawat, Leon-Garcia; IEEE Access, 2026). Their live k3d/k3s testbed used custom Go microservices, a transformer forecaster, and a multi-cloud marketplace. This project borrows and re-tests two claims at small scale:
1. Combining a fast reactive arm with a forecasting arm under max-arbitration mainly **reduces run-to-run variance** in scaling behaviour (not just average backlog).
2. Pre-cordoned warm reserve nodes cut node-promotion time by over an order of magnitude vs provisioning new capacity.
(The paper also found ~50% higher node-seconds from holding a warm pool.)

### Layperson explanation
A cinema opens extra counters only after the queue is long (that is how Kubernetes normally reacts). We add a forecaster that says "the cricket toss is in 3 minutes, a rush is coming" and opens counters early. A referee takes whichever of the two wants more counters. Spare, prepared rooms stay locked until needed, so extra capacity opens in seconds.

### One-sentence system summary
Fake viewers hit a streaming-style app on a small local Kubernetes cluster; two programs watch load and each propose how many copies of the app should run; a referee takes the bigger proposal, applies it, and wakes a pre-warmed spare machine if needed; dashboards show all of this live.

### Architecture (matches the user's Figure 1, numbered 1-6)
1. Users (traffic) -> 2. App Pods (on active nodes) -> 3. Prometheus (watches CPU and pending pods) -> 4. Reactive Arm ("scale now", every 15 s) and 5. Predictive Arm ("scale ahead", forecasts load) -> 6. Decision/Arbiter (takes the BIGGER of the two) -> wakes a warm node from **Reserve Nodes** (pre-warmed, idle, dashed grey) -> "more pods scheduled in seconds" loops back to App Pods.

### One control-loop cycle
1. Prometheus scrapes request rate, CPU, pending pods (15 s).
2. Reactive check (15 s): pending > 0 or CPU above threshold -> `reactive_target`.
3. Predictive check (30 s): ~30 min history -> Holt-Winters -> `predictive_target` for T+2 min.
4. Arbitrate: max, clamp.
5. Act on pods: set `Deployment.spec.replicas`.
6. Act on nodes: uncordon next reserve node if more schedulable capacity is needed (or cordon one back).
7. Converge: scheduler places pods on the now-schedulable node (image cached), pods Ready in seconds.

---

## 3. Decided technology stack

| Layer | Choice | Notes |
|---|---|---|
| Container runtime | **Rancher Desktop**, set to **dockerd (moby)**, built-in Kubernetes **disabled** | Its built-in k8s is single-node k3s, so cordon/uncordon of reserve nodes is impossible. Used only as the container engine to save RAM. |
| Multi-node cluster | **k3d** (k3s in Docker) | e.g. `k3d cluster create demo --agents 5`. Same stack as the AgileSphere testbed. |
| Metrics | **Prometheus** + **kube-state-metrics** (+ cAdvisor/kubelet metrics) | **Correction:** "pending pods" is not available by default; kube-state-metrics is required. |
| Baseline metrics | **metrics-server** | Needed only for the plain-HPA baseline run. |
| Decision logic | **Python 3**, **statsmodels** (Holt-Winters), official **Kubernetes Python client** | |
| Load generator | **k6** (Homebrew) | AGPL-3.0, CLI free. |
| Dashboards | **Grafana OSS** | |
| Live visual | **FastAPI + HTML** cluster map polling the Kubernetes API | |

**Cloud decision:** The user floated GCP/AWS. Recommendation given and (implicitly) accepted: **stay local** (Rancher Desktop + k3d). Reasons: IAM/quota/cluster-creation risk in 2 days, demo-day Wi-Fi dependency, hourly billing, local is free and offline-capable. Mention cloud (EKS/GKE with a node pool kept at zero for reserves) as **future work**. If cloud is ever required, GKE is easier than EKS. Keep local as fallback regardless.

---

## 4. Hardware plan

| | M4 Pro 24 GB (demo machine) | M4 Air 16 GB (backup) |
|---|---|---|
| Rancher VM | ~10 GB, 6 CPU | ~7 GB, 4 CPU |
| Cluster | 1 server, 3 active workers, 2 reserve | 1 server, 2 active, 1 reserve |
| Role | Everything live, projected | Identical clone plus screen-recorded full run |

- Run k6 on the **same machine** as the cluster (light; avoids Wi-Fi noise between laptops).
- Use arm64-friendly/multi-arch images (`python:3.12-slim`, Prometheus, Grafana are multi-arch).
- Scale is deliberately modest: ~3 active + 2 reserve nodes, ~20 max pods. State on a slide that it is a **scaled-down model** of JioHotstar.

---

## 5. Case study: JioHotstar match day

- A cricket match is a **known schedule**, so forecasting pays off.
- **Time compression:** one match (hours) -> a repeating **~10-minute cycle**: pre-match ramp, toss, first ball, boundary micro-spikes, innings-break dip, chase climb, final-overs peak.
- Say "tens of millions of concurrent viewers" qualitatively; do **not** quote a precise figure unless verified.
- **Deployed services:**
  - `playback-api`: the one **real autoscaled** service, with a CPU-burning endpoint so load is visible and a **~20 s readiness/startup delay** so scaling lag is clearly visible.
  - `gateway`, `catalog`: **cosmetic mock services** (not autoscaled) so the architecture looks like a streaming platform.
- Any traffic/seed data is **synthetic** (a model of match-day traffic); real JioHotstar data is not public. Say so on a slide.

---

## 6. Visual priorities for the evaluator (in order)

1. **Live cluster map** (FastAPI + HTML): nodes as boxes, pods as dots, reserve nodes greyed/dashed (matching the diagram). When the arbiter uncordons one it lights up and pods fill it. Most memorable visual.
2. **Grafana dashboard:** RPS, reactive target, predictive target, final max target, actual replicas, pending pods, p95 latency. Key picture: the predictive line rising *before* the traffic spike. Use annotations ("spike starts", "uncordon") to link cause and effect.
3. **Baseline vs engine comparison:** same trace, plain HPA vs this engine. Run live once; keep a pre-recorded version.

**Reserve-pool honesty trick:** Locally the image is already cached, so the warm-node benefit would be invisible. Make the cold path real: when no reserve is left, the arbiter runs `k3d node create` (~20-40 s). Contrast against uncordon (seconds). That contrast is the headline number. Cloud cold starts are slower (2-5 min), so the benefit would be larger there.

---

## 7. Key design decisions and corrections

1. **HPA conflict:** The arbiter and the HPA must **not** both control the same Deployment or they fight. For engine runs the **HPA is deleted**; the Reactive Arm reproduces the HPA's reactive logic. Reword the design doc's "alongside HPA behaviour" to "replaces the HPA's decision for that Deployment while reproducing its reactive logic in the Reactive Arm." For baseline runs, use HPA only.
2. **No ML training needed.** Holt-Winters is a statistical smoothing method (level, trend, seasonality; parameters alpha/beta/gamma), refit on each cycle from the last ~30 min in Prometheus. No offline training, saved model or GPU. (AgileSphere's transformer is the part this design deliberately drops.)
3. **History requirement:** needs at least ~2 full seasons. With a 10-min season at 15 s samples (40 points/season), ~80 points = ~20 min of traffic before forecasts are trustworthy. Until then the Reactive Arm covers (this is the cold-start argument).
   - **Warm-up options:** (a) replay the match-day trace with k6 for ~3 cycles (~30 min) before presenting and **do not tear down the cluster/Prometheus** (recommended for the presentation run); (b) seed the forecaster from a synthetic CSV of the same trace at startup (use for trials and as backup).
4. **Capacity calibration (most important single number):** forecast is in RPS but the arbiter needs replicas. Measure once, with a short k6 ramp on a single pod, how many RPS one `playback-api` pod handles at acceptable latency. Then `predictive_target = ceil(forecast_rps / rps_per_pod * safety_margin)`.
5. **Forecast horizon must exceed pod-ready time** (startup delay + scheduling), or early scaling is pointless.
6. **Seasonality limitation:** Holt-Winters only predicts patterns it has seen. A genuinely novel one-off spike is handled by the Reactive Arm. Good honest Q&A line and the reason for two arms.
7. **`--mode` flag** on the engine: `reactive`, `predictive`, `both`. Needed for the variance experiment and the live arm-kill demo.
8. **Forecast accuracy (MAPE):** split warm-up data; fit on earlier cycles, score against the held-out final cycle at the T+2 min horizon.

---

## 8. Experiments (how to prove it works)

| Experiment | What / metric | Live or pre-run |
|---|---|---|
| Scaling-lag comparison | Identical synthetic spike (e.g. RPS 50 -> 500 over 2 min), autoscaler off/HPA vs engine; time-to-adequate-capacity, error rate, p95 latency | **Live** (once), plus recording |
| Variance-reduction | Same trace over N trials for reactive-only, predictive-only, both; compare run-to-run **std dev of pending-pod-time** | **Pre-run** headlessly (compressed traces, run the night before); show bar chart |
| Reserve-pool promotion latency | Time from uncordon decision to pod Ready vs scheduling onto an active node, vs cold `k3d node create` | Pre-run or short live |
| Forecast accuracy | MAPE at T+2 min on a repeating pattern | Pre-run |
| Fallback safety | Kill reactive arm, kill predictive arm, kill both, disconnect Prometheus, exhaust reserve pool | **Live** (arm-kill at least) |
| Stability / churn | Count scale and cordon/uncordon events under noisy load; min/max bounds prevent flapping | Pre-run |

Baseline fairness: plain HPA with sensible default settings, stated openly on a slide (open question, see section 12).

---

## 9. Known limitations (state them openly)

- **Reserve pool is finite:** beyond N reserves, scale-up degrades to normal slower provisioning. Tunable knob; cost trade-off (paper saw ~50% higher node-seconds).
- **Arbiter is a single point of decision:** if it crashes neither arm can act. Leader election/standby replica is future work.
- **Predictive cold start:** needs ~20-30 min history; Reactive Arm covers it.
- **Reserve nodes cost while idle:** cheap on spot/on-prem, worse on full-price cloud VMs.
- **Both arms poll Prometheus independently:** negligible at this scale.
- Future work: leader election, multi-region reserve pools, GPU/transformer forecasting, real cloud deployment.

---

## 10. Suggested 6-minute demo flow

1. Show the architecture diagram and the JioHotstar match-day problem.
2. Run baseline HPA on the match trace: latency spikes, pods stay pending.
3. Run the engine on the same trace: scaling starts early, a reserve node lights up on the cluster map, Grafana shows the predictive line leading.
4. Kill the predictive arm: graceful fallback to reactive.
5. Show the variance chart, the uncordon-vs-cold-node numbers, and the finite-reserve limitation.

---

## 11. Two-day schedule (once implementation is approved)

- **Day 1:** Rancher (dockerd) + k3d up -> Prometheus + kube-state-metrics -> `playback-api` (+ mocks) -> calibrate RPS per pod -> reactive arm, predictive arm, arbiter -> cordon/uncordon logic -> one end-to-end spike test.
- **Day 2 morning:** k6 match-day trace, Grafana dashboard, cluster-map UI, start the long warm-up.
- **Day 2 afternoon:** shortened variance trials, screen recording of the full run, two full rehearsals with a 6-minute script. Backup clone on the Air.

---

## 12. Open questions (unresolved, ask the user before implementing)

1. **Time scale:** is a 10-minute compressed match acceptable to the mentor?
2. **Baseline fairness:** default-settings HPA (recommended) or tuned?
3. **Scale numbers:** ~3 active + 2 reserve nodes, ~20 max pods OK?
4. **Live vs pre-recorded split:** proposed live = spike comparison + arm-kill; pre-run = variance chart.
5. **Warm-up approach:** live ~30 min warm-up for the presentation run plus CSV seeding for trials (recommended), or CSV-only.
6. Whether to also build an optional Grafana dashboard JSON and the cluster map (recommended: yes, both).

---

## 13. Glossary (what each term is and why the demo needs it)

**Platform**
- **Container:** packaged app plus dependencies; everything ships as containers.
- **Rancher Desktop:** Mac app running a Linux VM with a container engine; lets the Mac run containers.
- **dockerd (moby):** Docker-API engine option in Rancher Desktop; k3d needs it.
- **Kubernetes (K8s):** orchestrator that keeps containers in a desired state; the thing being autoscaled.
- **Node:** one machine in the cluster; scaling nodes is half the story.
- **k3s / k3d:** lightweight Kubernetes / k3s clusters with each node as a Docker container (gives multi-node on a laptop).
- **Server / agent node:** control plane vs worker.
- **kubectl:** CLI for setup and live inspection.

**Workload**
- **Pod:** smallest runnable unit (dots on the cluster map).
- **Deployment / `spec.replicas`:** declares N copies; **the number this engine decides.**
- **Scheduler:** places pods on *schedulable* nodes (why cordon works).
- **Pending pod:** created but not yet placed/started; reactive early-warning signal.
- **Readiness probe / startup delay:** artificial ~20 s lag that makes the problem visible.

**Observability**
- **Prometheus / scrape / PromQL:** time-series DB pulling metrics every 15 s; query language used by both arms.
- **cAdvisor / kubelet metrics:** per-container CPU source.
- **kube-state-metrics:** exports object state such as pending pods; must be installed.
- **metrics-server:** in-cluster CPU/memory feed for the HPA baseline.
- **Grafana:** dashboards.
- **p95 latency:** response time 95% of requests beat; the user-visible damage metric.

**Decision engine**
- **HPA:** Kubernetes's built-in reactive autoscaler; the **baseline**.
- **Reactive Arm / Predictive Arm / Arbiter:** see section 2.
- **Holt-Winters, season, forecast horizon, warm-up:** see section 7.
- **Clamp (`minReplicas`/`maxReplicas`):** floor/ceiling on the target.
- **Per-arm fallback:** try/except abstention enabling the arm-kill demo.
- **Kubernetes Python client / statsmodels:** libraries used by the arbiter and forecaster.

**Reserve pool**
- **Cordon / uncordon:** mark node unschedulable / schedulable again (the "wake up" action, seconds).
- **Image pre-pull:** image already on the node, removing pull time.
- **Cold path (`k3d node create`):** building a brand-new node when reserves run out (~20-40 s).
- **Finite pool:** only N reserves exist; stated limitation.

**Load generation**
- **k6, RPS, VU:** load tool, requests per second, virtual user (simulated viewer).
- **Steady / spike / diurnal patterns:** stability, lag, and forecaster-feeding patterns.
- **Match-day trace / time compression:** custom JioHotstar pattern squeezed to ~10 min.

**Visuals and evaluation**
- **Live cluster map / FastAPI / annotations:** the memorable visual, its web framework, chart markers.
- **Time-to-adequate-capacity, pending-pod-time, trial, variance/std dev, MAPE, churn, promotion latency:** metrics defined in section 8.

---

## 14. Instructions for the next agent

- The user wants the **idea fully built out first**; confirm before generating code.
- When asked to implement, a proposed repo layout is: k3d/cluster setup script, Kubernetes manifests (`playback-api`, mocks, Prometheus, kube-state-metrics, Grafana, reserve-node prep), Python package (`reactive`, `predictive`, `arbiter`, `reserve_pool`, `--mode` flag), k6 match-day and spike scripts, trial runner for variance experiments, Grafana dashboard JSON, FastAPI cluster-map page, and a 6-minute demo script. Target Python with the Kubernetes client, as in the design doc.
- Keep the demo reproducible, offline-capable, and arm64-friendly.
- Keep claims honest: synthetic traffic, scaled-down model, finite reserve pool, local-vs-cloud cold-start difference.
