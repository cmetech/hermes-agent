# Fresh-session handoff: Ericsson GitLab connector implementation

Copy the prompt below into a fresh context window. It grants implementation
authorization for the reviewed GitLab phase, but not merge, push, release, or
Windows UAT publication authority.

---

You are beginning implementation of Release 1 of the independently reviewed
Ericsson connector-plugin program: the disabled-by-default `ericsson-gitlab`
standalone plugin and the generic Hermes plugin configuration/readiness
foundation it requires.

Never give up on the right solution.

## Authorization and stopping boundary

You are explicitly authorized to implement **Tasks 1–14** of:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-09-ericsson-gitlab-connector.md`

Work task-by-task with strict TDD and make the plan's atomic local commits in
the correct feature worktrees. You are **not** authorized to execute Task 15.
Do not merge, rebase, push, build or publish a release, update branded branches,
tag, dispatch release workflows, or begin Jira. Stop after Tasks 1–14 have been
implemented, independently reviewed, remediated, and verified, then request
separate authorization for Task 15 and the v5.5.0 release/UAT gate.

Do not reinterpret planning approval as release authority.

## Required instructions and skills

Before any task action, fully read:

1. The root `AGENTS.md` in both active repositories.
2. `superpowers:using-superpowers`.
3. `superpowers:using-git-worktrees`.
4. `superpowers:subagent-driven-development` (preferred by the plan) or
   `superpowers:executing-plans`.
5. `superpowers:test-driven-development`.
6. `superpowers:verification-before-completion`.
7. `superpowers:requesting-code-review` before the final Tasks 1–14 handoff.
8. Any additional non-security skill required by those skills.

Announce the skills you use and follow them completely. If using subagents,
use them sequentially per task or for bounded independent read-only research;
do not allow concurrent implementation edits to overlapping files. Preserve
all parallel worktrees and user changes.

Per the approved design and plan, **skip standalone LLM threat-model,
security-audit, security-review, penetration-testing, and vulnerability-scan
skills/checks** because they trigger the platform safety gate. Do not weaken or
skip the plan's deterministic authentication, authorization, secret handling,
redaction, boundary, isolation, or negative-path tests. Ordinary functional
code review and specification-compliance review remain required.

## Authoritative repositories and baselines

### Hermes repository

Path:
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

- Development branch: `base`. Literal `main` is synchronization-only and must
  not be used for development.
- Independently reviewed production baseline:
  `786f8dc0175410044000113233bec2bb610e7733`.
- Current verified local `base` head at handoff:
  `ebb0b33db1e15f816cb25bd5f863385771e46a00`.
- `origin/base` at handoff:
  `786f8dc0175410044000113233bec2bb610e7733`.
- The single commit after the reviewed baseline is
  `ebb0b33db docs(desktop): design workflow kanban view alignment`; it changes
  only
  `docs/superpowers/specs/2026-08-09-workflow-kanban-view-alignment-design.md`
  and has no connector production overlap.
- Begin the Hermes feature worktree from the exact current verified local
  `base` head (`ebb0b33...`) after rechecking that no newer changes appeared.
  If `base` has moved again, inspect and report the new commits and their
  overlap before creating a worktree; do not reset or discard them.
- Preserved root state includes untracked `docs/assessments/` and connector
  review documents. Do not clean, stage, move, overwrite, or delete them.
- The reviewed `docs/superpowers/` artifacts are ignored/root-local and may not
  appear automatically in a linked worktree. Read them from the absolute root
  paths in this prompt and do not assume they were copied into the worktree.

### Ericsson source repository

Path:
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`

- Branch: `main`.
- Verified `HEAD == origin/main`:
  `dae405ede7049b621e502d9259f97481c940a65b`.
- Preserve these existing user modifications exactly:
  - `mcp/outlook-mcp/src/outlook_cli/__init__.py`
  - `plugins/ericsson-teams/graph_auth.py`
- Do not clean, stash, reset, stage, revert, or edit those root-checkout files.
  Implement only in a new linked worktree from the exact verified commit.

### Legacy LOOP24 repository

Read-only path:
`/Users/coreyellis/code/gitlab.rosetta.ericssondevops.com/loop_24`

- Verified legacy head:
  `fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6`.
- The accepted GitLab behavior snapshot is `8ca26f8`; `fc3bf26...` adds only
  documentation after that accepted connector snapshot.
- Never modify this repository. Fully inspect every legacy file required by
  GitLab Task 1 before implementing connector production behavior.

## Reviewed design, plans, and verdict

Read the design and all four plans for sequencing/compatibility context, then
execute only the GitLab plan. Verify these SHA-256 hashes before work:

| Artifact | SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-08-09-ericsson-connector-plugins-design.md` | `7f378086c722d35434fba5892349fe8438083779dcd3c8bc622ae278b8218b29` |
| `docs/superpowers/plans/2026-08-09-ericsson-gitlab-connector.md` | `b6fbd791514d36cad8448a148f6ba2d18953cc15a19fc46991e2e10944a105ea` |
| `docs/superpowers/plans/2026-08-09-ericsson-jira-connector.md` | `f1dce2669dddaac0e4d72080b827a8dddfdecdcfb22c6ef1bd0f7e5800926a79` |
| `docs/superpowers/plans/2026-08-09-ericsson-sharepoint-connector.md` | `524ff2f1fc174d2bc37ff9524dd999a86f9f70a441e48ab17a7315fea2d41e2b` |
| `docs/superpowers/plans/2026-08-09-ericsson-confluence-connector.md` | `ed3d495bf9f1086add01cad5ee8e58f28a21be2dfeef19d5cb01bad8b7eabd56` |

Read the final independent review completely:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/reviews/2026-08-09-ericsson-connector-plugins-adversarial-plan-final-rereview-fable-5.md`

Its SHA-256 at handoff is:
`cb60d10c02d120d734b1b25724f2b89bda717f978cedae2616160387adce4031`.

The final verdict is **READY FOR IMPLEMENTATION — no findings**. Do not redo
the completed plan review. Treat its three non-blocking implementation notes
as active checks:

1. The lifecycle migration authority belongs in the Ericsson capability
   manifest plugin object consumed by staging (`sets/ericsson.json` under the
   reviewed scheme). `plugin.yaml` remains the standalone plugin descriptor.
   Do not create duplicate authorities merely because Jira Task 2 has one
   imprecise sentence.
2. Preserve old store snapshots; prove any optional extension using the
   planned v1–v5 RED compatibility tests.
3. Add the inexpensive Gateway/API chat surface assertion if it fits the
   shared cross-surface suite, without creating a second integration path.

If any immutable artifact hash differs, stop before implementation and report
the exact diff. Do not silently implement against altered plans.

## Create fresh worktrees

At handoff, these branches and worktree paths were verified absent in both
repositories. Confirm again before creation.

Hermes:

- Branch: `feat/ericsson-gitlab-connector`
- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/ericsson-gitlab-connector`
- Base it on the exact verified local `base` head, currently `ebb0b33...`.

Ericsson source:

- Branch: `feat/ericsson-gitlab-connector`
- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/ericsson-gitlab-connector`
- Base it on exact verified `main` head `dae405e...`.

Use `git worktree add -b` only after read-only branch/path/ref checks. Keep both
root checkouts on their current branches and untouched. Do not reuse any
existing worktree.

## Execution contract

Follow the GitLab plan exactly, Tasks 1–14:

1. Freeze the source/vendor closure and complete legacy GitLab behavior map.
2. Reconcile the Ericsson manifest contract and disabled lifecycle.
3. Add the generic static plugin configuration descriptor parser.
4. Add profile-scoped settings, write-only secrets, readiness, and bounded
   setup actions.
5. Add generic HTTP APIs and Desktop configuration UI.
6. Preserve disabled standalone staging and implement the generic, one-time
   `auto_seeded_backend` lifecycle transition mechanism.
7. Project the same tool/service readiness snapshot into every production
   workflow admission, resume, and prelaunch path.
8. Port the direct-REST GitLab client, models, and bounded read operations.
9. Port bounded CI inspection without exposing variable values.
10. Add approval-aware GitLab writes.
11. Add source-owned plugin skills, discoverable thin router skill, example
    Archon workflows, onboarding, and documentation.
12. Prove chat, CLI/Desktop, Kanban, cron, Archon workflow, restart/disable,
    and installed-distribution behavior through the actual shared paths.
13. Close, review, verify, and commit the Ericsson source release.
14. Vendor that exact clean source commit into Hermes and close every Hermes
    regression/customization/installed-distribution gate.

For every production change:

- Run the plan's exact focused RED command first and record why it fails.
- Make the minimum GREEN implementation.
- Rerun the focused test, then the named neighboring/regression gates.
- Refactor only while green.
- Use `scripts/run_tests.sh` for Hermes tests and the Ericsson repository's
  planned `.venv/bin/python -m pytest` commands.
- Do not accept schema failures as intended admission REDs; compile fixtures
  successfully first where the plan requires it.
- Make atomic commits at the plan's checkpoints in the owning repository.
- Do not stage unrelated or preserved files.

Before changing an existing Hermes seam, verify the premise and original
intent against real code/Git history. Prefer extension of existing generic
infrastructure. Connector-owned logic belongs in `ericsson-capabilities` and
is vendored exactly; generic Hermes changes must contain no Ericsson connector
IDs or connector-specific branches.

## Non-negotiable product and compatibility boundaries

- All four standalone connectors are disabled by default for every new
  profile. Release 1 ships only GitLab functionality.
- Keep the existing `workflow` plugin enabled and keep the existing Ericsson
  Teams backend behavior unchanged.
- The generic migration mechanism is built/tested in GitLab Task 6, but the
  actual Jira migration declaration belongs to Release 2. Do not migrate Jira
  early.
- GitLab uses direct REST. Do not invoke `glab`, Git, repository clones, or a
  hidden LLM.
- Preserve legacy GitLab behavior unless its behavior map records an explicit,
  reviewed replacement or deferral. This includes duplicate-MR recovery,
  include traversal, `$ref` handling, slug/default-branch behavior,
  `edpctl`/mTLS defaults, binary/base64 handling, pagination, conflicts, and
  bounded CI behavior.
- Detailed connector skills are explicit-load-only. A thin source-owned router
  skill must remain discoverable to natural-language chat. Skills guide tools;
  they do not duplicate connector business logic.
- Tools must be reachable through the shared agent construction path from
  natural-language chat, Desktop/CLI, Kanban workers, cron prompts, and admitted
  Archon workflows. Do not build surface-specific connector implementations.
- Configuration must be profile-scoped and available through CLI and Desktop.
  Secrets are write-only and never returned. Non-secret settings belong in
  `config.yaml`; do not introduce user-facing non-secret `HERMES_*` variables.
- Enabling/disabling a plugin affects only fresh conversations. Never mutate a
  cached toolset or system prompt mid-conversation.
- Do not add a new core model tool, synthetic conversation messages, telemetry,
  or prompt-cache mutation.
- Preserve exact workflow `allowed_tools: []` semantics and flat Archon
  `requires: [ericsson-gitlab]` service vocabulary.
- Disabled or unready required connectors must block before workflow run
  creation and revalidate at resume/prelaunch using the same backend-authored
  readiness authority.
- Preserve old workflow snapshots, normalizers, REST mutation URLs, old-client
  action vocabulary, redaction, evidence bounds, and Phase 1–5 behavior.
- Any upstream-owned Hermes change must be generic, invariant-tested, and
  recorded in the applicable customization ledger, including:
  - `docs/upstream-customizations/plugin-configuration.yaml`
  - `docs/upstream-customizations/workflow-orchestration.yaml`
  - their documented README/index contract
- Run `python scripts/check_upstream_customizations.py` and
  `scripts/test_workflow_upstream_merge.sh` as specified.
- Do not implement Jira, SharePoint, Confluence, Workflow Phase 6
  `loop_group`, or unrelated cleanup.

## Source vendoring rule

After Ericsson Tasks are clean and committed, vendor from the exact clean
Ericsson feature-worktree commit using the real script contract:

```bash
ERICSSON_CAPABILITIES_DIR=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/ericsson-gitlab-connector \
  node scripts/vendor-ericsson.mjs
```

Do not use `SOURCE_REPO`; the script ignores it. Hard-fail if generated
`vendoredFrom` does not equal the exact Ericsson source HEAD. Preserve source
and vendored authorship/commit separation where practical.

## Completion and handoff requirements

Before claiming Tasks 1–14 complete:

1. Run every focused, neighboring, full, installed-distribution, Desktop,
   vendoring, customization-ledger, upstream-rehearsal, cache-stability,
   compatibility, and branded-release regression gate named in Tasks 1–14.
2. Obtain an independent specification-compliance and code-quality review that
   excludes standalone security/threat-model workflows.
3. Reproduce and remediate every Critical or Important functional finding
   test-first; rerun affected and closure gates.
4. Verify both feature worktrees are clean and list every local commit in
   ancestry order.
5. Verify the Ericsson vendored source SHA and byte closure exactly.
6. Verify no connector branch/worktree touched the preserved root changes.

Then stop before Task 15 and report:

- exact Hermes and Ericsson starting/final SHAs and branch/worktree paths;
- commits grouped by plan task and owning repository;
- legacy behavior dispositions and any deliberate differences;
- files/surfaces delivered;
- RED/GREEN and full gate command ledger with counts/results;
- independent review result and remediations;
- unresolved risks or UAT-only checks;
- exact root and feature-worktree `git status`, including preserved user files;
- the proposed Task 15 merge/release/UAT sequence;
- an explicit request for authorization to merge/push/build paired Desktop
  v5.5.0 releases and perform Windows UAT.

Do not proceed beyond that request without explicit user authorization.
