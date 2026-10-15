# Predictive-Reactive Kubernetes Autoscaler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a laptop-local Kubernetes autoscaler where a reactive arm (15s) and a Holt-Winters predictive arm (30s) are combined by a `max()` arbiter that also promotes cordoned warm reserve nodes. Then run the five feasibility experiments.

**Architecture:** A kind cluster (1 control-plane, 2 active workers, 3 cordoned reserve workers) runs a CPU-bound Flask app, exposed through a NodePort. kube-prometheus-stack scrapes cAdvisor and kube-state-metrics. One Python process (`autoscaler/arbiter.py`) calls both arms as plain functions each cycle and is the only thing that writes to the cluster: Deployment scale and node `spec.unschedulable`. Pure decision logic is kept apart from Prometheus/Kubernetes I/O so all logic is unit-tested without a cluster. Live behaviour is checked with scripts against the real cluster.

**Tech Stack:** Docker Desktop, kind, kubectl, Helm (kube-prometheus-stack), Python 3.14 venv (kubernetes, requests, statsmodels, numpy, flask, pytest), k6.

**Spec:** `docs/spec/BUILD_GUIDE.md` (copied from `~/Downloads/BUILD_GUIDE.md`) plus `docs/spec/Feasibility_Analysis_Predictive_Autoscaling.docx` (experiments and failure semantics). Both are copied in during Task 1.

## Global Constraints

- Cluster name `autoscaler-demo`; kubectl context `kind-autoscaler-demo`.
- Nodes: 1 control-plane + 2 active workers (`role=active`) + 3 reserve workers (`role=reserve`, cordoned at creation). The guide's prose says "3 active + 3 reserve", but its YAML defines 2 active workers, so this plan follows the YAML.
- App: Deployment + Service both named `sample-app`, namespace `default`, image `sample-app:1.0`, CPU request `150m`, limit `300m`.
- `MIN_REPLICAS, MAX_REPLICAS = 2, 12`; `PODS_PER_NODE = 4`; `COOLDOWN_SECONDS = 20`.
- Reactive arm period 15s; predictive arm period 30s; forecast horizon 2 minutes = 8 steps of 15s; predictive needs ≥ 40 history points before recommending.
- Arbiter rule: `target = clamp(max(reactive_target, predictive_target))`, ignoring arms that returned `None`.
- Each arm wraps its whole computation in try/except and returns `None` on any failure. If both arms return `None`, the arbiter makes **no** change and logs a warning.
- The arbiter is the **only** component that patches Deployment replicas or node schedulability.
- Everything free/open-source and local. No cloud accounts.
- Project root: `/Users/aanchalgupta/VSCode/ACC/micro project/autoscaler-project` (the parent path contains a space, so **always quote paths**). All commands below run from the project root with the venv active (`source .venv/bin/activate`) unless stated otherwise.

## Deviations From the Build Guide (and why)

The guide's code, run as written, would never promote a reserve node or trigger the CPU rule on kind. Each fix below is applied in the task noted.

| # | Guide says | Problem | Plan does | Task |
|---|---|---|---|---|
| D1 | Reserve nodes `autoscaler-demo-worker-3..5` | kind names workers `…-worker`, `…-worker2`, `…-worker3` (no hyphen) | Script discovers workers and labels/cordons by sorted index | 2 |
| D2 | CPU request 150m decides node capacity | Every kind node reports the laptop's *full* CPU count, so ~50 pods fit per node. Pods never go Pending and reserves never get used | Advertise extended resource `autoscaler.demo/slot = 4` per worker; each pod requests 1 slot, so capacity is exactly `PODS_PER_NODE` | 2, 3 |
| D3 | `kubectl port-forward svc/sample-app 8080:80` | port-forward pins to **one pod**, so new replicas never receive load | NodePort 30080 + kind `extraPortMappings` → `localhost:8080`, balanced by kube-proxy. Prometheus the same way on 30090 → `localhost:9090` | 2, 3, 4 |
| D4 | `avg(rate(container_cpu_usage_seconds_total{pod=~"sample-app.*"}[1m]))*100 > 80` | That's % of one core. With a 300m limit it can't exceed ~30, so the rule never fires. The unfiltered selector also double-counts pod-level cgroup series | CPU utilization = % of CPU **request** (HPA semantics), filtered on `container="sample-app"` | 5 |
| D5 | Pending = `kube_pod_status_phase{phase="Pending"}` | Phase Pending includes ContainerCreating, so every scale-up looks "pending" next cycle and the target keeps climbing | Reactive arm uses `kube_pod_status_unschedulable` (pods that truly can't be placed) | 5 |
| D6 | `query_range` with `start="-30m"`, `end="now"` | Prometheus rejects relative strings (HTTP 400), so the predictive arm always fails | Numeric unix timestamps | 5 |
| D7 | Forecast *average per-pod* CPU, divide by `CAPACITY_PER_POD = 20` req/s | Units don't match (cores vs req/s), and the per-pod average shrinks as replicas grow (feedback loop) | Forecast **total** app CPU cores; `replicas = ceil(cores / (0.150 × 80%))` | 7 |
| D8 | `ExponentialSmoothing(trend="add", seasonal=None)` called "Holt-Winters" | That is Holt's linear trend (no seasonality) | Same default, plus optional additive seasonality via `HW_SEASONAL_PERIODS` once 2 seasons of history exist | 7 |
| D9 | Arbiter calls predictive every 15s | Spec says 30s | `PredictiveArm` caches its result for 30s | 7 |
| D10 | `active_nodes = list_node("role!=reserve")` | Counts the control-plane and never counts promoted reserves. With MAX 12 it needs 4 > 3 nodes, so it **never uncordons** | Count schedulable workers. Capacity check runs every cycle (even inside cooldown or when replicas are unchanged) and can promote several nodes | 8 |
| D11 | "comment out `compute_predictive_target`" | Error-prone across 5–10 trials | `--mode both\|reactive\|predictive` flag, plus fault-injection files to kill an arm live | 9 |
| D12 | k6 VUs with `sleep(0.1)` | Closed-loop load slows itself down when the app is slow, which hides under-provisioning | Open-model `ramping-arrival-rate` with `BASE_RPS`/`PEAK_RPS` env vars, calibrated in Task 10 | 10 |
| D13 | MAPE of `predicted_target` vs pending pods | Pending pods are mostly 0, so MAPE is undefined | MAPE of forecast total CPU at T+2min vs the CPU actually observed at T+2min | 11 |
| D14 | `pip install … --break-system-packages` | Pollutes system Python 3.14 | Project `.venv` | 1 |
| D15 | Warm-up "~30 min" | 40 points × 15s = 10 min | README states 10 min | 13 |
| D16 | Reactive: `cpu > 80 → current+1`, else `target = current` | `max(current, predictive) ≥ current`, so in both-arms mode the system can **never scale down** or return reserve nodes. +1 per cycle also needs ~5 min to go from 2 to 12 | HPA-style proportional rule `ceil(current × cpu / 80%)` with a ±10% hold band (scale-up still ≥ +1). Unschedulable pods still add `+pending`. A CPU query with no data makes the arm abstain (`None`) instead of reading as 0% | 5, 6 |

## File Structure

```
autoscaler-project/
├── .gitignore
├── requirements.txt
├── pytest.ini                     # pythonpath = autoscaler app experiments
├── kind-config.yaml               # 1 cp + 5 workers, NodePort host mappings
├── README.md                      # quick start, experiments, results, limitations
├── docs/spec/                     # BUILD_GUIDE.md + feasibility docx (copied)
├── scripts/
│   ├── cluster-up.sh              # create cluster, label active/reserve, slots, cordon
│   ├── verify-cluster.sh          # asserts 2 active + 3 cordoned reserve, 4 slots
│   ├── deploy-app.sh              # build image, kind load (pre-warms reserves), apply
│   ├── install-monitoring.sh      # helm upgrade --install kube-prometheus-stack
│   ├── verify-monitoring.sh       # asserts the PromQL the arms use returns data
│   └── reset.sh                   # replicas→2, drain+cordon reserves (between trials)
├── app/
│   ├── app.py                     # Flask CPU-bound "/" + "/healthz"
│   ├── Dockerfile
│   └── deployment.yaml            # Deployment (slot request) + NodePort Service
├── monitoring/
│   └── prometheus-values.yaml     # lean kube-prometheus-stack for kind
├── autoscaler/
│   ├── common.py                  # config, PromQL strings, Prometheus + lazy k8s client, clamp
│   ├── reactive_arm.py            # reactive_target() pure + compute_reactive_target()
│   ├── predictive_arm.py          # forecast_load(), replicas_for_load(), PredictiveArm
│   ├── decision_log.py            # CSV writer for per-cycle decisions
│   └── arbiter.py                 # decide() pure, ClusterAdapter, Arbiter, CLI
├── loadtest/
│   ├── summary.js                 # shared handleSummary (p95, failures, JSON export)
│   ├── steady.js                  # constant (optionally jittered) arrival rate
│   ├── spike.js                   # baseline → spike → hold → recover
│   └── diurnal.js                 # repeating sine wave
├── experiments/
│   ├── metrics.py                 # pure: pod_seconds, time_to_capacity, mape, align, churn
│   ├── run_trials.py              # Exp 1 + 2: N spike trials per mode → results/trials.csv
│   ├── promotion_latency.py       # Exp 3: uncordon→Ready vs active-node scheduling
│   └── analyze_log.py             # Exp 5 MAPE + stability churn from decision CSVs
├── results/                       # experiment outputs (gitignored except summary.md)
└── tests/
    ├── test_environment.py
    ├── test_app.py
    ├── test_common.py
    ├── test_reactive_arm.py
    ├── test_predictive_arm.py
    ├── test_arbiter_decide.py
    ├── test_arbiter_runtime.py
    └── test_metrics.py
```

---

### Task 1: Toolchain and project scaffold

**Files:**
- Create: `.gitignore`, `requirements.txt`, `pytest.ini`, `tests/test_environment.py`, `docs/spec/` (copies)

**Interfaces:**
- Consumes: nothing
- Produces: `.venv` with all Python deps; `pytest` configured with `pythonpath = autoscaler app experiments` and `testpaths = tests`, so tests `import common`, `import app`, `import metrics` directly.

- [ ] **Step 1: Start Docker Desktop and give it resources**

Open Docker Desktop → Settings → Resources: CPUs ≥ 6, Memory ≥ 8 GB, then Apply & Restart. (The daemon is currently **not running**, and `docker info` shows no server.)

Run: `docker info --format '{{.NCPU}} CPUs / {{.MemTotal}} bytes'`
Expected: non-zero values, e.g. `8 CPUs / 8589934592 bytes`

- [ ] **Step 2: Install missing CLIs** (kubectl 1.36 and Python 3.14 are already present)

```bash
brew install kind helm k6
```

Run: `kind version && helm version --short && k6 version`
Expected: three version lines, no "command not found".

- [ ] **Step 3: Create the repo and copy the spec**

```bash
cd "/Users/aanchalgupta/VSCode/ACC/micro project/autoscaler-project"
git init
mkdir -p docs/spec app monitoring autoscaler loadtest experiments scripts results tests
cp ~/Downloads/BUILD_GUIDE.md docs/spec/
cp "../Feasibility_Analysis_Predictive_Autoscaling.docx" "../presentation_architecture.png" docs/spec/
```

- [ ] **Step 4: Write config files**

`.gitignore`:
```
.venv/
__pycache__/
.pytest_cache/
autoscaler/faults/
results/*
!results/summary.md
```

`requirements.txt`:
```
kubernetes>=31
requests>=2.32
statsmodels>=0.14.5
numpy>=2.0
flask>=3.0
pytest>=8.0
```

`pytest.ini`:
```ini
[pytest]
pythonpath = autoscaler app experiments
testpaths = tests
```

- [ ] **Step 5: Write the failing environment test**

`tests/test_environment.py`:
```python
import importlib

import pytest


@pytest.mark.parametrize("module", ["kubernetes", "requests", "statsmodels", "numpy", "flask"])
def test_dependency_importable(module):
    importlib.import_module(module)
```

Run: `python3 -m pytest -q` (system Python, before the venv exists)
Expected: FAIL with `ModuleNotFoundError` for at least `kubernetes`/`statsmodels` (or `No module named pytest`).

- [ ] **Step 6: Create venv, install, re-run**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```
Expected: `5 passed`

- [ ] **Step 7: Commit**

```bash
git add .gitignore requirements.txt pytest.ini tests/test_environment.py docs/
git commit -m "chore: scaffold autoscaler project with venv and spec docs"
```

---

### Task 2: kind cluster with a warm, cordoned reserve pool

**Files:**
- Create: `kind-config.yaml`, `scripts/cluster-up.sh`, `scripts/verify-cluster.sh`

**Interfaces:**
- Consumes: Docker, kind, kubectl (Task 1)
- Produces: nodes labelled `role=active` (2, schedulable) and `role=reserve` (3, cordoned); every worker advertises `autoscaler.demo/slot: 4`; host ports `8080→NodePort 30080` and `9090→NodePort 30090`. `SLOTS_PER_NODE` (4) must equal `PODS_PER_NODE` in `autoscaler/common.py` (Task 5).

- [ ] **Step 1: Write the verification script (the "test")**

`scripts/verify-cluster.sh`:
```bash
#!/usr/bin/env bash
# Asserts the reserve-pool topology the arbiter depends on.
set -euo pipefail
fail() { echo "FAIL: $*"; exit 1; }
count() { kubectl get nodes -l "$1" -o name | wc -l | tr -d ' '; }
cordoned() { kubectl get nodes -l "$1" -o jsonpath='{range .items[*]}{.spec.unschedulable}{"\n"}{end}' | grep -c true || true; }

kubectl config current-context | grep -qx kind-autoscaler-demo || fail "context is not kind-autoscaler-demo"
[ "$(count role=active)" = 2 ] || fail "expected 2 active nodes, got $(count role=active)"
[ "$(count role=reserve)" = 3 ] || fail "expected 3 reserve nodes, got $(count role=reserve)"
[ "$(cordoned role=active)" = 0 ] || fail "active nodes must be schedulable"
[ "$(cordoned role=reserve)" = 3 ] || fail "all reserve nodes must start cordoned"
for n in $(kubectl get nodes -l 'role in (active,reserve)' -o jsonpath='{.items[*].metadata.name}'); do
  slots=$(kubectl get node "$n" -o jsonpath='{.status.allocatable.autoscaler\.demo/slot}')
  [ "$slots" = 4 ] || fail "$n advertises '$slots' slots, expected 4"
done
echo "PASS: 2 active + 3 cordoned reserve nodes, 4 slots each"
```

Run: `chmod +x scripts/*.sh && scripts/verify-cluster.sh`
Expected: `FAIL: context is not kind-autoscaler-demo` (or a kubectl connection error).

- [ ] **Step 2: Write the kind config**

`kind-config.yaml`:
```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: autoscaler-demo
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30080   # sample-app NodePort -> http://localhost:8080
        hostPort: 8080
      - containerPort: 30090   # Prometheus NodePort -> http://localhost:9090
        hostPort: 9090
  - role: worker   # active-1
  - role: worker   # active-2
  - role: worker   # reserve-1
  - role: worker   # reserve-2
  - role: worker   # reserve-3
```

- [ ] **Step 3: Write the cluster bootstrap script**

`scripts/cluster-up.sh`:
```bash
#!/usr/bin/env bash
# Create the kind cluster and turn workers 3-5 into a cordoned warm reserve pool.
set -euo pipefail
CLUSTER=autoscaler-demo
SLOTS_PER_NODE="${SLOTS_PER_NODE:-4}"   # must equal PODS_PER_NODE in autoscaler/common.py
cd "$(dirname "$0")/.."

if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --config kind-config.yaml
fi
kubectl config use-context "kind-$CLUSTER"
kubectl wait --for=condition=Ready nodes --all --timeout=180s

# kind names workers <cluster>-worker, <cluster>-worker2, ... ; sorted, the first two are active.
WORKERS=($(kubectl get nodes -l '!node-role.kubernetes.io/control-plane' \
  -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | sort))

for i in "${!WORKERS[@]}"; do
  node="${WORKERS[$i]}"
  if [ "$i" -lt 2 ]; then role=active; else role=reserve; fi
  kubectl label node "$node" role="$role" --overwrite
  # Every kind node sees the whole laptop's CPU, so capacity is modelled as a fixed slot count.
  kubectl patch node "$node" --subresource=status --type=json \
    -p "[{\"op\":\"add\",\"path\":\"/status/capacity/autoscaler.demo~1slot\",\"value\":\"$SLOTS_PER_NODE\"}]"
  if [ "$role" = reserve ]; then kubectl cordon "$node"; else kubectl uncordon "$node"; fi
done

kubectl get nodes -L role
```

- [ ] **Step 4: Create the cluster and verify**

Run: `scripts/cluster-up.sh && scripts/verify-cluster.sh`
Expected: `kubectl get nodes -L role` shows `autoscaler-demo-worker3..5` as `Ready,SchedulingDisabled` with `ROLE=reserve`, then `PASS: 2 active + 3 cordoned reserve nodes, 4 slots each`.

If the slot check fails right after creation, wait 10s and re-run `scripts/verify-cluster.sh` (allocatable updates on the next kubelet status sync).

- [ ] **Step 5: Confirm it's idempotent**

Run: `scripts/cluster-up.sh && scripts/verify-cluster.sh`
Expected: no "cluster already exists" error; `PASS` again.

- [ ] **Step 6: Commit**

```bash
git add kind-config.yaml scripts/cluster-up.sh scripts/verify-cluster.sh
git commit -m "feat: kind cluster with cordoned reserve pool and fixed per-node slots"
```

---

### Task 3: Sample application (slot-bounded, reachable through NodePort)

**Files:**
- Create: `app/app.py`, `app/Dockerfile`, `app/deployment.yaml`, `scripts/deploy-app.sh`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: cluster + `autoscaler.demo/slot` capacity (Task 2)
- Produces: Deployment `sample-app` (label `app=sample-app`, container name `sample-app`, CPU request 150m, 1 slot per pod) and Service `sample-app` of type NodePort 30080 → `http://localhost:8080/`. `WORK_ITERATIONS` env var tunes CPU cost per request (Task 10 calibration).

- [ ] **Step 1: Write the failing test**

`tests/test_app.py`:
```python
import app as app_module


def test_index_does_work_and_returns_ok(monkeypatch):
    monkeypatch.setattr(app_module, "WORK_ITERATIONS", 10)
    response = app_module.app.test_client().get("/")
    assert response.status_code == 200
    assert response.data == b"ok"


def test_healthz_is_ok():
    response = app_module.app.test_client().get("/healthz")
    assert response.status_code == 200
```

Run: `pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'` (or `AttributeError` on `app_module.app`).

- [ ] **Step 2: Write the app**

`app/app.py`:
```python
import os

from flask import Flask

WORK_ITERATIONS = int(os.environ.get("WORK_ITERATIONS", "200000"))

app = Flask(__name__)


@app.route("/")
def index():
    # simulate CPU-bound work so load is visible in container CPU metrics
    x = 0
    for _ in range(WORK_ITERATIONS):
        x += 1
    return "ok", 200


@app.route("/healthz")
def healthz():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_app.py -v`
Expected: `2 passed`

- [ ] **Step 4: Write Dockerfile, manifest and deploy script**

`app/Dockerfile`:
```dockerfile
FROM python:3.11-slim
RUN pip install --no-cache-dir "flask>=3,<4"
COPY app.py /app.py
EXPOSE 8080
CMD ["python", "/app.py"]
```

`app/deployment.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sample-app
spec:
  replicas: 2
  selector:
    matchLabels: { app: sample-app }
  template:
    metadata:
      labels: { app: sample-app }
    spec:
      # prefer active nodes; reserve nodes are only used once the arbiter uncordons them
      affinity:
        nodeAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              preference:
                matchExpressions:
                  - key: role
                    operator: NotIn
                    values: ["reserve"]
      containers:
        - name: sample-app
          image: sample-app:1.0
          imagePullPolicy: IfNotPresent
          env:
            - name: WORK_ITERATIONS
              value: "200000"
          resources:
            # one slot per pod caps each node at PODS_PER_NODE app pods (see scripts/cluster-up.sh)
            requests: { cpu: "150m", autoscaler.demo/slot: "1" }
            limits: { cpu: "300m", autoscaler.demo/slot: "1" }
          ports:
            - containerPort: 8080
          readinessProbe:
            httpGet: { path: /healthz, port: 8080 }
            periodSeconds: 2
            timeoutSeconds: 3
            failureThreshold: 5
---
apiVersion: v1
kind: Service
metadata:
  name: sample-app
spec:
  type: NodePort
  selector: { app: sample-app }
  ports:
    - port: 80
      targetPort: 8080
      nodePort: 30080
```

`scripts/deploy-app.sh`:
```bash
#!/usr/bin/env bash
# Build the image, load it onto EVERY node (this pre-warms the reserve pool), and deploy.
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -t sample-app:1.0 app/
kind load docker-image sample-app:1.0 --name autoscaler-demo
kubectl apply -f app/deployment.yaml
kubectl rollout status deployment/sample-app --timeout=120s
```

- [ ] **Step 5: Deploy and verify end to end**

```bash
chmod +x scripts/deploy-app.sh
scripts/deploy-app.sh
curl -s http://localhost:8080/ ; echo
kubectl get pods -l app=sample-app -o wide
```
Expected: `ok`; 2 pods `Running`, both on `autoscaler-demo-worker` / `autoscaler-demo-worker2` (never worker3–5).

- [ ] **Step 6: Prove slot capacity produces unschedulable pods (the reserve-pool trigger)**

```bash
kubectl scale deployment/sample-app --replicas=10
sleep 10
kubectl get pods -l app=sample-app --field-selector=status.phase=Pending
kubectl describe pod $(kubectl get pods -l app=sample-app --field-selector=status.phase=Pending -o name | head -1) | grep -i insufficient
kubectl scale deployment/sample-app --replicas=2
```
Expected: exactly 2 Pending pods (8 slots on 2 active nodes), with an event containing `Insufficient autoscaler.demo/slot` (and `node(s) were unschedulable` for the reserves).

- [ ] **Step 7: Commit**

```bash
git add app/ scripts/deploy-app.sh tests/test_app.py
git commit -m "feat: CPU-bound sample app with slot requests and NodePort service"
```

---

### Task 4: Prometheus (kube-prometheus-stack) reachable at localhost:9090

**Files:**
- Create: `monitoring/prometheus-values.yaml`, `scripts/install-monitoring.sh`, `scripts/verify-monitoring.sh`

**Interfaces:**
- Consumes: cluster (Task 2), running `sample-app` (Task 3)
- Produces: Prometheus at `http://localhost:9090` with 15s scrapes of cAdvisor (`container_cpu_usage_seconds_total`) and kube-state-metrics (`kube_pod_status_unschedulable`, `kube_pod_status_phase`, `kube_pod_container_resource_requests`, `kube_deployment_status_replicas_available|unavailable`). The Prometheus CR is named `monitoring-kube-prometheus-prometheus` (used in the Task 13 outage test).

- [ ] **Step 1: Write the verification script (the "test")**

`scripts/verify-monitoring.sh`:
```bash
#!/usr/bin/env bash
# Asserts that every PromQL expression the arms depend on returns data.
set -euo pipefail
PROM="${PROM_URL:-http://localhost:9090}"
SEL='namespace="default",container="sample-app"'
q() {
  curl -fsG "$PROM/api/v1/query" --data-urlencode "query=$1" |
    python3 -c 'import json,sys; r=json.load(sys.stdin)["data"]["result"]; print(r[0]["value"][1] if r else "EMPTY")'
}

for _ in $(seq 1 36); do curl -fs "$PROM/-/ready" >/dev/null 2>&1 && break; sleep 5; done
curl -fs "$PROM/-/ready" >/dev/null || { echo "FAIL: Prometheus not ready at $PROM"; exit 1; }

# cAdvisor needs a couple of scrapes before rate() has data
for _ in $(seq 1 36); do [ "$(q "sum(rate(container_cpu_usage_seconds_total{$SEL}[1m]))")" != EMPTY ] && break; sleep 5; done

declare -a CHECKS=(
  "sum(rate(container_cpu_usage_seconds_total{$SEL}[1m]))"
  "sum(kube_pod_container_resource_requests{$SEL,resource=\"cpu\"})"
  "sum(kube_pod_status_unschedulable{namespace=\"default\",pod=~\"sample-app-.*\"}) or vector(0)"
  "kube_deployment_status_replicas_available{namespace=\"default\",deployment=\"sample-app\"}"
)
for expr in "${CHECKS[@]}"; do
  v=$(q "$expr")
  [ "$v" != EMPTY ] || { echo "FAIL: no data for: $expr"; exit 1; }
  echo "ok  $v  <=  $expr"
done
echo "PASS: Prometheus exposes all autoscaler metrics"
```

Run: `chmod +x scripts/verify-monitoring.sh && scripts/verify-monitoring.sh`
Expected: after ~3 minutes, `FAIL: Prometheus not ready at http://localhost:9090`.

- [ ] **Step 2: Write lean Helm values**

`monitoring/prometheus-values.yaml`:
```yaml
# Lean kube-prometheus-stack for a laptop kind cluster.
alertmanager:
  enabled: false
grafana:
  enabled: false          # optional: set true to graph both arms' targets
nodeExporter:
  enabled: false          # not needed; its hostPath mounts often fail under Docker Desktop
kubeEtcd:
  enabled: false
kubeControllerManager:
  enabled: false
kubeScheduler:
  enabled: false
kubeProxy:
  enabled: false
defaultRules:
  create: false
prometheus:
  service:
    type: NodePort
    nodePort: 30090       # kind maps this to localhost:9090
  prometheusSpec:
    scrapeInterval: 15s
    evaluationInterval: 15s
    retention: 12h
    nodeSelector:
      role: active        # keep monitoring off reserve nodes
    resources:
      requests: { cpu: 200m, memory: 512Mi }
prometheusOperator:
  nodeSelector:
    role: active
kube-state-metrics:
  nodeSelector:
    role: active
```

- [ ] **Step 3: Write the install script**

`scripts/install-monitoring.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo update
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f monitoring/prometheus-values.yaml \
  --wait --timeout 10m
kubectl -n monitoring get pods -o wide
```

- [ ] **Step 4: Install and verify**

Run: `chmod +x scripts/install-monitoring.sh && scripts/install-monitoring.sh && scripts/verify-monitoring.sh`
Expected: all monitoring pods `Running` on active workers only; four `ok` lines (CPU request sum ≈ `0.3` for 2 pods; unschedulable `0`; available `2`); then `PASS: Prometheus exposes all autoscaler metrics`.

If the CPU check stays EMPTY, open `http://localhost:9090/targets` and confirm `serviceMonitor/monitoring/monitoring-kube-prometheus-kubelet` (cAdvisor endpoint) is UP.

- [ ] **Step 5: Commit**

```bash
git add monitoring/ scripts/install-monitoring.sh scripts/verify-monitoring.sh
git commit -m "feat: lean kube-prometheus-stack exposed on localhost:9090"
```

---

### Task 5: Shared helpers (`common.py`)

**Files:**
- Create: `autoscaler/common.py`
- Test: `tests/test_common.py`

**Interfaces:**
- Consumes: Prometheus at `PROM_URL` (Task 4), kubeconfig context (Task 2) — only when called, never at import
- Produces:
  - Constants: `PROM_URL: str`, `DEPLOYMENT_NAME = "sample-app"`, `NAMESPACE = "default"`, `MIN_REPLICAS = 2`, `MAX_REPLICAS = 12`, `CPU_REQUEST_CORES = 0.15`, `TARGET_CPU_UTILIZATION = 80.0`, `PODS_PER_NODE = 4` (all env-overridable)
  - PromQL strings: `UNSCHEDULABLE_QUERY`, `CPU_UTILIZATION_QUERY`, `TOTAL_CPU_QUERY`
  - `k8s() -> tuple[AppsV1Api, CoreV1Api]` (lazy, cached)
  - `prom_query(promql: str, allow_empty: bool = True) -> float` (0.0 if empty; raises `ValueError` if empty and `allow_empty=False`)
  - `prom_query_range(promql: str, minutes: float, step_seconds: int = 15, end: float | None = None) -> list[tuple[float, float]]`
  - `pending_pods() -> float`, `cpu_utilization_pct() -> float` (raises `ValueError` when Prometheus has no data: unknown ≠ idle), `current_replicas() -> int`, `clamp(n: int) -> int`

- [ ] **Step 1: Write the failing tests**

`tests/test_common.py`:
```python
import pytest

import common


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def fake_get_returning(payload, captured):
    def fake_get(url, params, timeout):
        captured.update(url=url, params=params)
        return FakeResponse(payload)
    return fake_get


def test_prom_query_returns_first_sample_value(monkeypatch):
    captured = {}
    payload = {"data": {"result": [{"value": [1700000000.0, "3.5"]}]}}
    monkeypatch.setattr(common.requests, "get", fake_get_returning(payload, captured))
    assert common.prom_query("up") == 3.5
    assert captured["url"] == f"{common.PROM_URL}/api/v1/query"
    assert captured["params"] == {"query": "up"}


def test_prom_query_empty_result_is_zero(monkeypatch):
    monkeypatch.setattr(common.requests, "get", fake_get_returning({"data": {"result": []}}, {}))
    assert common.prom_query("up") == 0.0


def test_prom_query_can_require_data(monkeypatch):
    monkeypatch.setattr(common.requests, "get", fake_get_returning({"data": {"result": []}}, {}))
    with pytest.raises(ValueError):
        common.prom_query("up", allow_empty=False)


def test_prom_query_range_uses_numeric_timestamps(monkeypatch):
    captured = {}
    payload = {"data": {"result": [{"values": [[100.0, "1.5"], [115.0, "2.0"]]}]}}
    monkeypatch.setattr(common.requests, "get", fake_get_returning(payload, captured))
    points = common.prom_query_range("x", minutes=30, step_seconds=15, end=10_000.0)
    assert points == [(100.0, 1.5), (115.0, 2.0)]
    assert captured["url"] == f"{common.PROM_URL}/api/v1/query_range"
    assert captured["params"]["start"] == 10_000.0 - 1800
    assert captured["params"]["end"] == 10_000.0
    assert captured["params"]["step"] == 15


def test_prom_query_range_empty_result_is_empty_list(monkeypatch):
    monkeypatch.setattr(common.requests, "get", fake_get_returning({"data": {"result": []}}, {}))
    assert common.prom_query_range("x", minutes=1) == []


@pytest.mark.parametrize("n, expected", [(0, 2), (2, 2), (7, 7), (12, 12), (40, 12)])
def test_clamp(n, expected):
    assert common.clamp(n) == expected


def test_cpu_queries_target_only_the_app_container():
    for query in (common.CPU_UTILIZATION_QUERY, common.TOTAL_CPU_QUERY):
        assert 'container="sample-app"' in query
    assert "kube_pod_container_resource_requests" in common.CPU_UTILIZATION_QUERY
    assert "kube_pod_status_unschedulable" in common.UNSCHEDULABLE_QUERY
```

Run: `pytest tests/test_common.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'common'`

- [ ] **Step 2: Write the implementation**

`autoscaler/common.py`:
```python
"""Shared config, PromQL, and Prometheus/Kubernetes helpers for both arms and the arbiter."""
import os
import time
from functools import lru_cache

import requests

PROM_URL = os.environ.get("PROM_URL", "http://localhost:9090")
DEPLOYMENT_NAME = os.environ.get("DEPLOYMENT_NAME", "sample-app")
NAMESPACE = os.environ.get("NAMESPACE", "default")
MIN_REPLICAS = int(os.environ.get("MIN_REPLICAS", "2"))
MAX_REPLICAS = int(os.environ.get("MAX_REPLICAS", "12"))
CPU_REQUEST_CORES = float(os.environ.get("CPU_REQUEST_CORES", "0.15"))  # must match app/deployment.yaml
TARGET_CPU_UTILIZATION = float(os.environ.get("TARGET_CPU_UTILIZATION", "80"))  # % of CPU request
PODS_PER_NODE = int(os.environ.get("PODS_PER_NODE", "4"))  # must match SLOTS_PER_NODE in cluster-up.sh

_APP = f'namespace="{NAMESPACE}",container="{DEPLOYMENT_NAME}"'

# pods the scheduler cannot place (phase=Pending would also count ContainerCreating)
UNSCHEDULABLE_QUERY = (
    f'sum(kube_pod_status_unschedulable{{namespace="{NAMESPACE}",pod=~"{DEPLOYMENT_NAME}-.*"}}) or vector(0)'
)
# total app CPU in cores
TOTAL_CPU_QUERY = f"sum(rate(container_cpu_usage_seconds_total{{{_APP}}}[1m]))"
# utilization as a % of CPU request, like the HPA
CPU_UTILIZATION_QUERY = (
    f'100 * {TOTAL_CPU_QUERY} / sum(kube_pod_container_resource_requests{{{_APP},resource="cpu"}})'
)


@lru_cache(maxsize=1)
def k8s():
    """Load kubeconfig on first use so importing this module never needs a cluster."""
    from kubernetes import client, config

    config.load_kube_config()
    return client.AppsV1Api(), client.CoreV1Api()


def prom_query(promql, allow_empty=True):
    r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": promql}, timeout=5)
    r.raise_for_status()
    result = r.json()["data"]["result"]
    if not result:
        if allow_empty:
            return 0.0
        raise ValueError(f"no data for: {promql}")
    return float(result[0]["value"][1])


def prom_query_range(promql, minutes, step_seconds=15, end=None):
    end = time.time() if end is None else end
    params = {"query": promql, "start": end - minutes * 60, "end": end, "step": step_seconds}
    r = requests.get(f"{PROM_URL}/api/v1/query_range", params=params, timeout=10)
    r.raise_for_status()
    result = r.json()["data"]["result"]
    return [(float(ts), float(v)) for ts, v in result[0]["values"]] if result else []


def pending_pods():
    return prom_query(UNSCHEDULABLE_QUERY)


def cpu_utilization_pct():
    # no data means "unknown", not "idle": raise so the reactive arm abstains instead of scaling down
    return prom_query(CPU_UTILIZATION_QUERY, allow_empty=False)


def current_replicas():
    apps_v1, _ = k8s()
    return apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE).spec.replicas


def clamp(n):
    return max(MIN_REPLICAS, min(MAX_REPLICAS, n))
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_common.py -v`
Expected: `11 passed`

- [ ] **Step 4: Live smoke check against the cluster**

Run: `python -c "import sys; sys.path.insert(0,'autoscaler'); import common as c; print(c.pending_pods(), round(c.cpu_utilization_pct(),1), c.current_replicas())"`
Expected: something like `0.0 3.2 2` (idle utilization is a few %).

- [ ] **Step 5: Commit**

```bash
git add autoscaler/common.py tests/test_common.py
git commit -m "feat: shared Prometheus/k8s helpers with request-relative CPU utilization"
```

---

### Task 6: Reactive arm

**Files:**
- Create: `autoscaler/reactive_arm.py`
- Test: `tests/test_reactive_arm.py`

**Interfaces:**
- Consumes (from `common`): `pending_pods() -> float`, `cpu_utilization_pct() -> float` (raises when no data), `current_replicas() -> int`, `clamp(int) -> int`, `TARGET_CPU_UTILIZATION = 80.0`
- Produces:
  - `reactive_target(pending: float, cpu_pct: float, current: int) -> int` (pure)
  - `compute_reactive_target() -> int | None` (`None` on any failure)
  - `TOLERANCE = 0.1`

- [ ] **Step 1: Write the failing tests**

`tests/test_reactive_arm.py`:
```python
import pytest

import reactive_arm


@pytest.mark.parametrize(
    "pending, cpu, current, expected",
    [
        (2, 10.0, 4, 6),     # unschedulable pods: add that many replicas
        (0.5, 10.0, 4, 5),   # any unschedulable pod adds at least one
        (0, 160.0, 4, 8),    # 2x target utilization: double
        (0, 90.0, 4, 5),     # just outside the band: at least +1
        (0, 85.0, 4, 4),     # within ±10% of 80%: hold steady
        (0, 40.0, 8, 4),     # half target utilization: halve
        (0, 0.0, 6, 2),      # idle: clamped to MIN_REPLICAS
        (5, 0.0, 11, 12),    # clamped to MAX_REPLICAS
        (0, 200.0, 10, 12),  # clamped to MAX_REPLICAS
    ],
)
def test_reactive_target(pending, cpu, current, expected):
    assert reactive_arm.reactive_target(pending, cpu, current) == expected


def test_compute_uses_live_readings(monkeypatch):
    monkeypatch.setattr(reactive_arm, "pending_pods", lambda: 0.0)
    monkeypatch.setattr(reactive_arm, "cpu_utilization_pct", lambda: 160.0)
    monkeypatch.setattr(reactive_arm, "current_replicas", lambda: 3)
    assert reactive_arm.compute_reactive_target() == 6


def test_compute_returns_none_when_prometheus_fails(monkeypatch):
    def boom():
        raise ConnectionError("prometheus down")

    monkeypatch.setattr(reactive_arm, "pending_pods", boom)
    assert reactive_arm.compute_reactive_target() is None
```

Run: `pytest tests/test_reactive_arm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reactive_arm'`

- [ ] **Step 2: Write the implementation**

`autoscaler/reactive_arm.py`:
```python
"""Reactive arm: respond to pressure that already exists (unschedulable pods, CPU vs request)."""
import math
import time

from common import TARGET_CPU_UTILIZATION, clamp, cpu_utilization_pct, current_replicas, pending_pods

TOLERANCE = 0.1  # like the HPA: ignore utilization within ±10% of the target


def reactive_target(pending, cpu_pct, current):
    if pending > 0:
        target = current + max(1, int(pending))
    else:
        ratio = cpu_pct / TARGET_CPU_UTILIZATION
        if abs(ratio - 1) <= TOLERANCE:
            target = current  # close enough to target, hold steady
        elif ratio > 1:
            target = max(current + 1, math.ceil(round(current * ratio, 6)))
        else:
            target = math.ceil(round(current * ratio, 6))
    return clamp(target)


def compute_reactive_target():
    try:
        return reactive_target(pending_pods(), cpu_utilization_pct(), current_replicas())
    except Exception as e:
        print(f"[reactive] failed this cycle: {e}")
        return None  # arbiter treats this as "no recommendation"


if __name__ == "__main__":
    while True:
        print("[reactive] target =", compute_reactive_target())
        time.sleep(15)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_reactive_arm.py -v`
Expected: `11 passed`

- [ ] **Step 4: Live smoke check (read-only)**

Run: `python autoscaler/reactive_arm.py`, wait for the first line, then Ctrl-C
Expected: `[reactive] target = 2` while idle.

- [ ] **Step 5: Commit**

```bash
git add autoscaler/reactive_arm.py tests/test_reactive_arm.py
git commit -m "feat: reactive arm with unschedulable-pod and proportional CPU rules"
```

---

### Task 7: Predictive arm (Holt / Holt-Winters)

**Files:**
- Create: `autoscaler/predictive_arm.py`
- Test: `tests/test_predictive_arm.py`

**Interfaces:**
- Consumes (from `common`): `prom_query_range(promql, minutes, step_seconds) -> list[tuple[float, float]]`, `TOTAL_CPU_QUERY`, `CPU_REQUEST_CORES = 0.15`, `TARGET_CPU_UTILIZATION = 80.0`, `clamp`
- Produces:
  - Constants `INTERVAL_SECONDS = 30`, `HISTORY_MINUTES = 30`, `STEP_SECONDS = 15`, `HORIZON_STEPS = 8`, `MIN_POINTS = 40`, `SEASONAL_PERIODS: int | None` (from env `HW_SEASONAL_PERIODS`)
  - `forecast_load(series: Sequence[float], horizon_steps: int = HORIZON_STEPS, seasonal_periods: int | None = SEASONAL_PERIODS) -> np.ndarray | None` (non-negative, `None` if < `MIN_POINTS`)
  - `replicas_for_load(cores: float) -> int`
  - `class PredictiveArm(fetch=None, clock=time.time, interval=INTERVAL_SECONDS, seasonal_periods=SEASONAL_PERIODS)` with `compute() -> int | None` and public attributes `target: int | None`, `forecast_cores: float | None` (forecast total cores at T+horizon) and `last_run: float | None` (clock time of the last real computation)
  - `compute_predictive_target() -> int | None` (module-level default arm)

- [ ] **Step 1: Write the failing tests**

`tests/test_predictive_arm.py`:
```python
import numpy as np
import pytest

import predictive_arm as pa

RNG = np.random.default_rng(42)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def rising_fetch(calls):
    def fetch():
        calls.append(1)
        return list(np.linspace(0.2, 1.0, 60))
    return fetch


def test_forecast_needs_minimum_history():
    assert pa.forecast_load([0.5] * (pa.MIN_POINTS - 1)) is None


def test_forecast_extrapolates_rising_trend():
    series = np.linspace(0.2, 1.0, 60) + RNG.normal(0, 0.01, 60)
    forecast = pa.forecast_load(series, horizon_steps=8, seasonal_periods=None)
    assert len(forecast) == 8
    assert forecast[-1] > 1.0


def test_forecast_of_flat_series_stays_flat():
    series = 0.5 + RNG.normal(0, 0.01, 60)
    forecast = pa.forecast_load(series, horizon_steps=8, seasonal_periods=None)
    assert np.all(np.abs(forecast - 0.5) < 0.1)


def test_forecast_is_never_negative():
    forecast = pa.forecast_load(np.linspace(1.0, 0.02, 60), horizon_steps=8, seasonal_periods=None)
    assert np.all(forecast >= 0)


def test_seasonal_forecast_tracks_repeating_pattern():
    t = np.arange(120)
    series = 1 + 0.5 * np.sin(2 * np.pi * t / 40)
    forecast = pa.forecast_load(series, horizon_steps=8, seasonal_periods=40)
    expected = 1 + 0.5 * np.sin(2 * np.pi * np.arange(120, 128) / 40)
    assert np.max(np.abs(forecast - expected)) < 0.1


def test_seasonal_falls_back_to_trend_when_history_is_short():
    series = 0.5 + RNG.normal(0, 0.01, 60)  # fewer than 2 seasons of 40
    assert len(pa.forecast_load(series, horizon_steps=8, seasonal_periods=40)) == 8


@pytest.mark.parametrize("cores, expected", [(0.0, 2), (0.6, 5), (0.61, 6), (100.0, 12)])
def test_replicas_for_load(cores, expected):
    # 0.15 cores request * 80% target = 0.12 cores per pod
    assert pa.replicas_for_load(cores) == expected


def test_arm_returns_none_during_warmup():
    arm = pa.PredictiveArm(fetch=lambda: [0.5] * 10, clock=Clock())
    assert arm.compute() is None
    assert arm.forecast_cores is None


def test_arm_sizes_replicas_for_forecast_peak():
    arm = pa.PredictiveArm(fetch=rising_fetch([]), clock=Clock(), seasonal_periods=None)
    target = arm.compute()
    assert 9 <= target <= 12  # peak > 1.0 cores / 0.12 per pod
    assert arm.forecast_cores > 1.0
    assert arm.last_run == 1000.0


def test_arm_caches_result_for_its_interval():
    calls, clock = [], Clock()
    arm = pa.PredictiveArm(fetch=rising_fetch(calls), clock=clock, interval=30, seasonal_periods=None)
    first = arm.compute()
    clock.t += 15
    assert arm.compute() == first
    assert len(calls) == 1
    clock.t += 15
    arm.compute()
    assert len(calls) == 2


def test_arm_returns_none_when_fetch_fails():
    def boom():
        raise ConnectionError("prometheus down")

    arm = pa.PredictiveArm(fetch=boom, clock=Clock())
    assert arm.compute() is None
    assert arm.forecast_cores is None
```

Run: `pytest tests/test_predictive_arm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'predictive_arm'`

- [ ] **Step 2: Write the implementation**

`autoscaler/predictive_arm.py`:
```python
"""Predictive arm: forecast total app CPU ~2 minutes ahead and size replicas for the peak."""
import math
import os
import time
import warnings

import numpy as np
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from common import CPU_REQUEST_CORES, TARGET_CPU_UTILIZATION, TOTAL_CPU_QUERY, clamp, prom_query_range

INTERVAL_SECONDS = 30
HISTORY_MINUTES = 30
STEP_SECONDS = 15
HORIZON_STEPS = 8  # ~2 minutes ahead at 15s steps
MIN_POINTS = 40  # 10 minutes of history before the arm recommends anything
_seasonal = os.environ.get("HW_SEASONAL_PERIODS")
SEASONAL_PERIODS = int(_seasonal) if _seasonal else None  # e.g. 40 = a 10-minute cycle at 15s steps


def forecast_load(series, horizon_steps=HORIZON_STEPS, seasonal_periods=SEASONAL_PERIODS):
    values = np.asarray(series, dtype=float)
    if len(values) < MIN_POINTS:
        return None  # warm-up period, the reactive arm covers this gap
    # additive Holt-Winters needs at least two full seasons, otherwise use Holt's linear trend
    seasonal = seasonal_periods if seasonal_periods and len(values) >= 2 * seasonal_periods else None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # statsmodels convergence chatter
        model = ExponentialSmoothing(
            values,
            trend="add",
            seasonal="add" if seasonal else None,
            seasonal_periods=seasonal,
        ).fit()
        forecast = np.asarray(model.forecast(horizon_steps), dtype=float)
    if not np.all(np.isfinite(forecast)):
        raise ValueError("non-finite forecast")
    return np.clip(forecast, 0.0, None)


def replicas_for_load(cores):
    cores_per_pod = CPU_REQUEST_CORES * TARGET_CPU_UTILIZATION / 100
    return clamp(math.ceil(round(cores / cores_per_pod, 6)))


class PredictiveArm:
    def __init__(self, fetch=None, clock=time.time, interval=INTERVAL_SECONDS, seasonal_periods=SEASONAL_PERIODS):
        self._fetch = fetch or self._fetch_total_cpu
        self._clock = clock
        self._interval = interval
        self._seasonal_periods = seasonal_periods
        self.last_run = None
        self.target = None
        self.forecast_cores = None

    @staticmethod
    def _fetch_total_cpu():
        return [v for _, v in prom_query_range(TOTAL_CPU_QUERY, HISTORY_MINUTES, STEP_SECONDS)]

    def compute(self):
        now = self._clock()
        if self.last_run is not None and now - self.last_run < self._interval:
            return self.target  # the arbiter runs every 15s; this arm only every 30s
        self.last_run = now
        try:
            forecast = forecast_load(self._fetch(), seasonal_periods=self._seasonal_periods)
            if forecast is None:
                print("[predictive] warming up, not enough history yet")
                self.target, self.forecast_cores = None, None
            else:
                self.target = replicas_for_load(float(forecast.max()))
                self.forecast_cores = float(forecast[-1])
        except Exception as e:
            print(f"[predictive] failed this cycle: {e}")
            self.target, self.forecast_cores = None, None
        return self.target


_default_arm = PredictiveArm()


def compute_predictive_target():
    return _default_arm.compute()


if __name__ == "__main__":
    while True:
        print("[predictive] target =", compute_predictive_target())
        time.sleep(INTERVAL_SECONDS)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_predictive_arm.py -v`
Expected: `14 passed`

- [ ] **Step 4: Live smoke check (read-only)**

Run: `python autoscaler/predictive_arm.py` for ~40s, then Ctrl-C.
Expected: `[predictive] target = 2` if the app has run for ≥ 10 min since Task 4, otherwise `warming up` followed by `target = None`. Either is correct; an HTTP 400 is not (that would mean D6 regressed).

- [ ] **Step 5: Commit**

```bash
git add autoscaler/predictive_arm.py tests/test_predictive_arm.py
git commit -m "feat: predictive arm forecasting total CPU with Holt/Holt-Winters"
```

---

### Task 8: Arbiter decision logic (pure)

**Files:**
- Create: `autoscaler/arbiter.py` (decision logic only; Task 9 adds the runtime to this same file)
- Test: `tests/test_arbiter_decide.py`

**Interfaces:**
- Consumes (from `common`): `PODS_PER_NODE = 4`, `clamp`
- Produces:
  - `COOLDOWN_SECONDS = 20`
  - `@dataclass(frozen=True) Decision(target: int | None, scale: bool, uncordon: int, cordon: int, reason: str)`
  - `nodes_needed(replicas: int) -> int`
  - `decide(reactive: int | None, predictive: int | None, current: int, schedulable_nodes: int, cordoned_reserves: int, promoted_reserves: int, now: float, last_change: float) -> Decision`

Rules encoded:
1. `target = clamp(max(non-None arms))`; both `None` → no replica **and** no node change.
2. Replicas change only when `target != current` and `now - last_change >= COOLDOWN_SECONDS`.
3. Promote `min(nodes_needed(max(target, current)) - schedulable_nodes, cordoned_reserves)` reserves every cycle, even during cooldown. Note "reserve pool EMPTY" when that shortfall can't be covered.
4. Return at most 1 reserve per cycle, and only when scaling down this cycle, nothing is being promoted, a reserve is currently promoted, and `nodes_needed(target) < schedulable_nodes`.

- [ ] **Step 1: Write the failing tests**

`tests/test_arbiter_decide.py`:
```python
import pytest

from arbiter import decide, nodes_needed

NOW = 1000.0
LONG_AGO = 0.0


def call(reactive=None, predictive=None, current=2, schedulable=2, cordoned=3, promoted=0, last_change=LONG_AGO):
    return decide(reactive, predictive, current, schedulable, cordoned, promoted, NOW, last_change)


@pytest.mark.parametrize("replicas, nodes", [(1, 1), (4, 1), (5, 2), (8, 2), (12, 3)])
def test_nodes_needed(replicas, nodes):
    assert nodes_needed(replicas) == nodes


def test_predictive_wins_when_larger():
    d = call(reactive=3, predictive=7, current=3)
    assert (d.target, d.scale, d.uncordon, d.cordon) == (7, True, 0, 0)


def test_reactive_wins_when_larger():
    d = call(reactive=8, predictive=4, current=4)
    assert (d.target, d.scale) == (8, True)


def test_failed_arm_is_ignored():
    assert call(reactive=None, predictive=5).target == 5
    assert call(reactive=5, predictive=None).target == 5


def test_both_arms_failed_changes_nothing():
    d = call(reactive=None, predictive=None, current=12, schedulable=2, cordoned=3)
    assert (d.target, d.scale, d.uncordon, d.cordon) == (None, False, 0, 0)
    assert "BOTH arms failed" in d.reason


def test_target_is_clamped():
    assert call(predictive=50).target == 12
    assert call(reactive=1, current=3).target == 2


def test_holds_when_target_equals_current():
    d = call(reactive=4, predictive=4, current=4)
    assert d.scale is False
    assert "hold 4" in d.reason


def test_cooldown_blocks_replica_change():
    d = call(reactive=6, current=3, last_change=NOW - 5)
    assert d.scale is False
    assert "cooldown" in d.reason


def test_cooldown_expires_after_cooldown_seconds():
    assert call(reactive=6, current=3, last_change=NOW - 20).scale is True


def test_promotes_reserve_when_target_exceeds_schedulable_capacity():
    d = call(reactive=12, current=8, schedulable=2, cordoned=3)
    assert (d.scale, d.uncordon) == (True, 1)


def test_promotes_several_reserves_for_a_big_jump():
    d = call(predictive=12, current=2, schedulable=1, cordoned=3)
    assert d.uncordon == 2


def test_promotes_even_during_cooldown():
    d = call(reactive=12, current=8, schedulable=2, cordoned=3, last_change=NOW - 5)
    assert (d.scale, d.uncordon) == (False, 1)


def test_warns_when_reserve_pool_is_empty():
    d = call(reactive=12, current=12, schedulable=2, cordoned=0)
    assert d.uncordon == 0
    assert "EMPTY" in d.reason


def test_returns_a_reserve_on_scale_down():
    d = call(reactive=3, predictive=4, current=12, schedulable=3, cordoned=2, promoted=1)
    assert (d.target, d.scale, d.cordon) == (4, True, 1)


def test_never_cordons_without_a_promoted_reserve():
    assert call(reactive=3, current=8, schedulable=2, cordoned=3, promoted=0).cordon == 0


def test_no_cordon_during_cooldown():
    d = call(reactive=3, current=12, schedulable=3, cordoned=2, promoted=1, last_change=NOW - 5)
    assert (d.scale, d.cordon) == (False, 0)
```

Run: `pytest tests/test_arbiter_decide.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'arbiter'`

- [ ] **Step 2: Write the implementation**

`autoscaler/arbiter.py`:
```python
"""Arbiter: the only component that changes the cluster (Deployment replicas and reserve-node cordons)."""
from dataclasses import dataclass

from common import PODS_PER_NODE, clamp

COOLDOWN_SECONDS = 20


@dataclass(frozen=True)
class Decision:
    target: int | None  # clamped max of the arms, None when both arms failed
    scale: bool  # patch Deployment replicas to target this cycle
    uncordon: int  # reserve nodes to promote now
    cordon: int  # reserve nodes to return to the pool now (0 or 1)
    reason: str


def nodes_needed(replicas):
    return -(-replicas // PODS_PER_NODE)  # ceiling division


def decide(reactive, predictive, current, schedulable_nodes, cordoned_reserves, promoted_reserves, now, last_change):
    arms = f"(reactive={reactive}, predictive={predictive})"
    candidates = [t for t in (reactive, predictive) if t is not None]
    if not candidates:
        return Decision(None, False, 0, 0, f"BOTH arms failed, no change this cycle {arms}")

    target = clamp(max(candidates))
    in_cooldown = now - last_change < COOLDOWN_SECONDS
    scale = target != current and not in_cooldown

    # capacity for the larger of current/target is ensured every cycle, even during cooldown,
    # so unschedulable pods get a node without waiting for the next replica change
    shortfall = max(0, nodes_needed(max(target, current)) - schedulable_nodes)
    uncordon = min(shortfall, cordoned_reserves)
    cordon = int(
        scale
        and target < current
        and uncordon == 0
        and promoted_reserves > 0
        and nodes_needed(target) < schedulable_nodes
    )

    parts = []
    if scale:
        parts.append(f"scale {current} -> {target}")
    elif target != current:
        parts.append(f"within cooldown, holding {current} (wants {target})")
    else:
        parts.append(f"hold {current}")
    if uncordon:
        parts.append(f"promote {uncordon} reserve node(s)")
    if shortfall > cordoned_reserves:
        parts.append("reserve pool EMPTY, extra pods will stay Pending")
    if cordon:
        parts.append("return 1 reserve node to the pool")
    return Decision(target, scale, uncordon, cordon, "; ".join(parts) + " " + arms)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_arbiter_decide.py -v`
Expected: `20 passed`

- [ ] **Step 4: Commit**

```bash
git add autoscaler/arbiter.py tests/test_arbiter_decide.py
git commit -m "feat: pure arbiter decision with max arbitration, cooldown and reserve promotion"
```

---

### Task 9: Arbiter runtime — cluster adapter, decision log, fault injection, CLI

**Files:**
- Create: `autoscaler/decision_log.py`, `scripts/reset.sh`
- Modify: `autoscaler/arbiter.py` (replace the whole file with the version below; `Decision`, `nodes_needed`, `decide` are unchanged from Task 8)
- Test: `tests/test_arbiter_runtime.py`

**Interfaces:**
- Consumes: `decide`/`Decision` (Task 8); `compute_reactive_target()` (Task 6); `PredictiveArm` with `.compute()`, `.forecast_cores`, `.last_run` (Task 7); `k8s()`, `DEPLOYMENT_NAME`, `NAMESPACE` (Task 5)
- Produces:
  - `decision_log.FIELDS: list[str]` = `timestamp, reactive_target, predictive_target, forecast_cores, forecast_made_at, current_replicas, target, scaled, uncordon, cordon, schedulable_nodes, reason`
  - `DecisionLog(path)` with `.write(row: dict)` and `.close()`. The header is written only for a new or empty file.
  - Cluster port (duck-typed, implemented by `ClusterAdapter` and the test fake): `current_replicas() -> int`, `node_counts() -> tuple[schedulable, cordoned_reserves, promoted_reserves]`, `reserve_names(unschedulable: bool) -> list[str]` (sorted), `set_unschedulable(name: str, value: bool)`, `set_replicas(n: int)`
  - `Arbiter(cluster, reactive_fn=None, predictive_arm=None, log=None, clock=time.time, fault_dir=FAULT_DIR)` with `.run_cycle() -> Decision` and `.last_change: float`
  - CLI: `python autoscaler/arbiter.py [--mode both|reactive|predictive] [--log-csv PATH] [--once]`
  - Fault injection: while the file `autoscaler/faults/fault-reactive` or `fault-predictive` exists, that arm returns no recommendation
  - `scripts/reset.sh`: replicas → 2, app pods drained off reserves, all reserves cordoned

- [ ] **Step 1: Write the failing tests**

`tests/test_arbiter_runtime.py`:
```python
import csv

from arbiter import Arbiter
from decision_log import FIELDS, DecisionLog


class FakeCluster:
    def __init__(self, replicas=2, active=2, reserves=("w3", "w4", "w5"), promoted=()):
        self.replicas = replicas
        self.active = active
        self.cordoned = {name: name not in promoted for name in reserves}
        self.calls = []

    def current_replicas(self):
        return self.replicas

    def node_counts(self):
        promoted = sum(1 for is_cordoned in self.cordoned.values() if not is_cordoned)
        return self.active + promoted, len(self.cordoned) - promoted, promoted

    def reserve_names(self, unschedulable):
        return sorted(n for n, is_cordoned in self.cordoned.items() if is_cordoned == unschedulable)

    def set_unschedulable(self, name, value):
        self.cordoned[name] = value
        self.calls.append(("unschedulable", name, value))

    def set_replicas(self, replicas):
        self.replicas = replicas
        self.calls.append(("replicas", replicas))


class FakeArm:
    def __init__(self, target, forecast_cores=None, last_run=None):
        self.target = target
        self.forecast_cores = forecast_cores
        self.last_run = last_run

    def compute(self):
        return self.target


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_scale_up_promotes_first_reserve_then_scales(tmp_path):
    cluster = FakeCluster(replicas=8)
    Arbiter(cluster, reactive_fn=lambda: 12, clock=Clock(), fault_dir=tmp_path).run_cycle()
    assert cluster.calls == [("unschedulable", "w3", False), ("replicas", 12)]


def test_both_arms_failed_touches_nothing(tmp_path):
    cluster = FakeCluster(replicas=12)
    arbiter = Arbiter(cluster, reactive_fn=lambda: None, predictive_arm=FakeArm(None), clock=Clock(), fault_dir=tmp_path)
    decision = arbiter.run_cycle()
    assert decision.target is None
    assert cluster.calls == []


def test_scale_down_returns_last_promoted_reserve(tmp_path):
    cluster = FakeCluster(replicas=12, promoted=("w3", "w4"))
    arbiter = Arbiter(cluster, reactive_fn=lambda: 3, predictive_arm=FakeArm(4), clock=Clock(), fault_dir=tmp_path)
    arbiter.run_cycle()
    assert cluster.calls == [("replicas", 4), ("unschedulable", "w4", True)]


def test_cooldown_applies_across_cycles(tmp_path):
    cluster, clock = FakeCluster(replicas=3), Clock()
    targets = iter([6, 8])
    arbiter = Arbiter(cluster, reactive_fn=lambda: next(targets), clock=clock, fault_dir=tmp_path)
    arbiter.run_cycle()
    clock.t += 15
    arbiter.run_cycle()
    assert cluster.calls == [("replicas", 6)]
    assert arbiter.last_change == 1000.0


def test_fault_file_silences_an_arm(tmp_path):
    (tmp_path / "fault-reactive").touch()
    cluster = FakeCluster(replicas=2)
    arbiter = Arbiter(cluster, reactive_fn=lambda: 12, predictive_arm=FakeArm(5), clock=Clock(), fault_dir=tmp_path)
    arbiter.run_cycle()
    assert cluster.calls == [("replicas", 5)]


def test_cycle_is_logged_to_csv(tmp_path):
    path = tmp_path / "decisions.csv"
    log = DecisionLog(path)
    arm = FakeArm(5, forecast_cores=0.55, last_run=990.0)
    Arbiter(FakeCluster(), predictive_arm=arm, log=log, clock=Clock(), fault_dir=tmp_path).run_cycle()
    log.close()
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == FIELDS
    assert len(rows) == 1
    row = rows[0]
    assert (row["target"], row["scaled"], row["forecast_cores"], row["forecast_made_at"]) == ("5", "True", "0.55", "990.0")
    assert row["reactive_target"] == ""


def test_decision_log_appends_without_repeating_header(tmp_path):
    path = tmp_path / "decisions.csv"
    for _ in range(2):
        log = DecisionLog(path)
        log.write({"timestamp": 1, "reason": "x"})
        log.close()
    lines = path.read_text().splitlines()
    assert lines[0].startswith("timestamp,")
    assert len(lines) == 3
```

Run: `pytest tests/test_arbiter_runtime.py -v`
Expected: FAIL with `ImportError: cannot import name 'Arbiter' from 'arbiter'`

- [ ] **Step 2: Write the decision log**

`autoscaler/decision_log.py`:
```python
"""Append-only CSV log with one row per arbiter cycle, consumed by the experiments."""
import csv
import os

FIELDS = [
    "timestamp", "reactive_target", "predictive_target", "forecast_cores", "forecast_made_at",
    "current_replicas", "target", "scaled", "uncordon", "cordon", "schedulable_nodes", "reason",
]


class DecisionLog:
    def __init__(self, path):
        is_new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._file = open(path, "a", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=FIELDS)
        if is_new:
            self._writer.writeheader()

    def write(self, row):
        self._writer.writerow(row)
        self._file.flush()  # experiments read this while the arbiter is still running

    def close(self):
        self._file.close()
```

- [ ] **Step 3: Replace `autoscaler/arbiter.py` with the full runtime**

`autoscaler/arbiter.py`:
```python
"""Arbiter: the only component that changes the cluster (Deployment replicas and reserve-node cordons)."""
import argparse
import os
import time
from dataclasses import dataclass
from pathlib import Path

from common import DEPLOYMENT_NAME, NAMESPACE, PODS_PER_NODE, clamp, k8s
from decision_log import DecisionLog
from predictive_arm import PredictiveArm
from reactive_arm import compute_reactive_target

COOLDOWN_SECONDS = 20
CYCLE_SECONDS = 15
FAULT_DIR = Path(os.environ.get("FAULT_DIR", Path(__file__).resolve().parent / "faults"))


@dataclass(frozen=True)
class Decision:
    target: int | None  # clamped max of the arms, None when both arms failed
    scale: bool  # patch Deployment replicas to target this cycle
    uncordon: int  # reserve nodes to promote now
    cordon: int  # reserve nodes to return to the pool now (0 or 1)
    reason: str


def nodes_needed(replicas):
    return -(-replicas // PODS_PER_NODE)  # ceiling division


def decide(reactive, predictive, current, schedulable_nodes, cordoned_reserves, promoted_reserves, now, last_change):
    arms = f"(reactive={reactive}, predictive={predictive})"
    candidates = [t for t in (reactive, predictive) if t is not None]
    if not candidates:
        return Decision(None, False, 0, 0, f"BOTH arms failed, no change this cycle {arms}")

    target = clamp(max(candidates))
    in_cooldown = now - last_change < COOLDOWN_SECONDS
    scale = target != current and not in_cooldown

    # capacity for the larger of current/target is ensured every cycle, even during cooldown,
    # so unschedulable pods get a node without waiting for the next replica change
    shortfall = max(0, nodes_needed(max(target, current)) - schedulable_nodes)
    uncordon = min(shortfall, cordoned_reserves)
    cordon = int(
        scale
        and target < current
        and uncordon == 0
        and promoted_reserves > 0
        and nodes_needed(target) < schedulable_nodes
    )

    parts = []
    if scale:
        parts.append(f"scale {current} -> {target}")
    elif target != current:
        parts.append(f"within cooldown, holding {current} (wants {target})")
    else:
        parts.append(f"hold {current}")
    if uncordon:
        parts.append(f"promote {uncordon} reserve node(s)")
    if shortfall > cordoned_reserves:
        parts.append("reserve pool EMPTY, extra pods will stay Pending")
    if cordon:
        parts.append("return 1 reserve node to the pool")
    return Decision(target, scale, uncordon, cordon, "; ".join(parts) + " " + arms)


class ClusterAdapter:
    """Every write this system makes to Kubernetes goes through this class."""

    def __init__(self):
        self.apps, self.core = k8s()

    def current_replicas(self):
        return self.apps.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE).spec.replicas

    def node_counts(self):
        workers = self.core.list_node(label_selector="role in (active,reserve)").items
        schedulable = sum(1 for n in workers if not n.spec.unschedulable)
        reserves = [n for n in workers if n.metadata.labels.get("role") == "reserve"]
        cordoned = sum(1 for n in reserves if n.spec.unschedulable)
        return schedulable, cordoned, len(reserves) - cordoned

    def reserve_names(self, unschedulable):
        nodes = self.core.list_node(label_selector="role=reserve").items
        return sorted(n.metadata.name for n in nodes if bool(n.spec.unschedulable) == unschedulable)

    def set_unschedulable(self, name, value):
        self.core.patch_node(name, {"spec": {"unschedulable": value}})

    def set_replicas(self, replicas):
        self.apps.patch_namespaced_deployment_scale(DEPLOYMENT_NAME, NAMESPACE, {"spec": {"replicas": replicas}})


class Arbiter:
    def __init__(self, cluster, reactive_fn=None, predictive_arm=None, log=None, clock=time.time, fault_dir=FAULT_DIR):
        self.cluster = cluster
        self.reactive_fn = reactive_fn
        self.predictive_arm = predictive_arm
        self.log = log
        self.clock = clock
        self.fault_dir = Path(fault_dir)
        self.last_change = 0.0

    def _run_arm(self, name, fn):
        if fn is None:
            return None
        if (self.fault_dir / f"fault-{name}").exists():
            print(f"[{name}] injected fault, no recommendation this cycle")
            return None
        return fn()

    def run_cycle(self):
        now = self.clock()
        reactive = self._run_arm("reactive", self.reactive_fn)
        predictive = self._run_arm("predictive", self.predictive_arm.compute if self.predictive_arm else None)
        current = self.cluster.current_replicas()
        schedulable, cordoned, promoted = self.cluster.node_counts()
        d = decide(reactive, predictive, current, schedulable, cordoned, promoted, now, self.last_change)

        # promote capacity before adding pods so they land on a schedulable node straight away
        for name in self.cluster.reserve_names(unschedulable=True)[: d.uncordon]:
            self.cluster.set_unschedulable(name, False)
            print(f"[arbiter] uncordoned {name}")
        if d.scale:
            self.cluster.set_replicas(d.target)
            self.last_change = now
        if d.cordon:
            name = self.cluster.reserve_names(unschedulable=False)[-1]
            self.cluster.set_unschedulable(name, True)
            print(f"[arbiter] cordoned {name} back to reserve")
        print(f"[arbiter] {d.reason}")

        if self.log:
            self.log.write({
                "timestamp": round(now, 3),
                "reactive_target": reactive,
                "predictive_target": predictive,
                "forecast_cores": getattr(self.predictive_arm, "forecast_cores", None),
                "forecast_made_at": getattr(self.predictive_arm, "last_run", None),
                "current_replicas": current,
                "target": d.target,
                "scaled": d.scale,
                "uncordon": d.uncordon,
                "cordon": d.cordon,
                "schedulable_nodes": schedulable,
                "reason": d.reason,
            })
        return d


def build_arbiter(mode, log_path=None):
    return Arbiter(
        ClusterAdapter(),
        reactive_fn=compute_reactive_target if mode in ("both", "reactive") else None,
        predictive_arm=PredictiveArm() if mode in ("both", "predictive") else None,
        log=DecisionLog(log_path) if log_path else None,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Predictive-reactive autoscaler arbiter")
    parser.add_argument("--mode", choices=["both", "reactive", "predictive"], default="both")
    parser.add_argument("--log-csv", help="append one row per cycle to this CSV file")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args(argv)

    arbiter = build_arbiter(args.mode, args.log_csv)
    print(f"[arbiter] mode={args.mode}; create {FAULT_DIR}/fault-reactive or fault-predictive to kill an arm")
    while True:
        try:
            arbiter.run_cycle()
        except Exception as e:
            # e.g. API server unreachable: stop this cycle and retry on the next one
            print(f"[arbiter] cycle failed, retrying next cycle: {e}")
        if args.once:
            break
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the whole unit suite**

Run: `pytest -q`
Expected: `70 passed` (5 env + 2 app + 11 common + 11 reactive + 14 predictive + 20 decide + 7 runtime)

- [ ] **Step 5: Write the reset script**

`scripts/reset.sh`:
```bash
#!/usr/bin/env bash
# Return to the experiment baseline: 2 replicas on active nodes, every reserve cordoned and empty.
set -euo pipefail
kubectl scale deployment/sample-app --replicas=2
for node in $(kubectl get nodes -l role=reserve -o jsonpath='{.items[*].metadata.name}'); do
  # drain = cordon + evict; only app pods are evicted
  kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --pod-selector=app=sample-app --timeout=120s >/dev/null
done
kubectl rollout status deployment/sample-app --timeout=120s
kubectl get nodes -L role
```

Run: `chmod +x scripts/reset.sh && scripts/reset.sh && scripts/verify-cluster.sh`
Expected: rollout succeeds; `PASS: 2 active + 3 cordoned reserve nodes, 4 slots each`

- [ ] **Step 6: Live smoke test: one read-only-ish cycle**

Run: `mkdir -p autoscaler/faults && python autoscaler/arbiter.py --once --log-csv results/smoke.csv && cat results/smoke.csv`
Expected: `[arbiter] hold 2 (reactive=2, predictive=…)` and a CSV with the header plus one row.

- [ ] **Step 7: Live end-to-end test of reserve promotion and return**

Terminal A: `python autoscaler/arbiter.py --mode reactive`
Terminal B:
```bash
kubectl scale deployment/sample-app --replicas=10   # 8 slots on active nodes -> 2 unschedulable
while true; do clear; kubectl get nodes -L role; kubectl get pods -l app=sample-app -o wide; sleep 2; done   # macOS has no `watch`
```
Expected in Terminal A within ~45s: `promote 1 reserve node(s)`, `uncordoned autoscaler-demo-worker3`, `scale 10 -> 12`. Terminal B then shows worker3 schedulable and 4 Running pods on it. Because the app is idle, within about another minute: `scale 12 -> 2; return 1 reserve node to the pool` and `cordoned autoscaler-demo-worker3 back to reserve`.

Stop Terminal A (Ctrl-C), then run `scripts/reset.sh`.

- [ ] **Step 8: Commit**

```bash
git add autoscaler/arbiter.py autoscaler/decision_log.py scripts/reset.sh tests/test_arbiter_runtime.py
git commit -m "feat: arbiter runtime with reserve promotion, CSV decision log and fault injection"
```

---

### Task 10: k6 load scripts and capacity calibration

**Files:**
- Create: `loadtest/summary.js`, `loadtest/steady.js`, `loadtest/spike.js`, `loadtest/diurnal.js`
- Modify (after calibration): default `BASE_RPS`/`PEAK_RPS` in `loadtest/spike.js` and `loadtest/diurnal.js`; optionally `WORK_ITERATIONS` in `app/deployment.yaml`

**Interfaces:**
- Consumes: `http://localhost:8080/` (Task 3), Prometheus (Task 4)
- Produces:
  - `spike.js`: 1m at `BASE_RPS` → 30s ramp → 2m at `PEAK_RPS` → 1m back to `BASE_RPS` (**spike starts 60s after launch**, used by Task 12 as `SPIKE_START_OFFSET = 60`)
  - `steady.js`: `RATE` for `DURATION_S`; `JITTER` > 0 randomizes the rate ±JITTER every 15s
  - `diurnal.js`: `CYCLES` sine periods of `PERIOD_MIN` minutes between `BASE_RPS` and `PEAK_RPS` (default 10 min × 6)
  - Every script prints `p95=…ms failed=…% reqs=… dropped=…` and writes full JSON to `$SUMMARY_JSON` when set
  - Calibrated numbers `PER_POD_RPS`, `BASE_RPS`, `PEAK_RPS`, `REQUIRED_REPLICAS` recorded in README

- [ ] **Step 1: Write the shared summary and the three scenarios**

`loadtest/summary.js`:
```javascript
// Shared end-of-test summary: one stdout line plus optional full JSON (SUMMARY_JSON=path).
export function summarize(data) {
  const m = data.metrics;
  const dropped = m.dropped_iterations ? m.dropped_iterations.values.count : 0;
  const line =
    `p95=${m.http_req_duration.values['p(95)'].toFixed(0)}ms ` +
    `failed=${(m.http_req_failed.values.rate * 100).toFixed(2)}% ` +
    `reqs=${m.http_reqs.values.count} dropped=${dropped}\n`;
  const out = { stdout: line };
  if (__ENV.SUMMARY_JSON) out[__ENV.SUMMARY_JSON] = JSON.stringify(data, null, 2);
  return out;
}
```

`loadtest/steady.js`:
```javascript
import http from 'k6/http';
import { summarize } from './summary.js';

const RATE = Number(__ENV.RATE || 20);
const DURATION_S = Number(__ENV.DURATION_S || 300);
const JITTER = Number(__ENV.JITTER || 0); // e.g. 0.3 = random ±30% every 15s (stability experiment)
const TARGET = __ENV.TARGET_URL || 'http://localhost:8080/';

const steady = {
  executor: 'constant-arrival-rate',
  rate: RATE,
  timeUnit: '1s',
  duration: `${DURATION_S}s`,
  preAllocatedVUs: 50,
  maxVUs: 400,
};

const jittered = {
  executor: 'ramping-arrival-rate',
  startRate: RATE,
  timeUnit: '1s',
  preAllocatedVUs: 50,
  maxVUs: 400,
  stages: Array.from({ length: Math.ceil(DURATION_S / 15) }, () => ({
    duration: '15s',
    target: Math.max(1, Math.round(RATE * (1 + JITTER * (2 * Math.random() - 1)))),
  })),
};

export const options = { scenarios: { steady: JITTER > 0 ? jittered : steady } };

export default function () {
  http.get(TARGET, { timeout: '10s' });
}

export function handleSummary(data) {
  return summarize(data);
}
```

`loadtest/spike.js`:
```javascript
import http from 'k6/http';
import { summarize } from './summary.js';

const BASE = Number(__ENV.BASE_RPS || 10);
const PEAK = Number(__ENV.PEAK_RPS || 150);
const TARGET = __ENV.TARGET_URL || 'http://localhost:8080/';

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-arrival-rate', // open model: load doesn't back off when the app is slow
      startRate: BASE,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 600,
      stages: [
        { duration: '1m', target: BASE },  // baseline (spike starts 60s after launch)
        { duration: '30s', target: PEAK }, // spike
        { duration: '2m', target: PEAK },  // hold
        { duration: '1m', target: BASE },  // recover
      ],
    },
  },
};

export default function () {
  http.get(TARGET, { timeout: '10s' });
}

export function handleSummary(data) {
  return summarize(data);
}
```

`loadtest/diurnal.js`:
```javascript
import http from 'k6/http';
import { summarize } from './summary.js';

const BASE = Number(__ENV.BASE_RPS || 10);
const PEAK = Number(__ENV.PEAK_RPS || 150);
const PERIOD_MIN = Number(__ENV.PERIOD_MIN || 10); // 10 min = 40 steps of 15s (HW_SEASONAL_PERIODS=40)
const CYCLES = Number(__ENV.CYCLES || 6);
const STEPS_PER_PERIOD = 20;
const TARGET = __ENV.TARGET_URL || 'http://localhost:8080/';

function sineStages() {
  const stepSeconds = Math.round((PERIOD_MIN * 60) / STEPS_PER_PERIOD);
  const stages = [];
  for (let c = 0; c < CYCLES; c++) {
    for (let s = 1; s <= STEPS_PER_PERIOD; s++) {
      const phase = (2 * Math.PI * s) / STEPS_PER_PERIOD;
      const rate = BASE + ((PEAK - BASE) * (1 - Math.cos(phase))) / 2; // starts and ends each cycle at BASE
      stages.push({ duration: `${stepSeconds}s`, target: Math.round(rate) });
    }
  }
  return stages;
}

export const options = {
  scenarios: {
    diurnal: {
      executor: 'ramping-arrival-rate',
      startRate: BASE,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 600,
      stages: sineStages(),
    },
  },
};

export default function () {
  http.get(TARGET, { timeout: '10s' });
}

export function handleSummary(data) {
  return summarize(data);
}
```

- [ ] **Step 2: Verify the scripts parse and generate the expected shape**

```bash
k6 inspect loadtest/spike.js | python3 -c 'import json,sys; s=json.load(sys.stdin)["scenarios"]["spike"]; print(s["executor"], len(s["stages"]))'
k6 inspect loadtest/diurnal.js | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["scenarios"]["diurnal"]["stages"]))'
k6 inspect -e JITTER=0.3 -e DURATION_S=60 loadtest/steady.js | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["scenarios"]["steady"]["stages"]))'
```
Expected: `ramping-arrival-rate 4`, then `120`, then `4`.

- [ ] **Step 3: Smoke-run against the app**

Run: `RATE=5 DURATION_S=20 SUMMARY_JSON=results/smoke-k6.json k6 run --quiet loadtest/steady.js`
Expected: one line like `p95=45ms failed=0.00% reqs=100 dropped=0`, and `results/smoke-k6.json` exists.

- [ ] **Step 4: Calibrate per-pod capacity (arbiter NOT running)**

```bash
scripts/reset.sh
RATE=10 DURATION_S=180 k6 run --quiet loadtest/steady.js &
sleep 150
curl -sG http://localhost:9090/api/v1/query --data-urlencode \
  'query=100 * sum(rate(container_cpu_usage_seconds_total{namespace="default",container="sample-app"}[1m])) / sum(kube_pod_container_resource_requests{namespace="default",container="sample-app",resource="cpu"})' \
  | python3 -c 'import json,sys; print("utilization % of request:", json.load(sys.stdin)["data"]["result"][0]["value"][1])'
wait
```
Compute `PER_POD_RPS = (10 / 2) × 80 / utilization` (the RPS one pod handles at 80% of its request).
- If `PER_POD_RPS < 3` or `> 60`: change `WORK_ITERATIONS` in `app/deployment.yaml` in proportion (e.g. utilization 400% → divide by 5), then `kubectl apply -f app/deployment.yaml` and repeat this step. Aim for `PER_POD_RPS` of 10–20.
- If utilization reads ≈ 200% (throttled at the 300m limit), halve `RATE` and repeat, since the number is unreliable when saturated.

Then set `BASE_RPS = round(1.5 × PER_POD_RPS)` (→ ~2 pods) and `PEAK_RPS = round(10 × PER_POD_RPS)` (→ ~10 pods, needing 1 reserve node), and `REQUIRED_REPLICAS = min(12, ceil(PEAK_RPS / PER_POD_RPS))`. Put the `BASE_RPS`/`PEAK_RPS` values in as the `|| …` defaults in `spike.js` and `diurnal.js`. Export the two integers that Tasks 12–13 use in every shell you run them from, e.g. `export PER_POD_RPS=15 REQUIRED_REPLICAS=10`, with your measured numbers.

- [ ] **Step 5: End-to-end spike with the arbiter (sanity, not a measured trial)**

Terminal A: `python autoscaler/arbiter.py --log-csv results/sanity-decisions.csv`
Terminal B: `k6 run loadtest/spike.js`
Expected: within ~60s of the spike starting (t≈60s), Terminal A logs a `scale 2 -> N` driven by `reactive=` and at least one `promote 1 reserve node(s)`. The k6 summary prints. After the recovery stage replicas trend back down. Then Ctrl-C Terminal A and run `scripts/reset.sh`.

- [ ] **Step 6: Commit**

```bash
git add loadtest/ app/deployment.yaml
git commit -m "feat: open-model k6 steady/spike/diurnal scenarios with calibrated rates"
```

---

### Task 11: Experiment analysis library (pure)

**Files:**
- Create: `experiments/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: decision-log rows as `dict[str, str]` keyed by `decision_log.FIELDS` (Task 9); Prometheus range points `list[tuple[float, float]]` (Task 5)
- Produces:
  - `pod_seconds(points: list[tuple[float, float]], step_seconds: int) -> float`
  - `time_to_capacity(available_points, required: int, start_ts: float) -> float | None`
  - `mean_std(values: list[float | None]) -> tuple[float | None, float | None]`
  - `forecasts_from_rows(rows) -> list[tuple[float, float]]` (made_at, forecast_cores), deduplicated
  - `align_forecasts(forecasts, actuals, horizon_seconds=120, tolerance_seconds=15) -> list[tuple[float, float]]` (forecast, actual)
  - `mape(pairs, min_actual=0.01) -> float` (percent; raises `ValueError` if nothing usable)
  - `count_events(rows) -> dict[str, int]` with keys `scale_up, scale_down, uncordon, cordon`

- [ ] **Step 1: Write the failing tests**

`tests/test_metrics.py`:
```python
import pytest

from metrics import align_forecasts, count_events, forecasts_from_rows, mape, mean_std, pod_seconds, time_to_capacity


def test_pod_seconds_integrates_samples():
    assert pod_seconds([(0, 0), (15, 2), (30, 1)], 15) == 45


def test_time_to_capacity_measures_from_start():
    points = [(0, 2), (60, 4), (75, 10), (90, 12)]
    assert time_to_capacity(points, required=10, start_ts=60) == 15


def test_time_to_capacity_ignores_points_before_start_and_can_fail():
    assert time_to_capacity([(0, 12), (60, 2), (75, 3)], required=10, start_ts=60) is None


def test_mean_std():
    assert mean_std([1, 2, 3]) == (2.0, 1.0)
    assert mean_std([5]) == (5.0, 0.0)
    assert mean_std([None, 4, 6]) == (5.0, pytest.approx(1.41421356))
    assert mean_std([]) == (None, None)


def test_forecasts_from_rows_dedupes_cached_forecasts_and_skips_blanks():
    rows = [
        {"forecast_cores": "0.5", "forecast_made_at": "100.0"},
        {"forecast_cores": "0.5", "forecast_made_at": "100.0"},
        {"forecast_cores": "", "forecast_made_at": ""},
        {"forecast_cores": "0.7", "forecast_made_at": "130.0"},
    ]
    assert forecasts_from_rows(rows) == [(100.0, 0.5), (130.0, 0.7)]


def test_align_forecasts_pairs_each_forecast_with_actual_at_horizon():
    forecasts = [(100.0, 0.5), (130.0, 0.7), (500.0, 1.0)]
    actuals = [(215.0, 0.4), (250.0, 0.7)]
    pairs = align_forecasts(forecasts, actuals, horizon_seconds=120, tolerance_seconds=15)
    assert pairs == [(0.5, 0.4), (0.7, 0.7)]


def test_mape():
    assert mape([(110, 100), (90, 100)]) == pytest.approx(10.0)


def test_mape_skips_near_zero_actuals():
    assert mape([(1.0, 0.0), (0.9, 1.0)]) == pytest.approx(10.0)


def test_mape_without_usable_actuals_raises():
    with pytest.raises(ValueError):
        mape([(1.0, 0.0)])


def test_count_events():
    rows = [
        {"scaled": "True", "target": "6", "current_replicas": "2", "uncordon": "1", "cordon": "0"},
        {"scaled": "True", "target": "2", "current_replicas": "6", "uncordon": "0", "cordon": "1"},
        {"scaled": "False", "target": "", "current_replicas": "2", "uncordon": "0", "cordon": "0"},
    ]
    assert count_events(rows) == {"scale_up": 1, "scale_down": 1, "uncordon": 1, "cordon": 1}
```

Run: `pytest tests/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'metrics'`

- [ ] **Step 2: Write the implementation**

`experiments/metrics.py`:
```python
"""Pure analysis helpers for the feasibility experiments."""
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
    """The arbiter logs the cached forecast every 15s; keep one entry per real computation."""
    by_time = {}
    for r in rows:
        if r.get("forecast_cores") and r.get("forecast_made_at"):
            by_time[float(r["forecast_made_at"])] = float(r["forecast_cores"])
    return sorted(by_time.items())


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
    events = {"scale_up": 0, "scale_down": 0, "uncordon": 0, "cordon": 0}
    for r in rows:
        if r["scaled"] == "True":
            events["scale_up" if int(r["target"]) > int(r["current_replicas"]) else "scale_down"] += 1
        events["uncordon"] += int(r["uncordon"] or 0)
        events["cordon"] += int(r["cordon"] or 0)
    return events
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest -q`
Expected: `80 passed`

- [ ] **Step 4: Commit**

```bash
git add experiments/metrics.py tests/test_metrics.py
git commit -m "feat: pure metrics for pod-seconds, time-to-capacity, MAPE and churn"
```

---

### Task 12: Live experiment scripts

**Files:**
- Create: `experiments/run_trials.py`, `experiments/promotion_latency.py`, `experiments/analyze_log.py`, `experiments/fallback_safety.sh`

**Interfaces:**
- Consumes: `metrics.*` (Task 11); `common.prom_query_range`, `common.k8s`, `TOTAL_CPU_QUERY`, `PODS_PER_NODE` (Task 5); `autoscaler/arbiter.py` CLI (Task 9); `scripts/reset.sh` (Task 9); `loadtest/spike.js` with a 60s baseline and `SUMMARY_JSON` (Task 10)
- Produces:
  - `run_trials.py --required-replicas N [--modes off reactive predictive both] [--trials 5] [--settle 60]` → `results/trials.csv` (`mode, trial, start, end, pending_pod_seconds, unavailable_pod_seconds, time_to_capacity_s, p95_ms, failed_pct`) plus a printed mean ± std table
  - `promotion_latency.py [--repeats 3]` → `results/promotion_latency.csv` (`kind, node, repeat, seconds`) plus mean ± std
  - `analyze_log.py mape LOG` and `analyze_log.py churn LOG [LOG…]`
  - `fallback_safety.sh` → exits 0 with `PASS` when cluster state is unchanged 60s after `kill -9` of the arbiter mid-spike

- [ ] **Step 1: Write `experiments/run_trials.py`**

```python
#!/usr/bin/env python3
"""Experiments 1 and 2: repeat spike.js per autoscaler mode and record scaling-lag metrics per trial."""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "autoscaler"))

from common import DEPLOYMENT_NAME, NAMESPACE, prom_query_range  # noqa: E402
from metrics import mean_std, pod_seconds, time_to_capacity  # noqa: E402

STEP = 15
SPIKE_START_OFFSET = 60  # spike.js holds the baseline for 1m before the spike
MODES = ["off", "reactive", "predictive", "both"]
PENDING_QUERY = (
    f'sum(kube_pod_status_phase{{phase="Pending",namespace="{NAMESPACE}",pod=~"{DEPLOYMENT_NAME}-.*"}}) or vector(0)'
)
UNAVAILABLE_QUERY = (
    f'sum(kube_deployment_status_replicas_unavailable{{namespace="{NAMESPACE}",deployment="{DEPLOYMENT_NAME}"}})'
    " or vector(0)"
)
AVAILABLE_QUERY = (
    f'sum(kube_deployment_status_replicas_available{{namespace="{NAMESPACE}",deployment="{DEPLOYMENT_NAME}"}})'
)
FIELDS = ["mode", "trial", "start", "end", "pending_pod_seconds", "unavailable_pod_seconds",
          "time_to_capacity_s", "p95_ms", "failed_pct"]


def run_trial(mode, trial, results, required, settle):
    subprocess.run([str(ROOT / "scripts" / "reset.sh")], check=True, stdout=subprocess.DEVNULL)
    time.sleep(settle)
    arbiter = None
    if mode != "off":
        arbiter = subprocess.Popen([
            sys.executable, str(ROOT / "autoscaler" / "arbiter.py"), "--mode", mode,
            "--log-csv", str(results / f"{mode}-{trial}-decisions.csv"),
        ], stdout=subprocess.DEVNULL)
    summary_path = results / f"{mode}-{trial}-k6.json"
    start = time.time()
    try:
        subprocess.run(["k6", "run", "--quiet", str(ROOT / "loadtest" / "spike.js")],
                       env={**os.environ, "SUMMARY_JSON": str(summary_path)}, check=True)
    finally:
        if arbiter:
            arbiter.terminate()
            arbiter.wait(timeout=30)
    end = time.time()
    time.sleep(2 * STEP)  # let Prometheus scrape the final state
    minutes = (end - start) / 60
    k6 = json.loads(summary_path.read_text())["metrics"]
    return {
        "mode": mode,
        "trial": trial,
        "start": round(start),
        "end": round(end),
        "pending_pod_seconds": pod_seconds(prom_query_range(PENDING_QUERY, minutes, STEP, end), STEP),
        "unavailable_pod_seconds": pod_seconds(prom_query_range(UNAVAILABLE_QUERY, minutes, STEP, end), STEP),
        "time_to_capacity_s": time_to_capacity(
            prom_query_range(AVAILABLE_QUERY, minutes, STEP, end), required, start + SPIKE_START_OFFSET
        ),
        "p95_ms": round(k6["http_req_duration"]["values"]["p(95)"]),
        "failed_pct": round(100 * k6["http_req_failed"]["values"]["rate"], 2),
    }


def fmt(mean, std):
    return f"{'n/a':>20}" if mean is None else f"{mean:>11.1f} ± {std:<6.1f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=MODES)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--required-replicas", type=int, required=True, help="REQUIRED_REPLICAS from Task 10")
    parser.add_argument("--settle", type=int, default=60, help="idle seconds after reset before each trial")
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    args = parser.parse_args()

    args.results.mkdir(parents=True, exist_ok=True)
    out = args.results / "trials.csv"
    is_new = not out.exists()
    rows = []
    with out.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        for trial in range(1, args.trials + 1):
            for mode in args.modes:  # interleave modes so laptop drift affects every mode equally
                print(f"=== {mode} trial {trial}/{args.trials} ===", flush=True)
                row = run_trial(mode, trial, args.results, args.required_replicas, args.settle)
                writer.writerow(row)
                f.flush()
                rows.append(row)

    keys = ["pending_pod_seconds", "unavailable_pod_seconds", "time_to_capacity_s", "p95_ms", "failed_pct"]
    print(f"\n{'mode':<11}" + "".join(f"{k:>20}" for k in keys))
    for mode in args.modes:
        mine = [r for r in rows if r["mode"] == mode]
        print(f"{mode:<11}" + "".join(fmt(*mean_std([r[k] for r in mine])) for k in keys))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write `experiments/promotion_latency.py`**

```python
#!/usr/bin/env python3
"""Experiment 3: reserve promotion latency (uncordon -> pod Ready) vs scheduling onto an active node."""
import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "autoscaler"))

from common import DEPLOYMENT_NAME, NAMESPACE, PODS_PER_NODE, k8s  # noqa: E402
from metrics import mean_std  # noqa: E402

ACTIVE_NODES = 2


def app_pods(core):
    pods = core.list_namespaced_pod(NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}").items
    return [p for p in pods if p.metadata.deletion_timestamp is None]


def is_ready(pod):
    return any(c.type == "Ready" and c.status == "True" for c in (pod.status.conditions or []))


def wait_for(predicate, timeout=180, poll=0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(poll)
    raise TimeoutError("condition not met within timeout")


def scale(apps, replicas):
    apps.patch_namespaced_deployment_scale(DEPLOYMENT_NAME, NAMESPACE, {"spec": {"replicas": replicas}})


def reset():
    subprocess.run([str(ROOT / "scripts" / "reset.sh")], check=True, stdout=subprocess.DEVNULL)


def measure_reserve(apps, core, node):
    reset()
    full = ACTIVE_NODES * PODS_PER_NODE
    scale(apps, full + 1)  # fill the active nodes and leave exactly one pod unschedulable

    def one_unschedulable():
        pods = app_pods(core)
        return sum(map(is_ready, pods)) == full and sum(p.spec.node_name is None for p in pods) == 1

    wait_for(one_unschedulable)
    t0 = time.time()
    core.patch_node(node, {"spec": {"unschedulable": False}})
    wait_for(lambda: any(p.spec.node_name == node and is_ready(p) for p in app_pods(core)))
    return time.time() - t0


def measure_active(apps, core):
    reset()
    before = {p.metadata.name for p in app_pods(core)}
    t0 = time.time()
    scale(apps, len(before) + 1)  # active nodes still have free slots
    wait_for(lambda: any(p.metadata.name not in before and is_ready(p) for p in app_pods(core)))
    return time.time() - t0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "promotion_latency.csv")
    args = parser.parse_args()

    apps, core = k8s()
    reserves = sorted(n.metadata.name for n in core.list_node(label_selector="role=reserve").items)
    rows = []
    for repeat in range(1, args.repeats + 1):
        for node in reserves:
            rows.append({"kind": "reserve", "node": node, "repeat": repeat,
                         "seconds": round(measure_reserve(apps, core, node), 2)})
            print(rows[-1], flush=True)
        rows.append({"kind": "active", "node": "-", "repeat": repeat, "seconds": round(measure_active(apps, core), 2)})
        print(rows[-1], flush=True)
    reset()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["kind", "node", "repeat", "seconds"])
        writer.writeheader()
        writer.writerows(rows)
    for kind in ("reserve", "active"):
        mean, std = mean_std([r["seconds"] for r in rows if r["kind"] == kind])
        print(f"{kind:<8} {mean:.2f}s ± {std:.2f}s")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write `experiments/analyze_log.py`**

```python
#!/usr/bin/env python3
"""Experiment 5 (forecast MAPE at T+2min) and stability churn, from arbiter decision CSVs."""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "autoscaler"))

from common import TOTAL_CPU_QUERY, prom_query_range  # noqa: E402
from metrics import align_forecasts, count_events, forecasts_from_rows, mape  # noqa: E402


def load_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def cmd_mape(args):
    forecasts = forecasts_from_rows(load_rows(args.log))
    if not forecasts:
        sys.exit("no forecasts in log (was the predictive arm still warming up?)")
    start, end = forecasts[0][0], forecasts[-1][0] + 150  # run this >= 150s after the arbiter stopped
    actuals = prom_query_range(TOTAL_CPU_QUERY, (end - start) / 60, 15, end=end)
    pairs = align_forecasts(forecasts, actuals)
    print(f"forecasts={len(forecasts)} matched={len(pairs)} MAPE@T+2min={mape(pairs):.1f}%")


def cmd_churn(args):
    for path in args.logs:
        rows = load_rows(path)
        minutes = (float(rows[-1]["timestamp"]) - float(rows[0]["timestamp"])) / 60 if rows else 0
        print(f"{path}: {count_events(rows)} over {minutes:.1f} min")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(required=True)
    p_mape = sub.add_parser("mape", help="forecast accuracy of one decision log")
    p_mape.add_argument("log")
    p_mape.set_defaults(func=cmd_mape)
    p_churn = sub.add_parser("churn", help="count scale and cordon events")
    p_churn.add_argument("logs", nargs="+")
    p_churn.set_defaults(func=cmd_churn)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write `experiments/fallback_safety.sh`**

```bash
#!/usr/bin/env bash
# Experiment 4a: kill -9 the arbiter mid-spike and prove cluster state stays exactly as it was.
set -euo pipefail
cd "$(dirname "$0")/.."
state() {
  echo "replicas=$(kubectl get deploy sample-app -o jsonpath='{.spec.replicas}')" \
    "reserves=$(kubectl get nodes -l role=reserve -o jsonpath='{range .items[*]}{.metadata.name}:{.spec.unschedulable}{" "}{end}')"
}

scripts/reset.sh >/dev/null
mkdir -p results
python autoscaler/arbiter.py --log-csv results/fallback-decisions.csv >results/fallback-arbiter.log 2>&1 &
ARBITER=$!
k6 run --quiet loadtest/spike.js >results/fallback-k6.log 2>&1 &
K6=$!

sleep 120 # 60s baseline + 60s into the spike, so the arbiter has scaled up and promoted a node
kill -9 "$ARBITER"
before="$(state)"
echo "arbiter killed; state: $before"
sleep 60
after="$(state)"
wait "$K6" || true

if [ "$before" = "$after" ]; then
  echo "PASS: state unchanged 60s after arbiter death ($after)"
else
  echo "FAIL: state drifted: $before -> $after"
  exit 1
fi
```

- [ ] **Step 5: Verify each script's CLI parses**

```bash
chmod +x experiments/*.py experiments/fallback_safety.sh
python experiments/run_trials.py --help
python experiments/promotion_latency.py --help
python experiments/analyze_log.py churn results/sanity-decisions.csv
```
Expected: two usage texts; then a churn line like `results/sanity-decisions.csv: {'scale_up': 3, 'scale_down': 2, 'uncordon': 1, 'cordon': 1} over 6.0 min`.

- [ ] **Step 6: Short live dry run (~8 minutes)**

```bash
python experiments/run_trials.py --modes reactive --trials 1 --settle 10 --required-replicas "$REQUIRED_REPLICAS" --results results/dry
python experiments/promotion_latency.py --repeats 1 --out results/dry/promotion_latency.csv
```
Expected: `results/dry/trials.csv` has one row with numeric `pending_pod_seconds`/`p95_ms` and a numeric (not empty) `time_to_capacity_s`. The promotion run prints 3 `reserve` rows and 1 `active` row, each a few seconds. `scripts/verify-cluster.sh` passes afterwards.

- [ ] **Step 7: Commit**

```bash
git add experiments/
git commit -m "feat: experiment runners for trials, promotion latency, fallback safety and log analysis"
```

---

### Task 13: Run the experiments and write the README

**Files:**
- Create: `README.md`, `results/summary.md`

**Interfaces:**
- Consumes: everything above, plus `PER_POD_RPS`, `BASE_RPS`, `PEAK_RPS`, `REQUIRED_REPLICAS` from Task 10
- Produces: the numbers for the feasibility report in `results/summary.md`; a README anyone can follow from zero

- [ ] **Step 1: Pre-flight**

Run: `scripts/verify-cluster.sh && scripts/verify-monitoring.sh && pytest -q`
Expected: both `PASS` lines and `80 passed`.

- [ ] **Step 2: Experiments 1 + 2 — scaling lag and variance (~2.5 h, laptop plugged in, sleep disabled)**

Run: `caffeinate -i python experiments/run_trials.py --trials 5 --required-replicas "$REQUIRED_REPLICAS"`
Expected: 20 trials, then a table with one row each for `off`, `reactive`, `predictive`, `both`. `off` should show `n/a` time-to-capacity and the worst p95/failed%. Compare the **± std** of `pending_pod_seconds` across `reactive` / `predictive` / `both` (the AgileSphere variance claim). Copy the table into `results/summary.md` under "Experiments 1–2".

- [ ] **Step 3: Experiment 3 — reserve promotion latency (~15 min)**

Run: `python experiments/promotion_latency.py --repeats 3`
Expected: 9 reserve and 3 active measurements, then `reserve X.XXs ± …` / `active Y.YYs ± …`. Both should be seconds, not minutes, since the image is pre-loaded. Copy into `results/summary.md` under "Experiment 3".

- [ ] **Step 4: Experiment 4 — fallback safety**

4a. Run: `experiments/fallback_safety.sh`
Expected: `PASS: state unchanged 60s after arbiter death (replicas=… reserves=…)`

4b. Kill arms independently while the arbiter runs:
```bash
mkdir -p autoscaler/faults
python autoscaler/arbiter.py --log-csv results/arm-faults.csv &   # leave running
k6 run --quiet loadtest/spike.js &
sleep 90;  touch autoscaler/faults/fault-predictive   # expect "[predictive] injected fault", scaling continues on reactive
sleep 45;  touch autoscaler/faults/fault-reactive     # expect "BOTH arms failed, no change this cycle", replicas frozen
sleep 45;  rm autoscaler/faults/fault-*               # expect normal decisions to resume next cycle
wait %2; kill %1
```

4c. Prometheus outage:
```bash
python autoscaler/arbiter.py --log-csv results/prom-outage.csv &
kubectl -n monitoring patch prometheus monitoring-kube-prometheus-prometheus --type merge -p '{"spec":{"replicas":0}}'
sleep 60   # expect "[reactive] failed this cycle", "[predictive] failed this cycle", "BOTH arms failed" and no replica/node changes
kubectl -n monitoring patch prometheus monitoring-kube-prometheus-prometheus --type merge -p '{"spec":{"replicas":1}}'
sleep 120  # expect the reactive arm to recover; predictive logs "warming up" (emptyDir history was wiped) for ~10 min
kill %1
```
Record what you saw for 4a–4c in `results/summary.md` under "Experiment 4", quoting the log lines.

- [ ] **Step 5: Experiment 5 — forecast accuracy on a diurnal pattern (~70 min)**

```bash
scripts/reset.sh
HW_SEASONAL_PERIODS=40 python autoscaler/arbiter.py --log-csv results/diurnal-decisions.csv &
caffeinate -i k6 run loadtest/diurnal.js
sleep 150; kill %1
python experiments/analyze_log.py mape results/diurnal-decisions.csv
```
Expected: `forecasts=… matched=… MAPE@T+2min=…%`. Copy into `results/summary.md` under "Experiment 5". Note that seasonality only engages once 2 × 40 points (20 min) of history exist. (Optional comparison: repeat without `HW_SEASONAL_PERIODS` to get trend-only Holt MAPE.)

- [ ] **Step 6: Stability under noisy load (feasibility doc §10) (~15 min)**

```bash
scripts/reset.sh
python autoscaler/arbiter.py --log-csv results/noisy-decisions.csv &
JITTER=0.4 RATE=$((4 * PER_POD_RPS)) DURATION_S=900 k6 run --quiet loadtest/steady.js
kill %1
python experiments/analyze_log.py churn results/noisy-decisions.csv
```
Expected: event counts over ~15 min. Replicas must stay within [2, 12]. Record the counts in `results/summary.md` under "Stability".

- [ ] **Step 7: Write `README.md`**

````markdown
# Predictive-Reactive Kubernetes Autoscaler

Two autoscaling arms, **reactive** (every 15s) and **predictive** (Holt-Winters, every 30s), combined by a `max()` arbiter, plus a warm, pre-cordoned **reserve node pool** promoted with a single uncordon. Everything runs on one laptop with free, open-source tools.

```
k6 ─► localhost:8080 (NodePort) ─► sample-app pods ─► Prometheus (localhost:9090)
                                                        ├─► Reactive arm   (unschedulable pods, CPU vs request)
                                                        └─► Predictive arm (forecast total CPU at T+2min)
                                                                  │ max()
                                                                  ▼
                                                  Arbiter ─► Deployment replicas + reserve node (un)cordon
```

## Prerequisites

Docker Desktop (≥ 6 CPUs, ≥ 8 GB RAM), `brew install kind helm k6`, kubectl, Python 3.10+.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
scripts/cluster-up.sh && scripts/verify-cluster.sh           # 2 active + 3 cordoned reserve nodes
scripts/deploy-app.sh                                        # image pre-loaded onto every node
scripts/install-monitoring.sh && scripts/verify-monitoring.sh
pytest -q                                                    # 80 unit tests, no cluster needed
python autoscaler/arbiter.py --log-csv results/decisions.csv # terminal 1
k6 run loadtest/spike.js                                     # terminal 2
```

Teardown: `kind delete cluster --name autoscaler-demo`

## How it works

- **Reactive arm** (`autoscaler/reactive_arm.py`): unschedulable pods → `current + pending`. Otherwise it applies the HPA-style `ceil(current × cpu% / 80%)` with a ±10% hold band.
- **Predictive arm** (`autoscaler/predictive_arm.py`): pulls 30 min of total app CPU, fits Holt's trend (or additive Holt-Winters when `HW_SEASONAL_PERIODS` is set and 2 seasons exist), forecasts 8 × 15s ahead, and sizes replicas for the peak at 0.12 cores/pod. It returns nothing for the first 10 min (40 points).
- **Arbiter** (`autoscaler/arbiter.py`): `target = clamp(max(arms), 2, 12)` with a 20s cooldown on replica changes. Every cycle it promotes as many reserve nodes as `ceil(max(target, current) / 4)` requires, and on scale-down returns one reserve node. If both arms fail it changes nothing. It is the only component that writes to the cluster.
- **Reserve pool**: each worker advertises `autoscaler.demo/slot: 4` and each pod requests one slot. This matters because every kind node sees the whole laptop's CPU and would otherwise never run out of room.

Arbiter flags: `--mode both|reactive|predictive`, `--log-csv PATH`, `--once`. Kill an arm live with `touch autoscaler/faults/fault-reactive` (or `fault-predictive`); `rm` to revive.

## Calibration (Task 10)

| PER_POD_RPS | BASE_RPS | PEAK_RPS | REQUIRED_REPLICAS | WORK_ITERATIONS |
|---|---|---|---|---|
| (measured) | (chosen) | (chosen) | (computed) | (final value) |

## Experiments

| # | What | Command | Output |
|---|---|---|---|
| 1–2 | Scaling lag (off vs on) and run-to-run variance (reactive / predictive / both) | `python experiments/run_trials.py --trials 5 --required-replicas N` | `results/trials.csv` |
| 3 | Reserve promotion latency vs active-node scheduling | `python experiments/promotion_latency.py --repeats 3` | `results/promotion_latency.csv` |
| 4 | Fallback safety: arbiter killed, arms killed, Prometheus down | `experiments/fallback_safety.sh` + fault files + Prometheus replicas 0 | console / `results/*.csv` |
| 5 | Forecast MAPE at T+2min on a diurnal pattern | `HW_SEASONAL_PERIODS=40 python autoscaler/arbiter.py --log-csv results/diurnal-decisions.csv` + `k6 run loadtest/diurnal.js`, then `python experiments/analyze_log.py mape …` | console |
| 6 | Stability (churn) under noisy load | `JITTER=0.4 … k6 run loadtest/steady.js`, then `python experiments/analyze_log.py churn …` | console |

Results: see [`results/summary.md`](results/summary.md).

## Differences from the original build guide

The guide's snippets needed fixes to work on kind: node names, per-node capacity, port-forward pinning to one pod, CPU % units, relative `query_range` times, node counting in the arbiter, and scale-down never happening under `max()`. Each is listed with its reason in `docs/superpowers/plans/2026-09-12-predictive-reactive-autoscaler.md` ("Deviations From the Build Guide").

## Known limitations

| Limitation | Where it shows up | Why it's OK here |
|---|---|---|
| Reserve pool is finite (3 nodes) | Arbiter logs `reserve pool EMPTY, extra pods will stay Pending` | Same as vanilla Kubernetes when capacity runs out, and it's visible rather than silent |
| Single arbiter process, no leader election | `autoscaler/arbiter.py` | Out of scope for a mini-project; future work |
| Predictive warm-up of 10 min (seasonal needs 20 min) | `forecast_load()` returns `None` | Reactive arm covers the gap by design |
| Arms query Prometheus independently | 3 queries per cycle | Negligible at this scale |
| Cordon is not drain | A returned reserve node may keep app pods until the ReplicaSet removes them | Serving pods are never disrupted; `scripts/reset.sh` drains between trials |
| Slots model node capacity | kind nodes share the laptop CPU, so promotion adds scheduling room, not real compute | Scheduling behaviour and promotion latency are still real |
| Prometheus uses emptyDir | Restarting Prometheus wipes history, so the predictive arm warms up again | Demonstrated deliberately in Experiment 4c |
| `kubectl apply -f app/deployment.yaml` resets replicas to 2 | Manual re-deploys | Arbiter re-scales on its next cycle |
````

When writing the real file, fill in the calibration row with the numbers from Task 10 Step 4.

- [ ] **Step 8: Write `results/summary.md`**

It collects the tables and log excerpts from Steps 2–6 under the headings "Experiments 1–2", "Experiment 3", "Experiment 4", "Experiment 5" and "Stability". Also add one sentence per experiment stating what the numbers show (e.g. whether `both` has a lower std of pending pod-seconds than `reactive` alone).

- [ ] **Step 9: Final verification and commit**

Run: `pytest -q && scripts/verify-cluster.sh`
Expected: `80 passed`; `PASS` (reserve pool back at baseline after `scripts/reset.sh`).

```bash
git add README.md results/summary.md
git commit -m "docs: README and experiment results summary"
```
