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
- Do not provision or destroy paid resources as part of CI.
- Keep all backup/restore steps offline and preserve existing volumes on teardown.
