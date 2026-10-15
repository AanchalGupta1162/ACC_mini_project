"""All tunables in one place. Every value can be overridden with an environment variable."""
import os

PROM_URL = os.environ.get("PROM_URL", "http://localhost:9090")
NAMESPACE = os.environ.get("NAMESPACE", "default")
DEPLOYMENT = os.environ.get("DEPLOYMENT", "playback-api")

# scaling bounds and capacity model
MIN_REPLICAS = int(os.environ.get("MIN_REPLICAS", "2"))
MAX_REPLICAS = int(os.environ.get("MAX_REPLICAS", "12"))
PODS_PER_NODE = int(os.environ.get("PODS_PER_NODE", "4"))  # must equal SLOTS_PER_NODE in cluster/label-nodes.sh
CPU_REQUEST_CORES = float(os.environ.get("CPU_REQUEST_CORES", "0.15"))  # must match k8s/playback-api.yaml
TARGET_CPU_UTILIZATION = float(os.environ.get("TARGET_CPU_UTILIZATION", "80"))  # % of CPU request
CPU_TOLERANCE = float(os.environ.get("CPU_TOLERANCE", "0.1"))  # HPA-style ±10% hold band

# capacity calibration: how many requests/second one pod serves at acceptable latency
RPS_PER_POD = float(os.environ.get("RPS_PER_POD", "12"))  # calibrated: ~10 rps/pod at 80% of CPU request
SAFETY_MARGIN = float(os.environ.get("SAFETY_MARGIN", "1.2"))

# loop timing
CYCLE_SECONDS = float(os.environ.get("CYCLE_SECONDS", "15"))
PREDICTIVE_INTERVAL_SECONDS = float(os.environ.get("PREDICTIVE_INTERVAL_SECONDS", "30"))
COOLDOWN_SECONDS = float(os.environ.get("COOLDOWN_SECONDS", "20"))

# forecasting
HISTORY_MINUTES = float(os.environ.get("HISTORY_MINUTES", "30"))
STEP_SECONDS = int(os.environ.get("STEP_SECONDS", "15"))
HORIZON_STEPS = int(os.environ.get("HORIZON_STEPS", "8"))  # 8 x 15s = T+2min
MIN_POINTS = int(os.environ.get("MIN_POINTS", "40"))  # 10 min before Holt's trend is trusted
_seasonal = os.environ.get("SEASONAL_PERIODS", "40")  # 40 x 15s = the 10-minute match-day cycle
SEASONAL_PERIODS = int(_seasonal) if _seasonal and _seasonal != "0" else None

# cluster/demo
CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "jiohotstar")
EXPORTER_PORT = int(os.environ.get("EXPORTER_PORT", "9900"))
ENABLE_COLD_PATH = os.environ.get("ENABLE_COLD_PATH", "1") == "1"  # k3d node create when reserves run out
