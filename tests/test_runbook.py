"""Exercise the documented shell flow with a failing Kubernetes CLI boundary."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RunbookFailureSafety(unittest.TestCase):
    def run_block(self, contains, failure):
        blocks = re.findall(r"```bash\n(.*?)```", (ROOT / "docs/operations.md").read_text(), re.S)
        block = next(b for b in blocks if contains in b)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace"
            archive = Path(directory) / "backup.tar.gz"
            archive.touch()
            # Substitute only the external CLI. The actual documented Bash is run.
            stub = '''kubectl() {
  printf '%s\\n' "$*" >> "$TRACE"
  if [[ "$*" == *"$FAILURE"* ]]; then return 42; fi
  return 0
}
'''
            result = subprocess.run(
                ["bash", "-c", stub + block], cwd=directory,
                env={**os.environ, "DOKS_CONTEXT": "test-cluster", "TRACE": str(trace),
                     "FAILURE": failure, "BACKUP_FILE": str(archive)},
                capture_output=True, text=True,
            )
            calls = trace.read_text().splitlines()
            self.assertIn(failure, calls[-1], "Commands continued after a failed prerequisite")
            self.assertEqual(42, result.returncode)

    def test_backup_stops_before_reading_live_data(self):
        for failure in ["--replicas=0", "--for=delete"]:
            with self.subTest(failure=failure):
                self.run_block("--replicas=0", failure)

    def test_restore_never_starts_application_after_failure(self):
        for failure in ["-xzf", "delete pod"]:
            with self.subTest(failure=failure):
                self.run_block("--no-same-owner", failure)


if __name__ == "__main__":
    unittest.main()
