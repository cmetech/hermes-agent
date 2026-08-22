---
name: otto-upstream-merge
description: Use when assessing, rehearsing, or performing an upstream Hermes release merge into base and then regenerating branded branches without losing fork behavior.
---

# OTTO upstream merge

Preserve full upstream ancestry and every verified fork invariant. Never use
conflict difficulty, deadline pressure, or apparent irrelevance as a reason to
drop an upstream commit.

## Required context

Before changing Git state, read:

1. the repository `AGENTS.md` plus any scoped `AGENTS.md` files;
2. `../CLAUDE.md` from the repository root for the branding/customization
   surface;
3. `docs/upstream-customizations/README.md` and every applicable manifest;
4. [procedure.md](references/procedure.md) and
   [disposition-review.md](references/disposition-review.md) completely.

In this fork, `base` is the development main. Literal `main` is sync-only.
Always leave the working checkout on `base`.
If the legacy `CLAUDE.md` workflow conflicts with `AGENTS.md` or this skill,
follow `AGENTS.md` and this skill; use `CLAUDE.md` only for its fork surface and
verification contracts.

## Non-negotiable controls

- Pin the requested upstream release to an immutable commit in
  `UPSTREAM_TARGET`. Never merge the moving `origin/main` ref. Verify the
  release version from that commit's `pyproject.toml`.
- Use `origin/main` only to report whether later upstream commits exist.
- Run the preflight against every
  committed `docs/upstream-customizations/*.yaml` manifest, not a hand-picked
  subset. Uncommitted merge-control files block the rehearsal.
- Rehearse in an isolated detached worktree. Do not stash, clean, or alter the
  user's live working tree.
- Stop after the trial report for explicit user approval before a real merge.
- Do not cherry-pick around, revert, or omit upstream commits. Reconcile
  behavior at the conflicting surface.
- Whole-file `ours`/`theirs` resolutions are forbidden for shared or
  ledger-owned source files. Exact generator-owned outputs may be regenerated
  from the tested neutral base, and binary brand overlays may retain verified
  brand bytes, as documented in `../CLAUDE.md`.
- A clean textual merge is not proof that fork behavior survived.
- An unclear applicability or behavioral relationship is a merge blocker.
- Every ledger baseline must be an ancestor of the pinned upstream target;
  divergent ranges are invalid evidence.
- A release is incomplete until the live checkout is back on `base`.

## Decision rule

Classify each meaningful overlap as `take-upstream`, `union-adapt`,
`remove-downstream-as-equivalent`, `retain-downstream-behavior`, or
`blocked-unclear`. `retain-downstream-behavior` maps to the existing machine
decision `adapt`; it does not authorize excluding the upstream commit.

Record importance, applicability, relationship, evidence, tests, residual
risk, and revisit condition in the durable merge report. Difficulty alone is
never evidence.

## Current assessed target

For upstream Hermes v0.20.5, the verified release commit is
`fcbd1076a93841fa88855acce810e342a5b78101`. Re-verify it at execution time;
do not substitute the newer `origin/main` tip.
