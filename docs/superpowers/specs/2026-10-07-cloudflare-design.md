# Cloudflare DNS and Kubernetes TLS

Approved conversation goal: keep apexfission.com and its nameservers at Cloudflare, use Pulumi to provision DigitalOcean and Cloudflare, and derive the stack owner from actual Pulumi organizations. Start from current main. Python/uv wizard remains cross-platform.

Pulumi owns cluster, a dedicated Traefik ingress controller and its TCP load balancer, cert-manager, a Cloudflare DNS API token Secret, a Let's Encrypt ClusterIssuer, and one DNS-only A record. Cloudflare zone is discovered, never created or imported wholesale. Application Deployment, PVC, Service, Ingress and Certificate remain owned by the private-bootstrap/manual-deploy process. No Reposilite route exists during infrastructure setup. Default ingress class is not global; use reposilite-edge explicitly. DNS record content comes from the gateway's actual load balancer IP. cert-manager uses DNS-01 to issue and renew publicly trusted certificates without moving nameservers.

Pulumi receives tokens via environment only, marks the Kubernetes Secret and kubeconfig as Pulumi secrets, never exports them, and doesn't place Cloudflare credentials in GitHub. GitHub gets TLS_MODE=cloudflare, hostname and cluster UUID; the action waits for certificate readiness. Preserve legacy DigitalOcean TLS mode for existing deployments.

Migration from the failed legacy stack: explicitly explain and confirm switching TLS modes; preserve the old DigitalOcean zone and any actually existing certificate in Pulumi state, retain existing cluster identity/properties. Never destroy or replace resources automatically. Reuse declared configuration on subsequent runs and refuse mismatched or missing managed infrastructure. No live provisioning during development/CI.

Organization prompt lists discovered organizations; configured default wins only if a member, otherwise a sole organization or sorted first available is the default. No username fallback. DIY backends keep their unqualified stack behavior.

All infrastructure dependencies, charts and images are pinned. Error messages identify operation/subcommand and provide a safe stack diagnostic link without exposing raw provider output. Tests cover migration, no early public app exposure, DNS conflicts, secrets, organization discovery, renewal configuration and bootstrap preflight. Document limited-token permissions, costs, readiness, migration and manual acceptance checks.

Additional approved requirement: ask for an existing DigitalOcean project, using the account's default project for first setup and the saved selection on reruns. Manage cluster project membership with Pulumi. Display names and IDs to disambiguate. Document that DOKS-associated resources may be created in the default project independently of cluster membership.

Existing Cloudflare A records not owned by this stack are rejected before provisioning. Importing while changing their address would violate Pulumi import matching; such DNS cutovers require separate review. Existing records already in this stack remain reconciled normally.
