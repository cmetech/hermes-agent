# Workflow Catalog + Desktop Trigger — Adversarial Review

**Date:** 2026-07-20
**Branch:** `feat/workflow-catalog-desktop-trigger` @ `8335ea91b` (off `origin/base`;
9 feature commits, Tasks 1–9)
**Scope:** the full v3.0.1 catalog feature — GET catalog list + detail/preflight
endpoints, redacted definition + mermaid topology, `POST /runs` reuse with the
Desktop provenance derivation (D1), the serializable IPC error contract, and
the Desktop Workflows tab (datagrid, View modal with Diagram|Definition toggle,
Review & Run modal).
**Method:** four independent adversarial reviewers — (1) catalog/detail
endpoints + provenance + redaction; (2) scope-creep/regression hunt on the
surprising core-file touches; (3) Desktop UI/bridge/IPC; (4) gate + e2e +
evidence audit with a full independent gate reproduction. ~140 backend tests
run per reviewer plus the full merge gate reproduced from scratch. The two
severity-decisive items (trust-vocabulary broadening, admission path change)
were re-verified line-by-line by the coordinating reviewer.

## Verdict

**READY FOR MAINTAINER MERGE REVIEW — no Critical, High, or Important finding.**
Two Minor and a few Low/Info items, none merge-blocking; one Minor should be
fixed before the branded v3.0.1 release.

The feature is genuinely thin and additive at the host layer: the generic host
files (`plugin_services.py`, `plugins.py`, `web_server.py`, `gateway/run.py`)
are untouched, no new `HERMES_*` env var, `_STORE_SCHEMA_VERSION` unchanged
(13 — no migration), `_SUPPORTED_INPUT_TYPES` unchanged (flat only). Every core
constraint holds and is test-pinned.

## What was verified

| Area | Verdict | Evidence |
|---|---|---|
| Auth on the two new endpoints | CLOSED | `_verified_operator` + `require("read")` before any filesystem/discovery work; server-derived caps; 401/403 typed; negative tests |
| Read-only catalog/detail | CLOSED | No write primitive in the call graph; byte-identical store+trust-store snapshot asserted before/after a detail call, incl. under snapshot-race |
| Redaction | CLOSED (1 Minor) | Secret-bearing defaults absent from BOTH list and detail; sensitive node values `[REDACTED]`; paths/digests stripped; hostile mermaid labels sanitized; no return-route leak (NF-M1 class) |
| D1 Desktop provenance | CLOSED | Local session-token → `desktop`, remote token → `api`, OAuth → `desktop`; forged headers ignored; existing-source digests pinned byte-stable by golden fixtures |
| POST /runs background-only | CLOSED | Zero `advance`/scheduler refs in the admission path; e2e monkeypatches `advance` to raise AND asserts node `ready` at response time |
| Scope creep (core files) | NONE | coordinator_store (+205) = read-only health snapshot via `query_only=ON` copy, election/fence/lease untouched; trust (+181) = read-only classifiers, no trust writes on the read path; sanitize/store = strengthened, not weakened |
| Idempotency safety (marquee) | CLOSED | Key minted once per modal via lazy `useState`, reused on retry, double-click-coalesced; tests prove one POST / one key on 503 + transport-failure retries |
| Server-outcome rendering | CLOSED | 202/existing/409/503/422 each distinct; 409 correctly has no retry; retries reuse the same key |
| IPC error contract | CLOSED | New `hermes:api:structured` channel, additive; normalizes both `detail.code` and `error.code`; no message-text parsing on the new path; 22 legacy callers untouched |
| View modal | CLOSED | Reuses secure `mermaid-embed` (strict, single init); full fallback ladder tested; Definition view read-only (no textarea/input/contenteditable), single-fetch cached toggle |
| Datagrid + i18n | CLOSED | Run-disabled reasons, View always enabled, inputs chip variants, corrupt-entry row, loading/empty/error states; identical key sets across all four locales |
| Merge gate | REPRODUCED | 745 passed / 1 skipped (independently re-run, exit 0); +77 fully additive (73 net-new test fns, 0 removed); new test files in green gates + pinned by meta-test |
| Real-middleware e2e | CLOSED | Real FastAPI app + real RunStore/CoordinatorStore/TrustStore; catalog→detail→POST /runs; asserts 202, desktop provenance, background-only, board visibility |
| UAT truthfulness | CLOSED | Fixture-based walkthrough truthfully labeled (not bundled showcase); L-A/L-B recorded; omitted-topology fixture genuinely 101 nodes > 100 bound |
| Base drift | CONFLICT-FREE | 2 base commits ahead touch only the confluence skill; zero overlap with feature files |
| Customization ledger | COMPLETE | Every touched non-test source file recorded, incl. the four surprising core files |

## Findings

- **CF-1 (Minor) — catalog-list `description` redaction parity.** The catalog
  *list* `description` is the raw definition description passed only through
  the generic `sanitize_projection`, which (unlike the detail path's
  `_complete_projection`) does not redact absolute/home paths. An author who
  embedded a filesystem path in a workflow *description* would have it surface
  in the list response but not in detail. Author-controlled display prose and
  secret-pattern strings are still caught, so low impact — but a real
  asymmetry. Fix: route the list description through the same
  `_complete_projection` path-redaction (or assert parity), and add a
  path-in-description list test.
- **CF-2 (Minor — fix before branded release) — upstream-domain leak in a
  user-facing link.** `catalog.tsx:18`
  `WORKFLOW_DOCS_URL = 'https://hermes-agent.nousresearch.com/docs/...'` renders
  on the empty-catalog state, so a branded OTTO/LOOP24 user with no workflows
  clicks "Docs" and lands on upstream Nous Research documentation. Lowercase-glued
  so the build transform does not rebrand it (same class as the known intentional
  reinstall-URL leaks, but this one is new and user-facing). Not a workflow-safety
  issue, but it should be a branded docs URL (or the link removed) before the
  branded v3.0.1 release ships. Note a real docs page now exists at
  `website/docs/user-guide/features/workflows.md`.
- **CF-3 (Low) — admission `resolve()` → `abspath` change.** `store.py:~2151`
  changed the workflow-path relative computation from `.resolve().relative_to(...)`
  to `os.path.abspath(...)`, no longer canonicalizing symlink components (done to
  align keys with the resource-budget cache's logical key). Safe as reasoned
  (resource symlinks are already rejected in `_contained_resource`; both sides use
  the same abspath basis so containment stays self-consistent), but it is the one
  non-additive semantic change in reviewed admission code. Confirm no caller passes
  a symlinked `package.root`.
- **CF-4 (Low/ack) — provenance `desktop` → `local_admin_claim` broadening.**
  `desktop` was added to the `local_admin_claim` source set and
  `authenticated_api` gained a `source` param (fail-closed guard `source in
  {"api","desktop"}`). This is the intended D1 behavior. Trust-model ack: correct
  — the local Desktop session genuinely IS a local-admin claim (not a verified
  adapter), so `desktop`/`local_admin_claim` is honest; the return-route
  prohibition on local-admin claims is preserved (no security regression).
- **CF-5 (Info) — legacy inspector-mutation 409 regex.** `index.tsx:39`
  `isConflict` uses `/^409/.test(error.message)` on the legacy `api()` run-mutation
  path (inspector actions) — outside the new structured contract (which correctly
  uses typed `error.code`), so no constraint breach, but it is the exact fragile
  pattern the new contract replaced. Migrate the mutation path onto `apiStructured`
  in a follow-up.
- **CF-6 (Info) — evidence outside the reproduced gate.** "Scoped ESLint 0
  errors", "Workflow UI 110/110", and "native portability 208/3" are
  doc-recorded, not part of the reproduced `test_workflow_merge_gate.sh` (which
  covers 745/1 + installed-dist 1 + Desktop 51 + tsc). The doc self-discloses one
  non-blocking padding-warning in `review-run-dialog.tsx`. Re-run the two matrices
  in CI before merge to close the loop.

## Deviations from the plan (all approved in-session, recorded in the verification doc)

Base target (not `main`); CLI parity via `show --json` embedded topology (not the
invalid `--topology --json`); additive serializable IPC error channel; D1
local-admin Desktop provenance (fix after Task 3's OAuth-only initial pass);
fixture-based UAT (bundled showcase not catalog-visible / not flat-input). Each is
recorded with rationale and verified against the code.

## Disposition

No Critical, High, or Important finding on `feat/workflow-catalog-desktop-trigger`.
The feature meets the merge bar. Before merge/release:
1. Fix **CF-2** before the branded v3.0.1 release (branding leak on the empty state).
2. **CF-1** (list-description redaction parity) is a small, worthwhile fix; safe to
   defer to a fast-follow if desired.
3. Confirm **CF-3** (no symlinked `package.root` caller) — quick check.
4. `merge base` into the branch (2 commits behind; conflict-free) before opening
   the PR to `base`.
5. Re-run the ESLint / workflow-UI / portability matrices in CI (**CF-6**).
6. Ships for BOTH brands (OTTO + LOOP24) per the paired-release rule once merged
   to `base` and restamped. L-A/L-B carried on the v3.0.2 backlog.
