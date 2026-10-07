"""Interactive Windows/Linux setup for DigitalOcean, Cloudflare and GitHub production.

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
from pathlib import Path

import setup_ui as ui
from token_store import DEFAULT_PATH, load_tokens

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
        ui.check_cancelled()
        try:
            result = ui.run_command(
                [self.executable, *args], input=stdin, text=True, encoding="utf-8",
                capture_output=True, env=self.env, timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise ui.SetupError("GitHub request timed out; its completion is unknown") from None
        if result.returncode:
            # The command boundary redacts input and tokens before streaming diagnostics.
            raise ui.SetupError(
                f"GitHub command failed (exit {result.returncode}). "
                "Check connectivity, gh auth status, and repository/token permissions."
            )
        return result.stdout


def hidden(prompt):
    return ui.hidden(prompt)


def ask(label, default=""):
    return ui.ask(label, default)


def authenticate(client):
    # Check the credentials actually used for requests, not every saved gh login.
    try:
        return client.run("api", "user", "--jq", ".login").strip()
    except RuntimeError:
        raise ui.SetupError("Sign in first with: gh auth login --hostname github.com --web\n"
                           "Or rerun this wizard with --token-auth for a private PAT prompt.") from None


def confirm(repo, values, replacing):
    ui.say(f"\nRepository: {repo}\nEnvironment: {ENVIRONMENT}")
    for name, value in values.items():
        ui.say(f"  {name} = {value}")
    ui.say(f"  {SECRET}: {'set/replace (hidden)' if replacing else 'keep existing'}")
    ui.say("Existing variables with these names will be updated; other settings are preserved.")
    return ask("Save these settings to GitHub? yes/no", "no").lower() == "yes"


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
            raise ui.SetupError("Variable verification did not match requested settings")
        secrets = json.loads(client.run("secret", "list", "--repo", repo,
                                        "--env", ENVIRONMENT, "--json", "name"))
        if SECRET not in {item["name"] for item in secrets}:
            raise ui.SetupError("Secret metadata verification failed")
    except (RuntimeError, ValueError, KeyError) as exc:
        done = ", ".join(completed) or "none confirmed"
        raise ui.SetupError(
            f"Setup stopped during {current}. Completed: {done}. "
            "Changes are not rolled back; the current operation may also have completed. "
            "Fix access/connectivity and rerun. " + (str(exc) if isinstance(exc, ui.SafeError) else 'Unexpected response; details withheld to protect credentials.')
        ) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plain", action="store_true", help="Use basic terminal prompts instead of Textual")
    parser.add_argument("--tokens-file", type=Path, default=DEFAULT_PATH,
                        help="Encrypted token file (default: .local/tokens.gpg in this repository)")
    parser.add_argument("--repo", help=f"GitHub owner/repo (default prompt: {DEFAULT_REPO})")
    parser.add_argument("--token-auth", action="store_true",
                        help="Privately prompt for a GitHub PAT instead of using gh's saved login")
    parser.add_argument("--github-only", action="store_true",
                        help="Configure GitHub only, using an existing cluster UUID and TLS mode")
    parser.add_argument("--saved-login", action="store_true",
                        help="Use saved gh login instead of prompting for a GitHub token")
    args = parser.parse_args()
    if args.saved_login and args.token_auth:
        parser.error("--saved-login and --token-auth cannot be combined")
    if not sys.stdin.isatty():
        raise ui.SetupError("Run this wizard interactively in a terminal, without redirected input")
    if args.plain:
        ui.logged(lambda: execute(args))
        return 0
    steps = ["tokens", "github", "review", "save"] if args.github_only else None
    return ui.run(lambda: ui.logged(lambda: execute(args)), steps=steps)


def execute(args):
    ui.stage("tokens")
    args.tokens = load_tokens(args.tokens_file, ask, hidden, ui.say)
    ui.register_secrets(args.tokens.values())
    ui.stage("github")
    if not args.github_only:
        from setup_cloud import run
        run(args)
        return
    executable = shutil.which("gh")
    if not executable:
        raise ui.SetupError("Install GitHub CLI from https://cli.github.com/ and reopen your terminal")
    repo = args.repo or ask("GitHub repository", DEFAULT_REPO)
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
        raise ui.InputError("Repository must use owner/repo format on github.com")
    github_token = None if args.saved_login else args.tokens.get("github")
    if not github_token and args.token_auth:
        github_token = hidden("GitHub access token (hidden): ")
    if args.token_auth and not github_token:
        raise ui.InputError("A GitHub token is required with --token-auth")
    client = GitHub(executable, github_token)
    identity = authenticate(client)
    repository = json.loads(client.run("api", f"repos/{repo}"))
    if not repository.get("permissions", {}).get("admin"):
        raise ui.SetupError("An account with repository administrator access is required")
    repo = repository["full_name"]
    ui.say(f"Signed in as {identity}. Configuring {repo} / {ENVIRONMENT}.")
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
    mode = ask("TLS mode (cloudflare/digitalocean)", defaults.get("TLS_MODE", "cloudflare"))
    values = configuration({
        "TLS_MODE": mode,
        "DOKS_CLUSTER_ID": ask("DOKS cluster UUID", defaults.get("DOKS_CLUSTER_ID", "")),
        "REPOSILITE_HOSTNAME": ask("Maven hostname", defaults.get("REPOSILITE_HOSTNAME", "")),
        "DO_CERTIFICATE_NAME": (ask("DO certificate name", defaults.get("DO_CERTIFICATE_NAME", ""))
                                if mode == "digitalocean" else ""),
    })
    ui.stage("review")
    suffix = " (Enter keeps existing)" if secret_exists else " (required)"
    token = args.tokens.get("digitalocean") or hidden(f"DigitalOcean API token{suffix}: ")
    if not token and not secret_exists:
        raise ui.InputError("A DigitalOcean token is required for a new setup")
    if any(char.isspace() or ord(char) < 32 for char in token):
        raise ui.InputError("The token must be a single value without whitespace")
    if not confirm(repo, values, replacing=bool(token)):
        ui.cancelled("Cancelled. No settings were changed.")
        return
    ui.stage("save")
    save(client, repo, values, token, existing)
    ui.say("\nSetup complete. Variables verified and secret metadata found.")
    ui.say("Secret contents cannot be read back; DigitalOcean authentication was not tested.")
    ui.say(f"Environment: https://github.com/{repo}/settings/environments")
    ui.say("Once the deployment workflow is merged: Actions > Deploy Reposilite > Run workflow > main")
    ui.say("No cluster was created and no deployment was triggered.")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        detail = str(exc) if isinstance(exc, ui.SafeError) else type(exc).__name__
        ui.say(f"Setup failed: {detail}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        ui.say("\nInterrupted. If saving had started, some settings may have changed; rerun to verify.",
              file=sys.stderr)
        sys.exit(130)
