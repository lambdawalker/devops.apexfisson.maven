# Pulumi infrastructure

The setup wizard uses the Python project in `infrastructure/` to manage the DOKS
cluster, DNS zone (when selected), and managed TLS certificate. Pulumi owns those
resources. The existing **Deploy Reposilite** action owns the Kubernetes application,
Service and persistent storage. Do not manage the same resources with both tools.

## Install and authenticate

Install [Pulumi CLI](https://www.pulumi.com/docs/install/) (tested with **3.268.0**),
Python 3.10+, and GitHub CLI. From the repository root:

Windows Command Prompt:

```text
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r infrastructure/requirements.txt
pulumi login
.venv\Scripts\python.exe scripts/setup_environment.py
```

Linux:

```text
python3 -m venv .venv
.venv/bin/python -m pip install -r infrastructure/requirements.txt
pulumi login
.venv/bin/python scripts/setup_environment.py
```

No PowerShell or shell activation is required. Python SDK versions are pinned in
`infrastructure/requirements.txt`. The wizard uses the same Python executable for
the Pulumi program. `--saved-login` reuses GitHub CLI authentication; it does not
change Pulumi authentication. `--github-only` still needs only Python and gh.

`pulumi login` authenticates separately to Pulumi Cloud. The wizard uses its saved
login or an existing `PULUMI_ACCESS_TOKEN` environment variable; it never requests
a Pulumi token as a command-line argument. Pulumi CLI manages its own credentials.
The wizard still privately asks for the DigitalOcean token and, by default, a
GitHub token.

## Backend and stack

The backend prompt defaults to `https://api.pulumi.com`, or `PULUMI_BACKEND_URL`
when set. The Cloud stack defaults to `YOUR_PULUMI_ACCOUNT/apexfission-maven/production`;
you can enter an organization's stack instead. The project name remains
`apexfission-maven`. For a DIY backend, the stack prompt defaults to `production`.

Use **the same backend and stack every time**. Do not import a resource into more
than one stack. Stack selection can create an empty state record before the
infrastructure review; it creates no DigitalOcean resources.

Self-managed `s3://`, `gs://`, `azblob://`, and `file://` backends are supported by
Pulumi. Configure storage credentials and the secrets provider before running the
wizard. A passphrase backend needs `PULUMI_CONFIG_PASSPHRASE` (or its file variant)
available to the CLI; never use an empty passphrase for production. A local file
backend is suitable for experiments, but is not a shared CI backend. Back up state
and retain the secrets-provider key. See [state and backends](https://www.pulumi.com/docs/iac/concepts/state-and-backends/).

Nonsecret stack configuration is stored under ignored `.local/pulumi/`, in a YAML
file named by a hash of the backend/stack identity. This prevents stacks in
different accounts/backends from overwriting each other's local configuration.
After an update, config is also associated with the deployment in the backend.
On a new checkout, the wizard restores it from the backend. If local config exists,
it is treated as the operator's desired configuration, including unapplied edits.
The wizard prints the full nonsecret desired configuration before preview.

The Pulumi config file is not the state itself. State stays in the chosen backend.
Credentials are not put in the config: the DO token goes to the child process via
`DIGITALOCEAN_TOKEN`. Cluster `kubeConfigs` outputs are explicitly secret in state;
only cluster UUID, hostname and certificate name are exported. Treat backend
access as privileged even though secret outputs are encrypted.

## Preview and apply

The wizard performs read-only DO discovery, then prepares a configuration proposal.
Confirm preparation to run a Pulumi preview. The preview refreshes the resource
view, prints operations, and saves a temporary plan. Review the displayed desired
configuration and operations, then type `yes` to apply that plan and save GitHub
settings. Enter cancels. The wizard refuses deletions, replacements and unknown
operation kinds; `protect=True` also guards every owned resource.

Cancellation retains staged local config but applies no cloud/GitHub update.
A failed apply may leave resources and Pulumi state. The temporary plan is discarded
on success, failure or cancellation; the next attempt gets a fresh preview.
Pulumi failures show a safe summary rather than raw provider diagnostics that might
contain credentials. Inspect the stack's update history for details. CLI operations
have a 45-minute timeout; an interrupted update needs inspection before retrying.

A new managed certificate depends on the DNS zone, and the cluster depends on the
certificate. The provider waits for certificate verification before creating the
cluster. Domain purchase, nameserver delegation and migration of existing DNS
records remain operator tasks. Existing certificates must already be verified.

## Import existing infrastructure

The wizard discovers resources by unique name and configures Pulumi imports:

| Resource | Provider import ID |
| --- | --- |
| DOKS cluster | DigitalOcean cluster UUID |
| DNS zone | Domain name |
| Managed certificate | Certificate **name**, which survives renewal |

New resources are managed immediately. Imported resources start with
`preserveImported: true`, intentionally leaving their current configuration under
external management while Pulumi records and protects them. This uses
`ignoreChanges: ["*"]`: editing desired inputs has no effect until you opt in.
The review shows imports, not replacement resources. An existing custom certificate
is read as an external reference because DO cannot return its private key; Pulumi
does not manage its renewal or deletion.

On reruns, the selected stack's existing config is authoritative. If discovery
finds a different/missing ID than the stack owns, the wizard stops instead of
silently recreating or adopting another resource. It also rejects name/hostname
changes made through setup prompts against an established config; deliberate
identity changes need a separate migration review.

Before disabling `preserveImported`, compare the program with the imported state.
The wizard snapshots only the basic cluster fields and first node pool; unusual
clusters with multiple pools, autoscaling, custom maintenance, labels, taints,
firewalls or integrations need their settings represented in the program first.
Keeping preservation enabled retains all of those settings. Do not enable full
management simply to silence a diff. Refer to [Pulumi import guidance](https://www.pulumi.com/docs/iac/guides/migration/import/).

## Later infrastructure changes

For resources created by this project, edit the `infrastructure` JSON value in the
selected `.local/pulumi/<hash>.yaml` file, then rerun the wizard with the same stack.
For example, change `cluster.properties.nodePool.nodeCount` from `1` to `2`.
Pulumi previews the resize before applying it. This adds a Kubernetes worker;
Reposilite itself still has exactly one replica.

For imports, first model the existing settings as described above and deliberately
set that resource's `preserveImported` to false. All owned resources remain protected.

When `cluster.properties.autoUpgrade` is true, version drift is deliberately
ignored so a DO patch upgrade is not reverted to the original version. To manage
a version explicitly, set `autoUpgrade` false and choose a supported newer version
before previewing. Do not attempt Kubernetes downgrades.

Normal setup and updates happen locally through the wizard. The existing GitHub
application deployment action neither runs Pulumi nor needs Pulumi credentials.
If infrastructure CI is added later, it must use this same backend/stack, have
explicit authorization and concurrency controls, and keep previews credential-free
on untrusted pull requests. Do not add `pulumi up` to the validation workflow.

Protection guards Pulumi operations; it cannot stop deletion from the DO console.
The cluster resource also sets `destroyAllAssociatedResources` false. Neither is
a backup. Preserve the existing retained-volume and offline backup procedures.

## Next steps after infrastructure setup

Connect with doctl/kubectl and complete [setup](setup.md) steps 2–3 to bootstrap
Reposilite privately. Then run [Deploy Reposilite](github-deployment.md) and set
the final DNS A record to its load-balancer IP. The wizard does not create a
Reposilite administrator, deploy the application or run a GitHub workflow.

Tests use Pulumi mocks and simulated CLI results; the Windows/Linux CI matrix
creates no cloud resources. Live provisioning still requires your credentials and
confirmation of the preview.
