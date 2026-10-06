"""Test deployment orchestration without cloud credentials or a real cluster."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import json

ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "scripts/deploy.py"
        self.assertTrue(path.exists(), "Deployment script has not been implemented")
        spec = importlib.util.spec_from_file_location("deploy", path)
        self.deploy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.deploy)
        self.config = {
            "DOKS_CLUSTER_ID": "11111111-1111-4111-8111-111111111111",
            "REPOSILITE_HOSTNAME": "maven.example.com",
            "DO_CERTIFICATE_NAME": "maven-cert",
        }

    def test_rejects_missing_or_unsafe_configuration(self):
        for key, value in [("DOKS_CLUSTER_ID", ""), ("DOKS_CLUSTER_ID", "--help"),
                           ("REPOSILITE_HOSTNAME", "https://maven.example.com"),
                           ("REPOSILITE_HOSTNAME", "host\nother"),
                           ("DO_CERTIFICATE_NAME", ""), ("DO_CERTIFICATE_NAME", "a\nb")]:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    self.deploy.configuration({**self.config, key: value})

    def test_render_escapes_certificate_values_without_yaml_injection(self):
        conf = self.deploy.configuration({**self.config, "DO_CERTIFICATE_NAME": 'cert: "production"'})
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = self.deploy.write_overlay(conf, Path(directory))
            result = subprocess.check_output(
                [os.environ.get("KUBECTL", "kubectl"), "kustomize", str(path)], text=True)
            import yaml
            svc = next(d for d in yaml.safe_load_all(result) if d["kind"] == "Service")
            self.assertEqual('cert: "production"', svc["metadata"]["annotations"][
                "service.beta.kubernetes.io/do-loadbalancer-certificate-name"])
            self.assertEqual({80, 443}, {p["port"] for p in svc["spec"]["ports"]})

    def fake_cluster(self, blocker=None, fail_dry_run=False):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if "get" in args:
                resource = args[args.index("get") + 1]
                value = {
                    "deployment": {"spec": {"replicas": 1}},
                    "pvc": {"status": {"phase": "Bound"}},
                    "secret": {}, "pod": {},
                }[resource]
                if resource == blocker:
                    value = {"metadata": {"name": "blocker"}}
                if blocker == "scaled-down" and resource == "deployment":
                    value = {"spec": {"replicas": 0}}
                if blocker == "missing-pvc" and resource == "pvc":
                    raise subprocess.CalledProcessError(1, args)
                return subprocess.CompletedProcess(args, 0, json.dumps(value) if value else "", "")
            if fail_dry_run and "--dry-run=server" in args:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, "", "")
        return calls, run

    def test_blockers_prevent_any_apply(self):
        for blocker in ["secret", "pod", "scaled-down", "missing-pvc"]:
            with self.subTest(blocker=blocker):
                calls, run = self.fake_cluster(blocker)
                with patch.object(self.deploy.subprocess, "run", side_effect=run):
                    with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                        self.deploy.apply(Path("rendered.yaml"))
                self.assertFalse(any("apply" in args for args in calls))

    def test_failed_server_validation_prevents_real_apply(self):
        calls, run = self.fake_cluster(fail_dry_run=True)
        with patch.object(self.deploy.subprocess, "run", side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):
                self.deploy.apply(Path("rendered.yaml"))
        self.assertEqual(1, sum("apply" in args for args in calls))

    def test_success_applies_once_and_waits_for_rollout(self):
        calls, run = self.fake_cluster()
        with patch.object(self.deploy.subprocess, "run", side_effect=run):
            self.deploy.apply(Path("rendered.yaml"))
        applies = [args for args in calls if "apply" in args]
        self.assertEqual(2, len(applies))
        self.assertIn("--dry-run=server", applies[0])
        self.assertNotIn("--dry-run=server", applies[1])
        self.assertTrue(any("rollout" in args and "status" in args for args in calls))
        self.assertFalse(any("delete" in args or "--prune" in args for args in calls))


if __name__ == "__main__":
    unittest.main()
