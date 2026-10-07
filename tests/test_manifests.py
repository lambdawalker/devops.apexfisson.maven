"""Safety checks against actual Kustomize output; no cluster credentials needed."""
import os
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def render(path):
    result = subprocess.run(
        [os.environ.get("KUBECTL", "kubectl"), "kustomize", str(ROOT / path)],
        check=True, text=True, capture_output=True,
    )
    return list(yaml.safe_load_all(result.stdout))


class DeploymentSafety(unittest.TestCase):
    def test_private_bootstrap_has_no_public_endpoint_or_embedded_secret(self):
        docs = render("k8s/base")
        self.assertFalse(any(d["kind"] in {"Secret", "Ingress"} for d in docs))
        service = next(d for d in docs if d["kind"] == "Service")
        self.assertEqual("ClusterIP", service["spec"]["type"])
        self.assertNotIn("externalIPs", service["spec"])

    def test_single_writer_and_retained_persistent_data_in_both_targets(self):
        for target in ["k8s/base", "k8s/overlays/production", "k8s/overlays/cloudflare"]:
            with self.subTest(target=target):
                docs = render(target)
                dep = next(d for d in docs if d["kind"] == "Deployment")
                self.assertEqual(1, dep["spec"]["replicas"])
                self.assertEqual("Recreate", dep["spec"]["strategy"]["type"])
                pvc = next(d for d in docs if d["kind"] == "PersistentVolumeClaim")
                sc = next(d for d in docs if d["kind"] == "StorageClass")
                self.assertEqual("Retain", sc["reclaimPolicy"])
                self.assertEqual(sc["metadata"]["name"], pvc["spec"]["storageClassName"])
                pod = dep["spec"]["template"]["spec"]
                self.assertFalse(pod["automountServiceAccountToken"])
                self.assertTrue(pod["securityContext"]["runAsNonRoot"])
                container = pod["containers"][0]
                mounts = {m["mountPath"]: m["name"] for m in container["volumeMounts"]}
                self.assertTrue({"/app/data", "/var/log/reposilite", "/tmp"} <= mounts.keys())
                volume = next(v for v in pod["volumes"] if v["name"] == mounts["/app/data"])
                self.assertEqual(pvc["metadata"]["name"], volume["persistentVolumeClaim"]["claimName"])
                self.assertNotIn(":latest", container["image"])
                self.assertNotIn(":nightly", container["image"])

    def test_public_endpoint_terminates_tls_and_cannot_load_bootstrap_secret(self):
        docs = render("k8s/overlays/production")
        service = next(d for d in docs if d["kind"] == "Service")
        self.assertEqual("LoadBalancer", service["spec"]["type"])
        self.assertEqual({80, 443}, {p["port"] for p in service["spec"]["ports"]})
        annotations = service["metadata"]["annotations"]
        prefix = "service.beta.kubernetes.io/do-loadbalancer-"
        self.assertEqual("REGIONAL", annotations[prefix + "type"])
        self.assertEqual("443", annotations[prefix + "tls-ports"])
        self.assertEqual("true", annotations[prefix + "redirect-http-to-https"])
        self.assertTrue(annotations[prefix + "certificate-name"])
        dep = next(d for d in docs if d["kind"] == "Deployment")
        container = dep["spec"]["template"]["spec"]["containers"][0]
        self.assertFalse(container.get("envFrom"))
        self.assertFalse(any("--token" in e.get("value", "") for e in container["env"]))


if __name__ == "__main__":
    unittest.main()
