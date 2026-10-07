"""Interactive Windows/Linux setup for DigitalOcean and GitHub production.

Run with uv run python scripts/setup_environment.py (Python 3.13+ project).
Requires GitHub CLI and Pulumi CLI; Pulumi login is guided.
The --github-only mode needs only Python and GitHub CLI.
"""
import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import warnings

from deploy import configuration

ENVIRONMENT = "production"
SECRET = "DIGITALOCEAN_ACCESS_TOKEN"
DEFAULT_REPO = "lambdawalker/devops.apexfisson.maven"


class GitHub:
    def __init__(self, executable, token=None):
        self.executable = executable
        self.env = dict(os.environ, GH_HOST="github.com")
        self.env.pop("GH_DEBUG", None)
        if token:
            self.env["GH_TOKEN"] = token

    def run(self, *args, stdin=None):
        try:
            result = subprocess.run(
                [self.executable, *args], input=stdin, text=True, encoding="utf-8",
                capture_output=True, env=self.env, timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("GitHub request timed out; its completion is unknown") from None
        if result.returncode:
            # Never echo subprocess output: a secret upload error could contain input.
            raise RuntimeError(
                f"GitHub command failed (exit {result.returncode}). "
                "Check connectivity, gh auth status, and repository/token permissions."
            )
        return result.stdout


def hidden(prompt):
    # getpass otherwise falls back to echoing input on unsupported terminals.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt).strip()
        except getpass.GetPassWarning:
            raise RuntimeError("Use a real terminal that supports hidden input") from None


def ask(label, default=""):
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or default


def authenticate(client):
    # Check the credentials actually used for requests, not every saved gh login.
    try:
        return client.run("api", "user", "--jq", ".login").strip()
    except RuntimeError:
        raise RuntimeError("Sign in first with: gh auth login --hostname github.com --web\n"
                           "Or rerun this wizard with --token-auth for a private PAT prompt.") from None


def confirm(repo, values, replacing):
    print(f"\nRepository: {repo}\nEnvironment: {ENVIRONMENT}")
    for name, value in values.items():
        print(f"  {name} = {value}")
    print(f"  {SECRET}: {'set/replace (hidden)' if replacing else 'keep existing'}")
    print("Existing variables with these names will be updated; other settings are preserved.")
    return input("Save these settings to GitHub? Type yes: ").strip().lower() == "yes"


def save(client, repo, values, token, existing):
    completed = []
    current = "environment creation"
    try:
        if not existing:
            client.run("api", "--method", "PUT", f"repos/{repo}/environments/{ENVIRONMENT}",
                       "--input", "-", stdin="{}")
            completed.append("environment creation")
        for name, value in values.items():
            current = name
            client.run("variable", "set", name, "--repo", repo, "--env", ENVIRONMENT,
                       "--body", value)
            completed.append(name)
        if token:
            current = SECRET
            # gh encrypts the value using the environment's public key before upload.
            client.run("secret", "set", SECRET, "--repo", repo, "--env", ENVIRONMENT,
                       stdin=token)
            completed.append(SECRET)
        current = "verification"
        actual = json.loads(client.run("variable", "list", "--repo", repo,
                                       "--env", ENVIRONMENT, "--json", "name,value"))
        actual = {item["name"]: item["value"] for item in actual}
        if any(actual.get(name) != value for name, value in values.items()):
            raise RuntimeError("Variable verification did not match requested settings")
        secrets = json.loads(client.run("secret", "list", "--repo", repo,
                                        "--env", ENVIRONMENT, "--json", "name"))
        if SECRET not in {item["name"] for item in secrets}:
            raise RuntimeError("Secret metadata verification failed")
    except (RuntimeError, ValueError, KeyError) as exc:
        done = ", ".join(completed) or "none confirmed"
        raise RuntimeError(
            f"Setup stopped during {current}. Completed: {done}. "
            "Changes are not rolled back; the current operation may also have completed. "
            "Fix access/connectivity and rerun. " + str(exc)
        ) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help=f"GitHub owner/repo (default prompt: {DEFAULT_REPO})")
    parser.add_argument("--token-auth", action="store_true",
                        help="Privately prompt for a GitHub PAT instead of using gh's saved login")
    parser.add_argument("--github-only", action="store_true",
                        help="Configure GitHub only, using an existing cluster UUID and certificate")
    parser.add_argument("--saved-login", action="store_true",
                        help="Use saved gh login instead of prompting for a GitHub token")
    args = parser.parse_args()
    if args.saved_login and args.token_auth:
        parser.error("--saved-login and --token-auth cannot be combined")
    if not sys.stdin.isatty():
        raise RuntimeError("Run this wizard interactively in a terminal, without redirected input")
    if not args.github_only:
        from setup_cloud import run
        run(args)
        return
    executable = shutil.which("gh")
    if not executable:
        raise RuntimeError("Install GitHub CLI from https://cli.github.com/ and reopen your terminal")
    repo = args.repo or ask("GitHub repository", DEFAULT_REPO)
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Repository must use owner/repo format on github.com")
    github_token = hidden("GitHub access token (hidden): ") if args.token_auth else None
    if args.token_auth and not github_token:
        raise ValueError("A GitHub token is required with --token-auth")
    client = GitHub(executable, github_token)
    identity = authenticate(client)
    repository = json.loads(client.run("api", f"repos/{repo}"))
    if not repository.get("permissions", {}).get("admin"):
        raise RuntimeError("An account with repository administrator access is required")
    repo = repository["full_name"]
    print(f"Signed in as {identity}. Configuring {repo} / {ENVIRONMENT}.")
    names = client.run("api", f"repos/{repo}/environments", "--paginate",
                       "--jq", ".environments[].name").splitlines()
    # Environment names are case insensitive; never PUT an already existing one.
    existing = any(name.casefold() == ENVIRONMENT.casefold() for name in names)
    defaults = {}
    secret_exists = False
    if existing:
        defaults = {item["name"]: item["value"] for item in json.loads(client.run(
            "variable", "list", "--repo", repo, "--env", ENVIRONMENT, "--json", "name,value"))}
        secret_exists = SECRET in {item["name"] for item in json.loads(client.run(
            "secret", "list", "--repo", repo, "--env", ENVIRONMENT, "--json", "name"))}
    values = configuration({
        "DOKS_CLUSTER_ID": ask("DOKS cluster UUID", defaults.get("DOKS_CLUSTER_ID", "")),
        "REPOSILITE_HOSTNAME": ask("Maven hostname", defaults.get("REPOSILITE_HOSTNAME", "")),
        "DO_CERTIFICATE_NAME": ask("DO certificate name", defaults.get("DO_CERTIFICATE_NAME", "")),
    })
    suffix = " (Enter keeps existing)" if secret_exists else " (required)"
    token = hidden(f"DigitalOcean API token{suffix}: ")
    if not token and not secret_exists:
        raise ValueError("A DigitalOcean token is required for a new setup")
    if any(char.isspace() or ord(char) < 32 for char in token):
        raise ValueError("The token must be a single value without whitespace")
    if not confirm(repo, values, replacing=bool(token)):
        print("Cancelled. No settings were changed.")
        return
    save(client, repo, values, token, existing)
    print("\nSetup complete. Variables verified and secret metadata found.")
    print("Secret contents cannot be read back; DigitalOcean authentication was not tested.")
    print(f"Environment: https://github.com/{repo}/settings/environments")
    print("Once the deployment workflow is merged: Actions > Deploy Reposilite > Run workflow > main")
    print("No cluster was created and no deployment was triggered.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted. If saving had started, some settings may have changed; rerun to verify.",
              file=sys.stderr)
        sys.exit(130)
