# Brand-aware Version Label Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make neutral Hermes builds report `Hermes Agent` and every OTTO/LOOP24 branded build report `Co-worker Agent` consistently, then publish paired v2.0.6 releases.

**Architecture:** Add one import-safe `version_agent_label()` helper to `hermes_constants.py`, deriving branded versus neutral identity from the existing generator-owned default home basename. Route both the regular banner formatter and the Termux ultra-fast version path through it; the CLI, gateway, and startup banner already consume those paths.

**Tech Stack:** Python 3.11+, pytest, Ruff, Node brand generator tests, Git/GitHub Actions release workflows.

## Global Constraints

- Neutral upstream output remains exactly `Hermes Agent v<VERSION> (<RELEASE_DATE>)` before any git provenance suffix.
- OTTO and LOOP24 output becomes exactly `Co-worker Agent v<VERSION> (<RELEASE_DATE>)` before any git provenance suffix.
- Do not add configuration, environment variables, dependencies, model-facing tools, or prompt changes.
- Build identity must not depend on argv, aliases, profiles, `HERMES_HOME`, or the current working directory.
- Keep base, OTTO, and LOOP24 commits separately reviewable and release only exact gated SHAs.
- Preserve unrelated work in the original checkout.

---

### Task 1: Add the shared product-label contract test-first

**Files:**
- Modify: `hermes_constants.py:48-64`
- Modify: `hermes_cli/banner.py:509-523`
- Modify: `hermes_cli/main.py:246-254`
- Create: `tests/hermes_cli/test_version_product_label.py`
- Modify: `tests/hermes_cli/test_banner_git_state.py:4-36`
- Modify: `tests/hermes_cli/test_tui_resume_flow.py:400-415`

**Interfaces:**
- Consumes: existing `home_dir_basename() -> str`, `VERSION`, and `RELEASE_DATE`.
- Produces: `version_agent_label() -> str`, returning only `Hermes Agent` or `Co-worker Agent`.

- [ ] **Step 1: Write failing identity tests**

```python
import pytest

import hermes_constants


@pytest.mark.parametrize("basename", ["hermes", ".hermes"])
def test_version_agent_label_preserves_neutral_identity(monkeypatch, basename):
    monkeypatch.setattr(hermes_constants, "home_dir_basename", lambda: basename)
    assert hermes_constants.version_agent_label() == "Hermes Agent"


@pytest.mark.parametrize("basename", ["otto", ".otto", "loop24", ".loop24"])
def test_version_agent_label_uses_generic_branded_identity(monkeypatch, basename):
    monkeypatch.setattr(hermes_constants, "home_dir_basename", lambda: basename)
    assert hermes_constants.version_agent_label() == "Co-worker Agent"
```

- [ ] **Step 2: Make banner and Termux tests require the shared label**

Patch `banner.version_agent_label` to `Co-worker Agent` and assert the full base
label and carried-commit suffix. In the Termux test, patch
`hermes_constants.version_agent_label` before invoking
`_try_termux_ultrafast_version()` and assert `Co-worker Agent v` is printed.

- [ ] **Step 3: Run the tests and verify RED**

Run:

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_version_product_label.py \
  tests/hermes_cli/test_banner_git_state.py \
  tests/hermes_cli/test_tui_resume_flow.py
```

Expected: the identity tests fail because `version_agent_label` does not exist,
and the integration assertions fail because the two output paths are hardcoded.

- [ ] **Step 4: Implement the minimal shared helper and consumers**

Add to `hermes_constants.py`:

```python
def version_agent_label() -> str:
    """Return the source-stamped product label used by version output."""
    identity = home_dir_basename().removeprefix(".").lower()
    return "Hermes Agent" if identity == "hermes" else "Co-worker Agent"
```

In `hermes_cli/banner.py`, import `version_agent_label` and change the base
formatter to:

```python
base = f"{version_agent_label()} v{VERSION} ({RELEASE_DATE})"
```

In `_print_fast_version_info()` in `hermes_cli/main.py`, import the same helper
inside the function and print:

```python
print(f"{version_agent_label()} v{__version__} ({__release_date__})")
```

- [ ] **Step 5: Run focused tests and lint**

Run:

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_version_product_label.py \
  tests/hermes_cli/test_banner_git_state.py \
  tests/hermes_cli/test_tui_resume_flow.py \
  tests/cli/test_version_command.py \
  tests/gateway/test_version_command.py
.venv/bin/ruff check \
  hermes_constants.py hermes_cli/banner.py hermes_cli/main.py \
  tests/hermes_cli/test_version_product_label.py \
  tests/hermes_cli/test_banner_git_state.py \
  tests/hermes_cli/test_tui_resume_flow.py
```

Expected: all tests and Ruff checks pass.

- [ ] **Step 6: Commit the behavior**

```bash
git add hermes_constants.py hermes_cli/banner.py hermes_cli/main.py \
  tests/hermes_cli/test_version_product_label.py \
  tests/hermes_cli/test_banner_git_state.py \
  tests/hermes_cli/test_tui_resume_flow.py
git commit -m "fix(branding): use co-worker version label"
```

---

### Task 2: Record the upstream customization and prove generator compatibility

**Files:**
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Consumes: `version_agent_label()` and existing home-emitter neutral/branded literals.
- Produces: ledger ownership for `version_agent_label`, `format_banner_version_label`, and `_print_fast_version_info`.

- [ ] **Step 1: Add a ledger entry**

Add an `upstream_changes` entry with:

```yaml
- id: brand-aware-version-label
  change_class: branding-generic
  owner: downstream-branding
  files:
  - hermes_constants.py
  - hermes_cli/banner.py
  - hermes_cli/main.py
  - tests/hermes_cli/test_version_product_label.py
  - tests/hermes_cli/test_banner_git_state.py
  - tests/hermes_cli/test_tui_resume_flow.py
  owned_symbols:
  - version_agent_label
  - format_banner_version_label
  - _print_fast_version_info
  tests:
  - tests/hermes_cli/test_version_product_label.py
  - tests/hermes_cli/test_banner_git_state.py
  - tests/hermes_cli/test_tui_resume_flow.py
  - tests/cli/test_version_command.py
  - tests/gateway/test_version_command.py
  expected_commit_subject: 'fix(branding): use co-worker version label'
  upstream_candidate: true
  merge_guidance: Preserve neutral Hermes identity while deriving every branded
    version label from the generator-owned build identity, including the
    dependency-light Termux fast path.
  removal_condition: Remove when upstream exposes an equivalent generated
    product identity consumed by every version-output path.
  last_verified_upstream: aaf5691261f12601db845386d650dce1cdfa30f9
```

- [ ] **Step 2: Run ledger and brand-generator gates**

Run:

```bash
python3 scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml \
  --diff c2c02e1e12ae645f2741ab930fcc2c49486b46c1..HEAD
node --test scripts/brand/__tests__/home.test.mjs scripts/brand/__tests__/generate.test.mjs
node scripts/brand/generate.mjs otto --neutralize
```

Expected: ledger coverage passes, Node tests pass, and neutralization is a
no-write dry run that reports the already-neutral base anchors.

- [ ] **Step 3: Commit the ledger entry**

```bash
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "docs(branding): record version label customization"
```

---

### Task 3: Prepare and gate v2.0.6 on base

**Files:**
- Modify: `acp_registry/agent.json`
- Modify: `apps/desktop/package.json`
- Modify: `hermes_cli/__init__.py`
- Modify: `package-lock.json`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Consumes: tested label implementation and existing six-file release version contract.
- Produces: exact base v2.0.6 source commit.

- [ ] **Step 1: Update only the six release metadata files**

Replace `2.0.5` with `2.0.6` in the six named files and retain release date
`2026.7.17`. Verify no unrelated version occurrence changed:

```bash
git diff -- acp_registry/agent.json apps/desktop/package.json \
  hermes_cli/__init__.py package-lock.json pyproject.toml uv.lock
```

- [ ] **Step 2: Commit release metadata**

```bash
git add acp_registry/agent.json apps/desktop/package.json hermes_cli/__init__.py \
  package-lock.json pyproject.toml uv.lock
git commit -m "chore(release): prepare v2.0.6"
```

- [ ] **Step 3: Exclude only the exact metadata commit from workflow coverage**

Add the full commit SHA to `coverage.excluded_commits` with reason
`Separate user-requested v2.0.6 release preparation.`, validate the ledger,
and commit:

```bash
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "docs(release): exclude v2.0.6 metadata from workflow coverage"
```

- [ ] **Step 4: Run the exact base gates**

Run:

```bash
scripts/test_workflow_merge_gate.sh --phase base
scripts/run_tests.sh \
  tests/hermes_cli/test_version_product_label.py \
  tests/hermes_cli/test_banner_git_state.py \
  tests/hermes_cli/test_tui_resume_flow.py \
  tests/cli/test_version_command.py \
  tests/gateway/test_version_command.py \
  tests/test_packaging_metadata.py
uv build
```

Expected: the base merge gate, focused tests, and wheel/sdist build pass; the
neutral CLI output begins with `Hermes Agent v2.0.6`.

---

### Task 4: Merge, gate, and publish paired branded releases

**Files:**
- Generated OTTO overlay files on branch `otto`
- Generated LOOP24 overlay files on branch `loop24`

**Interfaces:**
- Consumes: exact gated base v2.0.6 commit.
- Produces: exact gated OTTO and LOOP24 source SHAs plus public v2.0.6 releases.

- [ ] **Step 1: Merge the exact base commit into temporary brand worktrees**

Run from the base worktree:

```bash
MAIN_ROOT=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
BASE_SHA=$(git rev-parse HEAD)
git -C "$MAIN_ROOT" worktree add "$MAIN_ROOT/.worktrees/release-otto-v2.0.6" \
  -b release/otto-v2.0.6 origin/otto
git -C "$MAIN_ROOT" worktree add "$MAIN_ROOT/.worktrees/release-loop24-v2.0.6" \
  -b release/loop24-v2.0.6 origin/loop24
git -C "$MAIN_ROOT/.worktrees/release-otto-v2.0.6" merge --no-ff "$BASE_SHA" \
  -m "Merge base v2.0.6 into otto"
git -C "$MAIN_ROOT/.worktrees/release-loop24-v2.0.6" merge --no-ff "$BASE_SHA" \
  -m "Merge base v2.0.6 into loop24"
cd "$MAIN_ROOT/.worktrees/release-otto-v2.0.6"
node scripts/brand/generate.mjs otto --write
node scripts/brand/generate.mjs otto --check
uv sync --extra dev --locked
git add -u
git diff --cached --quiet || git commit -m "chore(brand): refresh OTTO v2.0.6 overlay"
cd "$MAIN_ROOT/.worktrees/release-loop24-v2.0.6"
node scripts/brand/generate.mjs loop24 --write
node scripts/brand/generate.mjs loop24 --check
uv sync --extra dev --locked
git add -u
git diff --cached --quiet || git commit -m "chore(brand): refresh LOOP24 v2.0.6 overlay"
```

- [ ] **Step 2: Prove both branded outputs and full release gates**

For OTTO run:

```bash
node scripts/brand/generate.mjs otto --check
scripts/test_workflow_merge_gate.sh --phase brand
scripts/run_tests.sh \
  tests/hermes_cli/test_version_product_label.py \
  tests/hermes_cli/test_banner_git_state.py \
  tests/hermes_cli/test_tui_resume_flow.py \
  tests/cli/test_version_command.py \
  tests/gateway/test_version_command.py \
  tests/test_packaging_metadata.py
.venv/bin/otto --version
npm ci
npm --prefix apps/desktop run build
```

For LOOP24 run the same gates with these brand-specific commands:

```bash
node scripts/brand/generate.mjs loop24 --check
scripts/test_workflow_merge_gate.sh --phase brand
scripts/run_tests.sh \
  tests/hermes_cli/test_version_product_label.py \
  tests/hermes_cli/test_banner_git_state.py \
  tests/hermes_cli/test_tui_resume_flow.py \
  tests/cli/test_version_command.py \
  tests/gateway/test_version_command.py \
  tests/test_packaging_metadata.py
.venv/bin/loop24 --version
npm ci
npm --prefix apps/desktop run build
```

Expected: each version command's first line is
`Co-worker Agent v2.0.6 (2026.7.17)` and both Desktop builds pass.

- [ ] **Step 3: Atomically push base, OTTO, and LOOP24 refs**

Run from either release worktree:

```bash
git fetch origin base otto loop24
BASE_SHA=$(git -C "$MAIN_ROOT/.worktrees/fix-version-product-label" rev-parse HEAD)
OTTO_SHA=$(git -C "$MAIN_ROOT/.worktrees/release-otto-v2.0.6" rev-parse HEAD)
LOOP24_SHA=$(git -C "$MAIN_ROOT/.worktrees/release-loop24-v2.0.6" rev-parse HEAD)
git merge-base --is-ancestor "$BASE_SHA" "$OTTO_SHA"
git merge-base --is-ancestor "$BASE_SHA" "$LOOP24_SHA"
git push --atomic origin \
  "$BASE_SHA:refs/heads/base" \
  "$OTTO_SHA:refs/heads/otto" \
  "$LOOP24_SHA:refs/heads/loop24"
```

Do not create a tag or release in `cmetech/hermes-agent`.

- [ ] **Step 4: Dispatch and monitor the release workflows**

```bash
gh workflow run release.yml -R cmetech/otto \
  -f ref="$OTTO_SHA" -f stamp_branch=otto -f version=2.0.6 -f prerelease=false
gh workflow run release.yml -R cmetech/loop24 \
  -f ref="$LOOP24_SHA" -f stamp_branch=loop24 -f version=2.0.6 -f prerelease=false
```

Capture and monitor the newest dispatched run in each release repository:

```bash
OTTO_RUN_ID=$(gh run list -R cmetech/otto --workflow release.yml \
  --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')
LOOP24_RUN_ID=$(gh run list -R cmetech/loop24 --workflow release.yml \
  --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$OTTO_RUN_ID" -R cmetech/otto --exit-status
gh run watch "$LOOP24_RUN_ID" -R cmetech/loop24 --exit-status
```

- [ ] **Step 5: Verify public release provenance and assets**

Run:

```bash
gh release view v2.0.6 -R cmetech/otto \
  --json assets,isDraft,isPrerelease,tagName,targetCommitish,url
gh release view v2.0.6 -R cmetech/loop24 \
  --json assets,isDraft,isPrerelease,tagName,targetCommitish,url
```

Verify both releases have `isDraft=false`, `isPrerelease=false`, tag
`v2.0.6`, successful workflow logs naming the expected exact source SHA, and
the complete seven-asset set before reporting completion.
