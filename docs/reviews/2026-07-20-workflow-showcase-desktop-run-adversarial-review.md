# Workflow Showcase Desktop Run — Adversarial Review

Date: 2026-07-20
Target: `base`
Reviewed range: `origin/base..65e54b784f423d7943892e6cfd660b02741ba6e9`
Release intent: v3.0.2 for OTTO and LOOP24

## Verdict

**READY FOR MAINTAINER REVIEW — zero Critical, High, Medium, or Low open
findings.**

The review was restarted after each security remediation rather than treating
the original findings as waived. The final clean pass found no remaining
security, correctness, or merge-blocking issue. Nothing was pushed, merged,
tagged, released, or written to `main`, `base`, a brand branch, or a release
branch.

## Scope and method

The review covered the bundled showcase loader, digest cache, catalog/detail
projection, background API admission, source identity transport, Desktop trust
and run policy, all four locales, the new approval package, real middleware
E2E, installed-wheel coverage, merge-gate membership, documentation, and the
upstream customization ledger.

The final pass used a threat-model-driven source review plus fresh focused and
full-gate execution. Earlier review passes deliberately used adversarial
filesystem replacements, bounded-enumeration instrumentation, package-ancestor
symlinks, distribution tampering, incompatible environments, wrong source
selectors, forged/unverified copies, and an exception-raising
`RunScheduler.advance` seam.

## Threat model

Assets protected:

- the authenticated bundled workflow bytes and their catalog/package digests;
- the distinction between verified bundled content and user-authored copies;
- the run/trust stores and immutable run snapshot;
- authenticated operator identity, Desktop trigger provenance, and
  idempotency namespace;
- the no-inline-execution HTTP boundary; and
- secrets, absolute paths, home paths, and hostile Mermaid content in catalog
  projections.

Trust boundaries:

- installed showcase distribution to verified loader;
- verified loader to read-only catalog/detail projection;
- authenticated Desktop request to server-side admission;
- admission to coordinator-owned background execution; and
- server response to renderer policy and presentation.

Attacker capabilities considered include modifying bundled files, replacing
files between verification and parsing, inserting symlinks at leaf or ancestor
paths, copying a valid-looking package outside the distribution, colliding a
user workflow name or concurrency key, forging source/provenance fields,
submitting unsupported inputs, and making one scenario environment-incompatible.
Compromise of the local administrator account or the signed release pipeline is
outside this feature's threat model.

## Security findings and closure

### SR-1 — authenticated-byte/parser TOCTOU (resolved)

- Severity before remediation: Medium.
- STRIDE: Tampering / Elevation of Privilege.
- Reachability: the verified loader authenticated package bytes, then the
  generic parser reopened the workflow and sidecar. A transient replacement
  could therefore make parsed/admitted content differ from authenticated
  content while the cached signature remained stable.
- Reproduction: the RED regression accepted a transient unverified definition
  and sidecar.
- Remediation: `load_workflow_snapshot` parses caller-authenticated bytes
  without reopening files (`plugins/workflow/schema.py:915-1054`), while the
  showcase loader requires both definition and optional sidecar from the
  sealed verification budget (`plugins/workflow/showcase.py:404-458`).
- Verification: the exact-byte regression at
  `tests/plugins/workflow/test_showcase_catalog.py:188` passes.

### SR-2 — entry bound applied after eager enumeration (resolved)

- Severity before remediation: Medium.
- STRIDE: Denial of Service.
- Reachability: recursive enumeration previously materialized the entire tree
  before enforcing `max_files`, so the intended list-path latency/memory bound
  did not bound directory traversal.
- Reproduction: an instrumented overlong tree observed enumeration beyond the
  configured limit.
- Remediation: `_tree_entries` uses non-following incremental `os.scandir`,
  checks the limit before retaining the next entry, and sorts only the bounded
  collection (`plugins/workflow/showcase.py:145-168`).
- Verification: the counted-enumeration regression at
  `tests/plugins/workflow/test_showcase_catalog.py:263` passes.

### SR-3 — package-ancestor symlink acceptance (resolved)

- Severity before remediation: Medium.
- STRIDE: Tampering / Elevation of Privilege.
- Reachability: a package directory or `packages/` ancestor symlink was
  accepted by the unbudgeted CLI loader; the verified Desktop loader rejected
  it only later as a generic cache miss.
- Reproduction: four RED cases covered CLI and verified loading with both
  ancestor positions.
- Remediation: containment rejects every lexical symlink component from bundle
  root through the selected workflow, and enumeration rejects a symlink scan
  root (`plugins/workflow/showcase.py:145-150,226-240`).
- Verification: the four-case regression at
  `tests/plugins/workflow/test_showcase_catalog.py:314-355` passes.

## Final STRIDE review

| Category | Result | Evidence |
| --- | --- | --- |
| Spoofing | Closed | Showcase admission ignores client trust claims, force-reverifies the installed distribution, and derives `trigger_source` from authenticated authority (`plugins/workflow/api_admission.py:100-146,266-280`). A copied explicit catalog is not bundled provenance. |
| Tampering | Closed | Catalog, package tree, path/symlink safety, exact authenticated parse bytes, package/risk digest re-derivation, sealed resource budget, and immutable snapshot digest are checked before persistence (`plugins/workflow/showcase.py:133-251,404-489`; `plugins/workflow/api_admission.py:171-250`). |
| Repudiation | Closed | Durable metadata separates `trigger=desktop` from `showcase_provenance=verified_bundled` and records bundle/risk digests (`plugins/workflow/api_admission.py:257-280`). Existing trigger-source digest goldens stayed byte-stable. |
| Information disclosure | Closed | Showcase list/detail reuse the catalog qualification and complete projection redactor; definition secrets/paths and hostile Mermaid labels remain redacted. The real middleware E2E asserts `[REDACTED]` (`tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py:89-98`). |
| Denial of service | Closed | Showcase verification has per-file, aggregate-byte, file-count, and incremental traversal bounds; cache hits are keyed by authenticated bundle digest plus tree signature. Showcase bytes do not consume the user-row truncation allowance (`plugins/workflow/catalog_api.py:550-638`). |
| Elevation of privilege | Closed | Verified bundles bypass user trust actions only after bundled provenance verification. API admission independently enforces background eligibility, compatibility, preflight, coordinator health, and immutable snapshot identity before `start_run` (`plugins/workflow/api_admission.py:160-283`). |

## Required invariants

- **Integrity is atomic.** Catalog digest mismatch, package tree mismatch, or a
  safety/symlink violation omits the entire showcase distribution or returns a
  typed verification failure. It never becomes an “incompatible” row.
- **Compatibility is scenario-local.** With MCP unavailable,
  `ai-extensions` remains visible and honestly incompatible while the other
  four showcase rows remain visible. CLI execution and API admission still
  reject an incompatible scenario.
- **Visibility is read-only.** List/detail require read authority and tests
  prove byte-identical run and trust stores before/after. View remains enabled
  for an incompatible or run-disabled verified row.
- **Execution is fail-closed.** Admission force-reverifies, re-derives support,
  compatibility and digests, runs execution preflight, checks coordinator
  health, and persists nothing on a failed check.
- **HTTP is background-only.** The handler admits an immutable snapshot with
  `execution_mode="background"`, wakes the coordinator, and returns 202. The
  real E2E patches `RunScheduler.advance` to raise and observes the approval
  node still `ready` (`tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py:54-124`).
- **Identity is unambiguous.** `(catalog_source, name)` is transported through
  detail query identity, modal transitions, and POST. Wrong-source detail is a
  typed 404, and same-name user/showcase rows coexist.
- **Run policy remains 2 of 5.** `approval-gate` and `resilience` are Desktop
  runnable. `ai-extensions` and `scheduling` return
  `showcase_cli_required`; `laptop-diagnostic` returns
  `unsupported_inputs`. The renderer consumes authoritative `run_support` and
  permits trust only for `trusted` or `verified_bundled`
  (`apps/desktop/src/app/workflows/catalog-run-policy.ts:1-5`).
- **Idempotency is stable.** Existing-source digest goldens are unchanged; two
  admissions of the same showcase produce the same start digest and join as
  `existing` (`tests/plugins/workflow/test_desktop_api.py:241-322`).

## Fresh verification

On exact reviewed commit
`65e54b784f423d7943892e6cfd660b02741ba6e9`:

- decisive backend selection: **181 passed, 0 failed in 34.8s**;
- decisive Desktop selection: **76 passed, 0 failed in 2.16s**;
- full base Python gate: **773 passed, 1 skipped in 59.77s**;
- installed-distribution integration: **1 passed in 3.78s**;
- Desktop merge selection: **84 passed across 11 files in 2.20s**; and
- gate marker:
  `TESTED_BASE_SHA=65e54b784f423d7943892e6cfd660b02741ba6e9`.

For both OTTO and LOOP24, separate detached no-ref worktrees at the exact SHA
ran `generate.mjs <brand> --write`, then `--check` with **8/8 emitters OK**,
then the brand gate with
`TESTED_BRAND_SHA=65e54b784f423d7943892e6cfd660b02741ba6e9`.
Both rehearsal worktrees were removed.

The real Electron UAT was completed before the two filesystem-verification
remediations and is recorded in the verification document. It exercised the
actual renderer, authenticated backend, coordinator, approval action, and
durable store from catalog to successful completion. After remediation, the
real middleware E2E, Desktop behavior tests, focused backend suites, full base
gate, and both brand rehearsals were rerun on the final commit; this document
does not misstate the Electron session as rerun at the final SHA.

## Accepted awareness and deferred scope

- `concurrency_key = "showcase:<id>"` shares the user-authored concurrency-key
  namespace. A deliberate collision can cause lane contention only; it cannot
  change source selection, admitted bytes, or execution correctness.
- Background schedule creation remains deferred because cron creation,
  exact-ID/nonce ownership, and cleanup live in the CLI wrapper rather than an
  admittable workflow package.
- Desktop AI consent remains deferred pending verification of whether the AI
  side effect lives in the package or wrapper and, if applicable, a reviewed
  confirmation-token UX.
- Rich Desktop inputs remain deferred; `laptop-diagnostic` is honestly
  CLI-only.

## Completion assessment

The final clean review found no Critical, High, Medium, or Low finding. The
digest-verification boundary, visibility/execution distinction, auth and
redaction invariants, background-only admission, exact source identity,
idempotency, gate membership, installed distribution, paired-brand rehearsal,
and 2-of-5 run policy are all evidence-backed. The branch is ready for the
maintainer's review against `base`.
