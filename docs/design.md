# Reposilite on DigitalOcean Kubernetes

Host ApexFission Maven artifacts in a self-managed repository. This is a separate
Maven endpoint; it does not publish to, or become part of, Maven Central.

Use kubectl's built-in Kustomize support. A private base installs a namespace,
retained DigitalOcean block-storage class, 20 GiB PVC, one Reposilite 3.6.3
Deployment and ClusterIP Service. Recreate deployment strategy prevents overlapping
writers during upgrades. Run as UID/GID 977 with writable data, logs and temporary
mounts, probes, resource limits and no Kubernetes API token.

Bootstrap a temporary administrator through a Kubernetes Secret and localhost
port-forward. Create a persistent administrator, remove the temporary Secret and
restart before public exposure. Reposilite configuration, tokens and artifacts
live on the PVC. Logs are ephemeral; collect them externally if needed.

The Cloudflare overlay keeps the application Service private and publishes an
HTTPS-only Ingress through the explicitly named `reposilite-edge` class. Pulumi
installs a dedicated Traefik gateway with a TCP DigitalOcean load balancer and
cert-manager, plus the Cloudflare credential Secret and ACME ClusterIssuer.
Cloudflare retains DNS authority. A DNS-only A record points to the gateway IP;
cert-manager verifies DNS challenges and renews TLS automatically. Traefik watches
the resulting TLS Secret. Backend traffic from gateway to Reposilite uses HTTP.

Infrastructure setup exposes only an unrouted gateway. Private administrator
bootstrap remains mandatory; the manual GitHub deployment action then creates
the application Ingress, waits for its certificate and publishes readiness.
The legacy production overlay remains available for existing DigitalOcean
certificate installations. Switching a running legacy deployment requires
reviewing its old load balancer and DNS separately.

The accepted ownership change extends Pulumi to shared Kubernetes edge/TLS
infrastructure and one Cloudflare DNS record. It does not own the application
namespace, Deployment, PVC, Service or Ingress. No entire Cloudflare zone or
DigitalOcean project is adopted. The cluster is assigned to a discovered existing
project; associated resources may have separate project placement. The wizard
retains legacy resources during partial-state migration instead of deleting them.
See [Pulumi setup](pulumi.md) for state, secrets, imports and migration.

One worker is the economical starting point, not high availability. Maintenance,
backups and upgrades can interrupt service. A second worker can improve recovery
but does not make this single-instance SQLite deployment highly available. Never
scale Reposilite above one without redesigning its database and storage.

Verification: render both Kustomize targets, validate Kubernetes schemas and
check storage/security/exposure invariants in CI. Live DOKS tests (volume mount,
restart persistence, HTTPS, WebSocket console, authenticated upload/download and
restore) are documented acceptance steps; they require an operator's account.

