# Pulumi infrastructure implementation plan

Goal: replace direct DigitalOcean creation in the interactive Python wizard with
Pulumi while preserving prompts, generated UUID handoff, and application deployment.

Design: a Python project in infrastructure/ owns the DOKS cluster and optional
DNS zone/managed certificate. The existing HTTPS client becomes discovery-only.
A standard-library CLI adapter selects a shared backend/stack, stores nonsecret
configuration, previews a saved plan, rejects deletes/replacements, asks for final
confirmation, applies that exact plan, and returns only safe stack outputs.
The backend defaults to Pulumi Cloud using an existing `pulumi login`; a DIY
backend can be selected. Stack configuration stays in ignored .local/pulumi/.
The existing GitHub action continues to own all Kubernetes workloads/storage.

Imports: match unique existing resources by name and provider IDs; use domain
name, certificate name, and cluster UUID as import IDs. Freeze imported resource
inputs with ignoreChanges until the operator deliberately enables management.
Protect all owned resources. An existing custom certificate stays an external
reference, because its private key cannot be read back. A selected stack is the
source of truth on reruns: never turn its managed resources into external refs or
replace its configuration with newly discovered cloud defaults. Fail on identity
changes and absent resources already owned by the stack. Never manage the same
resources from multiple stacks.

New managed certificates precede the cluster via depends_on; the DO provider
waits for verified issuance. Pre-existing certificates must already be verified.
Tokens go only through child-process environment, not configuration or arguments;
mark cluster credential outputs secret and do not export kubeconfig. Existing
DO secret upload to GitHub still uses encrypted gh stdin. Provider failures may
leave resources and state; no automatic cleanup or rollback.

- [x] Test and implement resource graph, protected import IDs and exports using
      Pulumi mocks and pinned Python dependencies (no live cloud calls).
- [x] Test and implement CLI adapter: state identity/defaults, backend/stack
      selection, exact saved-plan preview/apply, forbidden operations, error
      redaction, tokens absent from files/arguments, cancellation before writes.
- [x] Integrate wizard, retain GitHub-only mode, make DO adapter reject mutations.
- [x] Update Windows/Linux CI and docs for install/login/state, imports,
      controlled updates, DNS/bootstrap boundaries and recovery.
- [x] Full offline suite, actionlint/schema validation, fresh code review, PR.

Validation: 45 offline tests pass; Pulumi CLI 3.268.0 local-backend configuration
and preview/saved-plan/apply smoke checks pass without cloud resources. Fresh
review identified the streaming-preview environment flag; regression added and
fixed. Auto-upgrade version drift is ignored deliberately. Live DigitalOcean
imports/provisioning remain an operator acceptance check, not a validation-CI task.
