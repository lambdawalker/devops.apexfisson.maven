import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class SetupTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "scripts/setup_environment.py"
        self.assertTrue(path.exists(), "Setup wizard has not been implemented")
        spec = importlib.util.spec_from_file_location("setup_environment", path)
        self.wizard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.wizard)
        self.values = {"DOKS_CLUSTER_ID": "11111111-1111-4111-8111-111111111111",
                       "REPOSILITE_HOSTNAME": "maven.example.com",
                       "DO_CERTIFICATE_NAME": "maven-cert"}

    def test_secret_uses_stdin_not_shell_arguments_or_error_output(self):
        token = "test-secret-never-display"
        client = self.wizard.GitHub("gh")
        result = subprocess.CompletedProcess([], 1, token, token)
        with patch.object(self.wizard.subprocess, "run", return_value=result) as run:
            with self.assertRaises(RuntimeError) as error:
                client.run("secret", "set", "DIGITALOCEAN_ACCESS_TOKEN", stdin=token)
        args, kwargs = run.call_args
        self.assertNotIn(token, args[0])
        self.assertEqual(token, kwargs["input"])
        self.assertFalse(kwargs.get("shell", False))
        self.assertNotIn(token, str(error.exception))

    def test_existing_environment_is_not_updated_and_blank_keeps_secret(self):
        calls = []
        class Client:
            def run(_, *args, **kwargs):
                calls.append((args, kwargs))
                if args[:2] == ("variable", "list"):
                    return json.dumps([{"name": k, "value": v} for k, v in self.values.items()])
                if args[:2] == ("secret", "list"):
                    return json.dumps([{"name": "DIGITALOCEAN_ACCESS_TOKEN"}])
                return ""
        self.wizard.save(Client(), "owner/repo", self.values, "", existing=True)
        self.assertFalse(any(args[0] == "api" for args, _ in calls))
        self.assertFalse(any(args[:2] == ("secret", "set") for args, _ in calls))
        self.assertEqual(3, sum(args[:2] == ("variable", "set") for args, _ in calls))

    def test_partial_failure_reports_names_without_claiming_rollback(self):
        token = "test-secret-never-display"
        class Client:
            def run(_, *args, **kwargs):
                if args[:2] == ("secret", "set"):
                    raise RuntimeError("GitHub command failed")
                return ""
        with self.assertRaises(RuntimeError) as error:
            self.wizard.save(Client(), "owner/repo", self.values, token, existing=False)
        message = str(error.exception)
        self.assertIn("DOKS_CLUSTER_ID", message)
        self.assertIn("not rolled back", message)
        self.assertNotIn(token, message)

    def test_missing_secret_is_detected_during_verification(self):
        class Client:
            def run(_, *args, **kwargs):
                if args[:2] == ("variable", "list"):
                    return json.dumps([{"name": k, "value": v} for k, v in self.values.items()])
                if args[:2] == ("secret", "list"):
                    return "[]"
                return ""
        with self.assertRaisesRegex(RuntimeError, "verification"):
            self.wizard.save(Client(), "owner/repo", self.values, "", existing=True)

    def test_refuses_getpass_echo_fallback(self):
        import warnings
        def fallback(prompt):
            warnings.warn("cannot hide input", self.wizard.getpass.GetPassWarning)
            return "would-echo"
        with patch.object(self.wizard.getpass, "getpass", side_effect=fallback):
            with self.assertRaisesRegex(RuntimeError, "terminal"):
                self.wizard.hidden("Token: ")

    def test_authentication_uses_active_identity_not_all_saved_accounts(self):
        class Client:
            def run(_, *args, **kwargs):
                if args == ("api", "user", "--jq", ".login"):
                    return "active-admin\n"
                raise RuntimeError("An unrelated saved account has expired")
        self.assertEqual("active-admin", self.wizard.authenticate(Client()))

    def test_declining_review_performs_no_writes(self):
        with patch("builtins.input", return_value="no"), patch("sys.stdout", new_callable=io.StringIO):
            self.assertFalse(self.wizard.confirm("owner/repo", self.values, replacing=True))


if __name__ == "__main__":
    unittest.main()
