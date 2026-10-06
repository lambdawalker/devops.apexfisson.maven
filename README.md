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

1. **[Setup](docs/setup.md)**: cluster, administrator, domain and TLS.
2. **[Publishing and consuming](docs/publishing.md)**: scoped tokens and Gradle.
3. **[Operations](docs/operations.md)**: backups, restore, upgrades and diagnostics.

You need a DigitalOcean account/API token, a domain, `doctl`, `kubectl`, and
Bash (WSL on Windows works). Python is only needed for local validation.
No DigitalOcean credentials are needed by this repository's CI.

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
| `docs/design.md` | Architecture and tradeoffs |

## References

- [Reposilite Docker and bootstrap](https://reposilite.com/guide/docker)
- [Tokens](https://reposilite.com/guide/tokens) and [route permissions](https://reposilite.com/guide/routes)
- [Create DOKS](https://docs.digitalocean.com/products/kubernetes/how-to/create-clusters/)
- [Connect to DOKS](https://docs.digitalocean.com/products/kubernetes/how-to/connect-to-cluster/)
- [Volumes](https://docs.digitalocean.com/products/kubernetes/how-to/add-volumes/)
- [Load balancer configuration](https://docs.digitalocean.com/products/kubernetes/how-to/configure-load-balancers/)
