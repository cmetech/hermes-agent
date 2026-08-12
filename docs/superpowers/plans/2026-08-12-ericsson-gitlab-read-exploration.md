# Ericsson GitLab Read Exploration Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to
> implement this plan task-by-task, with `superpowers:test-driven-development`
> for every production change and `superpowers:verification-before-completion`
> before reporting success.

**Goal:** Add bounded recursive GitLab group/project discovery, commit and
merge-request exploration, review discussions, and natural-language daily
digests that work consistently in chat, Kanban, and cron.

**Architecture:** Implement all remote behavior in the standalone
`ericsson-gitlab` plugin in `ericsson-capabilities`, using focused read tools
over the existing bounded REST client and pagination machinery. Improve
plugin-owned skills and the always-indexed Ericsson GitLab router for natural
intent selection. Commit and verify the source repository first, then vendor
that exact clean revision into Hermes `base` and add only generic
cross-surface/parity tests in Hermes.

**Tech stack:** Python 3.11+, `httpx`, `respx`, `pytest`, Hermes standalone
plugin APIs, Markdown/XML-structured `SKILL.md`, Node.js vendoring script, and
Hermes cron/Kanban profile isolation.

**Approved design:**
`docs/superpowers/specs/2026-08-12-ericsson-gitlab-read-exploration-design.md`

---

## Repository rules

- Do not edit the dirty `ericsson-capabilities/main` checkout. It contains
  unrelated user changes in Outlook and Teams files.
- Branch the source work from commit `922a162e21ac88498cdd918d78132bea76704d4d`
  (`feat/ericsson-gitlab-connector`) in a fresh linked worktree.
- Do not alter or clean the existing source worktree's untracked `.venv`
  symlink.
- Use the root source virtual environment directly rather than creating a
  second environment.
- Create a Hermes linked worktree from the approved-plan commit on `base` and
  make all vendoring/test changes on its feature branch. Fast-forward that
  branch into `base` only after verification.
- Make source commits in `ericsson-capabilities` before vendoring.
- Vendor only from a clean source commit by setting
  `ERICSSON_CAPABILITIES_DIR` explicitly.
- Commit shared vendored content only on Hermes `base`; literal `main` and
  brand branches are out of scope.
- Preserve every unrelated untracked file currently present in Hermes.

## Task 1: Create isolated source and Hermes worktrees and prove the baselines

**Files:** No production changes.

**Step 1: Create the worktree**

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities
git worktree add \
  .worktrees/gitlab-read-exploration \
  -b feat/gitlab-read-exploration \
  922a162e21ac88498cdd918d78132bea76704d4d
```

Expected: the new worktree reports branch `feat/gitlab-read-exploration`, and
the dirty root checkout is unchanged.

**Step 2: Create the Hermes worktree**

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
git worktree add \
  .worktrees/gitlab-read-exploration \
  -b feat/gitlab-read-exploration \
  60db0056d
```

Expected: the worktree starts from the committed approved design and plan;
the main checkout remains on `base` with all unrelated untracked files
untouched.

**Step 3: Record the worktree paths**

```bash
SOURCE_WT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/gitlab-read-exploration
SOURCE_PY=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.venv/bin/python
HERMES_WT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/gitlab-read-exploration
git -C "$SOURCE_WT" status --short --branch
git -C "$HERMES_WT" status --short --branch
```

Expected: both worktrees are clean.

**Step 4: Run the focused source baseline**

```bash
cd "$SOURCE_WT"
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_client.py \
  tests/test_gitlab_plugin.py \
  tests/test_gitlab_reads.py \
  tests/test_gitlab_skills.py
```

Expected: PASS before any new tests are added. If it fails, stop and diagnose
the baseline rather than changing production behavior opportunistically.

**Step 5: Run the focused Hermes baseline**

```bash
cd "$HERMES_WT"
./.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_ericsson_connector_surfaces.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/hermes_cli/test_kanban_worker_spawn_toolsets.py \
  tests/cron/test_cron_profile_isolation.py
```

Expected: PASS before vendoring or cross-surface test changes.

## Task 2: Add recursive group and project discovery

**Files:**

- Create: `tests/test_gitlab_exploration.py`
- Modify: `plugins/ericsson-gitlab/operations.py`
- Modify: `plugins/ericsson-gitlab/tools.py`
- Modify: `plugins/ericsson-gitlab/plugin.yaml`
- Modify: `tests/test_gitlab_plugin.py`

**Step 1: Write failing group-discovery tests**

Add tests that prove:

- `sd-macs-att-rnam-hosting` is encoded as one group identifier;
- a same-origin group URL resolves, while a foreign URL and project-style URL
  are rejected;
- the root group and descendant groups are normalized with canonical URLs;
- `include_subgroups=true`, `with_shared=false`, and archived filtering are
  sent correctly;
- empty subgroups remain in the hierarchy;
- projects are associated with their owning namespace;
- shared and archived projects are excluded by default and identified when
  explicitly included;
- group and project pagination have independent source-labelled continuation;
- cross-origin URLs, malformed parent IDs, invalid namespace shapes, and
  unexpected scalar/list responses fail as `invalid_remote_data`; and
- the combined operation uses one aggregate deadline.

Use `respx` fixtures only; do not access a live GitLab instance.

**Step 2: Run the new tests and confirm RED**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_exploration.py -k group
```

Expected: failures because `gitlab_list_group_projects` and its operation do
not exist.

**Step 3: Implement the narrow group reference and response helpers**

In `operations.py`:

- add bounded group constants;
- add `_parse_group_reference()` and `_group_endpoint()` without weakening
  `_project_endpoint()`;
- add strict group, namespace, and project normalizers;
- reuse `_same_origin_url()` / `_canonical_remote_url()`;
- extend pagination so callers can start from an explicit page/offset returned
  by the existing continuation contract; and
- add `list_group_projects()` that returns root group, visible subgroups,
  projects, warnings, truncation, and source-labelled continuations.

Do not infer invisible groups. Do not claim completeness after any bounded
collection truncates.

**Step 4: Register the tool contract**

In `tools.py`, add `_GROUP`, continuation fields, and the
`gitlab_list_group_projects` schema. Dispatch it to
`GitLabOperations.list_group_projects()` with defaults:

- `recursive=True`;
- `include_shared=False`;
- `include_archived=False`;
- bounded `max_groups` and `max_projects`.

Add the tool to `plugin.yaml` and update plugin tests to assert schema,
registration, dispatch, and absence of PAT/certificate arguments.

**Step 5: Run the focused tests and confirm GREEN**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_exploration.py -k group \
  tests/test_gitlab_plugin.py
```

**Step 6: Commit the slice**

```bash
git add plugins/ericsson-gitlab tests/test_gitlab_exploration.py tests/test_gitlab_plugin.py
git diff --cached --check
git commit -m "feat(gitlab): add recursive group project discovery"
```

## Task 3: Add recent commit listing and detail

**Files:**

- Modify: `tests/test_gitlab_exploration.py`
- Modify: `plugins/ericsson-gitlab/operations.py`
- Modify: `plugins/ericsson-gitlab/tools.py`
- Modify: `plugins/ericsson-gitlab/plugin.yaml`
- Modify: `tests/test_gitlab_plugin.py`

**Step 1: Write failing commit tests**

Cover:

- latest-first commit listing for an explicit ref;
- canonical project resolution and default-ref selection when `ref` is absent;
- path, RFC 3339 `since`, and `until` propagation;
- `lookback_hours=24` using an injected UTC wall clock;
- rejection when `lookback_hours` and `since` are both supplied;
- invalid or timezone-naive timestamps;
- normalized full/short SHA, full message, title, display names, dates,
  parents, and canonical URL;
- deliberate omission of author/committer email;
- pagination and continuation despite the Commits API omitting total headers;
- single-commit detail and bounded stats; and
- malformed SHAs, dates, parents, stats, and foreign URLs.

**Step 2: Confirm RED**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_exploration.py -k commit_history
```

**Step 3: Add reusable time and commit normalization**

In `operations.py`:

- let `GitLabOperations` accept an optional `now: Callable[[], datetime]`;
- keep the transport monotonic clock separate from wall time;
- validate aware RFC 3339 values and serialize UTC consistently;
- add one strict commit normalizer reused by list, detail, and MR commit tools;
- add `list_commits()` using `_paginate()`; and
- add `read_commit()` with bounded stats.

In `tools.py`, keep `now` separate from `GitLabClient` keyword arguments in
`operations_from_configuration()` so tests can inject wall time without
passing an unknown client option.

**Step 4: Add and dispatch schemas**

Register `gitlab_list_commits` and `gitlab_read_commit`. Ensure descriptions
mention commit history rather than pipelines and make all limits explicit.
Update `plugin.yaml` and exact-tool plugin tests.

**Step 5: Confirm GREEN**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_exploration.py -k 'commit_history or read_commit' \
  tests/test_gitlab_plugin.py
```

**Step 6: Commit**

```bash
git add plugins/ericsson-gitlab tests/test_gitlab_exploration.py tests/test_gitlab_plugin.py
git diff --cached --check
git commit -m "feat(gitlab): add bounded commit history reads"
```

## Task 4: Add commit comments and discussions

**Files:**

- Modify: `tests/test_gitlab_exploration.py`
- Modify: `plugins/ericsson-gitlab/operations.py`
- Modify: `plugins/ericsson-gitlab/tools.py`
- Modify: `plugins/ericsson-gitlab/plugin.yaml`
- Modify: `tests/test_gitlab_plugin.py`

**Step 1: Write failing feedback tests**

Cover:

- ordinary commit comments with body, author display identity, timestamp,
  optional path/line/line type, and no email/avatar;
- commit discussions with discussion ID, `individual_note`, bounded notes,
  system/resolution state, and bounded diff position;
- separate maximums for discussions and notes;
- truncated outer discussions and truncated notes are reported independently;
- pagination and continuation;
- full SHA/branch/tag encoding; and
- malformed authors, booleans, line metadata, positions, and oversized note
  bodies fail safely.

**Step 2: Confirm RED**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_exploration.py -k 'comment or discussion'
```

**Step 3: Implement shared safe note normalization**

Add helpers for display-safe users, bounded notes, optional diff positions,
and discussion normalization. Implement:

- `list_commit_comments()`; and
- `list_commit_discussions()`.

Never expose email, avatar, PAT, certificate path content, or raw remote error
bodies.

**Step 4: Register and dispatch the tools**

Add `gitlab_list_commit_comments` and
`gitlab_list_commit_discussions` schemas, manifest entries, and plugin tests.

**Step 5: Confirm GREEN and commit**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_exploration.py -k 'comment or discussion' \
  tests/test_gitlab_plugin.py
git add plugins/ericsson-gitlab tests/test_gitlab_exploration.py tests/test_gitlab_plugin.py
git diff --cached --check
git commit -m "feat(gitlab): read commit comments and discussions"
```

## Task 5: Add merge-request discovery, commits, and discussions

**Files:**

- Modify: `tests/test_gitlab_exploration.py`
- Modify: `plugins/ericsson-gitlab/operations.py`
- Modify: `plugins/ericsson-gitlab/tools.py`
- Modify: `plugins/ericsson-gitlab/plugin.yaml`
- Modify: `tests/test_gitlab_plugin.py`
- Modify only if required by a reproduced API-version failure:
  `tests/test_gitlab_reads.py`

**Step 1: Write failing MR exploration tests**

Cover:

- open/all/closed/merged state filters;
- source branch, target branch, bounded search, deterministic ordering, and
  pagination;
- `lookback_hours` mapping to `created_after` for “new” MRs;
- explicit `updated_after` for “recently active” MRs;
- mutual exclusion of ambiguous time-window combinations;
- normalized IID, title, state, draft, branches, display-safe author,
  timestamps, labels, note count, discussion-resolution summary, and canonical
  URL;
- MR commit listing reusing the commit normalizer;
- MR discussion listing reusing bounded note/discussion normalization;
- unresolved and resolved diff threads;
- malformed state, IID, timestamps, labels, authors, URLs, and nested notes;
  and
- 401/403/404/same-origin behavior through the fixed safe error taxonomy.

**Step 2: Confirm RED**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_exploration.py -k merge_request
```

**Step 3: Implement the three MR read operations**

Add:

- `list_merge_requests()`;
- `list_merge_request_commits()`; and
- `list_merge_request_discussions()`.

Do not enable expensive merge-status rechecks by default. Do not change the
existing `gitlab_read_merge_request` contract. Migrate its diff transport only
if a deterministic test proves the currently targeted GitLab API rejects the
existing endpoint.

**Step 4: Register and dispatch schemas**

Add `gitlab_list_merge_requests`, `gitlab_list_merge_request_commits`, and
`gitlab_list_merge_request_discussions` to schemas and `plugin.yaml`. Update
exact registration and invocation tests.

**Step 5: Confirm GREEN and run all connector reads**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_exploration.py \
  tests/test_gitlab_reads.py \
  tests/test_gitlab_plugin.py
```

**Step 6: Commit**

```bash
git add plugins/ericsson-gitlab tests/test_gitlab_exploration.py tests/test_gitlab_plugin.py tests/test_gitlab_reads.py
git diff --cached --check
git commit -m "feat(gitlab): add merge request exploration"
```

## Task 6: Improve natural-language routing and activity-digest guidance

**Files:**

- Modify: `plugins/ericsson-gitlab/__init__.py`
- Modify: `plugins/ericsson-gitlab/skills/repository-research/SKILL.md`
- Modify: `plugins/ericsson-gitlab/skills/merge-request-review/SKILL.md`
- Create: `plugins/ericsson-gitlab/skills/gitlab-activity-digest/SKILL.md`
- Modify: `skills/ericsson/gitlab/SKILL.md`
- Modify: `skills/ericsson/onboard-ericsson-capabilities/references/capabilities/gitlab-tools.md`
- Modify: `docs/connector-porting/gitlab-baseline.md`
- Regenerate: `skills/ericsson/onboard-ericsson-capabilities/references/catalog.json`
- Modify: `tests/test_gitlab_skills.py`
- Modify: `tests/test_onboarding_catalog.py`
- Modify if required by existing contracts: `tests/test_onboarding_docs.py`

**Step 1: Write failing skill behavior tests**

Assert relationships, not prose snapshots:

- repository research declares the group, commit, comment, and discussion read
  tools and says pipelines are not commit history;
- merge-request review declares discovery, commit, detail, and discussion
  tools and distinguishes created from updated activity;
- the new activity skill has a natural-language `Use when` description that
  covers one-time and recurring commit/MR digests;
- the activity skill declares only GitLab read tools plus the local `cronjob`
  scheduling action;
- interactive scheduling instructions require a self-contained project,
  rolling window, qualified skill, connector toolset, and origin delivery;
- scheduled execution forbids recursive scheduling and uses exact `[SILENT]`
  behavior when there is no activity;
- the always-indexed router names all four qualified plugin skills and includes
  group exploration, latest commit, recent MR, and scheduled digest intents;
- all tool names referenced by skills exist in `SCHEMAS`; and
- the plugin registers exactly the skill set derived by contract rather than a
  frozen numeric count.

**Step 2: Confirm RED**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_skills.py
```

**Step 3: Implement the skill changes**

Keep each skill XML-structured, under the existing size bound, read-only with
respect to GitLab, and free of credentials or transport code. The activity
skill may instruct an explicitly requested local cron mutation but must never
perform GitLab writes.

Update `_PLUGIN_SKILLS` in `__init__.py` with a discovery-oriented description
for `gitlab-activity-digest`.

Update the onboarding reference and baseline so configuration, PAT/mTLS,
Cloudflare failure expectations, natural prompts, tool output, truncation, and
cron/Kanban behavior are truthful.

**Step 4: Regenerate and validate onboarding**

```bash
"$SOURCE_PY" skills/ericsson/onboard-ericsson-capabilities/scripts/build_catalog.py
"$SOURCE_PY" skills/ericsson/onboard-ericsson-capabilities/scripts/build_catalog.py --check
"$SOURCE_PY" skills/ericsson/onboard-ericsson-capabilities/scripts/validate_catalog.py
```

**Step 5: Confirm GREEN**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_skills.py \
  tests/test_onboarding_catalog.py \
  tests/test_onboarding_docs.py
```

**Step 6: Commit**

```bash
git add \
  plugins/ericsson-gitlab \
  skills/ericsson/gitlab/SKILL.md \
  skills/ericsson/onboard-ericsson-capabilities/references/capabilities/gitlab-tools.md \
  skills/ericsson/onboard-ericsson-capabilities/references/catalog.json \
  docs/connector-porting/gitlab-baseline.md \
  tests/test_gitlab_skills.py \
  tests/test_onboarding_catalog.py \
  tests/test_onboarding_docs.py
git diff --cached --check
git commit -m "feat(gitlab): guide natural activity exploration"
```

## Task 7: Close and verify the authoritative source revision

**Files:** Source repository only.

**Step 1: Run all GitLab tests**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_client.py \
  tests/test_gitlab_plugin.py \
  tests/test_gitlab_reads.py \
  tests/test_gitlab_exploration.py \
  tests/test_gitlab_ci.py \
  tests/test_gitlab_writes.py \
  tests/test_gitlab_skills.py \
  tests/test_gitlab_workflows.py
```

**Step 2: Run the complete source gate**

```bash
"$SOURCE_PY" -m pytest -q
```

**Step 3: Verify source integrity**

```bash
git diff --check
git status --short
git log --oneline --decorate -n 8
git rev-parse HEAD
```

Expected: clean source worktree. Record the full SHA as `SOURCE_SHA`. If a
test-only correction is needed, commit it in source before continuing.

## Task 8: Vendor the exact source and add Hermes cross-surface contracts

**Files:**

- Vendored: `plugins/ericsson-gitlab/**`
- Vendored: `skills/ericsson/gitlab/SKILL.md`
- Vendored/generated capability metadata updated by
  `scripts/vendor-ericsson.mjs`
- Modify: `tests/hermes_cli/test_ericsson_connector_surfaces.py`
- Modify: `tests/hermes_cli/test_ericsson_connector_distribution.py`
- Modify: `tests/hermes_cli/test_kanban_worker_spawn_toolsets.py`
- Modify: `tests/cron/test_cron_profile_isolation.py`
- Create: `tests/cron/test_ericsson_gitlab_activity_digest.py`
- Modify if required by current installed-package contract:
  `tests/plugins/workflow/test_installed_distribution_e2e.py`

**Step 1: Write failing Hermes contract tests before vendoring**

Run every step in this task from `HERMES_WT`, not from the main `base`
checkout.

Update expected tool/skill sets to derive the new focused reads and activity
skill. Add tests that prove:

- a fresh enabled plugin surface exposes every new deferred tool while a
  previously created surface remains unchanged;
- all qualified plugin skills load only when the plugin is enabled;
- a Kanban worker command keeps the executing profile's
  `ericsson-gitlab` toolset and forwards an explicitly attached qualified
  activity skill through its own `--skills` argument;
- a cron job storing the qualified activity skill and self-contained rolling
  prompt builds a future prompt containing the skill instructions;
- the future cron `AIAgent` gets the profile's connector toolset;
- the activity prompt preserves exact `[SILENT]` behavior and does not call
  `cronjob` during scheduled execution; and
- authentication/configuration failure remains safely categorized without
  PAT, PEM, or remote-body leakage.

Use temp profile homes and mocked connector HTTP. Never touch the user's real
cron store or Kanban board.

**Step 2: Confirm the new expectations are RED against the old vendor**

```bash
./.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_ericsson_connector_surfaces.py \
  tests/hermes_cli/test_kanban_worker_spawn_toolsets.py \
  tests/cron/test_cron_profile_isolation.py \
  tests/cron/test_ericsson_gitlab_activity_digest.py
```

Expected: failures for missing tools/skill and digest behavior.

**Step 3: Vendor only from the clean source worktree**

```bash
SOURCE_WT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/gitlab-read-exploration
SOURCE_SHA=$(git -C "$SOURCE_WT" rev-parse HEAD)
test -z "$(git -C "$SOURCE_WT" status --porcelain)"
ERICSSON_CAPABILITIES_DIR="$SOURCE_WT" node scripts/vendor-ericsson.mjs
```

Verify the vendored manifest records `SOURCE_SHA` and that no unrelated
capability was changed unexpectedly.

**Step 4: Complete the generic Hermes tests**

Implement only test/support changes necessary to prove existing generic
Kanban, cron, ACP/deferred-tool, and distribution behavior. Do not add
GitLab-specific branching to core runtime code unless a failing integration
test identifies a real generic defect.

**Step 5: Run the focused Hermes suite**

```bash
./.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_ericsson_connector_surfaces.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/hermes_cli/test_kanban_worker_spawn_toolsets.py \
  tests/cron/test_cron_profile_isolation.py \
  tests/cron/test_ericsson_gitlab_activity_digest.py \
  tests/plugins/workflow/test_installed_distribution_e2e.py
```

**Step 6: Prove source/vendor byte parity**

```bash
diff -ru --exclude='__pycache__' \
  "$SOURCE_WT/plugins/ericsson-gitlab" \
  plugins/ericsson-gitlab
diff -u \
  "$SOURCE_WT/skills/ericsson/gitlab/SKILL.md" \
  skills/ericsson/gitlab/SKILL.md
```

**Step 7: Commit the Hermes slice**

```bash
git add \
  plugins/ericsson-gitlab \
  skills/ericsson/gitlab/SKILL.md \
  capabilities \
  tests/hermes_cli/test_ericsson_connector_surfaces.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/hermes_cli/test_kanban_worker_spawn_toolsets.py \
  tests/cron/test_cron_profile_isolation.py \
  tests/cron/test_ericsson_gitlab_activity_digest.py \
  tests/plugins/workflow/test_installed_distribution_e2e.py
git diff --cached --check
git commit -m "feat(gitlab): add read activity exploration"
```

Stage only paths that actually changed. Do not stage unrelated untracked
assessment, handoff, review, or `.otto` files.

## Task 9: Final regression and release-ready UAT handoff

**Files:** No production changes unless verification finds a reproduced defect.

**Step 1: Run connector-source tests through the Hermes source authority**

```bash
SOURCE_WT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/gitlab-read-exploration
SOURCE_SHA=$(git -C "$SOURCE_WT" rev-parse HEAD)
ERICSSON_CAPABILITIES_DIR="$SOURCE_WT" \
ERICSSON_CAPABILITIES_TEST_EXPECTED_SHA="$SOURCE_SHA" \
./.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_ericsson_connector_surfaces.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/hermes_cli/test_kanban_worker_spawn_toolsets.py \
  tests/cron/test_cron_profile_isolation.py \
  tests/cron/test_ericsson_gitlab_activity_digest.py
```

**Step 2: Run the relevant broad Hermes gates**

```bash
./.venv/bin/python -m pytest -q \
  tests/hermes_cli \
  tests/cron \
  tests/plugins/workflow
./scripts/run_tests.sh
git diff --check
```

If the repository's documented formatter or linter reports connector-owned
files, run it and commit only deterministic fixes.

**Step 3: Verify branch and worktree state**

```bash
git -C "$HERMES_WT" branch --show-current
git -C "$HERMES_WT" status --short --branch
git -C "$SOURCE_WT" status --short --branch
```

Expected: both feature worktrees are clean; unrelated user files in the main
Hermes and source checkouts are still present and untouched.

**Step 4: Fast-forward the completed Hermes feature into `base`**

```bash
HERMES_ROOT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
test "$(git -C "$HERMES_ROOT" branch --show-current)" = base
git -C "$HERMES_ROOT" merge --ff-only feat/gitlab-read-exploration
test "$(git -C "$HERMES_ROOT" branch --show-current)" = base
git -C "$HERMES_ROOT" status --short --branch
```

Expected: `base` points at the verified feature commit, the checkout remains on
`base`, and unrelated untracked files are unchanged.

**Step 5: Prepare read-only installed UAT prompts**

The handoff must include natural-language prompts for:

1. recursive discovery from `sd-macs-att-rnam-hosting`;
2. latest commits for project `56284` / `eventmesh`;
3. commit comments and discussions;
4. new MRs in the last 24 hours;
5. MR commits and unresolved review discussions;
6. a Kanban task using the same natural language;
7. creating and immediately running a daily MR digest cron job;
8. creating and immediately running a daily commit digest cron job; and
9. safe Cloudflare/PAT/mTLS failure after temporarily making the profile
   unavailable, without revealing credential values.

All UAT is read-only except creating/removing the local cron jobs and Kanban
test tasks. No GitLab write tool may be invoked.
