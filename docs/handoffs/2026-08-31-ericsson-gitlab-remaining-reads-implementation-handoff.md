# Fresh-session handoff: remaining Ericsson GitLab read implementation

Copy the prompt below into a fresh session. It authorizes implementation and
the plans' local integration checkpoints, but not a push, pull request,
release, brand build, or publication.

---

You are beginning implementation of the independently reviewed remaining
read-only Ericsson GitLab connector coverage. Implement the three approved
plans in their required order. Never give up on the right solution.

## Authorization and boundaries

You are authorized to:

- implement the source/vendor reconciliation prerequisite and all tasks in the
  three plans listed below;
- create fresh isolated worktrees and feature branches;
- make the plans' atomic local commits; and
- locally integrate completed, reviewed slices into
  `ericsson-capabilities/main` and Hermes `base` where the plans require that
  checkpoint so the next slice can begin.

You are not authorized to push, open or merge a pull request, publish a
release, tag, build or update a brand branch, dispatch a remote workflow, or
change literal Hermes `main`. Webhook enumeration and every new write remain
excluded. Stop and ask rather than broadening scope.

## Required skills

Before any implementation action, fully read the repository instructions and
these skills:

1. `superpowers:using-superpowers`
2. `superpowers:using-git-worktrees`
3. `superpowers:subagent-driven-development` when subagents are available;
   otherwise `superpowers:executing-plans`
4. `superpowers:test-driven-development`
5. `superpowers:verification-before-completion`
6. `superpowers:requesting-code-review` at every slice checkpoint
7. `superpowers:finishing-a-development-branch` only after all three plans are
   implemented and verified

Announce the skills you use and follow them completely. Use subagents for
bounded independent tasks or sequential implementation tasks with explicit
file ownership. Never allow concurrent edits to overlapping files.

## Repositories and verified handoff state

### Hermes

Path:
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

- Development branch: `base`; literal `main` is synchronization-only.
- Verified local `base` head: `bd1d42fdc0df8ea0c3e9dad3c940f7aed196b4d7`.
- `origin/base` at handoff: `2f200a1db9fc97b528ab0c1d5eff533a89916f6e`.
- Local `base` is intentionally three commits ahead of `origin/base`. Do not
  reset or branch from `origin/base`; the approved specs, plans, and review
  package are in the local commits.
- Preserve all unrelated untracked paths, especially `.otto/`,
  `docs/assessments/`,
  `docs/design/2026-08-12-deferred-tool-dispatch-findings.md`,
  `docs/handoffs/2026-08-09-ericsson-gitlab-connector-implementation-handoff.md`,
  and `docs/plans/2026-08-12-deferred-tool-dispatch-reliability-plan.md`.

### Authoritative Ericsson source

Path:
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`

- Development branch: `main`.
- Verified clean `HEAD == origin/main`:
  `0d7654d14db0afe0c688a752a2676d8cabe2f981`.
- Connector, skills, CLI descriptors, mappings, onboarding sources, and
  workflow packages are authored here first. Hermes receives an exact clean
  committed vendor snapshot.

Recheck both repositories before doing anything. If either required branch has
moved, inspect the new commits and report any overlap; do not reset, discard,
or overwrite them.

## Existing worktrees are not implementation starting points

Historical worktrees already exist, including:

- source `.worktrees/ericsson-gitlab-connector`;
- source `.worktrees/gitlab-coverage`;
- source `.worktrees/gitlab-read-exploration`; and
- Hermes `.worktrees/ericsson-gitlab-connector`.

Do not reuse, clean, modify, remove, or assume ownership of them. Verify branch
and path absence, then create fresh uniquely named worktrees from the current
required branches. Task 0 may use
`feat/gitlab-source-vendor-reconcile-20260831` and
`.worktrees/gitlab-source-vendor-reconcile-20260831` in each repository if
those names are still absent. Later slices use their plan-defined fresh
worktree names only after the preceding SHAs are integrated.

## Frozen approved artifacts

Read all six artifacts and the reconciliation before implementation. Verify
their SHA-256 hashes. If any differs, stop and report the exact diff rather
than silently implementing altered instructions.

| Artifact | SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md` | `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md` | `e80f75ae8af2a8e64b6f37017fd732da3504ea84805248394460a0c24eba6a30` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md` | `3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md` | `c0fe22fb9ecdfa97bf3addadaaa69a3a3e4c97edc1325410c18b30b51c4af028` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md` | `8269698319f777f6e6ee808c9cd91e2a10bfc57b036124108de9f54b989263e7` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md` | `f50b88b603a54807b4cf8a8f8fa2c7ed789908135ce726e0f6642b9b424f0888` |

Review evidence:

- `docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-reconciliation.md`
  — `4e4260d63bf53617b17f078e1e24d74ccd94bdf650d3627fb399ee99c40cc74e`
- `docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-convergence-rereview-fable-5.md`
  — `25ca275d1d54c243cdd3610e6780024f22214ad433a04d6a702c32a288d07a22`
- `docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-convergence-rereview-codex-5-6-xhigh.md`
  — `091ca97d0312c315199f97835012692744b98277bc125c5067116e871c280e17`

Both final reviewers returned `PASS`. Do not redo general plan design or reopen
resolved stylistic findings. If current source disproves an implementation
premise, stop, cite the exact evidence, and ask before changing the plan.

## Required implementation order

1. **CI plan Task 0 — source/vendor reconciliation.** This is blocking. Do
   not begin a new GitLab read operation until currently shipped managed
   Ericsson content is restored to source authority and exact byte/inventory
   parity is proven.
2. **Remaining CI read-coverage plan.** Implement job metadata, pipeline jobs,
   MR pipelines, project CI-variable metadata, descriptors, docs, routing
   corpus, deterministic/live routing gates, and exact vendoring.
3. **Repository-discovery plan.** Implement branches, tags, project search,
   and project-scoped code search; extend the same routing corpus and harness.
4. **Release/inbox plan.** Implement releases, To-Dos, and backward-compatible
   project/global merge-request queues; extend the same routing machinery.

After each slice, complete its source verification and independent code review,
commit source, vendor the exact full SHA, complete Hermes verification and code
review, locally integrate to the required branch, and record the exact source
and Hermes SHAs for the next slice's ancestry gates.

## First concrete action

Begin with read-only verification and Task 0 inventory:

```bash
SOURCE_REPO=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities
HERMES_REPO=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent

git -C "$SOURCE_REPO" status --short --branch
git -C "$SOURCE_REPO" rev-parse HEAD
git -C "$SOURCE_REPO" rev-parse origin/main
git -C "$SOURCE_REPO" worktree list
git -C "$HERMES_REPO" status --short --branch
git -C "$HERMES_REPO" branch --show-current
git -C "$HERMES_REPO" rev-parse base
git -C "$HERMES_REPO" worktree list
```

Then verify the frozen hashes, create fresh reconciliation worktrees only after
checking their branch/path names are absent, and perform CI Task 0 Step 1
exactly. Inventory every manifest-managed add/change/delete before editing.
Read Hermes commits `7bc872341b` and `70caca680f` plus the originating history
for every other drifted path. The first implementation decision must identify
the corresponding authoritative source path or generator for each retained
Hermes artifact. Do not solve parity by deleting or reverting behavior already
shipped on `base`.

## Non-negotiable implementation contracts

- Follow strict RED → GREEN → refactor TDD for every production behavior.
- Use the source repository's `.venv/bin/python -m pytest` commands there.
- Run every Hermes Python test through `scripts/run_tests.sh`, never direct
  `pytest`.
- Keep `tests/hermes_cli/test_ericsson_vendor_parity.py` integration-marked.
  Every explicit invocation uses `-m integration` and supplies both
  `ERICSSON_CAPABILITIES_DIR` and `ERICSSON_CAPABILITIES_EXPECTED_SHA`.
- Extend the explicit `scripts/run_tests.sh` test-variable allowlist with only
  those two parity variables; never add a wildcard environment pass-through.
- Preserve one stable model tool array and one ordinary LLM/tool conversation.
  Do not add a classifier call, swap category toolsets, mutate the system
  prompt, inject synthetic messages, or break prompt caching.
- Add no core tool, dependency, generic GitLab query layer, repository clone,
  archive download, hidden LLM call, or surface-specific connector path.
- Return allowlisted, bounded, redacted projections only. CI-variable values,
  PATs, email, avatars, raw remote mappings, and untrusted remote instructions
  must never enter results, logs, warnings, snapshots, or routing records.
- Preserve backward compatibility of existing GitLab operations and CLI
  commands. Extend `gitlab_list_merge_requests`; do not create a duplicate
  global-MR tool.
- Generated migration Markdown and onboarding catalogs are rebuilt from their
  source authorities, never hand-edited.
- Tests assert behavioral relationships and inventories, not frozen total tool
  counts.
- Live routing evaluation uses explicit Claude and OpenAI model identifiers,
  stubbed handlers, approval interception, and no real GitLab I/O. Do not
  silently skip a required live gate when credentials/models are unavailable;
  report the missing prerequisite.
- Webhooks and every new mutation are out of scope.

## Stop conditions

Stop and ask for direction if:

- an approved artifact hash differs;
- a required branch moved with overlapping changes;
- Task 0 cannot preserve shipped Hermes behavior in source authority;
- a plan command fails repeatedly after root-cause investigation;
- model credentials required for the two-family routing gate are unavailable;
- implementation would require a new dependency, core tool, write, webhook,
  dynamic tool-array swap, or prompt-cache mutation; or
- local integration would overwrite work not owned by this effort.

## Completion handoff

Do not report completion from file presence or subagent claims. Before the
final handoff, run every plan's complete source and Hermes gates, exact managed
byte/SHA parity, deterministic routing tests, both-family live routing matrix,
`git diff --check`, and clean tracked-state checks in both repositories. Use
`superpowers:requesting-code-review`, remediate verified findings, then use
`superpowers:verification-before-completion` and
`superpowers:finishing-a-development-branch` to present the allowed local
integration state. Report exact commit SHAs, test counts, routing reports, and
any preserved unrelated state. Do not push or publish.
