# Workflow language Phase 5 validation and activation

Date: 2026-08-07

Status: **POST-ACTIVATION VALIDATION COMPLETE — REVIEW GO**

This report records the implementation, activation, independent review, and
non-publishing regression closure for Workflow Language Phase 5 provider
portability. The exact reviewed and tested product-code commit is
`1373c306061d2f4a2cf1dd313df16f6453fa1939`. New `archon-2026-07`
admissions select normalizer v5; `hermes-legacy` remains on v2; explicit and
sealed v1-v4 packages retain their recorded behavior. Snapshot format remains
2. No push, merge, rebase, tag, release, publication, or brand mutation was
performed.

## Delivered contract

Phase 5 adds one backend provider-capability authority; config-only portable
model tiers and aliases; sealed selector, effective-provider, model, option,
fallback, and cache identity; bounded hook and package-contained MCP adapters;
shared inline-agent limits; authoritative settled-call cost-budget enforcement;
truthful provider-native sandbox blocking; and closed backend-authored
projections consumed by CLI, Gateway, REST, evidence, doctor, catalog/detail,
and Desktop.

Unsupported Archon behavior blocks. `allowed_tools: []` remains an exact empty
built-in tool allow-list. Skills are fully loaded into the current user turn
and never mutate the system prompt. Provider/tool/MCP/skill/hook identity
changes force fresh context. MCP resources are sealed, digest-bound, bounded,
isolated, and torn down. Secrets and unnecessary provider payloads remain out
of public evidence. Budget exhaustion is terminal and cannot be reset by
retry, repair, fallback, or inline children. Provider-native sandbox settings
remain unsupported unless the selected runtime can prove enforcement; the
existing `execution_environment: isolated_backend_required` policy remains the
separate companion boundary.

Phase 5 does not add `loop_group`, runtime child workflows, dynamic includes,
`include.with`, deep child-output navigation, input mapping, a core model tool,
synthetic conversation messages, system-prompt mutation, telemetry, or a new
OS sandbox.

## Activation and compatibility boundary

Normalizer v5 remained readable but dormant during implementation. Activation
changed only `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` from 4 to 5;
`LATEST_NORMALIZER_VERSION` is derived from that authority. Historical
runtime fixtures that intentionally exercise Phase 1-4 behavior load their
recorded v4 source and sidecar bytes explicitly. No v5 provider-authority rule
was weakened, and the v1-v4 readers, legacy v2 selection, snapshot-format-2
recovery, prompt-cache guarantees, and Phase 4 closure sealing remain intact.

## Independent review and remediation

A fresh high-reasoning Sol reviewer examined the complete implementation in
successive rounds. Each code finding was reproduced with a focused RED before
the smallest generic fix:

- `8cc0855f3988b9c5983f41b4d4b940d17a5eaf68` sealed primary/fallback
  capability obligations, inherited budget/sandbox decisions, and admitted
  only the exact supported bare hook allow forms.
- `dd9a07ae81734402d008335431787976be687f20` consumed route-specific
  structured-output strategy on fallback, correlated evidence to the route
  actually used, and rejected allow responses carrying ignored stop reasons.
- `0127268d6e944228013a04d0a1e0870eca92611a` separated routable provider
  selectors from effective provider identity and restored complete approval
  fallback wire shape.
- `1373c306061d2f4a2cf1dd313df16f6453fa1939` sealed and validated fallback
  `effective_provider`, compared provider-option decisions against effective
  identity, and exercised both AI and approval mismatched-selector routes
  through the real request validator.

The closure reviewer ran 5 files and 199 tests with retries disabled and
returned **Code GO — 0 Critical, 0 Important** for `1373c306...`. The prior
evidence-only Important finding was that this report still cited an older SHA
and omitted literal commands; the sections below correct both defects. No code
changed after the GO verdict.

## Literal verification commands and results

All commands ran from the Phase 5 worktree unless a clean detached checkout is
explicitly noted. Python used the repository's shared virtual environment.

### Focused Phase 5 and v1-v4 compatibility closure

```bash
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/hermes_cli/test_provider_capabilities.py tests/hermes_cli/test_workflow_model_resolution.py tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_phase5_provider_authority.py tests/plugins/workflow/test_phase5_provider_snapshot.py tests/plugins/workflow/test_phase5_admission_parity.py tests/plugins/workflow/test_phase5_execution_context.py tests/plugins/workflow/test_phase5_hooks.py tests/plugins/workflow/test_phase5_mcp.py tests/plugins/workflow/test_phase5_inline_limits.py tests/plugins/workflow/test_phase5_cost_budget.py tests/plugins/workflow/test_phase5_provider_options.py tests/plugins/workflow/test_phase5_surfaces.py tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_defensive_invariants.py -q
```

Result: **17 files, 273 passed, 0 failed in 5.9s**, 14 workers, zero file
retries.

### Installed distribution

```bash
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -q
```

Result: **1 passed, 0 failed in 9.6s**. The `integration` marker is the only
intentional selection restriction.

### Desktop

```bash
cd apps/desktop
npm run typecheck
npm test
npm run lint
cd ../../
```

Results:

- TypeScript typecheck passed in 21.86s.
- Vitest: **498 files passed, 1 skipped; 4,741 tests passed, 2 skipped in
  50.73s**.
- ESLint passed in 21.46s with **0 errors and 163 established warnings**.

### Customization ledger and merge-gate contracts

```bash
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py tests/scripts/test_workflow_merge_gate.py tests/scripts/test_workflow_upstream_merge.py tests/test_desktop_workflow_test_gate.py -q
```

Result: **4 files, 430 passed, 0 failed in 99.3s**, 14 workers, zero file
retries: 275 customization-checker tests, 49 merge-gate tests, 104
upstream-rehearsal tests, and 2 Desktop-gate tests. The ledger's upstream
baseline was not advanced, and every upstream-owned Phase 5 change remains
generic and invariant-tested.

### Combined upstream and production-descriptor rehearsal

```bash
PHASE5_UPSTREAM_REHEARSAL_ARGS=()
while IFS= read -r brand_slug; do
  PHASE5_UPSTREAM_REHEARSAL_ARGS+=(--brand-ref "origin/$brand_slug")
done < <(node --input-type=module -e 'import fs from "node:fs"; import {loadDescriptor} from "./scripts/brand/descriptor.mjs"; for (const file of fs.readdirSync("brands").filter(name => /^[a-z][a-z0-9-]*\.json$/.test(name) && name !== "schema.json")) { const slug=file.slice(0,-5); loadDescriptor(slug,{root:process.cwd()}); console.log(slug); }')
PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/test_workflow_upstream_merge.sh --upstream-ref origin/main --base-ref HEAD "${PHASE5_UPSTREAM_REHEARSAL_ARGS[@]}"
```

Result: exit 0 against exact detached base `1373c306...` and both dynamically
validated production descriptors, `loop24` and `otto`. The command emitted
bounded merge evidence; it was not separately instrumented for wall duration.

### Full no-retry Python regression

```bash
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh
```

Authoritative green result: **2,792 files, 32,641 tests passed, 0 failed in
718.1s**, 14 workers, zero file retries, with no Python test exclusion.

Full-suite diagnostics were retained rather than hidden:

- An earlier no-retry attempt reported 32,640 passed and one failure in
  `test_browser_manage_connect_defaults_to_loopback`; the complete 517-test
  gateway file immediately passed in isolation.
- Initial disposable-worktree attempts lacked gitignored `node_modules` and
  `.venv` links. Their parser and nested-runner failures were setup failures;
  after sharing the repository installations, the exact affected files passed
  15/15 and 53/53.
- A later dependency-complete, 14-worker attempt reported 32,640 passed and one
  process-reaping timing failure; the complete upstream-merge file immediately
  passed **104/104 in 97.5s** with retries disabled.

These diagnostic failures changed no source and are not counted as green
evidence. The successful full command above is the closure result. The two
non-reproducing failures are recorded as an existing host-saturation risk, not
silently retried by the test runner.

### Clean base release gate

The following command ran from a clean detached worktree at the exact tested
SHA. Its gitignored `.venv` and `node_modules` links pointed to the repository's
existing installations; they were removed with the disposable worktree.

```bash
PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/test_workflow_merge_gate.sh --phase base
```

Result: exit 0 in **176s**:

- Python release selection: **68 files, 3,670 passed, 0 failed in 125.1s**.
- Installed-distribution E2E: **1 passed, 0 failed in 9.4s**.
- Desktop release subset: **11 files, 173 passed in 4.83s**.
- `TESTED_BASE_SHA=1373c306061d2f4a2cf1dd313df16f6453fa1939`.

### External-state-audited brand rehearsals

The audit enumerated descriptor filenames, validated every descriptor through
`loadDescriptor()`, and executed the following loop from the same clean exact
SHA. The private snapshot helper captured worktrees, branch/status, local refs,
source-remote refs, tags, each descriptor's release-repository refs, and
`gh release list --json tagName,name,publishedAt,isDraft,isPrerelease` before
the run, after each brand, and at the end.

```bash
test "$(git rev-parse HEAD)" = "1373c306061d2f4a2cf1dd313df16f6453fa1939"
test -z "$(git status --porcelain)"
PHASE5_AUDIT_DIR="$(mktemp -d)"
case "$PHASE5_AUDIT_DIR" in
  /tmp/*|/private/tmp/*|/var/folders/*) ;;
  *) echo "unsafe temporary audit directory" >&2; exit 2 ;;
esac
phase5_snapshot_external_state() {
  local snapshot_label="$1"
  local source_remote source_remote_url brand_slug releases_repo
  mkdir -p "$PHASE5_AUDIT_DIR/$snapshot_label"
  git worktree list --porcelain > "$PHASE5_AUDIT_DIR/$snapshot_label/worktrees"
  git branch --show-current > "$PHASE5_AUDIT_DIR/$snapshot_label/branch"
  git status --porcelain=v2 --branch > "$PHASE5_AUDIT_DIR/$snapshot_label/status"
  git for-each-ref --format='%(refname) %(objectname)' refs/heads refs/remotes refs/tags > "$PHASE5_AUDIT_DIR/$snapshot_label/local-refs"
  git remote -v > "$PHASE5_AUDIT_DIR/$snapshot_label/source-remotes"
  for source_remote in $(git remote); do
    source_remote_url="$(git remote get-url "$source_remote")"
    git ls-remote "$source_remote_url" > "$PHASE5_AUDIT_DIR/$snapshot_label/source-$source_remote-refs"
  done
  while IFS=$'\t' read -r brand_slug releases_repo; do
    git ls-remote "https://github.com/$releases_repo.git" > "$PHASE5_AUDIT_DIR/$snapshot_label/$brand_slug-remote-refs"
    gh release list -R "$releases_repo" --limit 200 --json tagName,name,publishedAt,isDraft,isPrerelease > "$PHASE5_AUDIT_DIR/$snapshot_label/$brand_slug-releases.json"
  done < <(node --input-type=module -e 'import fs from "node:fs"; import {loadDescriptor} from "./scripts/brand/descriptor.mjs"; for (const file of fs.readdirSync("brands").filter(name => /^[a-z][a-z0-9-]*\.json$/.test(name) && name !== "schema.json")) { const slug=file.slice(0,-5); const d=loadDescriptor(slug,{root:process.cwd()}); console.log(`${slug}\t${d.releasesRepo}`); }')
}
phase5_snapshot_external_state before
PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/test_workflow_merge_gate.sh --phase base
PHASE5_TESTED_SHA="$(git rev-parse HEAD)"
while IFS= read -r brand_slug; do
  PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/test_workflow_upstream_merge.sh \
    --upstream-ref origin/main \
    --base-ref "$PHASE5_TESTED_SHA" \
    --brand-ref "origin/$brand_slug" \
    --report-dir "$PHASE5_AUDIT_DIR/rehearsal-$brand_slug"
  phase5_snapshot_external_state "after-$brand_slug"
  diff -ru "$PHASE5_AUDIT_DIR/before" "$PHASE5_AUDIT_DIR/after-$brand_slug"
done < <(node --input-type=module -e 'import fs from "node:fs"; import {loadDescriptor} from "./scripts/brand/descriptor.mjs"; for (const file of fs.readdirSync("brands").filter(name => /^[a-z][a-z0-9-]*\.json$/.test(name) && name !== "schema.json").sort()) { const slug=file.slice(0,-5); loadDescriptor(slug,{root:process.cwd()}); console.log(slug); }')
phase5_snapshot_external_state after
diff -ru "$PHASE5_AUDIT_DIR/before" "$PHASE5_AUDIT_DIR/after"
git diff --check
PHASE5_EXTERNAL_STATE_DIGEST_SHA256="$(cd "$PHASE5_AUDIT_DIR/before" && find . -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | awk '{print $1}')"
test "$PHASE5_EXTERNAL_STATE_DIGEST_SHA256" = "958778df69787ecc015dbf9300d052d6408eb1921068739895289f440e9a01df"
```

Results:

- `loop24`: exit 0 in **1,018s**; post-brand comparison empty.
- `otto`: exit 0 in **1,019s**; post-brand comparison empty.
- Final comparison empty.
- Baseline comparison digest:
  `958778df69787ecc015dbf9300d052d6408eb1921068739895289f440e9a01df`.
- The disposable exact-SHA checkout was removed after the final comparison.

The private evidence directory was retained through independent evidence
review; only its temporary path and bounded merge-evidence payloads are
intentionally omitted from this report. No push, merge into a persistent
branch, rebase, tag, release, publication, or brand/ref mutation occurred.

## Exact Git and preservation state before this evidence commit

- Feature branch: `feat/workflow-language-phase-5-provider-portability`.
- Tested and independently reviewed product-code HEAD:
  `1373c306061d2f4a2cf1dd313df16f6453fa1939`.
- Root checkout: clean `base` at
  `cff7875049a7f369c2eae758503c63b6467c4433`; `origin/base` matches.
- `origin/otto`: `3cf9a3a89f01133ceb4e0cbf79123e632bfeab5c`.
- `origin/loop24`: `6e15b6611edcf88a2bb0569beffe977e035b088f`.
- Literal `main`, `otto`, `loop24`, release repositories, tags, and releases
  were not modified.
- The Phase 4 worktree was not entered, cleaned, reused, or mutated. Its two
  preserved untracked review documents retained their recorded hashes.

The documentation-only evidence commit will necessarily differ from the
tested product-code SHA. It changes no product code, test contract, normalizer,
snapshot, runtime, Desktop, ledger, or release behavior.

## Final disposition

Phase 5 implementation is complete and independently reviewed: **GO — 0
Critical, 0 Important code findings**. There are no unresolved Phase 5
blockers. The only non-blocking operational risk is the documented pair of
non-reproducing full-suite timing failures under saturated parallel load; both
complete affected files pass independently, and the full no-retry suite has a
32,641/32,641 green result at the exact code SHA.
