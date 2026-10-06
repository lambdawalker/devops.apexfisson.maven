# Deploy from GitHub Actions

The **Deploy Reposilite** workflow manually deploys the production configuration
to your existing DOKS cluster. It runs validation first, uses the `production`
environment, and serializes deployment runs without cancelling an active run.
Only the `main` branch can deploy. The workflow must be merged to `main` before
GitHub shows its **Run workflow** button.

## Configure the environment

For a guided setup on Windows or Linux, use the
[Python setup wizard](setup-wizard.md). It creates the environment and fills
the following settings using your GitHub CLI login and a hidden token prompt.
Alternatively, configure them manually:

Open **Settings → Environments → production** in this repository. Set these
exact names (environment variables are entered under **Environment variables**,
not the Secrets section):

| Name | Type | Value |
| --- | --- | --- |
| `DIGITALOCEAN_ACCESS_TOKEN` | Environment secret | DigitalOcean API token |
| `DOKS_CLUSTER_ID` | Environment variable | Cluster UUID from `doctl kubernetes cluster list` |
| `REPOSILITE_HOSTNAME` | Environment variable | DNS hostname, e.g. `maven.your-domain.com`; no `https://`, port or path |
| `DO_CERTIFICATE_NAME` | Environment variable | Existing DO certificate name, e.g. `apexfission-maven-tls` |

The DO token must permit reading the target cluster and obtaining its kubeconfig.
The resulting Kubernetes credentials must permit reading the existing Deployment,
PVC and bootstrap/maintenance resource metadata and applying this project's
namespace, storage class, PVC, Deployment and Service. Do not use a Reposilite
publisher token here. Store the DO token only in GitHub, never in a file or chat.

You can restrict the environment's deployment branches to `main` and add required
reviewers if desired. If your cluster API has an IP allowlist, the GitHub-hosted
runner must be allowed to reach it; otherwise use an appropriately networked
self-hosted runner instead of opening broad access solely for this workflow.

## One-time prerequisites

Complete [setup](setup.md) steps 1–4: create the cluster, deploy the private base,
create a permanent administrator, remove the bootstrap Secret and verify login
after restarting, and obtain the certificate. This workflow does not create a
cluster or initialize administrator credentials.

You may use the workflow instead of setup step 5 to create the first public
load balancer. Its IP appears in the job log. Create/update your hostname's DNS
A record after that first run, then verify HTTPS and the dashboard Console.
If already publicly deployed, use the same certificate name and hostname.
The workflow generates its own `.local/ci` overlay; an uncommitted `.local/`
file from your laptop is not available to GitHub Actions.

## Run a deployment

1. Merge the configuration/image change into `main`.
2. Take a backup before image upgrades; migrations may prevent an image-only rollback.
3. Open **Actions → Deploy Reposilite → Run workflow** and select **main**.
4. Approve the environment deployment if you configured required reviewers.
5. Inspect the deployment summary and Service address in the job log.

The action validates all manifests/tests, installs kubectl matching the target
cluster's Kubernetes version, obtains credentials valid for 30 minutes, checks
deployment prerequisites, performs server-side dry-run validation, applies the
production configuration, and waits for the pod rollout and load balancer address.
It removes the temporary kubeconfig on exit. There is no automatic deployment on
push, pull request, or merge.

It refuses deployment if the existing PVC is not Bound, the Deployment is missing
or scaled away from one replica, the bootstrap Secret still exists, or the
maintenance pod exists. Resolve that condition before rerunning. Authentication,
RBAC and API connectivity errors also stop the run before application changes.
The workflow never prunes resources, deletes volumes, or automatically rolls back
a database migration.

Successful completion means Kubernetes readiness and load balancer assignment,
not verified public DNS/TLS or a completed artifact publication. Verify those
externally using [publishing checks](publishing.md). No hostname annotation is
automatically applied: first deployment may precede DNS. If in-cluster clients
need DO's hostname workaround, add the annotation as described in setup after
DNS is working and keep that override in version control for repeatable deploys.

## Maintenance and failure handling

The concurrency group serializes runs of this workflow only. GitHub keeps one
active and one pending run; a newer pending run may replace an older pending run.
Avoid manual kubectl changes during deployment. Before starting a backup, ensure
no deployment is active, then scale the application to zero; subsequent runs will
refuse to start while it is scaled down or the maintenance pod remains.

On failure, inspect the failed step and the [operations runbook](operations.md).
Some resources may already have changed if the actual apply or rollout failed.
Fix the issue and rerun; no automatic data-destroying cleanup is attempted.

No real deployment is executed by the validation workflow or PR checks. Running
**Deploy Reposilite** can provision/update a billable load balancer and storage
according to the manifests, and Recreate image updates cause brief downtime.
