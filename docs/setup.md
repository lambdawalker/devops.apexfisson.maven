# Setup

Run commands from the repository root in Bash (Linux, macOS or WSL). Keep
credentials and kubeconfig outside Git. Provisioning creates billable resources.

For automated infrastructure and GitHub environment setup on Windows or Linux,
start with the [Python wizard](setup-wizard.md). If you used it, skip cluster and
certificate creation below; connect to that cluster and complete private setup
steps 2–3 before deploying publicly.

## 1. Create or select DOKS

Install [doctl](https://docs.digitalocean.com/reference/doctl/how-to/install/)
and [kubectl](https://kubernetes.io/docs/tasks/tools/) (within one minor version
of your cluster), then authenticate interactively:

```bash
doctl auth init
doctl kubernetes options regions
doctl kubernetes options sizes
doctl kubernetes options versions
```

Choose supported values from the lists. A 2 vCPU / 4 GiB worker is a reasonable
starting allocation. Edit these example values for your region:

```bash
CLUSTER_NAME=apexfission-maven
REGION=nyc1
NODE_SIZE=s-2vcpu-4gb
read -r -p 'Exact supported DOKS version slug: ' K8S_VERSION
```

For a **new** cluster only, after setting all four variables:

```bash
doctl kubernetes cluster create "$CLUSTER_NAME" \
  --region "$REGION" --version "${K8S_VERSION:?Choose a supported version}" \
  --node-pool "name=maven;size=$NODE_SIZE;count=1" \
  --ha=false --auto-upgrade=true --surge-upgrade=true \
  --maintenance-window saturday=06:00 --wait
```

`--ha=false` explicitly disables paid control-plane HA regardless of version
defaults; change it if desired. This does not make the application HA.
Maintenance times are UTC. Surge upgrades may temporarily add billable workers.

```bash
doctl kubernetes cluster kubeconfig save "$CLUSTER_NAME"
DOKS_CONTEXT=$(kubectl config current-context)
printf 'Target context: %s\n' "$DOKS_CONTEXT"
kubectl --context "$DOKS_CONTEXT" get nodes
kubectl --context "$DOKS_CONTEXT" get csidriver dobs.csi.digitalocean.com
```

Verify this is the intended cluster. Set `DOKS_CONTEXT` again in any new shell;
all following commands use it explicitly.

## 2. Deploy privately

```bash
kubectl kustomize k8s/base
kubectl --context "$DOKS_CONTEXT" apply -k k8s/base
kubectl --context "$DOKS_CONTEXT" -n reposilite rollout status deployment/reposilite --timeout=10m
kubectl --context "$DOKS_CONTEXT" -n reposilite get pvc,pods,svc
```

The PVC binds after scheduling (`WaitForFirstConsumer`). ClusterIP creates no
public load balancer. The volume is billable. The cluster-scoped storage class
requires administrator permission.

## 3. Create a persistent administrator

Generate a 32+ character alphanumeric password in your password manager. The
following sends it over stdin, not the kubectl command line. It creates a new
Secret and deliberately fails if one already exists; investigate before replacing
an existing bootstrap Secret. Do not enable shell tracing.

```bash
(
  set -euo pipefail
  set +x
  read -r -s -p 'Temporary bootstrap password: ' BOOTSTRAP_PASSWORD
  printf '\n'
  [[ "$BOOTSTRAP_PASSWORD" =~ ^[A-Za-z0-9]{32,}$ ]] || {
    printf 'Use at least 32 alphanumeric characters.\n' >&2
    exit 1
  }
  printf '%s' "--token bootstrap:$BOOTSTRAP_PASSWORD" |
    kubectl --context "$DOKS_CONTEXT" -n reposilite create secret generic reposilite-bootstrap \
      --from-file=REPOSILITE_OPTS=/dev/stdin
)
kubectl --context "$DOKS_CONTEXT" -n reposilite rollout restart deployment/reposilite
kubectl --context "$DOKS_CONTEXT" -n reposilite rollout status deployment/reposilite --timeout=10m
kubectl --context "$DOKS_CONTEXT" -n reposilite port-forward --address 127.0.0.1 service/reposilite 8080:8080
```

Leave port-forward running. Open `http://127.0.0.1:8080`, log in as `bootstrap`
with your password, and enter this in the dashboard **Console**:

```text
token-generate admin m
```

Save the generated secret. Log out and verify the permanent `admin` login.
Inspect Settings: confirm `releases` and `snapshots` visibility (PUBLIC if you
want anonymous downloads), and PRIVATE visibility for `private`. Keep release
redeployment disabled. Enable snapshot redeployment if your workflow needs it.

Stop port-forward with Ctrl+C. Remove the temporary token source and restart:

```bash
kubectl --context "$DOKS_CONTEXT" -n reposilite delete secret reposilite-bootstrap
kubectl --context "$DOKS_CONTEXT" -n reposilite rollout restart deployment/reposilite
kubectl --context "$DOKS_CONTEXT" -n reposilite rollout status deployment/reposilite --timeout=10m
kubectl --context "$DOKS_CONTEXT" -n reposilite port-forward --address 127.0.0.1 service/reposilite 8080:8080
```

Verify `admin` still works after restart and `bootstrap` fails. Stop port-forward.
Do not expose the service until both checks pass. Production also removes the
Secret reference, so a forgotten Secret cannot reactivate bootstrap credentials.

## 4. Prepare the hostname and certificate

Choose a hostname, e.g. `maven.your-domain.com`.

- With DigitalOcean DNS, create a managed Let's Encrypt certificate for that
  hostname in **Networking → Certificates**. Give it a unique name such as
  `apexfission-maven-tls` and wait for issuance.
- With external DNS, import a certificate/private key into DO and arrange
  renewal, or move DNS to DO for managed issuance. No cert-manager is installed.

See [certificate management](https://docs.digitalocean.com/products/networking/load-balancers/how-to/manage-certificates/).
Use `doctl compute certificate list` to get the certificate **name**, which
survives managed renewal. The certificate must exist before public deployment.
TLS terminates at the load balancer; the internal backend connection uses HTTP.

## 5. Expose HTTPS

You can use the manual [GitHub deployment workflow](github-deployment.md) for
this step and subsequent updates, or follow the local commands below.

Run `mkdir -p .local` and save this as `.local/kustomization.yaml`, replacing
the certificate name. This directory is ignored by Git:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../k8s/overlays/production
patches:
  - target:
      kind: Service
      name: reposilite
    patch: |-
      - op: replace
        path: /metadata/annotations/service.beta.kubernetes.io~1do-loadbalancer-certificate-name
        value: apexfission-maven-tls
```

Render and review before applying:

```bash
kubectl kustomize .local > .local/rendered.yaml
if grep -q 'CHANGE-ME' .local/rendered.yaml; then
  printf 'Replace the certificate placeholder before applying.\n' >&2
else
  kubectl --context "$DOKS_CONTEXT" apply --dry-run=server -f .local/rendered.yaml &&
  kubectl --context "$DOKS_CONTEXT" apply -f .local/rendered.yaml
fi
kubectl --context "$DOKS_CONTEXT" -n reposilite rollout status deployment/reposilite --timeout=10m
kubectl --context "$DOKS_CONTEXT" -n reposilite get service reposilite --watch
```

This creates a billable regional load balancer. Once its external IP appears,
stop the watch and create an **A record** for your hostname pointing to that IP.
Automatic DNS creation by the load balancer is disabled; you own this explicit
step. Do not add an AAAA record without configuring IPv6.

Open `https://maven.your-domain.com`. Verify the certificate, dashboard login,
and Console WebSocket connection. `curl -I http://maven.your-domain.com` should
redirect to HTTPS. Always send publishing credentials directly to HTTPS.

For clients inside DOKS, add
`service.beta.kubernetes.io/do-loadbalancer-hostname: maven.your-domain.com`
to the Service via a local patch **after** DNS resolves. This follows DO's
documented workaround for reaching its external load balancer from the cluster.

Use `.local/` for future public deployments. Applying `k8s/base` again changes
the Service to ClusterIP and can remove the load balancer/IP. Keep a secure copy
of your local override for recovery.
