# Setup UI and token storage implementation plan

Goal: finish the authorized UI and encrypted-token workflow without changing infrastructure ownership.
Spec: ../specs/2026-10-07-setup-ui-tokens.md

## Task 1: UI adapter
Files scripts/setup_ui.py (new), tests/test_setup_ui.py (new), pyproject.toml, uv.lock, requirements-dev.txt.
Interface: ask(label, default='')->str; hidden(prompt)->str; say(*objects, sep=' ', end='\n', flush=False, file=None); stage(key); skip(key); cancelled(message); terminal(callback)->result; run(callback, steps=None)->int.
Default backend is plain for direct function tests and --plain CLI. run launches Textual, installs adapter only for callback lifetime, and returns 0 success/1 failure/130 cancel after user closes result screen. Public SetupCancelled exception. Standard steps keys: tokens, github, pulumi, discovery, review, preview, provision, save. Stage transition completes prior active stage; skipped stages don't become completed; failures mark current only. Intro footer and progress represent steps completed, not remote work percentage. Headless pilot tests exercise masked prompt, default input, workers, status/progress, safe errors, cancel, nonsecret output and terminal handoff. No raw subprocess log capture.

## Task 2: GPG token store and integration
Files scripts/token_store.py, scripts/save_tokens.py, tests/test_setup_tokens.py (new); setup_environment.py/setup_cloud.py/pulumi_setup.py.
Schema version=1, tokens={github,digitalocean,cloudflare}, values single-line nonempty strings; passphrase never stored. load_tokens(path, ask, hidden, say)->dict uses max3 tries/fallback. encrypt/decrypt helpers real tested. Token prompts consume dictionary on args; --saved-login wins. Wrap existing prints/prompts via UI, stage milestones, suspend TUI for login. Decrypt before credentials, no plain cache files. Use --plain and --tokens-file flags. Keep existing test contracts and add integrations for flow fallback/no Cloudflare/loaded tokens.

## Task 3: Validation and docs
Update setup docs, example commands, GPG installation/optional nature, file handling, recovery, UI progress semantics. CI installs Textual test dependency and ensures GPG for offline Windows/Linux tests. Run full suite under uv, headless UI pilot and fake-token real GPG roundtrip, actionlint. Review security and cancellation, open PR based on latest main. No cloud deployment/real credentials.
