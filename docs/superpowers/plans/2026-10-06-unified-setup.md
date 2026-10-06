# Unified setup implementation plan

Goal: extend the Python wizard to provision DigitalOcean infrastructure and configure
GitHub production, automatically passing the generated cluster UUID to GitHub.

Architecture: retain the GitHub CLI encryption adapter; add a standard-library
DigitalOcean API adapter and separate interactive orchestration. Python 3.10+,
Windows and Linux, no PowerShell or pip runtime dependencies.

Scope: named cluster creation/reuse, DO DNS zone creation when necessary, managed
certificate creation/reuse, readiness waits, and existing GitHub environment writes.
Every configuration prompt has a default; credentials have none. Discover supported
Kubernetes versions from the API. Prefer existing environment and resource values.
Never alter an existing cluster or certificate. Preview and confirm paid changes.
No application deployment, administrator bootstrap, domain purchase, registrar
changes, deletion, or automatic retries of uncertain create requests.

- [x] Add API tests for pagination, credential redaction, no redirects/retries,
      existing/ambiguous resources, supported version selection, certificate
      coverage/expiry, failure and timeout, and generated UUID handoff.
- [x] Implement scripts/digitalocean.py for HTTPS JSON API, discovery and bounded
      readiness waits; scripts/setup_cloud.py for plan/prompts/provision/save.
- [x] Make unified setup the default in setup_environment.py; preserve the old
      environment-only mode with --github-only. Keep existing protection rules.
- [x] Add orchestration tests for defaults, cancellation, reruns, token requirements
      and partial failures. Run those tests on both Windows and Linux in CI.
- [x] Update operator docs with defaults, permissions, DNS delegation, charges,
      recovery and remaining private administrator/bootstrap steps.
- [x] Run full tests and actionlint; review diff; commit and open a pull request.

Review focus: ambiguous cluster names must not silently select a target; failed
certificate issuance must prevent cluster creation; timed-out POST must not be
retried; old GitHub environment protections must remain; GitHub failures must not
cause cleanup/deletion of billable infrastructure or stored data.
