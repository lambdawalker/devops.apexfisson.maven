# Cross-platform environment setup wizard

Use the same Python script on Windows or Linux. It needs **Python 3.10+** and
[GitHub CLI](https://cli.github.com/) on PATH, with no pip packages, PowerShell
scripts, Bash scripts, WSL, DigitalOcean CLI or Kubernetes tools required.
Download/clone the repository so `scripts/setup_environment.py` and its sibling
`scripts/deploy.py` are both present.

Install Python from [python.org](https://www.python.org/downloads/) on Windows,
or your distribution's package manager on Linux. Install GitHub CLI using its
[official instructions](https://github.com/cli/cli#installation). Reopen the
terminal after installation so PATH changes take effect.

## Authenticate once

In Windows Command Prompt, Windows Terminal, or a Linux terminal:

```text
gh auth login --hostname github.com --web
```

Sign in as an administrator of the repository. The wizard reuses this login;
you do not need to paste a GitHub token into it. GitHub CLI manages storage of
that login using its normal credential handling.

Alternatively use `--token-auth` when starting the wizard. It privately prompts
for a fine-grained personal access token restricted to the target repository,
with **Administration: read/write** and **Environments: read/write** permissions.
The script passes this token through the child process environment, not command
arguments, and does not save it to disk or change your saved GitHub CLI login.
The authenticated account still needs repository administrator access.

## Run from the repository root

Windows (Command Prompt works; PowerShell is not required):

```text
py -3 scripts/setup_environment.py
```

Linux:

```text
python3 scripts/setup_environment.py
```

If your Python install exposes only `python`, use that instead. For a fork,
append `--repo YOUR_ACCOUNT/YOUR_REPOSITORY`.

The wizard asks for:

1. GitHub repository, defaulting to `lambdawalker/devops.apexfisson.maven`.
2. DOKS cluster UUID.
3. Maven hostname, without `https://`, port or path.
4. Existing DigitalOcean certificate name.
5. DigitalOcean API token, through a hidden prompt.

The environment is fixed to **production**, matching the deployment workflow.
It shows the signed-in account, target repository and nonsecret settings, then
requires `yes` before saving. Tokens are never shown in the preview.

On reruns, existing variables become the prompt defaults. If a DigitalOcean
secret already exists, Enter at the token prompt preserves it; providing a new
token replaces it. For a new setup, the token is required.

## What it changes

- Creates `production` only when absent. Existing environment protection rules,
  reviewers and deployment branch policies are left alone.
- Sets `DOKS_CLUSTER_ID`, `REPOSILITE_HOSTNAME`, and `DO_CERTIFICATE_NAME` as
  environment variables; unrelated variables and secrets are untouched.
- Saves `DIGITALOCEAN_ACCESS_TOKEN` as an environment secret. It is sent to
  GitHub CLI on stdin; GitHub CLI encrypts it before upload. The script does not
  write plaintext tokens to files or include them in process command arguments.
- Verifies the variable values and the secret's existence by metadata. GitHub
  does not allow reading back the secret, so this does not test its value or DO
  authentication.

New environments have GitHub's default protection settings. Add reviewers or
deployment branch restrictions in Settings if desired. The deployment workflow
itself is restricted to `main`.

The script does not create a cluster, configure DNS/certificates, bootstrap
Reposilite, or trigger a deployment. Finish the [one-time setup](setup.md), then
follow [GitHub deployment](github-deployment.md).

## Errors and reruns

Use a real terminal: redirected input and terminals without hidden-input support
are rejected rather than falling back to displaying tokens. Windows Command
Prompt inside Windows Terminal is supported; no Unix shell is needed.

If GitHub rejects a request, check `gh auth status --hostname github.com`, the
account's repository access, token permissions, and any organization SSO policy.
For fine-grained token setup, the wizard requires access to both environment
configuration and its secrets/variables.

Saving is not atomic. If a later request fails, earlier settings may already be
saved; the script reports their names and does not pretend to roll them back.
Secret updates cannot be automatically rolled back because old values cannot be
read. Resolve the error and rerun. Do not run multiple setup wizards against the
same environment simultaneously.

Tests run without real credentials on both Windows and Linux. Live GitHub
authentication and environment writes happen only when you run and confirm the
wizard yourself.
