VENV := .venv/bin
.PHONY: setup up deploy monitoring dashboard all engine ui spike matchday reset down test trials latency

setup:      ## python venv + dependencies
	python3 -m venv .venv && $(VENV)/pip install -q -r requirements.txt
up:         ## create the k3d cluster with the warm reserve pool
	cluster/create-cluster.sh && cluster/verify-cluster.sh
deploy:     ## build + import the image, deploy playback-api and the mock services
	cluster/deploy-app.sh
monitoring: ## Prometheus + kube-state-metrics + Grafana
	cluster/install-monitoring.sh && cluster/verify-monitoring.sh
dashboard:  ## load the Grafana dashboard
	cluster/install-dashboard.sh
all: up deploy monitoring dashboard ## everything, from nothing to demo-ready

engine:     ## run the autoscaling engine (reactive + predictive + demand)
	$(VENV)/python engine/main.py --log-csv results/live-decisions.csv
ui:         ## live cluster map on http://localhost:8000
	DECISION_LOG=results/live-decisions.csv ui/run.sh
spike:      ## sudden-rush load test
	k6 run loadtest/spike.js
matchday:   ## repeating compressed match-day trace (feeds the forecaster)
	k6 run loadtest/matchday.js

test:       ## unit tests, no cluster needed
	$(VENV)/python -m pytest -q
trials:     ## HPA vs reactive vs both vs full on the spike, 3 trials, alternating order (~1.5h)
	$(VENV)/python experiments/run_trials.py --trials 3
latency:    ## warm vs cold node promotion latency
	$(VENV)/python experiments/promotion_latency.py --repeats 3

reset:      ## back to the demo baseline
	cluster/reset.sh
down:       ## delete the cluster
	cluster/delete-cluster.sh

.PHONY: hpa-on hpa-off kill-predictive kill-reactive kill-demand revive-arms
hpa-on:     ## baseline: let Kubernetes' own HPA drive the Deployment (stop the engine first)
	kubectl apply -f k8s/hpa.yaml
hpa-off:    ## remove the HPA before running the engine again
	kubectl delete -f k8s/hpa.yaml --ignore-not-found
kill-predictive: ## demo: the predictive arm dies, the reactive arm carries on
	mkdir -p engine/faults && touch engine/faults/fault-predictive
kill-reactive:   ## demo: kill the other arm too, the engine then changes nothing
	mkdir -p engine/faults && touch engine/faults/fault-reactive
kill-demand:     ## demo: the demand arm dies too
	mkdir -p engine/faults && touch engine/faults/fault-demand
revive-arms:     ## demo: all arms come back
	rm -f engine/faults/fault-*

.PHONY: trials-matchday
trials-matchday: ## hpa vs full on the match-day workload, CYCLES=2 (20 min/trial, ~2h)
	CYCLES=2 $(VENV)/python experiments/run_trials.py --workload matchday --modes hpa full --trials 3
