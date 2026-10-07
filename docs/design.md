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

A separate public overlay changes the Service to a DigitalOcean regional HTTP
load balancer with managed-certificate TLS termination and HTTP-to-HTTPS redirect.
Reference the certificate by name so renewal does not require editing its UUID.
The setup wizard can create the DNS zone and managed certificate; the operator
provides domain ownership and DNS delegation. Backend traffic is HTTP within
the cluster; this is not end-to-end TLS.

The simpler alternative is a single Droplet with Docker, but the requested target
is DOKS. An ingress controller plus cert-manager is another option, useful for
sharing one load balancer among applications; it adds components unnecessary for
this dedicated endpoint. Pulumi manages DigitalOcean infrastructure through a Python project, driven by
the interactive setup wizard. Its protected resources, shared state, imports and
reviewed plans are described in [pulumi.md](pulumi.md). Direct DO API calls in the
wizard are read-only discovery. Manifests and the existing deployment action
continue to manage application state; Pulumi does not manage Kubernetes objects.

One worker is the economical starting point, not high availability. Maintenance,
backups and upgrades can interrupt service. A second worker can improve recovery
but does not make this single-instance SQLite deployment highly available. Never
scale Reposilite above one without redesigning its database and storage.

Verification: render both Kustomize targets, validate Kubernetes schemas and
check storage/security/exposure invariants in CI. Live DOKS tests (volume mount,
restart persistence, HTTPS, WebSocket console, authenticated upload/download and
restore) are documented acceptance steps; they require an operator's account.

