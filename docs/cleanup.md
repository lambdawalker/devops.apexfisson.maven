# Reset a failed setup

Use this only when you intend to discard this deployment, including its data.
The cleanup script works on Windows and Linux and does not require a working
Kubernetes API, Helm release or Pulumi provider configuration.

Stop all setup/deploy runs first. Use the same Pulumi login/backend as setup.
From the repository root, inspect the inventory:

```powershell
uv run python scripts/cleanup_environment.py
```

The defaults are `https://api.pulumi.com` and
`isdavid/apexfission-maven/production`. Override with `--backend` and `--stack`
when appropriate. This recovery utility supports Pulumi Cloud HTTPS backends.
It never creates a missing stack.

To perform the cleanup:

```powershell
uv run python scripts/cleanup_environment.py --execute
```

Review the IDs and type `DELETE isdavid/apexfission-maven/production` when asked.
There is no unattended confirmation bypass. Running without `--execute` only
reads cloud/state data and writes a local debug log.

Credentials come from `DIGITALOCEAN_TOKEN` / `CLOUDFLARE_API_TOKEN`, the existing
encrypted token file (with a hidden passphrase prompt), or hidden token prompts.
Cloudflare credentials are only required when a DNS record was recorded in state.
DigitalOcean credentials need read/delete access for Kubernetes clusters and their
associated load balancers, volumes and volume snapshots. Cloudflare needs read/delete
access to the recorded DNS record. Pulumi needs stack read/delete permissions.

## What gets removed

- The cluster identified in this stack, including its Kubernetes objects,
  workloads and worker nodes.
- Load balancers, volumes and volume snapshots reported by DigitalOcean as
  associated with that cluster, including persistent Maven data if any exists.
- The Cloudflare DNS record identified in this stack, by exact zone/record ID.
- The Pulumi stack after the recorded cloud resources are verified absent.
  Its local wizard config is moved into the recovery directory, so setup can
  start fresh.

Shared DigitalOcean projects, VPCs, domains and certificates are preserved.
Cloudflare zones/nameservers and unrelated records are preserved. Preserved
resources in the removed stack are detached from its state. GitHub environment
settings, encrypted local tokens, logs and recovery backups are retained; rerun
setup to update deployment settings with the new cluster ID before deploying.
The script displays preserved state resources before confirmation.

This is a deliberate recovery exception to normal Pulumi-only mutations and
volume-preserving teardown. It uses exact cloud IDs to recover when provider
configuration or Kubernetes access is broken; it does not disable setup's
protection/preview checks. Do not use it on a cluster shared with other workloads.

## Interrupted or repeated cleanup

Rerun the same command. Only HTTP 404 means already absent; authentication,
permission, rate-limit, network and other API failures remain errors. A failure
returns a nonzero exit status and retains Pulumi state. Some earlier deletions
may already have completed.

Before any deletion, the script saves an ID inventory and a Pulumi state backup
under `.local/cleanup/`. Keep these files until cleanup succeeds: they let it find
associated resources even after the cluster has disappeared. Backups may contain
private configuration and encrypted credentials; do not publish them. Debug logs
are under `.local/logs/` and are retained.

The script refuses unknown managed cloud resource types or pending Pulumi
operations rather than forgetting ownership. Resolve pending operations in
Pulumi first. Do not run cleanup concurrently with setup, deployments, or another
cleanup process.

If the stack never recorded the cluster, supply its exact ID after checking the
DigitalOcean control panel:

```powershell
uv run python scripts/cleanup_environment.py --cluster-id YOUR-CLUSTER-UUID --execute
```

If the cluster was already deleted outside this script and no cleanup inventory
exists, it cannot identify previously associated orphaned load balancers or
volumes. Inspect those in DigitalOcean separately; it never guesses ownership by
name or sweeps an entire account/project. Similarly, a cloud creation that was
never recorded in Pulumi must be reviewed manually.

After success, rerun `uv run python scripts/setup_environment.py` to start over.
No live teardown is performed by validation tests.
