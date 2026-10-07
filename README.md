# ApexFission Maven repository on DigitalOcean

Deploy [Reposilite](https://reposilite.com/) on DigitalOcean Kubernetes (DOKS)
to serve your Android/JVM Maven artifacts over HTTPS.

**This is an independent Maven repository.** Consumers add your URL to
Gradle/Maven; artifacts do not automatically appear in `mavenCentral()`.
This project contains deployment files, not an already running service.

| Component | Default |
| --- | --- |
| Application | Official `dzikoysk/reposilite:3.6.3` image |
| Deployment | Kustomize via kubectl; one replica, Recreate upgrades |
| Persistent data | 20 GiB DigitalOcean block volume, Retain reclaim policy |
| First setup | ClusterIP and localhost port-forward |
| Public endpoint | DigitalOcean TCP load balancer and Traefik HTTPS gateway |
| DNS and certificates | Cloudflare DNS-only record; cert-manager automatic renewal |
| Credentials | Temporary bootstrap Secret, then persistent Reposilite tokens |
| CI | Rendering, safety tests and Kubernetes schema checks |

The bot's `reposilite/repo:latest` example is replaced with the official image
and a fixed release. `/app/data` persists artifacts, configuration and database.
Retain reduces accidental volume deletion; it does **not** provide backups.

## Start here

1. **[Setup wizard](docs/setup-wizard.md)**: create/reuse DigitalOcean infrastructure
   and configure Cloudflare and GitHub from Windows or Linux.
2. **[Setup](docs/setup.md)**: private deployment and administrator bootstrap.
3. **[Publishing and consuming](docs/publishing.md)**: scoped tokens and Gradle.
4. **[Operations](docs/operations.md)**: backups, restore, upgrades and diagnostics.
5. **[GitHub deployment](docs/github-deployment.md)**: environment settings and the
   manual **Deploy Reposilite** workflow.

Optionally run `uv run python scripts/save_tokens.py` first to save encrypted local
tokens; setup will ask for the passphrase. See the [wizard guide](docs/setup-wizard.md).

The setup wizard uses uv to manage Python 3.13+ and SDKs, plus GitHub CLI and Pulumi CLI. It needs
GitHub, DigitalOcean and Cloudflare access tokens and an active Cloudflare zone. Start with [Pulumi setup](docs/pulumi.md) for
installation, state storage and importing existing infrastructure. Run
`uv run python scripts/setup_environment.py`; Pulumi login is included. The remaining private administrator setup uses `doctl`, `kubectl`, and
Bash (WSL on Windows works).
Validation CI needs no DigitalOcean credentials. Manual deployment uses a token
stored in the GitHub `production` environment.

One worker is the economical starting point if maintenance downtime is
acceptable. Workers, load balancer, storage and backups are separately billable;
check [current pricing](https://docs.digitalocean.com/products/kubernetes/details/pricing/).
The gateway and certificate controllers also consume worker capacity. A second worker helps rescheduling but does not make this single-instance
application highly available. Do not scale Reposilite beyond one replica.

| Path | Purpose |
| --- | --- |
| `k8s/base/` | Private application and persistent storage |
| `k8s/overlays/cloudflare/` | Private Service and HTTPS Ingress; removes bootstrap reference |
| `k8s/overlays/production/` | Legacy DigitalOcean certificate TLS Service |
| `.local/` | Ignored certificate/domain overrides and rendered manifests |
| `k8s/maintenance/pod.yaml` | Offline backup/restore helper, excluded from deployment |
| `tests/` | Storage, security and exposure checks |
| `.github/workflows/validate.yml` | Credential-free validation |
| `.github/workflows/deploy.yml` | Manual deployment to the existing DOKS cluster |
| `infrastructure/` | Pulumi cluster, project assignment, gateway/TLS and Cloudflare DNS |
| `scripts/setup_environment.py` | Textual forms with defaults, live output and plain-terminal fallback |
| `scripts/save_tokens.py` | Save local tokens encrypted with a GPG passphrase |
| `docs/design.md` | Architecture and tradeoffs |

## References

- [Reposilite Docker and bootstrap](https://reposilite.com/guide/docker)
- [Tokens](https://reposilite.com/guide/tokens) and [route permissions](https://reposilite.com/guide/routes)
- [Create DOKS](https://docs.digitalocean.com/products/kubernetes/how-to/create-clusters/)
- [Connect to DOKS](https://docs.digitalocean.com/products/kubernetes/how-to/connect-to-cluster/)
- [Volumes](https://docs.digitalocean.com/products/kubernetes/how-to/add-volumes/)
- [Load balancer configuration](https://docs.digitalocean.com/products/kubernetes/how-to/configure-load-balancers/)
