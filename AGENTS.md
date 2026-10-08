# Working on this repository

This project hosts a standalone Maven repository on DigitalOcean Kubernetes.
Read README.md and docs/design.md before changing deployment behavior.

- Keep one Reposilite replica and Recreate updates: local database and RWO volume.
- Keep artifacts, application configuration and token database on /app/data.
- The base is private. Cloudflare infrastructure creates an unrouted gateway LoadBalancer;
  only manual production deployment may publish a Reposilite Ingress. Keep legacy TLS overlay supported.
- Never commit tokens, kubeconfig, backups or real certificate private keys.
- Production must not import the temporary bootstrap Secret.
- Pin container releases; verify upstream entrypoint behavior before upgrading.
- Use kubectl kustomize plus the validation described in docs/operations.md.
- Validation CI must not provision or destroy paid resources. Only the explicitly
  dispatched Deploy Reposilite workflow may apply production resources.
- Deployment must use the production environment, main-only execution and a
  shared concurrency group; preserve the maintenance/bootstrap preflight checks.
- Keep all backup/restore steps offline and preserve existing volumes on teardown.

- Pulumi owns DigitalOcean cluster/project membership, shared Kubernetes edge/TLS
  infrastructure and one Cloudflare DNS record. Keep API discovery read-only.
  Application namespace/Deployment/PVC/Service/Ingress/Certificate remain outside Pulumi.
- Preserve Cloudflare nameservers; never import/manage the whole zone. DNS-only record.
  Mark Cloudflare Secret and kubeconfig as Pulumi secrets and never export them.
- Preserve stack identity and imported resource settings on setup reruns. Never
  silently recreate missing managed resources or bypass protect/preview checks.
- Pulumi tests use SDK mocks; install infrastructure/requirements.txt for validation.
  Never provision cloud resources in validation CI.

- Explicit failed-setup reset: `scripts/cleanup_environment.py` is the reviewed
  exception for deleting associated volumes and calling cloud deletion APIs. It
  requires an inventory and typed confirmation, journals IDs before deletion, and
  removes Pulumi state only after verifying absence. Keep normal teardown and
  setup protections unchanged; never execute live cleanup in validation.

- Independent setup scripts are an explicit alternative to the combined Pulumi
  wizard: DigitalOcean APIs/Helm/kubectl, then Cloudflare-only DNS, then DOKS
  HTTP-01 hostname/TLS, then GitHub settings. Keep provider credentials separated.
  Local public exposure is allowed only after private administrator bootstrap
  and confirmation. Preserve data and check exposure before bootstrap writes.
  Do not run the old Pulumi manager against resources switched to this flow.
