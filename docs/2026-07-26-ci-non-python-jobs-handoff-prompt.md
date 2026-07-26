# Handoff — turn the last 5 non-Python CI jobs green on `base`

> Copy everything below the horizontal rule into a fresh session whose working
> directory is `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`.
> Three of the five are diagnosed with a reproducing command; two are not.
> Re-verify anything load-bearing.

---

## 1. Where things stand

CI now runs on `base` (added 2026-07-25) and its **Python suite is fully green**
— 8/8 slices, down from 126 failures. The aggregate gate
`All required checks pass` is still RED because of five non-Python jobs, none of
which was ever in the Python failure set.

- Branch `base`, HEAD **`55fd2d99a`**, pushed, clean.
- `base` has branch protection: force-push and deletion blocked,
  `enforce_admins: true`, **no** required status checks, **no** required PR
  reviews. Direct pushes still work. To force-push you must temporarily
  `gh api -X DELETE repos/cmetech/hermes-agent/branches/base/protection`.
- Latest CI run: **30180814521**. Failing job IDs are listed per section below.
- OTTO/LOOP24 **v4.0.4** are released (otto `25ac86346`, loop24 `d08a5d389`).
  None of this work is in a release; it is all test/lint infrastructure.

**Goal:** get these five green, then the required-status-check decision becomes
available (it is deliberately still advisory — see §7).

## 2. STOP — environment invariants

```bash
.venv/bin/python -V                 # expect 3.11.x
.venv/bin/python -c "import acp"    # must succeed silently
```

If either fails: `rm -rf .venv && uv sync --locked --python 3.11 --extra all --extra dev --python-preference managed`.

- **Never use bare `pytest` for a verdict** — use `scripts/run_tests.sh`
  (CI parity: subprocess-per-file, `TZ=UTC`, scrubbed env).
- `node scripts/brand/generate.mjs <brand> --check` **fails on `base` by
  design**. Do not "fix" it.
- The showcase `__pycache__` problem is FIXED (`36da8d965`); if you see
  `showcase safety contract rejects binary resource`, something reverted
  `PYTHONPYCACHEPREFIX` in `scripts/run_tests.sh`.

## 3. DIAGNOSED — `Windows footguns (blocking)` (job 89736896784)

CI runs `python scripts/check-windows-footguns.py --all`. Note `--all`: without
it the checker passes, which is why this is easy to miss locally.

```bash
.venv/bin/python scripts/check-windows-footguns.py --all   # exit 1, 4 violations
```

All four are in **`plugins/ericsson-teams/graph_auth.py`** (lines 77, 90, 143,
181), rule `[bare os.getuid / os.geteuid / os.getgid]`:

```python
if directory_stat.st_uid != os.geteuid():
```

> os.getuid / os.geteuid / os.getgid do not exist on Windows and raise
> AttributeError at import time if referenced.

This is **vendored content** from the P2c Ericsson bake-in, so it is a real
portability concern, not a lint artifact: the Outlook/Teams plugins are the ones
most likely to run on Windows. The checks look like POSIX ownership assertions
on a credentials directory.

**Judgement needed.** Options, roughly in order of preference:
1. Guard the ownership checks behind `os.name != "nt"` / `hasattr(os, "geteuid")`
   and use an equivalent Windows check (or skip with a comment). Fixes the real
   bug.
2. Re-vendor from the upstream Ericsson source if it has since been fixed there
   — see `scripts/vendor-ericsson.mjs` and workspace memory
   `ericsson-capabilities.md`. **Do not hand-edit vendored files without also
   fixing the source**, or the next `node scripts/vendor-ericsson.mjs` reverts it.
3. Exclude vendored plugins from the checker (weakest — it is exactly the code
   that needs the check).

Prefer (2)+(1): fix in the Ericsson source, re-vendor, confirm the checker
passes. Confirm with Corey before editing vendored files in place.

## 4. DIAGNOSED — `check:test:ui` (job 89736943953)

```bash
cd apps/desktop && npm run test:ui    # EXIT=1
```

The trap: **every test passes.** `258 files, 2255 passed, 1 skipped, 0 failed` —
and it still exits 1, because vitest fails a run on an unhandled rejection:

```
⎯⎯ Unhandled Errors ⎯⎯
⎯⎯ Unhandled Rejection ⎯⎯
Error: Electron notification bridge unavailable
     Errors  1 error
```

Origin chain:
`clarify-hydration.test.tsx:58` → `use-message-stream/gateway-event.ts:712` →
`dispatchNativeNotification` (`src/store/native-notifications.ts:157`) →
`projectNativeNotification` (`native-notifications.ts:176`).

So a test triggers a gateway event that fires a native notification; outside
Electron the bridge is absent and the promise rejects with nobody awaiting it.

**Do not "fix" this by making the test stop firing the event** unless that is
genuinely what it should do. The more likely correct fix is in
`native-notifications.ts`: a fire-and-forget notification must not produce an
unhandled rejection when the bridge is unavailable — that is a real defect that
would also surface in the browser/dev-server build, not just tests. Catch and
degrade (debug-log) at the dispatch site.

## 5. DIAGNOSED — `check:lint` (job 89736943964)

`check:lint` = `npm run typecheck && npm run lint`. Two **pre-existing**
TypeScript errors, confirmed present on a clean tree (stash-verified during the
v4.0.3 work), both in test files:

```
src/app/workflows/index.test.tsx(605,5): error TS2322:
  '(selector: string) => boolean' is not assignable to ... must be a type predicate
src/app/workflows/review-run-dialog.test.tsx(132,15): error TS2345:
  Property 'openNewSessionTab' is missing in type '{ startFreshSession... }'
   but required in type 'KeybindRuntimeDeps'
```

```bash
cd apps/desktop && npx tsc --noEmit -p tsconfig.json
```

Both are small and self-contained: (605) a mock `matches` needs to be typed as a
type predicate (`(s: string): this is Element` shape, or cast); (132) a mock of
`KeybindRuntimeDeps` is missing the `openNewSessionTab` member. Check whether
`lint` (the second half) also fails once typecheck passes — it was never reached.

## 6. NOT DIAGNOSED — two jobs

Both need investigation; I ran out of context before reaching them.

**`check:test:desktop:all` (job 89736943950).** `npm run test:desktop:all`.
Locally the full desktop suite gives `2966 passed, 1 failed`, and the single
failure — `electron/git-worktree-ops.test.ts > listBranches: a branch claimed by
a worktree is flagged checked out` — is a **load-dependent flake**: it passes in
isolation and reproduces on a clean tree. Verify whether CI is failing on that
same flake (in which case fix the test's isolation, not the product) or on
something else. Pull the log with:

```bash
gh api repos/cmetech/hermes-agent/actions/jobs/89736943950/logs > /tmp/j.log
grep -nE 'FAIL|✗|Tests ' /tmp/j.log | head -20
```
Keep grep patterns SIMPLE — a complex regex exceeded ugrep's complexity limit
and downloading a job log takes >2 minutes.

**`check-attribution` (job 89736896581).** `Check contributors /
check-attribution`. Untouched. Plausibly fork-specific (a CLA/attribution check
that assumes upstream's contributor list) — if so the right answer may be to
disable it for this fork rather than satisfy it. Read the workflow before
assuming.

## 7. Do NOT enable the required status check yet

`ci.yml` has an aggregate job `All required checks pass` designed so branch
protection needs only one required check. **Leave it advisory until all five are
green.** Also note: required checks are evaluated against the branch tip and CI
runs *after* a push, so requiring them effectively forces a **PR workflow** —
this fork currently pushes directly to `base`. That is a workflow decision for
Corey, not an automatic follow-on from going green.

## 8. Conventions that apply to this work

- Upstream-owned files that this fork edits need a ledger entry under
  `docs/upstream-customizations/`; validate with
  `.venv/bin/python scripts/check_upstream_customizations.py --manifest <path>`
  (exit 0). Existing manifests: `browser-profiles`, `desktop-test-infra`,
  `platform-test-skips`, `python-isolation`, `test-harness`,
  `workflow-orchestration`. `desktop-test-infra.yaml` is the natural home for
  desktop test-config fixes.
- The recurring bug class in this repo: **an upstream test asserting upstream
  behaviour that this fork deliberately changed.** Eight of the nine causes
  fixed in `85a84696b` / `55fd2d99a` were that. Prefer *deriving* the expected
  value from production over hardcoding a brand literal.
- Work on a branch off `base`, merge back with `--ff-only`, push `base`. CI
  fires automatically on push to `base`.
- Nothing here needs a release; do not bump versions or dispatch release
  workflows.

## 9. Verification

```bash
.venv/bin/python scripts/check-windows-footguns.py --all      # exit 0
cd apps/desktop && npx tsc --noEmit -p tsconfig.json          # no errors
cd apps/desktop && npm run check:test:ui                      # exit 0
cd apps/desktop && npm run check:test:desktop:all             # exit 0
```

Then push `base` and confirm on the new run that all five jobs are green and the
Python slices stayed 8/8:

```bash
gh run list --repo cmetech/hermes-agent --branch base --limit 1
gh api "repos/cmetech/hermes-agent/actions/runs/<id>/jobs?per_page=100" \
  --jq '.jobs[] | select(.conclusion != "success" and .conclusion != "skipped") | .name'
```
