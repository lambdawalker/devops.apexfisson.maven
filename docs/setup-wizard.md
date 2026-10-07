# Set up DigitalOcean, Cloudflare and GitHub

Keep your domain and nameservers at Cloudflare. The Python wizard provisions a
DOKS cluster, a Traefik HTTPS gateway, cert-manager with Cloudflare DNS verification,
and a DNS-only A record. It then configures GitHub's `production` environment.
Reposilite stays private until you finish administrator bootstrap and dispatch the
application deployment workflow.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/),
[GitHub CLI](https://cli.github.com/), and [Pulumi CLI](https://www.pulumi.com/docs/install/).
From the repository root on Windows or Linux:

```text
uv run python scripts/setup_environment.py
```

uv supplies Python 3.13+ and the pinned SDKs. No PowerShell scripts or manual
virtual-environment activation are needed. Pulumi login runs interactively;
follow its browser/token prompts. `--saved-login` reuses your GitHub CLI login;
`--repo OWNER/REPO` selects another repository.

## Guided interface

The default Textual interface shows the ordered steps, their status, a progress bar,
elapsed time for the active step, and a scrollable **Output · stdout / stderr** panel.
Wizard messages and noninteractive GitHub/Pulumi command output stream into this
panel as complete lines arrive, including diagnostics on failed commands. Progress counts
completed steps; it does not estimate DigitalOcean deployment progress.
Pulumi login temporarily returns to the normal terminal, then the interface resumes.
Press Ctrl+C to request cancellation; an in-flight operation finishes before the
wizard stops, and already-created resources are retained. Press Enter or Q to
close the final result.

Configuration is entered in grouped, scrollable forms above the output panel:

1. **Credentials and repository** — prefilled repository, masked saved tokens and
   Pulumi backend. `--saved-login` omits the GitHub token field.
2. **Pulumi stack** — organization choices from your actual memberships and the
   stack name, after terminal login.
3. **Infrastructure and DNS** — cluster, project, worker settings, hostname,
   Cloudflare zone and ACME email. Choices come from provider discovery, with
   defaults from the selected stack and GitHub. Worker fields apply only to new
   clusters; they are disabled when an existing cluster name is entered.
4. **Review infrastructure** — choose **Prepare preview**, inspect the resulting
   change summary, then explicitly choose **Apply** to provision and save settings.

Use **Proceed** to validate each form. Required fields and invalid values show
errors next to the field; your other edits stay intact. Provider discovery conflicts
are displayed on the infrastructure form with your entries preserved. Scroll or
Tab through longer forms; the buttons and output panel remain visible. Enter in
an input moves focus to the next control rather than submitting the form.
Cancellation stops before the next operation and retains the debug log.

In `--github-only` mode, the infrastructure forms are replaced by a **GitHub
environment** form prefilled with existing settings, followed by **Save settings**.
A blank DigitalOcean token keeps the existing GitHub secret. The `--plain` option
continues to use sequential terminal prompts and yes/no confirmations.

Every run saves a UTF-8 debugging log at `.local/logs/setup-<UTC timestamp>-<unique id>.log`.
The path is displayed at startup and when the log is retained. Logs include setup
stages, command output, exit codes, durations and safe failure details; each write
is flushed to disk. `.local/` is ignored by Git. On success, the wizard asks
**“Delete the debugging log?”** with a default of **no**. Failed, cancelled or
interrupted runs retain their logs automatically. This also works with `--plain`.

Known tokens/passphrases and common credential fields are redacted before display
and logging. Decrypted GPG data, Pulumi state/configuration/stack-output payloads,
and resource properties in preview JSON are withheld. Preview diagnostic messages
are streamed, and the existing reviewed change summary is still shown. Interactive
Pulumi login stays on the real terminal and is not recorded, to keep its hidden
credential prompts private. Review a log before sharing it: it can contain resource
names, account identifiers and provider diagnostics.

Use basic terminal prompts on either Windows or Linux if preferred:

```text
uv run python scripts/setup_environment.py --plain
```

The wizard asks **“Is your domain's DNS managed by Cloudflare?”** (default: yes).
This refers to your authoritative DNS, not the company where you registered the
domain. Choosing no stops this Cloudflare provisioning flow before cloud changes;
the GitHub-only mode remains available for existing infrastructure.

## Save tokens locally (optional)

Install GnuPG 2.2+ and ensure `gpg --version` works in the same terminal:
[Gpg4win](https://www.gpg4win.org/) on Windows, or your Linux distribution's
`gnupg` package (for example, `sudo apt install gnupg` on Ubuntu/Debian).
On Windows, use native Gpg4win and place its GnuPG `bin` directory before any
Git/MSYS GPG directory in PATH; the MSYS build uses Unix-style paths.
See the [GnuPG downloads](https://www.gnupg.org/download/) page.

```text
uv run python scripts/save_tokens.py
uv run python scripts/setup_environment.py
```

The save script asks for all three access tokens (GitHub, DigitalOcean, Cloudflare)
and a passphrase of at least 12 characters, entered twice. It writes only GPG
AES-256 ciphertext to `.local/tokens.gpg`, ignored by Git. No public/private GPG
keypair is needed. Replacing an existing file requires confirmation, defaulting
to no. The helper does not contact providers or validate token permissions.

On setup:
- No encrypted file: normal token prompts.
- File found: enter its passphrase to use saved tokens for this run.
- Incorrect passphrase: choose whether to retry, up to three attempts total.
- Three failures, declining retry, or a blank passphrase: normal token prompts.
- GPG unavailable: explain the fallback and use normal prompts.

Failed unlocks never overwrite or delete the file. Decrypted tokens stay in
process memory and are passed to the existing provider integrations; the
passphrase is neither stored nor included in command arguments. GPG passphrase
caching is disabled. Keep your passphrase in a password manager; if lost, run
the save script to replace the file with new token entries.

Both scripts accept `--plain` and `--tokens-file PATH`. Use the same custom path
with both. `--saved-login` overrides the cached GitHub token and uses the GitHub
CLI login instead; cached DigitalOcean/Cloudflare tokens are still used. Pulumi
continues to manage its own login and credential storage.

## Prompts and defaults

Tokens use hidden input and have no displayed default. An unlocked local token file
supplies them automatically; otherwise the wizard asks for them. Other prompts offer discovered or
saved defaults. Saved stack configuration takes precedence on reruns.

| Setting | First-run default |
| --- | --- |
| GitHub repository | `lambdawalker/devops.apexfisson.maven` |
| Pulumi backend | `https://api.pulumi.com`, or `PULUMI_BACKEND_URL` |
| Pulumi organization | Configured default if accessible, otherwise a discovered organization |
| Pulumi stack | `<organization>/apexfission-maven/production`; DIY backend: `production` |
| DigitalOcean project | The account's default project |
| Cluster | `apexfission-maven` |
| Region / worker / count | `nyc1` / `s-2vcpu-4gb` / `1` |
| Kubernetes version | Highest stable version offered by the DO API |
| Cloudflare zone | A zone accessible to the supplied token |
| Hostname | `maven.<zone>`, or saved hostname |
| ACME email | Suggested address on the chosen domain; enter an address you monitor |
| Confirmation | `no` |

Your Pulumi username and organization need not match. For example, login
`lambdawalker` can own the stack through organization `isdavid`. The wizard no
longer constructs an organization from the username.

Select an existing DigitalOcean project by the displayed name or ID. The project
selection is saved with the stack; changing your account's default later does not
silently move an established setup. The cluster is assigned through Pulumi.
DOKS-associated resources, especially PVC volumes created later by Kubernetes,
can still start in the account's default project. Inspect their placement in the
DO console; cluster assignment does not imply all related resources move.

## Tokens

- **GitHub:** repository administrator access. A fine-grained token needs this
  repository's Administration and Environments read/write permissions, with any
  required organization approval. Only the DigitalOcean deployment token is saved
  as a GitHub secret; GitHub's token is used from process memory and may optionally
  be stored in the local GPG file described above.
- **DigitalOcean:** Kubernetes read/create/update/access-cluster, projects read
  and assign-resource, load-balancer read, plus required dependent scopes shown
  by the token editor (such as regions, sizes and actions read). Existing legacy
  state can also require domain/certificate read. The default flow saves this DO
  token in GitHub; replace it with a deployment-only token afterward if desired.
  Kubernetes controllers create the gateway load balancer and later PVC volume.
- **Cloudflare:** create a scoped API token with **Zone:DNS:Edit** and
  **Zone:Zone:Read**, restricted to **apexfission.com** (or your selected zone).
  Do not use the Global API Key. It supports Pulumi's DNS record and cert-manager's
  temporary DNS challenge records. The zone must already exist and be active.
- **Pulumi:** login uses Pulumi's normal local credential storage. No Pulumi token
  needs to be added to the application deployment workflow.

The Cloudflare token is passed to Pulumi via its environment and stored as a
Kubernetes Secret for renewal, encrypted as a secret in Pulumi state. It is not
written to stack configuration, exported, printed in preview or saved to GitHub.
Cluster administrators and Pulumi state decryptors are privileged: they can access
these credentials. A token rotation requires rerunning setup so cert-manager gets
the new token. See [Cloudflare DNS verification](https://cert-manager.io/docs/configuration/acme/dns01/cloudflare/).

## Review and billing

Review the desired configuration and Pulumi preview; type `yes` to apply. Deletion
and replacement operations are refused. New resources are protected. Setup creates
**billable workers and a load balancer**, even before Reposilite is deployed.
Private application deployment later adds a billable persistent volume. One worker
accepts downtime; extra gateway/certificate controllers also consume node capacity.
Automatic and surge upgrades are enabled; surge can add temporary billable workers.

The DNS A record uses the gateway's actual load-balancer address and is DNS-only
(`proxied=false`). Nameservers and unrelated records stay at Cloudflare. Conflicting
hostname records stop setup. An A record not already owned by this stack also
stops setup: use an unused hostname or plan a separate DNS migration first.
The wizard never replaces an unrelated existing record.
Do not put credentials in command arguments, checked-in files or chat.

## Continue after setup

1. Follow [setup](setup.md) steps 1 (connect only), 2 and 3: install the private
   application, create a permanent administrator, remove bootstrap and verify login.
2. Dispatch **Deploy Reposilite** on `main`. It adds the hostname route, requests
   its certificate through cert-manager and waits for readiness.
3. Verify HTTPS, HTTP redirect, dashboard Console, upload and download externally.

The gateway may answer 404 before step 2; it has no Reposilite route then. DNS is
already managed by Pulumi; do not create another A record manually.

## Reruns and failed legacy setup

Always reuse the same backend and stack. See [Pulumi setup](pulumi.md) for migration,
state protection, import and diagnostics. Existing GitHub environment protection
rules are preserved. Partial updates are retained; no rollback is attempted.

For GitHub settings only, run:

```text
uv run python scripts/setup_environment.py --github-only
```

Choose `cloudflare` TLS mode for this architecture, or `digitalocean` for the
legacy load-balancer certificate overlay. This mode uses saved `gh` login by
default when no cached token is available; `--token-auth` requests a PAT and
`--saved-login` explicitly selects the CLI login. An unlocked cached DO token
replaces the existing secret; during manual entry a blank DO token keeps it.
It does not provision or validate infrastructure.
