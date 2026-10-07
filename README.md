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
| Public endpoint | Regional load balancer, HTTPS and HTTP redirect |
| Credentials | Temporary bootstrap Secret, then persistent Reposilite tokens |
| CI | Rendering, safety tests and Kubernetes schema checks |

The bot's `reposilite/repo:latest` example is replaced with the official image
and a fixed release. `/app/data` persists artifacts, configuration and database.
Retain reduces accidental volume deletion; it does **not** provide backups.

## Start here

1. **[Setup wizard](docs/setup-wizard.md)**: create/reuse DigitalOcean infrastructure
   and configure GitHub from Windows or Linux.
2. **[Setup](docs/setup.md)**: private deployment and administrator bootstrap.
3. **[Publishing and consuming](docs/publishing.md)**: scoped tokens and Gradle.
4. **[Operations](docs/operations.md)**: backups, restore, upgrades and diagnostics.
5. **[GitHub deployment](docs/github-deployment.md)**: environment settings and the
   manual **Deploy Reposilite** workflow.

The setup wizard uses uv to manage Python 3.13+ and SDKs, plus GitHub CLI and Pulumi CLI. It needs
access tokens and a domain you own. Start with [Pulumi setup](docs/pulumi.md) for
installation, state storage and importing existing infrastructure. Run
`uv run python scripts/setup_environment.py`; Pulumi login is included. The remaining private administrator setup uses `doctl`, `kubectl`, and
Bash (WSL on Windows works).
Validation CI needs no DigitalOcean credentials. Manual deployment uses a token
stored in the GitHub `production` environment.

One worker is the economical starting point if maintenance downtime is
acceptable. Workers, load balancer, storage and backups are separately billable;
check [current pricing](https://docs.digitalocean.com/products/kubernetes/details/pricing/).
A second worker helps rescheduling but does not make this single-instance
application highly available. Do not scale Reposilite beyond one replica.

| Path | Purpose |
| --- | --- |
| `k8s/base/` | Private application and persistent storage |
| `k8s/overlays/production/` | Public TLS Service; removes bootstrap secret reference |
| `.local/` | Ignored certificate/domain overrides and rendered manifests |
| `k8s/maintenance/pod.yaml` | Offline backup/restore helper, excluded from deployment |
| `tests/` | Storage, security and exposure checks |
| `.github/workflows/validate.yml` | Credential-free validation |
| `.github/workflows/deploy.yml` | Manual deployment to the existing DOKS cluster |
| `infrastructure/` | Pulumi Python project for DigitalOcean infrastructure |
| `scripts/setup_environment.py` | Cross-platform DigitalOcean and GitHub setup wizard |
| `docs/design.md` | Architecture and tradeoffs |

## References

- [Reposilite Docker and bootstrap](https://reposilite.com/guide/docker)
- [Tokens](https://reposilite.com/guide/tokens) and [route permissions](https://reposilite.com/guide/routes)
- [Create DOKS](https://docs.digitalocean.com/products/kubernetes/how-to/create-clusters/)
- [Connect to DOKS](https://docs.digitalocean.com/products/kubernetes/how-to/connect-to-cluster/)
- [Volumes](https://docs.digitalocean.com/products/kubernetes/how-to/add-volumes/)
- [Load balancer configuration](https://docs.digitalocean.com/products/kubernetes/how-to/configure-load-balancers/)
