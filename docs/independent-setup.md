# Set up one provider at a time

Run these four scripts separately from the repository root. Each opens the same
Textual forms and live stdout/stderr panel, supports `--plain`, and **always keeps
its own redacted log** under `.local/logs/<stage>/`. Successful runs do not ask to
delete these logs. Errors retain completed cloud work; fix the reported issue and
rerun that stage.

These scripts use DigitalOcean APIs, Helm and kubectl directly, rather than the
combined Pulumi preview/apply flow. Nothing triggers the next script automatically.
Nonsecret outputs are saved in `.local/independent-setup.json` as editable defaults.
Cloud credentials are never saved there. The optional encrypted token file still
works, but each stage only requires the credentials listed below. Explicit token
environment variables take precedence over that file.

## 1. DigitalOcean and the IP test

Install `uv`, `doctl`, `kubectl` (matching your cluster's Kubernetes minor version),
and Helm. Run:

```powershell
uv run python scripts/setup_digitalocean.py
```

Requires only a DigitalOcean API token. The form offers current region, version,
size and project options. It creates a one-worker cluster or reuses an existing
cluster by exact name without changing its configuration. Existing saved cluster
IDs must match. It installs Reposilite with one replica, Recreate updates and the
retained 20 GiB volume, plus a pinned Traefik gateway and public load balancer.
These are billable resources. Associated volumes/load balancers can be in the
account's default project even when the cluster is assigned elsewhere.

The script first guides private administrator bootstrap:

1. Choose and save a temporary password of at least 32 ASCII letters/numbers.
2. Open the displayed localhost URL while the script holds a private tunnel open.
3. Sign in as `bootstrap`, run `token-generate admin m` in the dashboard Console,
   save the admin secret, and verify the permanent admin login.
4. Proceed. The script removes the bootstrap Secret/reference, restarts the app,
   and opens another local tunnel for verification that admin works and bootstrap
   no longer does. Confirm those checks before continuing.

Only then does it publish the IP route and verify `http://<load-balancer-ip>`.
Use that HTTP endpoint for anonymous browsing/testing, not credentials or uploads.
This stage neither requires nor configures DNS, Cloudflare, certificates or GitHub.
A deployment already configured for a hostname is not downgraded to IP mode.

## 2. Cloudflare DNS only

```powershell
uv run python scripts/setup_cloudflare.py
```

Requires only a Cloudflare token with Zone Read and DNS Edit for your zone.
Choose the zone, hostname and IPv4 from step 1. The default hostname is
`maven.apexfission.com`; edit it if another spelling/domain is intended.

Review the exact record before applying. The script creates or updates one
**DNS-only A record** and verifies its content. It refuses conflicting record
types or multiple records, preserves the zone/nameservers and other DNS records,
and calls no DigitalOcean, Kubernetes, Pulumi or GitHub API.

## 3. DigitalOcean hostname and HTTPS

```powershell
uv run python scripts/setup_digitalocean_hostname.py
```

Requires only the DigitalOcean token plus doctl/kubectl/Helm. Enter the cluster
UUID, gateway IP, hostname and a monitored email address. The hostname must
already resolve to that IPv4 without an IPv6/Cloudflare proxy mismatch.

This script configures the **gateway in the DigitalOcean cluster** to serve the
hostname. It installs cert-manager, creates a Let's Encrypt HTTP-01 issuer,
sets the DigitalOcean load-balancer hostname annotation for in-cluster access,
requests a publicly trusted certificate, waits for readiness, then replaces the
IP-only route with the hostname route and HTTP-to-HTTPS redirect. HTTP-01 renewal
continues through port 80. Keep ports 80/443 accessible and the A record pointing
to the gateway. No Cloudflare credentials are installed in Kubernetes and no DNS
changes are made by this script. It reuses the gateway/load balancer rather than
creating a separate DigitalOcean DNS zone or managed certificate.

## 4. GitHub configuration only

```powershell
uv run python scripts/setup_github.py
```

Requires `gh` and a GitHub administrator login/token. The DigitalOcean token is
only uploaded as the deployment secret; this stage does not call DigitalOcean.
Review the repository, cluster ID, hostname and `TLS_MODE=http01`. Saved outputs
from the preceding stages take precedence over stale GitHub defaults. Existing
environment protection rules are preserved and the saved variables/secret
metadata are verified. No deployment workflow is dispatched.

The deployment workflow now supports the `http01` overlay and issuer. Future
manual deployments preserve the hostname/TLS configuration and bootstrap checks.

## Existing partial setup and recovery

Reuse your existing cluster name in step 1; do not clean it up simply to switch
flows. These scripts do not destroy, rename, recreate or modify its worker pool.
They can install/reconcile the expected Helm releases in that cluster. The app
bootstrap refuses public routes/Services and maintenance mode before adding
bootstrap credentials, and never reinitializes or deletes an existing PVC.

**Stop using the old combined wizard/Pulumi apply for this deployment after
switching.** Existing Pulumi state remains intact, but cannot track direct Helm,
DNS or application changes from these scripts. Do not run both managers against
the deployment. The old cleanup script does not know about newly created DNS
records from this flow; review the saved record/cluster IDs before any teardown.

If an API call times out during creation, inspect DigitalOcean before retrying.
Cluster creation is recovered by exact name; completed IDs are saved immediately.
Project assignment is retried until verified. Kubernetes/Helm stages reconcile
existing objects, and DNS writes require another review. An interrupted bootstrap
keeps its existing Secret/password; use that original password when resuming.
Keep logs and the nonsecret settings file for troubleshooting. Do not run these
scripts concurrently or share a cluster with unrelated workloads.

The scripts have offline tests for provider separation, DNS conflicts, safe
bootstrap ordering, configuration rendering and retained logs. Live cloud setup
and Windows execution still require operator verification.
