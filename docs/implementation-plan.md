# DOKS Reposilite implementation plan

Goal: provide a reviewable deployment project without provisioning paid resources.
Architecture and constraints: [design.md](design.md).
Execution: inline in an isolated checkout and feature branch.

## Tasks

- [x] Create the private Kustomize base and public TLS overlay. Validate both
  renders and verify single-writer, retained storage, non-root execution and
  secret separation invariants.
- [x] Document DOKS provisioning, private bootstrap, certificate/DNS setup,
  Gradle consumption/publication and offline backup/restore. Keep credentials
  and actual deployment configuration outside Git.
- [x] Add CI for rendering, Kubernetes schemas and safety invariants. Run local
  validation, review the branch and open a PR.

## Review focus

- Public overlay must remove the internal 8080 Service port, leaving only 80/443.
- Bootstrap credentials must not be committed or survive public deployment.
- Recreate updates and offline backup instructions must prevent concurrent writers.
- Non-root image startup needs writable data, log and temporary paths.
- Retaining a volume is not a backup; restoration must use a clean volume and
  include the SQLite database as well as artifacts.


## Verification record

- Five tests pass, including failure-injection tests for backup/restore control flow.
- Kubeconform validates all 11 resources; both Kustomize targets render.
- Documented local certificate override renders and Bash examples parse.
- Independent review found a maintenance fail-fast issue; fixed and regression-tested.
- No cloud resources provisioned. Live DOKS acceptance remains operator work.
