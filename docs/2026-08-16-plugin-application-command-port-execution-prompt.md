# Session 7 — Wave 4A: owner-bound plugin application command port

**Repo:** `hermes-agent` · **Wave:** 4A · **Must run after Ericsson Waves 2-3 and credential storage are merged**

> This is the host half of the approved Ericsson connector CLI design. Wave 4B in
> `ericsson-capabilities` consumes the public port created here. Do not start this session
> early: its preconditions deliberately prove the connector contracts and protected-secret
> authority are settled first.

Execute an implementation plan task by task.

**Repository:** `hermes-agent` (this repo)

**Plan file:** `docs/plans/2026-08-16-plugin-application-command-port.md`

**Scope:** all 6 tasks, nothing outside the plan.

## Before you start

Run every check from the `hermes-agent` repository root. Stop on the first failure.

```bash
git fetch origin
git rev-parse --abbrev-ref HEAD
# expect: base

git status --porcelain --untracked-files=no
# expect: no output

git rev-list --count origin/base..base
# expect: 0
git rev-list --count base..origin/base
# expect: 0

test -f docs/plans/2026-08-16-plugin-application-command-port.md
test -f hermes_cli/secret_keystore.py
test -f hermes_cli/plugin_configuration.py

git -C ../ericsson-capabilities fetch origin
test "$(git -C ../ericsson-capabilities rev-list --count origin/main..main)" = 0
test "$(git -C ../ericsson-capabilities rev-list --count main..origin/main)" = 0
test -d ../ericsson-capabilities/plugins/ericsson-confluence
test -d ../ericsson-capabilities/plugins/ericsson-arm
git -C ../ericsson-capabilities grep -q 'gitlab_retry_pipeline' main -- plugins/ericsson-gitlab
git -C ../ericsson-capabilities grep -q 'jira_link_issues' main -- plugins/ericsson-jira
git -C ../ericsson-capabilities grep -q 'confluence_update_page' main -- plugins/ericsson-confluence
git -C ../ericsson-capabilities grep -q 'arm_deploy' main -- plugins/ericsson-arm
```

The relative sibling checkout is a precondition convenience, not an implementation dependency. If this environment stores the two repositories elsewhere, locate the `ericsson-capabilities` checkout by repository name/remote and run the same read-only checks there. Do not weaken or skip them.

Then establish the Hermes baseline:

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_plugins.py \
  tests/hermes_cli/test_plugin_cli_registration.py \
  tests/hermes_cli/test_plugin_tool_admission.py \
  tests/hermes_cli/test_startup_plugin_gating.py -q
```

Expected: PASS. If the runner cannot find the repository venv, set `HERMES_PYTHON` to this checkout's existing Python with pytest and rerun the exact command. That is environment setup, not a plan change.

Only after every check passes, create the branch:

```bash
git checkout -b feat/hermes-plugin-application-command-port base
```

## How to execute

Read the plan in full. Use `superpowers:subagent-driven-development` with a fresh implementer and review checkpoint per task, or `superpowers:executing-plans` for inline batches. Follow every red-green-refactor and commit step. Run only `scripts/run_tests.sh`, never pytest directly.

## Non-negotiable guardrails

- This is a generic Hermes host feature. No Ericsson or brand-specific behavior belongs here.
- Do not modify, alias, expose, or fabricate `PluginToolAdmission` for shell commands.
- Do not route through `tools.registry.dispatch`, model middleware, or `pre_tool_call`.
- Provider and caller identities come from their `PluginContext`; plugin code cannot assert either identity.
- Writes accept only explicit `dry_run` or `confirm` modes; reads accept only `read`.
- Never cache connector configuration, secrets, clients, or capability fingerprints.
- Do not import any Ericsson connector implementation into Hermes core.
- Stop if the current code invalidates a plan premise. Report evidence rather than forcing the design through.

## Definition of done

- all 6 tasks are complete in focused commits;
- focused tests and the full Hermes suite pass through `scripts/run_tests.sh`;
- branch `feat/hermes-plugin-application-command-port` is pushed;
- report API, tests, commits, deviations, and model-admission invariance.

**Do not merge to `base`, vendor Ericsson content, touch brand branches, or start Wave 4B.** Report back for review and merge authorization.
