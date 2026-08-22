# Upstream merge procedure

## 1. Pin scope and capture evidence

Set the requested release and immutable commit explicitly. For v0.20.5:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)" || exit 1
cd "$REPO_ROOT" || exit 1
test "$(git branch --show-current)" = base || exit 1
git fetch origin || exit 1
REQUESTED_VERSION=0.20.5
UPSTREAM_TARGET=fcbd1076a93841fa88855acce810e342a5b78101
git cat-file -e "$UPSTREAM_TARGET^{commit}" || exit 1
git show "$UPSTREAM_TARGET:pyproject.toml" |
  grep -Fx "version = \"$REQUESTED_VERSION\"" || exit 1
git merge-base --is-ancestor "$UPSTREAM_TARGET" origin/main || exit 1
git rev-list --count "$UPSTREAM_TARGET"..origin/main
```

The last count is post-release drift, not merge scope. Record the target SHA,
subject, merge base, commit count, changed-file count, and current `base` SHA.
Use two-dot ranges for commits and three-dot diffs only when merge-base diff
semantics are intended.

Select the repository interpreter once:

```bash
PYTHON="$PWD/.venv/bin/python"
test -x "$PYTHON" || PYTHON="$PWD/venv/bin/python"
test -x "$PYTHON"
```

## 2. Validate and classify every committed ledger

Do not maintain a hard-coded manifest list. The merge controls themselves must
already be reviewed and committed to `base`; otherwise a dirty or untracked
manifest can influence classification without existing in the sealed base.
Unrelated user changes may remain in the live checkout.

```bash
CONTROL_PATHS=(
  .agents/skills/otto-upstream-merge
  docs/upstream-customizations
  scripts/check_upstream_customizations.py
  scripts/run_workflow_ledger_invariants.py
)
git diff --quiet -- "${CONTROL_PATHS[@]}" || exit 1
git diff --cached --quiet -- "${CONTROL_PATHS[@]}" || exit 1
test -z "$(git ls-files --others --exclude-standard -- "${CONTROL_PATHS[@]}")" || exit 1

EVIDENCE_ROOT="$(mktemp -d)" || exit 1
EVIDENCE_DIR="$EVIDENCE_ROOT/evidence"
PREFLIGHT_ROOT="$(mktemp -d)" || exit 1
PREFLIGHT_WT="$PREFLIGHT_ROOT/base-v${REQUESTED_VERSION}-preflight"
cleanup_preflight() {
  case "${PREFLIGHT_WT:-}" in
    "$PREFLIGHT_ROOT"/*)
      git -C "$PREFLIGHT_WT" merge --abort 2>/dev/null || true
      git worktree remove --force "$PREFLIGHT_WT" 2>/dev/null || true
      ;;
  esac
  rmdir "$PREFLIGHT_ROOT" 2>/dev/null || true
}
trap cleanup_preflight EXIT INT TERM
mkdir -p "$EVIDENCE_DIR" || exit 1
git worktree add --detach "$PREFLIGHT_WT" base || exit 1
test -d "$REPO_ROOT/node_modules" || exit 1
test -d "$REPO_ROOT/apps/desktop/node_modules" || exit 1
ln -s "$REPO_ROOT/node_modules" "$PREFLIGHT_WT/node_modules" || exit 1
ln -s "$REPO_ROOT/apps/desktop/node_modules" \
  "$PREFLIGHT_WT/apps/desktop/node_modules" || exit 1

cd "$PREFLIGHT_WT" || exit 1
for manifest in docs/upstream-customizations/*.yaml; do
  test -f "$manifest" || exit 1
  name="$(basename "$manifest" .yaml)"
  baseline="$($PYTHON scripts/check_upstream_customizations.py \
    --manifest "$manifest" --strict --base-ref base \
    --print-verified-upstream)" || exit 1
  git merge-base --is-ancestor "$baseline" "$UPSTREAM_TARGET" || exit 1
  "$PYTHON" scripts/check_upstream_customizations.py \
    --manifest "$manifest" --strict --base-ref base || exit 1
  if "$PYTHON" scripts/check_upstream_customizations.py \
    --manifest "$manifest" --strict --base-ref base \
    --upstream-diff "$baseline..$UPSTREAM_TARGET" \
    --report "$EVIDENCE_DIR/$name-overlap.json"; then
    checker_status=0
  else
    checker_status=$?
  fi
  test "$checker_status" -eq 0 -o "$checker_status" -eq 2 || exit "$checker_status"
done
```

Exit 2 means explicit decisions are required, not that the checker failed.
Read every report. Review upstream intent and runtime behavior for every
decision-required or conflict-prone overlap. Use the disposition process in
`disposition-review.md`.

Run each manifest's declared invariants against committed `base` before
rehearsal and save the JSON results:

```bash
for manifest in docs/upstream-customizations/*.yaml; do
  name="$(basename "$manifest" .yaml)"
  "$PYTHON" scripts/run_workflow_ledger_invariants.py \
    --manifest "$manifest" --base-ref base \
    --output "$EVIDENCE_DIR/$name-base-invariants.json" || exit 1
done
```

Also run the neutral branding generator gate and the repository-specific
pre-merge checks in `../CLAUDE.md`.

The preflight checkout is intentionally detached, so identify it explicitly as
the neutral branch when invoking the branch-gated check:

```bash
cd "$PREFLIGHT_WT/apps/desktop" || exit 1
GITHUB_REF_NAME=base npm run check:brand-neutral || exit 1
```

Remove the preflight worktree after its evidence is captured, then return to
the live repository without switching its branch:

```bash
cd "$REPO_ROOT" || exit 1
cleanup_preflight
trap - EXIT INT TERM
test "$(git branch --show-current)" = base || exit 1
```

## 3. Rehearse without touching the live checkout

The live tree may contain user work. Use a detached worktree:

```bash
TRIAL_ROOT="$(mktemp -d)"
TRIAL_WT="$TRIAL_ROOT/base-v${REQUESTED_VERSION}-trial"
cleanup_trial() {
  case "${TRIAL_WT:-}" in
    "$TRIAL_ROOT"/*)
      git -C "$TRIAL_WT" merge --abort 2>/dev/null || true
      git worktree remove --force "$TRIAL_WT" 2>/dev/null || true
      ;;
  esac
  rmdir "$TRIAL_ROOT" 2>/dev/null || true
}
trap cleanup_trial EXIT INT TERM
git worktree add --detach "$TRIAL_WT" base
if git -C "$TRIAL_WT" merge --no-commit --no-ff "$UPSTREAM_TARGET"; then
  merge_status=0
else
  merge_status=$?
fi
test "$merge_status" -eq 0 -o "$merge_status" -eq 1 || exit "$merge_status"
unmerged="$(git -C "$TRIAL_WT" diff --name-only --diff-filter=U)"
if test "$merge_status" -ne 0 && test -z "$unmerged"; then
  exit "$merge_status"
fi
printf '%s\n' "$unmerged"
```

Record `TRIAL_WT` immediately. On every success, failure, interruption, or
context handoff, abort any active merge there and remove only that worktree.
Never run cleanup against an unresolved variable or the repository root.
Keep worktree creation, merge, and cleanup in one persistent shell with an
`EXIT INT TERM` cleanup trap. If tooling splits commands across shells, perform
the same validated cleanup before ending the turn rather than assuming a trap
survived.

Inventory textual conflicts and silent overlaps separately. Resolve only in
the trial worktree, preserving unrelated upstream edits. Run all applicable
ledger invariants, targeted tests for changed behavior, Python parse/import
checks, the neutral branding gate, Desktop typecheck/tests/build, and the
repository merge gates. Discover brands from `brands/*.json`, excluding
`schema.json` and `_`-prefixed fixtures; never hard-code a brand count.
The Desktop build may stamp tracked brand output, so run it only in the
throwaway trial or a brand worktree, never in the live neutral `base` checkout.

If the automated rehearsal applies cleanly, the existing workflow gate is an
additional check:

```bash
brand_args=()
for descriptor in brands/*.json; do
  slug="$(basename "$descriptor" .json)"
  case "$slug" in schema|_*) continue ;; esac
  git rev-parse --verify "$slug^{commit}" >/dev/null || exit 1
  brand_args+=(--brand-ref "$slug")
done
test "${#brand_args[@]}" -gt 0 || exit 1
scripts/test_workflow_upstream_merge.sh \
  --upstream-ref "$UPSTREAM_TARGET" --base-ref base "${brand_args[@]}"
```

Pass every decision-required ledger id with
`--decision id=preserve|adapt|remove-as-upstream-equivalent`. The report's
human disposition may be more descriptive, but machine values remain those
three existing values.

## 4. Report and approval checkpoint

Collect evidence under the external temporary `EVIDENCE_DIR`, then clean up the trial worktree. Write
`docs/merge-reports/<date>-upstream-v<version>-TRIAL.md` in the live repository
after cleanup; that report is the rehearsal's only intentional live-tree
output. Include scope, conflicts, silent overlaps, the disposition table, gate
results, new surface, risks, follow-ups, and `GO`, `GO-WITH-FIXES`, or `NO-GO`.

Abort and remove only the worktree created above:

```bash
cleanup_trial
trap - EXIT INT TERM
test "$(git branch --show-current)" = base
rm -rf "$EVIDENCE_ROOT"
```

Stop. A real merge requires explicit user approval after this report.

## 5. Real merge and brands (only after approval)

Create a dedicated real-merge worktree and branch from literal `base`, repeat
the reviewed resolution there using the same immutable target, and commit the
upstream merge and report only after all neutral-base gates pass. Require the
live checkout to have no tracked changes before advancing `base` with a
fast-forward-only merge of that tested branch; Git must also report any
untracked path collision rather than overwriting it. This keeps unrelated
untracked user files out of the merge worktree and preserves the correct
first-parent ancestry.

Advance each discovered brand from the tested base, regenerate its
descriptor-owned overlay, and run per-brand gates. Build and launch the
Desktop and CLI as required by `../CLAUDE.md`; a build without a launch is not
release proof. Generated text outputs may be regenerated from the tested base;
binary art conflicts retain the verified brand overlay. Neither exception
allows whole-file resolution of shared source code.

Do not publish or create a release unless separately authorized. Whether the
run succeeds or aborts, finish on `base` and report the exact tested SHAs.
