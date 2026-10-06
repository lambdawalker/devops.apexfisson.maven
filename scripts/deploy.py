"""Render the CI overlay and deploy to an explicitly selected kubeconfig context."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = "reposilite-deploy"


def configuration(environ):
    values = {key: environ.get(key, "").strip() for key in (
        "DOKS_CLUSTER_ID", "REPOSILITE_HOSTNAME", "DO_CERTIFICATE_NAME")}
    for key, value in values.items():
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"Set a nonempty, single-line {key} in the production environment")
    try:
        uuid.UUID(values["DOKS_CLUSTER_ID"])
    except ValueError as exc:
        raise ValueError("DOKS_CLUSTER_ID must be the cluster UUID, not its name") from exc
    hostname = values["REPOSILITE_HOSTNAME"]
    if len(hostname) > 253 or "." not in hostname or not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in hostname.split(".")
    ):
        raise ValueError("REPOSILITE_HOSTNAME must be a DNS hostname without a scheme, port or path")
    return values


def write_overlay(config, directory):
    directory.mkdir(parents=True, exist_ok=True)
    overlay = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
        "resources": [os.path.relpath(ROOT / "k8s/overlays/production", directory)],
        "patches": [{
            "target": {"kind": "Service", "name": "reposilite"},
            "patch": json.dumps([{
                "op": "replace",
                "path": "/metadata/annotations/service.beta.kubernetes.io~1do-loadbalancer-certificate-name",
                "value": config["DO_CERTIFICATE_NAME"],
            }]),
        }],
    }
    # JSON is valid YAML; Kustomize requires one of its recognized YAML filenames.
    (directory / "kustomization.yaml").write_text(json.dumps(overlay, indent=2) + "\n")
    return directory


def kubectl(*args, capture=False):
    return subprocess.run(
        [os.environ.get("KUBECTL", "kubectl"), "--context", CONTEXT,
         "--namespace", "reposilite", *args],
        check=True, text=True, capture_output=capture,
    ).stdout


def apply(manifest):
    # Fail closed: missing resources, RBAC/network errors and maintenance block writes.
    dep = json.loads(kubectl("get", "deployment", "reposilite", "-o", "json", capture=True))
    if dep["spec"].get("replicas") != 1:
        raise RuntimeError("Deployment must already exist with one replica; finish bootstrap/maintenance first")
    pvc = json.loads(kubectl("get", "pvc", "reposilite-data", "-o", "json", capture=True))
    if pvc.get("status", {}).get("phase") != "Bound":
        raise RuntimeError("Existing reposilite-data PVC must be Bound")
    for kind, name in [("secret", "reposilite-bootstrap"), ("pod", "reposilite-maintenance")]:
        if kubectl("get", kind, name, "--ignore-not-found", "-o", "name", capture=True).strip():
            raise RuntimeError(f"Remove {kind}/{name} after completing bootstrap/maintenance before deploying")
    kubectl("apply", "--dry-run=server", "-f", str(manifest))
    kubectl("apply", "-f", str(manifest))
    kubectl("rollout", "status", "deployment/reposilite", "--timeout=10m")
    kubectl("wait", "--for=jsonpath={.status.loadBalancer.ingress}",
            "service/reposilite", "--timeout=5m")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["render", "apply"])
    args = parser.parse_args()
    config = configuration(os.environ)
    directory = ROOT / ".local/ci"
    if args.operation == "render":
        write_overlay(config, directory)
    else:
        apply(directory / "rendered.yaml")
        kubectl("get", "service", "reposilite", "-o", "wide")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as stream:
                stream.write(f"## Reposilite deployment\n\n"
                             f"Pod rollout completed and a load balancer address is assigned.\n\n"
                             f"Endpoint: https://{config['REPOSILITE_HOSTNAME']}\n\n"
                             "Use the Service address in the job log for your DNS A record. "
                             "Verify DNS, the TLS certificate and dashboard externally; "
                             "this workflow checks Kubernetes readiness, not public reachability.\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Deployment stopped: {exc}", file=sys.stderr)
        sys.exit(1)
