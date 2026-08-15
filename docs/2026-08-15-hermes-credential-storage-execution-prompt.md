# Session 1 — Plan 1: Hermes credential storage parity

**Repo:** `hermes-agent` · **Wave:** 1 (independent — may run at any time, in parallel with everything else)

> Part of a six-session programme spanning two repos. The other five sessions, the gating
> diagram and the rationale for the wave structure live in
> `ericsson-capabilities:docs/superpowers/prompts/README.md`. This session has no
> dependency on any of them — that pointer is for context only.

---

Execute an implementation plan, task by task.

**Repository:** `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

**Plan file:** `docs/plans/2026-08-15-hermes-credential-storage-parity.md`

**Scope:** All 9 tasks. Nothing outside this plan.

## Before you start

Verify the starting point and stop if any check fails:

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
git rev-parse --abbrev-ref HEAD          # expect: base
git status --porcelain --untracked-files=no | wc -l   # expect: 0
git rev-list --count origin/base..base   # expect: 0
ls docs/plans/2026-08-15-hermes-credential-storage-parity.md
```

Then create the working branch off `base`:

```bash
git checkout -b feat/hermes-credential-storage-parity base
```

## How to execute

Read the plan file in full first. Then use the `superpowers:subagent-driven-development`
skill to work through it — a fresh subagent per task with a review checkpoint between
tasks. If you would rather run inline with batched checkpoints, use
`superpowers:executing-plans` instead. The plan's own header names both.

Follow the plan's TDD cycle exactly for every task: write the failing test, run it and
confirm it fails for the stated reason, implement, run it and confirm it passes, commit.
Do not skip the "confirm it fails" step — a test that passes before the implementation
exists is testing nothing.

## Guardrails specific to this repo

- **Test runner is `scripts/run_tests.sh` ONLY.** Never call `pytest` directly.
  `AGENTS.md:1339` is explicit about this.
- `base` is this fork's development trunk. Literal `main` is synchronisation-only —
  never commit, branch from, or target it.
- Read the plan's own **Global Constraints** section before Task 1 and treat every bullet
  as a requirement of every task. Several are load-bearing security properties: no plugin
  secret ever enters `os.environ`, no silent plaintext fallback, file modes `0700`/`0600`.
- Do not change `save_env_value` or `load_env` semantics for any key other than
  `HERMES_PLUGIN_*` — 556 test files depend on the current behaviour.

## Definition of done

- All 9 tasks complete, each with its own commit
- Full suite green via `scripts/run_tests.sh`
- Branch `feat/hermes-credential-storage-parity` pushed
- Report: what landed, anything you deviated from and why, anything the plan got wrong

**Do not merge to `base` and do not vendor anything.** Report back and let me decide.

## If the plan is wrong

These plans were derived by reverse-engineering a binary and reading the existing code.
If a task's premise does not match what you find, stop and say so rather than forcing the
plan through. A plan defect found in Task 3 is much cheaper than one discovered in Task 9.
