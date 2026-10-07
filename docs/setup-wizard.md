# Set up DigitalOcean, Cloudflare and GitHub

Keep your domain and nameservers at Cloudflare. The Python wizard provisions a
DOKS cluster, a Traefik HTTPS gateway, cert-manager with Cloudflare DNS verification,
and a DNS-only A record. It then configures GitHub's `production` environment.
Reposilite stays private until you finish administrator bootstrap and dispatch the
application deployment workflow.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/),
[GitHub CLI](https://cli.github.com/), and [Pulumi CLI](https://www.pulumi.com/docs/install/).
From the repository root on Windows or Linux:

```text
uv run python scripts/setup_environment.py
```

uv supplies Python 3.13+ and the pinned SDKs. No PowerShell scripts or manual
virtual-environment activation are needed. Pulumi login runs interactively;
follow its browser/token prompts. `--saved-login` reuses your GitHub CLI login;
`--repo OWNER/REPO` selects another repository.

## Prompts and defaults

Tokens have no default and use hidden input. Other prompts offer discovered or
saved defaults. Saved stack configuration takes precedence on reruns.

| Setting | First-run default |
| --- | --- |
| GitHub repository | `lambdawalker/devops.apexfisson.maven` |
| Pulumi backend | `https://api.pulumi.com`, or `PULUMI_BACKEND_URL` |
| Pulumi organization | Configured default if accessible, otherwise a discovered organization |
| Pulumi stack | `<organization>/apexfission-maven/production`; DIY backend: `production` |
| DigitalOcean project | The account's default project |
| Cluster | `apexfission-maven` |
| Region / worker / count | `nyc1` / `s-2vcpu-4gb` / `1` |
| Kubernetes version | Highest stable version offered by the DO API |
| Cloudflare zone | A zone accessible to the supplied token |
| Hostname | `maven.<zone>`, or saved hostname |
| ACME email | Suggested address on the chosen domain; enter an address you monitor |
| Confirmation | `no` |

Your Pulumi username and organization need not match. For example, login
`lambdawalker` can own the stack through organization `isdavid`. The wizard no
longer constructs an organization from the username.

Select an existing DigitalOcean project by the displayed name or ID. The project
selection is saved with the stack; changing your account's default later does not
silently move an established setup. The cluster is assigned through Pulumi.
DOKS-associated resources, especially PVC volumes created later by Kubernetes,
can still start in the account's default project. Inspect their placement in the
DO console; cluster assignment does not imply all related resources move.

## Tokens

- **GitHub:** repository administrator access. A fine-grained token needs this
  repository's Administration and Environments read/write permissions, with any
  required organization approval. Only the DigitalOcean deployment token is saved
  as a GitHub secret; GitHub's token stays in process memory.
- **DigitalOcean:** Kubernetes read/create/update/access-cluster, projects read
  and assign-resource, load-balancer read, plus required dependent scopes shown
  by the token editor (such as regions, sizes and actions read). Existing legacy
  state can also require domain/certificate read. The default flow saves this DO
  token in GitHub; replace it with a deployment-only token afterward if desired.
  Kubernetes controllers create the gateway load balancer and later PVC volume.
- **Cloudflare:** create a scoped API token with **Zone:DNS:Edit** and
  **Zone:Zone:Read**, restricted to **apexfission.com** (or your selected zone).
  Do not use the Global API Key. It supports Pulumi's DNS record and cert-manager's
  temporary DNS challenge records. The zone must already exist and be active.
- **Pulumi:** login uses Pulumi's normal local credential storage. No Pulumi token
  needs to be added to the application deployment workflow.

The Cloudflare token is passed to Pulumi via its environment and stored as a
Kubernetes Secret for renewal, encrypted as a secret in Pulumi state. It is not
written to stack configuration, exported, printed in preview or saved to GitHub.
Cluster administrators and Pulumi state decryptors are privileged: they can access
these credentials. A token rotation requires rerunning setup so cert-manager gets
the new token. See [Cloudflare DNS verification](https://cert-manager.io/docs/configuration/acme/dns01/cloudflare/).

## Review and billing

Review the desired configuration and Pulumi preview; type `yes` to apply. Deletion
and replacement operations are refused. New resources are protected. Setup creates
**billable workers and a load balancer**, even before Reposilite is deployed.
Private application deployment later adds a billable persistent volume. One worker
accepts downtime; extra gateway/certificate controllers also consume node capacity.
Automatic and surge upgrades are enabled; surge can add temporary billable workers.

The DNS A record uses the gateway's actual load-balancer address and is DNS-only
(`proxied=false`). Nameservers and unrelated records stay at Cloudflare. Conflicting
hostname records stop setup. An A record not already owned by this stack also
stops setup: use an unused hostname or plan a separate DNS migration first.
The wizard never replaces an unrelated existing record.
Do not put credentials in command arguments, checked-in files or chat.

## Continue after setup

1. Follow [setup](setup.md) steps 1 (connect only), 2 and 3: install the private
   application, create a permanent administrator, remove bootstrap and verify login.
2. Dispatch **Deploy Reposilite** on `main`. It adds the hostname route, requests
   its certificate through cert-manager and waits for readiness.
3. Verify HTTPS, HTTP redirect, dashboard Console, upload and download externally.

The gateway may answer 404 before step 2; it has no Reposilite route then. DNS is
already managed by Pulumi; do not create another A record manually.

## Reruns and failed legacy setup

Always reuse the same backend and stack. See [Pulumi setup](pulumi.md) for migration,
state protection, import and diagnostics. Existing GitHub environment protection
rules are preserved. Partial updates are retained; no rollback is attempted.

For GitHub settings only, run:

```text
uv run python scripts/setup_environment.py --github-only
```

Choose `cloudflare` TLS mode for this architecture, or `digitalocean` for the
legacy load-balancer certificate overlay. This mode uses saved `gh` login by
default; `--token-auth` requests a PAT. A blank DO token keeps the existing secret.
It does not provision or validate infrastructure.
