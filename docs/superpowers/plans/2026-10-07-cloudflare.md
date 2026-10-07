# Cloudflare setup implementation plan

Goal: provision Cloudflare DNS and automatic Kubernetes TLS while preserving the existing private Reposilite bootstrap.
Spec: ../specs/2026-10-07-cloudflare-design.md

## Task 1: Infrastructure and wizard
- Add read-only Cloudflare discovery, hidden token prompt, explicit migration, pinned Pulumi Kubernetes/Cloudflare SDKs and gateway Helm charts.
- Add organization selection from whoami membership; default-org lookup must be optional and safe.
- Infrastructure outputs: clusterId, hostname, tlsMode=cloudflare. Legacy certificateName stays available for legacy mode. New config adds tlsMode=cloudflare and cloudflare={zoneId,zoneName,recordId,acmeEmail}; preserve legacy declared domain/certificate when already created.
- Gateway class reposilite-edge; issuer cloudflare-letsencrypt; gateway namespace reposilite-edge; cert-manager namespace cert-manager. Application namespace remains app-owned.
- Verify mocked resources, token redaction, migration and discovery edge cases; never provision live resources.

## Task 2: Application deployment
- Preserve legacy production overlay; add cloudflare overlay with ClusterIP, explicit HTTPS-only Ingress class reposilite-edge, cert-manager issuer annotation and reposilite-tls Secret.
- configuration accepts TLS_MODE=cloudflare without DO_CERTIFICATE_NAME; default legacy for compatibility. Patch ingress hostname and TLS hosts. Wait for Certificate and Ingress rather than application LoadBalancer in this mode.
- Workflow receives TLS_MODE, keeps main-only/bootstrap/maintenance preflights. Offline tests render both modes. Extend schema validation without skipping ordinary Kubernetes validation.

## Task 3: Documentation and integration
- Update ownership guidance, setup/runbooks, token scopes, partial migration, DNS-only and renewals. Add focused diagnostic guidance.
- Test all wizard and deployment behavior under uv, render manifests and pinned charts, actionlint and schema validation; external cloud smoke remains operator-run.
- Review resulting diff and publish branch/PR based on main; do not deploy or merge.

Task 1 addition: read DO projects, default by is_default, persist selected ID/name, assign cluster through ProjectResources; configure gateway project annotation if supported. Test default and rerun behavior. Task 3 documents associated-volume project limitation.

Import safety ruling: unmanaged Cloudflare records fail closed instead of attempting an import with changed content; no automatic import-plus-cutover. Known managed DNS records continue to reconcile.
