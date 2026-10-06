# Set up DigitalOcean and GitHub from Windows or Linux

The Python wizard creates or reuses your DigitalOcean Kubernetes cluster and TLS
certificate, then configures the GitHub `production` environment. DigitalOcean
assigns the cluster UUID; the wizard reads it from the API and saves it as
`DOKS_CLUSTER_ID`. You do not choose or copy the UUID.

Requires **Python 3.10+** and [GitHub CLI](https://cli.github.com/) on PATH.
No pip packages, PowerShell, Bash, WSL, doctl or kubectl are needed for this
wizard. Clone/download the whole repository, including all files in `scripts/`.
Install Python from [python.org](https://www.python.org/downloads/) or your Linux
package manager, install GitHub CLI, and reopen your terminal.

## Run

Windows Command Prompt:

```text
py -3 scripts/setup_environment.py
```

Linux:

```text
python3 scripts/setup_environment.py
```

The default flow privately prompts for a GitHub token and a DigitalOcean token.
Neither token has a default; blank input is rejected. You can instead reuse your
saved GitHub CLI login:

```text
gh auth login --hostname github.com --web
python scripts/setup_environment.py --saved-login
```

Use `py -3` or `python3` in that example if appropriate for your installation.
The optional `--repo OWNER/REPO` flag selects a fork. `--token-auth` is still
accepted; in the unified flow prompting for a token is already the default.

## Defaults

Press Enter to accept a configuration default. Tokens are always hidden and
never included in the preview. Existing GitHub values take priority on reruns.

| Setting | Default |
| --- | --- |
| GitHub repository | `lambdawalker/devops.apexfisson.maven` |
| GitHub environment | Fixed `production`, matching the workflow |
| Cluster name | Existing GitHub cluster's name, otherwise `apexfission-maven` |
| Region for new cluster | `nyc1`, or first supported region if unavailable |
| Worker size | `s-2vcpu-4gb` (2 vCPU / 4 GiB); validated against current API options |
| Worker count | `1` |
| Kubernetes version | Highest stable version currently offered by the DO API |
| Maven hostname | Existing GitHub value, otherwise `maven.` plus first DO domain alphabetically |
| Certificate name | Existing GitHub value, otherwise `<cluster-name>-tls` |
| DNS zone for new certificate | Longest existing matching zone, otherwise hostname without first label |
| Confirm changes | `no` |

If there are no existing domains, the hostname prompt suggests
`maven.your-domain.com`. You must replace that placeholder with a hostname you
own. Review the DNS zone default, especially for apex hostnames and domains such
as `example.co.uk`; the script does not infer domain ownership or buy a domain.
Region, worker and version prompts appear only when creating a cluster. Existing
clusters retain their current configuration. Cluster names must be unique in the
DO account; ambiguous matches stop setup.

New clusters have control-plane HA disabled, automatic and surge upgrades enabled,
and maintenance Saturday at 06:00 UTC. One worker accepts downtime; it is not HA.
Surge upgrades can temporarily add workers. The wizard prints the node count/size
and [pricing link](https://docs.digitalocean.com/products/kubernetes/details/pricing/)
before confirmation. **Workers are billable and continue running after setup.**
Subsequent application deployment adds separately billable storage and a load balancer.

## Access tokens

Use an administrator account on the existing GitHub repository. For a fine-grained
GitHub PAT, select this repository with **Administration: read/write** and
**Environments: read/write**. Honor any organization approval/SSO requirements.
The script does not create the repository. See GitHub's API permission references
for [environments](https://docs.github.com/en/rest/deployments/environments),
[environment secrets](https://docs.github.com/en/rest/actions/secrets), and
[environment variables](https://docs.github.com/en/rest/actions/variables).

Create a DigitalOcean token for the team that will own the resources. For custom
scopes, enable:

- `kubernetes:read`, `kubernetes:create`, and `kubernetes:access_cluster` (the last
  is needed by the deployment workflow).
- `certificate:read` and `certificate:create` for TLS.
- `domain:read`, `domain:create`, and `domain:update` for DNS and managed issuance.
- All required dependent scopes the DO token editor lists for these permissions,
  including `regions:read`, `sizes:read`, and `actions:read` where required.

Consult the current [DO scope reference](https://docs.digitalocean.com/reference/api/scopes/),
particularly [Kubernetes creation](https://docs.digitalocean.com/reference/api/scopes/kubernetes/create/),
[cluster credentials](https://docs.digitalocean.com/reference/api/scopes/kubernetes/access_cluster/),
and [certificates](https://docs.digitalocean.com/reference/api/scopes/certificate/create/).
The wizard tests DO access by reading resource inventories, but does not test
kubeconfig access; the deployment workflow checks that when run. You can later
replace the GitHub DO secret with a deployment-only token using the GitHub-only
mode below. The default flow stores the supplied DO token in GitHub.

GitHub tokens stay in process memory and the `gh` child-process environment; they
are not saved by this script. DO tokens go to the DO API in HTTPS headers and to
`gh secret set` through stdin, where GitHub CLI encrypts the upload. No tokens
are written to files, echoed, or passed in command-line arguments. Saved `gh`
login credentials retain the CLI's normal persistence behavior. Run in a real
terminal that supports hidden input, with no redirected stdin.

## DNS and certificates

For a new certificate, the wizard creates the DO DNS zone if missing and requests
a Let's Encrypt certificate for the hostname. Your registrar or parent DNS zone
must delegate the zone to `ns1.digitalocean.com`, `ns2.digitalocean.com`, and
`ns3.digitalocean.com`. **Copy any existing DNS records before changing nameservers**;
the wizard does not migrate records. Delegation changes are outside the script.
Prepare delegation before running, or rerun after it propagates. Adding a zone in
DO alone does not transfer DNS authority.

An existing certificate with the chosen name is reused only if it covers the
hostname and, when issued, has not expired. This also supports a previously
imported certificate when its DNS-name metadata is available. External DNS users
can import their own certificate in DO beforehand and choose its name, without
creating a new DO zone. Renewal of imported certificates remains your responsibility.

Certificate readiness is checked **before creating a cluster**. The wizard waits
up to 15 minutes for issuance, then up to 30 minutes for a running cluster,
printing progress. Resource creation requests are never blindly retried. See
[DO certificate API](https://docs.digitalocean.com/reference/api/reference/certificates/)
and [Kubernetes API](https://docs.digitalocean.com/products/kubernetes/reference/api/).

## Changes, reruns and remaining steps

After showing the plan, the wizard requires `yes` to proceed. It then:

1. Creates/reuses the DNS zone and certificate as applicable; waits for TLS issuance.
2. Creates/reuses the cluster, prints its UUID, and waits for readiness.
3. Creates GitHub `production` only if absent, preserving existing protections.
4. Saves `DOKS_CLUSTER_ID`, `REPOSILITE_HOSTNAME`, `DO_CERTIFICATE_NAME`, and the
   `DIGITALOCEAN_ACCESS_TOKEN` secret; verifies values and secret metadata.

This is not atomic. Resources are retained on failure; some GitHub settings may
already have changed. Read the reported stage/error, inspect the DO control panel
and GitHub environment, and rerun using the same resource names. If a create
request timed out, check that it completed before rerunning; do not start concurrent
wizards. Existing resources are not resized, upgraded, deleted or replaced. A
certificate in `error` needs repair in DO or a new certificate name before retrying.
Old GitHub secrets cannot be read back or automatically restored. Interrupted
runs may leave billable workers; delete unwanted resources explicitly in DO only
after checking for data you need.

**The wizard prepares infrastructure; it does not expose an uninitialized Maven
repository.** Continue with [setup.md](setup.md): connect to the created cluster
using doctl/kubectl, then complete steps 2–3 to install the private base and create
a persistent Reposilite administrator. Those existing shell examples use Bash/WSL.
Then run [Deploy Reposilite](github-deployment.md), create the hostname A record
pointing to the new load balancer IP, and verify HTTPS/login. No workflow,
application deployment, administrator token, load balancer or A record is created
by this wizard.

## GitHub-only mode

For infrastructure you prepared separately, the previous workflow is available:

```text
python scripts/setup_environment.py --github-only
```

This mode uses saved `gh` login by default; add `--token-auth` for a hidden PAT
prompt. It asks for the existing cluster UUID, hostname, and certificate name.
Defaults are taken from the existing GitHub environment (on first use these values
must be supplied). A blank DO token preserves the existing GitHub secret. It makes
no DO API calls and cannot verify DO authentication. Use the default unified mode
for first-time provisioning with automatically generated UUIDs and suggested defaults.

Automated tests fake external API/CLI calls and run on Windows and Linux. Live
resource creation only happens when you run the wizard and confirm its plan.
