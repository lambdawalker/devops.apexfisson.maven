# Operations

Use the verified `DOKS_CONTEXT` from setup and run from the repository root.
The shell blocks use `set -euo pipefail` and stop on failed commands.
Run each block as a complete Bash script/block and resolve failures before moving
to the next block. The only application replica must remain one. Local SQLite and ReadWriteOnce
storage are not a multi-replica architecture. RWO alone does not prevent two
pods on the same node from writing; Recreate and offline maintenance matter.

## Health and troubleshooting

```bash
set -euo pipefail
: "${DOKS_CONTEXT:?Set the verified target context first}"
kubectl --context "$DOKS_CONTEXT" -n reposilite get pods,pvc,svc
kubectl --context "$DOKS_CONTEXT" -n reposilite logs deployment/reposilite --tail=100
kubectl --context "$DOKS_CONTEXT" -n reposilite describe pod -l app.kubernetes.io/name=reposilite
kubectl --context "$DOKS_CONTEXT" -n reposilite get events --sort-by=.lastTimestamp
kubectl --context "$DOKS_CONTEXT" -n reposilite exec deployment/reposilite -- df -h /app/data
```

| Symptom | Check |
| --- | --- |
| Pending PVC | CSI driver, node scheduling, storage class and account quota |
| Mount permission denied | UID/GID 977 and fsGroup 977; inspect restored ownership |
| Multi-attach | Old pod/maintenance pod must exit before a new writer starts |
| Pending load balancer | Certificate name/issuance, regional type and account limits |
| OOMKilled | 512 MiB heap plus native memory must fit the 1 GiB limit |
| Console fails | WebSocket connectivity through LB; compare localhost port-forward |
| Disk near capacity | Increase PVC size and arrange artifact retention/cleanup |

Logs on `/var/log/reposilite` use an ephemeral 256 MiB volume. Ship stdout/logs
to your monitoring service for durable retention. Monitor disk usage, pod
restarts, certificate renewal, HTTP failures and backup age.

## Upgrade and rollback

Take an offline backup first. Change the image tag in
`k8s/base/deployment.yaml` and `k8s/maintenance/pod.yaml` together after reading
upstream release notes. Validate locally, then:

```bash
set -euo pipefail
: "${DOKS_CONTEXT:?Set the verified target context first}"
kubectl --context "$DOKS_CONTEXT" diff -k .local || {
  result=$?
  test "$result" -eq 1 || exit "$result"
}
# diff exit code 1 means differences; inspect them before applying.
kubectl --context "$DOKS_CONTEXT" apply -k .local
kubectl --context "$DOKS_CONTEXT" -n reposilite rollout status deployment/reposilite --timeout=10m
```

Recreate deliberately causes downtime to avoid overlapping database writers.
Check login, WebSockets, upload and download after an upgrade. An image rollback
does not undo database migrations: if the older release cannot read the new
database, restore the pre-upgrade backup to a clean PVC and use its matching
image. Do not blindly run `rollout undo` across database-format changes.

To expand storage, increase the claim's request in `k8s/base/storage.yaml` and
apply `.local/`. Volumes can grow, not shrink. Confirm capacity with `get pvc`
and `df`; a pod restart may be needed. Keep the declared size in Git in sync.

## Offline backup

This process backs up the **entire data directory**, including artifacts,
configuration and token database. It causes downtime. Do not archive SQLite
while Reposilite is running. Pause deployments/other operators during maintenance.
The maintenance pod runs the same pinned Ubuntu-based image, overriding its
entrypoint with sleep; GNU tar is used below.

```bash
set -euo pipefail
: "${DOKS_CONTEXT:?Set the verified target context first}"
kubectl --context "$DOKS_CONTEXT" -n reposilite scale deployment/reposilite --replicas=0
kubectl --context "$DOKS_CONTEXT" -n reposilite wait --for=delete pod \
  -l app.kubernetes.io/name=reposilite --timeout=5m
kubectl --context "$DOKS_CONTEXT" apply -f k8s/maintenance/pod.yaml
kubectl --context "$DOKS_CONTEXT" -n reposilite wait --for=condition=Ready pod/reposilite-maintenance --timeout=10m
mkdir -p backups
umask 077
BACKUP_FILE="backups/reposilite-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
kubectl --context "$DOKS_CONTEXT" -n reposilite exec reposilite-maintenance -- \
  tar --exclude=./lost+found -C /data -czf - . > "$BACKUP_FILE.partial" &&
  gzip -t "$BACKUP_FILE.partial" &&
  mv "$BACKUP_FILE.partial" "$BACKUP_FILE"
```

Stop if any command fails. Confirm the final archive exists, inspect it with
`tar -tzf "$BACKUP_FILE"`, and record a SHA-256 checksum (`sha256sum` on Linux,
`shasum -a 256` on macOS), the Git commit and application image version. Encrypt
and copy the archive to storage outside this cluster (e.g. Spaces or a separate
backup account). The archive contains sensitive configuration/token hashes;
keep it private. Schedule this procedure according to your acceptable data-loss
window and rehearse restoration. This project does not install scheduled backups.

Remove the maintenance pod **before** restarting:

```bash
set -euo pipefail
: "${DOKS_CONTEXT:?Set the verified target context first}"
kubectl --context "$DOKS_CONTEXT" -n reposilite delete pod reposilite-maintenance --wait=true
kubectl --context "$DOKS_CONTEXT" -n reposilite scale deployment/reposilite --replicas=1
kubectl --context "$DOKS_CONTEXT" -n reposilite rollout status deployment/reposilite --timeout=10m
```

## Restore rehearsal / disaster recovery

Use a new test cluster or a **new empty PVC**, keeping the existing volume and
backup untouched. For a new cluster: apply the namespace and storage resources
only, without the Deployment, then the maintenance pod. Its scheduling provisions
the claim. Do not start Reposilite before restoring.

```bash
set -euo pipefail
: "${DOKS_CONTEXT:?Set the verified target context first}"
# DOKS_CONTEXT must now point to your NEW recovery/test cluster.
kubectl --context "$DOKS_CONTEXT" apply -f k8s/base/namespace.yaml
kubectl --context "$DOKS_CONTEXT" -n reposilite apply -f k8s/base/storage.yaml
kubectl --context "$DOKS_CONTEXT" apply -f k8s/maintenance/pod.yaml
kubectl --context "$DOKS_CONTEXT" -n reposilite wait --for=condition=Ready pod/reposilite-maintenance --timeout=10m
kubectl --context "$DOKS_CONTEXT" -n reposilite exec reposilite-maintenance -- ls -la /data
```

Only `lost+found` should be present. If application files exist, stop: select
a new claim, do not overlay a backup on a populated database. Verify the archive's
saved checksum and `gzip -t` before extraction. Set `BACKUP_FILE` to that archive:

```bash
set -euo pipefail
: "${DOKS_CONTEXT:?Set the verified target context first}"
kubectl --context "$DOKS_CONTEXT" -n reposilite exec -i reposilite-maintenance -- \
  tar --no-same-owner --no-same-permissions --no-overwrite-dir \
  -C /data -xzf - < "$BACKUP_FILE"
kubectl --context "$DOKS_CONTEXT" -n reposilite delete pod reposilite-maintenance --wait=true
kubectl --context "$DOKS_CONTEXT" apply -k k8s/base
kubectl --context "$DOKS_CONTEXT" -n reposilite rollout status deployment/reposilite --timeout=10m
kubectl --context "$DOKS_CONTEXT" -n reposilite port-forward --address 127.0.0.1 service/reposilite 8080:8080
```

Use the **backup's matching image version** in both manifests. Extraction as
UID 977 ensures restored files are writable; GNU tar's directory options avoid
attempting to change the root-owned volume mount's metadata. Validate permanent
login, settings, known artifact checksums and a test publication privately.
For real recovery, only then apply your `.local/` TLS overlay and update DNS.

## Teardown

To remove the public endpoint and application while keeping data:

```bash
set -euo pipefail
: "${DOKS_CONTEXT:?Set the verified target context first}"
kubectl --context "$DOKS_CONTEXT" -n reposilite delete service reposilite
kubectl --context "$DOKS_CONTEXT" -n reposilite delete deployment reposilite
```

Check DO for completion of load balancer deletion. Do **not** delete the namespace,
PVC or use `kubectl delete -k` as ordinary teardown. The Retain policy preserves
the underlying volume after PVC deletion, but a released PV needs manual recovery
and remains billable. Before deleting the cluster, export backups, record the PV
and cloud volume IDs, and review all associated resources. Avoid automatic
destructive-resource cleanup flags. Delete retained volumes only when you intend
permanent data loss and have verified your backups.

## Local validation and live acceptance

```bash
set -euo pipefail
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -v
kubectl kustomize k8s/base > /tmp/reposilite-private.yaml
kubectl kustomize k8s/overlays/production > /tmp/reposilite-public.yaml
kubeconform -strict -summary -kubernetes-version 1.35.0 /tmp/reposilite-private.yaml /tmp/reposilite-public.yaml k8s/maintenance/pod.yaml
```

Install [kubeconform](https://github.com/yannh/kubeconform) v0.6.7, as CI does.
Schema checks do not test DigitalOcean behavior. Before relying on this instance,
verify PVC binding, non-root startup, data/token persistence after pod restart,
HTTPS and redirect, Console WebSockets, scoped upload and anonymous download,
rejection of anonymous/unauthorized writes, and restoration to a clean volume.
