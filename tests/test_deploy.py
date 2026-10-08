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

    def test_cloudflare_render_uses_private_service_and_tls_ingress(self):
        conf = self.deploy.configuration({
            "DOKS_CLUSTER_ID": self.config["DOKS_CLUSTER_ID"],
            "REPOSILITE_HOSTNAME": "maven.apexfission.com", "TLS_MODE": "cloudflare"})
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = self.deploy.write_overlay(conf, Path(directory))
            result = subprocess.check_output(
                [os.environ.get("KUBECTL", "kubectl"), "kustomize", str(path)], text=True)
        import yaml
        docs = list(yaml.safe_load_all(result))
        service = next(d for d in docs if d["kind"] == "Service")
        self.assertEqual("ClusterIP", service["spec"]["type"])
        ingress = next(d for d in docs if d["kind"] == "Ingress")
        self.assertEqual("reposilite-edge", ingress["spec"]["ingressClassName"])
        self.assertEqual("maven.apexfission.com", ingress["spec"]["rules"][0]["host"])
        self.assertEqual(["maven.apexfission.com"], ingress["spec"]["tls"][0]["hosts"])
        self.assertEqual("reposilite-tls", ingress["spec"]["tls"][0]["secretName"])
        self.assertEqual("cloudflare-letsencrypt", ingress["metadata"]["annotations"][
            "cert-manager.io/cluster-issuer"])
        dep = next(d for d in docs if d["kind"] == "Deployment")
        self.assertFalse(dep["spec"]["template"]["spec"]["containers"][0].get("envFrom"))

    def test_http01_render_uses_matching_issuer_and_hostname_on_both_ingresses(self):
        conf = self.deploy.configuration({
            "DOKS_CLUSTER_ID": self.config["DOKS_CLUSTER_ID"],
            "REPOSILITE_HOSTNAME": "maven.apexfission.com", "TLS_MODE": "http01"})
        self.assertNotIn("DO_CERTIFICATE_NAME", conf)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = self.deploy.write_overlay(conf, Path(directory))
            result = subprocess.check_output(
                [os.environ.get("KUBECTL", "kubectl"), "kustomize", str(path)], text=True)
        import yaml
        docs = list(yaml.safe_load_all(result))
        ingresses = [d for d in docs if d["kind"] == "Ingress"]
        self.assertEqual(2, len(ingresses))
        for ingress in ingresses:
            self.assertEqual("maven.apexfission.com", ingress["spec"]["rules"][0]["host"])
            self.assertEqual("reposilite-edge", ingress["spec"]["ingressClassName"])
        tls = next(d for d in ingresses if d["metadata"]["name"] == "reposilite")
        self.assertEqual("reposilite-http01", tls["metadata"]["annotations"]["cert-manager.io/cluster-issuer"])
        self.assertEqual(["maven.apexfission.com"], tls["spec"]["tls"][0]["hosts"])
        service = next(d for d in docs if d["kind"] == "Service")
        self.assertEqual("ClusterIP", service["spec"]["type"])
        dep = next(d for d in docs if d["kind"] == "Deployment")
        self.assertFalse(dep["spec"]["template"]["spec"]["containers"][0].get("envFrom"))
        redirect = next(d for d in docs if d["kind"] == "Middleware")
        self.assertEqual("https", redirect["spec"]["redirectScheme"]["scheme"])
        self.assertNotIn("cloudflare", result.lower())

    def test_http01_waits_for_certificate_and_ingress(self):
        calls, run = self.fake_cluster()
        with patch.object(self.deploy.subprocess, "run", side_effect=run):
            self.deploy.apply(Path("rendered.yaml"), tls_mode="http01")
        self.assertTrue(any("certificate/reposilite-tls" in c for c in calls))
        self.assertTrue(any("ingress/reposilite" in c for c in calls))
        self.assertTrue(any("--dry-run=server" in c for c in calls))
        self.assertFalse(any("service/reposilite" in c and "wait" in c for c in calls))

    def test_invalid_tls_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.deploy.configuration({**self.config, "TLS_MODE": "unknown"})

    def test_cloudflare_waits_for_certificate_and_ingress(self):
        calls, run = self.fake_cluster()
        with patch.object(self.deploy.subprocess, "run", side_effect=run):
            self.deploy.apply(Path("rendered.yaml"), tls_mode="cloudflare")
        self.assertTrue(any("certificate/reposilite-tls" in c for c in calls))
        self.assertTrue(any("ingress/reposilite" in c for c in calls))
        self.assertFalse(any("service/reposilite" in c and "wait" in c for c in calls))

    def test_cloudflare_apply_writes_action_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            with patch.dict(os.environ, {"TLS_MODE": "cloudflare",
                    "DOKS_CLUSTER_ID": self.config["DOKS_CLUSTER_ID"],
                    "REPOSILITE_HOSTNAME": "maven.apexfission.com",
                    "GITHUB_STEP_SUMMARY": str(summary)}), \
                    patch("sys.argv", ["deploy.py", "apply"]), \
                    patch.object(self.deploy, "apply"), patch.object(self.deploy, "kubectl"):
                self.deploy.main()
            text = summary.read_text()
            self.assertIn("https://maven.apexfission.com", text)
            self.assertIn("Cloudflare DNS is managed by Pulumi", text)

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
