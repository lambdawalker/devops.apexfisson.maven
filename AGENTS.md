# Working on this repository

This project hosts a standalone Maven repository on DigitalOcean Kubernetes.
Read README.md and docs/design.md before changing deployment behavior.

- Keep one Reposilite replica and Recreate updates: local database and RWO volume.
- Keep artifacts, application configuration and token database on /app/data.
- The base is private; only the production overlay may create a LoadBalancer.
- Never commit tokens, kubeconfig, backups or real certificate private keys.
- Production must not import the temporary bootstrap Secret.
- Pin container releases; verify upstream entrypoint behavior before upgrading.
- Use kubectl kustomize plus the validation described in docs/operations.md.
- Validation CI must not provision or destroy paid resources. Only the explicitly
  dispatched Deploy Reposilite workflow may apply production resources.
- Deployment must use the production environment, main-only execution and a
  shared concurrency group; preserve the maintenance/bootstrap preflight checks.
- Keep all backup/restore steps offline and preserve existing volumes on teardown.

- Pulumi in infrastructure/ owns DigitalOcean infrastructure only. Keep DO discovery
  read-only and do not give Pulumi ownership of application/PVC/Service resources.
- Preserve stack identity and imported resource settings on setup reruns. Never
  silently recreate missing managed resources or bypass protect/preview checks.
- Pulumi tests use SDK mocks; install infrastructure/requirements.txt for validation.
  Never provision cloud resources in validation CI.
