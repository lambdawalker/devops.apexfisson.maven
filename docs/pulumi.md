# Pulumi infrastructure

Run `uv run python scripts/setup_environment.py` from the repository root. Install
uv, GitHub CLI and Pulumi CLI first; the wizard guides Pulumi login. See the
[setup wizard](setup-wizard.md) for token scopes, prompts and costs.

## Ownership

| Owner | Resources |
| --- | --- |
| Pulumi | DOKS cluster/project membership, Traefik gateway/LoadBalancer, cert-manager, Cloudflare credential Secret, ClusterIssuer, one Cloudflare A record |
| Application manifests and manual GitHub deployment | Reposilite namespace, Deployment, retained storage/PVC, private Service, HTTPS Ingress |
| cert-manager | Certificate generated from Ingress, TLS Secret and automatic renewals |
| Cloudflare account owner | Existing zone, nameservers and all unrelated DNS records |

Traefik serves the `reposilite-edge` ingress class. The gateway load balancer passes
TCP traffic through; TLS terminates at Traefik. cert-manager uses Cloudflare DNS-01
verification with Let's Encrypt and reloads certificates through Kubernetes Secrets.
Only the manual deployment creates the Reposilite route. Pulumi does not install or
bootstrap the application. Existing DigitalOcean TLS deployments retain their legacy
overlay and certificate support.

## Backend, organization and state

The backend defaults to Pulumi Cloud (`https://api.pulumi.com`) or
`PULUMI_BACKEND_URL`. The organization is discovered from `pulumi whoami --json`,
not inferred from your username. A configured accessible default is preferred.
For example, `lambdawalker` can select `isdavid/apexfission-maven/production`.
DIY backends use an unqualified stack such as `production`.

Use **the same backend and stack** on every run. Never import resources into two
stacks. Selecting a stack may create empty Pulumi state before infrastructure review.
A DIY backend needs its own storage credentials and configured secrets provider;
use a strong `PULUMI_CONFIG_PASSPHRASE` or file variant when appropriate. Back up
state and preserve the encryption key. A local file backend is not shared CI state.

Nonsecret desired configuration is stored under ignored `.local/pulumi/`, named by
a hash of backend/stack identity. On a new checkout, configuration can be recovered
from the backend after an update. Existing local configuration remains authoritative,
including unapplied edits. Tokens are environment inputs, not config fields. The
kubeconfig and Cloudflare credential Secret are encrypted as Pulumi secrets; neither
is exported. Backend access with decryption rights remains privileged.

## Preview, apply and recovery

The wizard performs read-only discovery, shows configuration and previews changes
before an explicit apply confirmation. It refuses deletions, replacements and
unrecognized operations. Owned resources are protected. Cancelled previews retain
local configuration; failed applies may retain partially created resources.

If an update fails, open the selected stack in Pulumi Cloud and inspect the latest
update's diagnostics. The script identifies the failed CLI operation and offers
safe diagnostic guidance; raw provider output is withheld because it can contain
credentials. Do not delete state or change stack names to get around an error.

On reruns, declared cluster settings and import policy are retained. A managed
resource missing from discovery is an error, not permission to recreate it.
Existing clusters are imported with `ignoreChanges: ["*"]` to preserve settings
not represented by the wizard (additional pools, autoscaling, taints and more).
Before disabling preservation, model all those settings and review the preview.
For new clusters, auto-upgraded version drift is ignored to avoid downgrades.

## Migrate the failed DigitalOcean DNS setup

For the reported partial stack `isdavid/apexfission-maven/production`, continue
with that exact stack and answer yes to “Is your domain's DNS managed by
Cloudflare?”. Review the migration details and preview before applying.

- Retain the previously created DigitalOcean zone in the same Pulumi state. It is
  non-authoritative while nameservers remain at Cloudflare, so no DNS cutover occurs.
- Retain any genuinely existing legacy certificate; do not request the failed
  DigitalOcean managed certificate again.
- Preserve any existing cluster and its identity/configuration.
- Add gateway/certificate infrastructure and a Cloudflare DNS-only record.
- Save `TLS_MODE=cloudflare` in GitHub. A leftover `DO_CERTIFICATE_NAME` variable is
  ignored in this mode.

Retained legacy resources are intentionally not deleted by this migration. Remove
them only in a separately reviewed cleanup after checking dependencies. A fully
running legacy deployment needs an explicit cutover plan: check its old load
balancer, DNS address and bootstrap status before switching public application mode.

## DigitalOcean projects

Select an existing project; the first-run default is the account's default project.
The saved selection wins on subsequent runs. Pulumi assigns the cluster without
importing or taking ownership of the entire project. Other project resources remain
unmanaged. DigitalOcean notes that associated load balancers and volumes can start
in the default project even when their cluster belongs elsewhere; check placement,
especially for volumes created later by Kubernetes.

## Verification

CI uses SDK mocks and simulated CLI/API results, creates no cloud resources and
requires no tokens. The setup wizard runs on Windows and Linux; uv locks SDKs.
The GitHub application action does not run Pulumi or need Cloudflare credentials.

After a live setup, verify the selected DO project, DNS-only A record, gateway IP,
`ClusterIssuer/cloudflare-letsencrypt` readiness, absence of a Reposilite route
before bootstrap, and application Certificate readiness after the manual deploy.
Then verify trusted HTTPS, redirect, WebSocket console, authenticated upload/download,
persistence after restart and the offline backup/restore procedure.

Useful diagnostics (after obtaining kubeconfig):

```text
kubectl -n reposilite-edge get pods,service
kubectl -n cert-manager get pods
kubectl get clusterissuer cloudflare-letsencrypt
kubectl -n reposilite get ingress,certificate
kubectl -n reposilite describe certificate reposilite-tls
kubectl -n reposilite get orders,challenges
```

Investigate failed DNS challenges using cert-manager events. Keep the Cloudflare
token valid for future renewals. Never print the credential Secret to collect logs.
