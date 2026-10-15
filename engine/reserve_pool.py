"""The only component that changes cluster state: replicas, node cordons, and cold nodes."""
import subprocess
import time

from config import CLUSTER_NAME, DEPLOYMENT, NAMESPACE, PODS_PER_NODE


class ClusterAdapter:
    def __init__(self):
        from kubernetes import client, config as kube_config

        kube_config.load_kube_config()
        self.apps = client.AppsV1Api()
        self.core = client.CoreV1Api()

    def current_replicas(self):
        return self.apps.read_namespaced_deployment(DEPLOYMENT, NAMESPACE).spec.replicas

    def set_replicas(self, replicas):
        self.apps.patch_namespaced_deployment_scale(DEPLOYMENT, NAMESPACE, {"spec": {"replicas": replicas}})

    def _workers(self):
        return self.core.list_node(label_selector="role in (active,reserve)").items

    def node_counts(self):
        workers = self._workers()
        schedulable = sum(1 for n in workers if not n.spec.unschedulable)
        reserves = [n for n in workers if n.metadata.labels.get("role") == "reserve"]
        cordoned = sum(1 for n in reserves if n.spec.unschedulable)
        return schedulable, cordoned, len(reserves) - cordoned

    def reserve_names(self, unschedulable):
        nodes = self.core.list_node(label_selector="role=reserve").items
        return sorted(n.metadata.name for n in nodes if bool(n.spec.unschedulable) == unschedulable)

    def set_unschedulable(self, name, value):
        self.core.patch_node(name, {"spec": {"unschedulable": value}})

    def create_cold_node(self):
        """The slow path: build a brand-new node, then pull the image onto it."""
        before = {n.metadata.name for n in self.core.list_node().items}
        name = f"cold-{int(time.time()) % 100000}"
        subprocess.run(
            ["k3d", "node", "create", name, "--cluster", CLUSTER_NAME, "--role", "agent",
             "--wait", "--timeout", "180s"],
            check=True, capture_output=True,
        )
        deadline = time.time() + 180
        while time.time() < deadline:
            new = {n.metadata.name for n in self.core.list_node().items} - before
            if new:
                node = sorted(new)[0]
                self.core.patch_node(node, {"metadata": {"labels": {"role": "active"}}})
                self.core.patch_node_status(
                    node, {"status": {"capacity": {"autoscaler.demo/slot": str(PODS_PER_NODE)}}}
                )
                # a brand-new node has no cached image, which is exactly why this path is slow
                subprocess.run(
                    ["k3d", "image", "import", "playback-api:1.0", "--cluster", CLUSTER_NAME],
                    check=False, capture_output=True,
                )
                return node
            time.sleep(2)
        raise TimeoutError(f"cold node {name} never joined the cluster")
