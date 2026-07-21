# Workflow Showcase Desktop Run Verification

Date: 2026-07-20
Target: `base`
Feature branch: `feat/workflow-showcase-desktop-run`
Release intent: v3.0.2 for both OTTO and LOOP24

## Result

The v3.0.1 L-A/L-B limitations are closed without weakening the showcase
distribution boundary. Desktop lists every digest-verified bundled showcase,
keeps same-name user and showcase rows source-distinct, renders the existing
redacted View projection, and admits `approval-gate` and `resilience` through
the authenticated background-only API. `ai-extensions`, `scheduling`, and
`laptop-diagnostic` remain visibly and honestly CLI-only.

No store schema change, new `HERMES_*` setting, generic-host workflow import,
foreground HTTP execution, user trust-store grant, push, merge, tag, release,
or publication was introduced.

## TDD and commit evidence

| Task | Commit | RED boundary | GREEN evidence |
| --- | --- | --- | --- |
| Design and amendments | `7e94de180`, `3f3c2b0d0` | Implementation was withheld pending the security design and maintainer decisions. | Five decisions, corrected scheduling rationale, caching policy, accepted namespace note, and v3.0.3+ backlog recorded. |
| 1 — approval package | `a23f2c5af` | Catalog/evidence tests failed because `approval-gate` and its registered digests did not exist. | The parameterless approval package, catalog entry, digests, and evidence claim passed. |
| 2 — verified loader | `d505a79f1` | Loader tests exposed missing bounded/cache behavior and execution-strict compatibility on the visibility path. | Rootless bounded verification, digest/signature cache invalidation, coalesced misses, permissive compatibility projection, strict CLI execution, and the 2-of-5 eligibility table passed. |
| 3 — catalog/detail | `bf52075e0` | Source-aware list/detail, absence degradation, wrong-source 404, tamper omission, and compatibility anti-suppression tests failed. | All five verified rows, exact source identity, atomic integrity omission, per-scenario incompatibility, redaction, and read-only behavior passed. |
| 4 — admission | `23f61bc5e` | Showcase POST, stable duplicate admission, incompatibility/preflight rejection, fresh tamper verification, and collision tests failed. | Verified-bundle metadata, strict compatibility/preflight, server-derived Desktop provenance, background-only admission, stable idempotency, and no-residue failures passed. |
| 5 — source transport | `903346f8d` | Renderer transport tests failed because source was absent from detail/cache/POST identity. | Exact source reached detail, React Query identity, Review, cancellation, and POST while existing profile/UUID behavior remained stable. |
| 6 — Desktop policy | `603a58b2b` | The policy module was missing and verified bundles rendered as untrusted Project rows; four UI suites failed at the new assertions. | 69 focused tests, then 117 workflow UI tests, passed with one shared trust predicate, authoritative `run_support`, all-locale labels, incompatibility badge, and accessible CLI guidance. |
| 7 — real middleware | `dee1904f6` | Structural membership failed because the prospective E2E was absent from both gates. | The unmocked middleware E2E passed and its membership test pinned the focused gate and native matrix. |
| 8 — docs/UAT | this document's commit | The docs gate failed because bundled source/trust, exact CLI-only coverage, and the approval walkthrough were absent. The first full gate also found two stale v3.0.1 assertions. | Docs gate passed; stale exact-list and four-showcase count assertions became behavioral user-row and verified-package invariants; full gates and real Electron UAT passed. |
| 8.5 — adversarial remediation | remediation commit | A transient parser-reopen test accepted definition/sidecar state that differed from authenticated bytes, and an instrumented overlong tree proved the entry bound ran after eager enumeration. | Parsing now consumes the authenticated byte snapshot and incremental non-following enumeration stops at the configured entry bound; the focused schema/showcase/admission/E2E selection passed 109 tests. |

The stale test updates removed no behavior. The renamed “four showcases” test
now proves named verified packages rather than freezing a count, and the
catalog E2E still asserts its exact user/error rows while separately requiring
the verified approval showcase.

## Security and behavior evidence

- List and detail load showcases only through the verified bundle loader.
  Catalog digest, package tree digest, symlink/path safety, and bundled
  provenance remain atomic integrity gates. A failure omits the bundle or
  returns the typed detail failure; it is never projected as mere
  incompatibility.
- Environment incompatibility is scenario-local. With MCP unavailable,
  `ai-extensions` remained visible as **Incompatible** while
  `approval-gate`, `resilience`, `scheduling`, and `laptop-diagnostic`
  remained visible.
- Catalog verification is cached by bundle digest plus a tree signature and
  invalidated by changed bytes. Admission forces fresh verification.
- Definition and sidecar parsing consume the exact bytes authenticated by the
  verification budget; they are not reopened between digest verification and
  risk/snapshot construction. Tree enumeration is bounded while walking, not
  after full recursive materialization.
- List/detail byte snapshots proved no mutation of the run store or trust
  store. A verified bundle requires no user trust action and never writes a
  user trust record.
- Admission re-derives run support, compatibility, risk/package digests, and
  execution preflight; failures are typed/nonretryable where appropriate and
  persist no run or staging residue.
- The real middleware test patches `RunScheduler.advance` to raise. POST still
  returns 202 with the approval node `ready`, proving the request path neither
  advances nor invokes the foreground showcase tour.
- Durable metadata separates trigger provenance from showcase identity:
  `trigger=desktop`, `execution_mode=background`, and provenance
  `source=desktop`; `showcase_provenance=verified_bundled` is run metadata.
- Existing-source provenance goldens and start digests remained byte-stable;
  repeated admission of the same showcase produced the same start digest and
  joined the existing run.

## Real-middleware E2E

`tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py` used the real
mounted web application, session middleware, REST routes, showcase loader,
admission service, SQLite store, and board query. No catalog, detail,
admission, or store mock was used.

The path proved:

1. catalog row `approval-gate` was `showcase` / `verified_bundled` / supported;
2. source-exact detail returned Mermaid and `[REDACTED]` approval content;
3. list/detail left the run and trust stores byte-identical;
4. authenticated POST returned 202 and a newly admitted run;
5. its approval node remained `ready`, execution mode was background, and
   provenance was server-derived Desktop;
6. verified bundle/risk digests were durable metadata; and
7. the new run appeared through `GET /runs?view=board`.

## Real Electron UAT

The real Electron renderer was launched against isolated user-data, Hermes
home, and project directories. The renderer was driven through Electron's
Chrome DevTools Protocol. Electron main/preload, the authenticated structured
bridge, real headless `hermes serve` backend, coordinator, REST middleware,
SQLite stores, and approval mutation were not mocked.

No `run_showcase`, copied package, direct `RunScheduler.advance`, trust
injection, or mocked middleware was used.

1. Workflows listed all five bundles as **Bundled showcase** / **Verified
   bundle**. `approval-gate` showed **No inputs** and enabled View/Run.
   MCP-unavailable `ai-extensions` appeared **Incompatible** rather than
   disappearing or suppressing its four siblings.
2. View rendered the approval Mermaid topology. Definition was a non-editable
   redacted projection with `nodes[0].value == "[REDACTED]"`.
3. Review & Run showed **Verified bundle**, package/risk digests, no inputs,
   and no trust action.
4. **Start workflow** used the authenticated Desktop POST, opened Active, and
   created run `22e755bac1d64525968819d2c6879345`.
5. The coordinator moved the run from running to paused and displayed an
   Attention item with “workflow approval · Approve” and the bundled approval
   message.
6. Selecting the real **Approve** action while the Attention item and selected
   run were visible resumed execution; the run reached `succeeded`, progress
   `1/1`, and left Attention.
7. The durable projection reported `desktop`, `background`,
   `local_admin_claim`, `showcase_id=approval-gate`, and
   `showcase_provenance=verified_bundled`; its approval node was `succeeded`.
8. `laptop-diagnostic` View rendered its topology, while Run remained disabled
   with “Run this bundled showcase from the CLI.”
9. `ai-extensions` and `scheduling` also remained CLI-only. AI consent is not
   bypassed; scheduling remains outside background admission because cron
   creation and exact-ID/nonce cleanup live in the CLI wrapper.

## Gate results and baseline reconciliation

The full base gate at `dee1904f6556a73a7182bedb2781b4e306f14fc5`
reported:

- Python: **767 passed, 1 skipped in 60.76s**;
- installed-distribution integration: **1 passed in 4.18s**;
- Desktop merge selection: **84 passed across 11 files in 2.27s**.

Against the recorded 745/1, 1, and 51/9 baseline:

- Python is **+22 passed, +0 skipped**: merge-gate structure +1, catalog API
  +6 collected cases, detail API +4, showcase catalog/loader +10, and real
  middleware E2E +1. No selected test was removed; the former exact-count
  showcase assertion was rewritten as a stronger invariant.
- Installed integration is **+0**; the existing test was strengthened to
  prove the registered approval package is installed and verified.
- Desktop is **+33 tests and +2 files**: newly selected catalog `index` +27,
  new policy table +3, Review +1, and View +2. No Desktop test was removed.

Additional verification:

- full Desktop workflow suite: **117 passed across 16 files in 2.48s**;
- renderer and Electron TypeScript projects: typecheck passed;
- scoped ESLint: zero errors, one pre-existing blank-line warning in
  `review-run-dialog.tsx`;
- focused showcase/admission selection: **71 passed across 4 files in 34.1s**;
- docs/production-gate contract: **2 passed in 0.3s**;
- customization ledger validation and `git diff --check`: passed.

After Task 8.5 remediation, the full base gate was repeated on the working
tree based at `89a21ac2ad734183002a02e2256e219874e05597`:

- Python: **769 passed, 1 skipped in 60.44s** — exactly the prior 767/1 plus
  the two authenticated-byte/enumeration regression tests;
- installed-distribution integration: **1 passed in 3.97s**;
- Desktop merge selection: **84 passed across 11 files in 2.06s**.

The remediation-focused schema/showcase/admission/real-middleware selection
also passed **109 tests across 4 files in 33.8s**. No test was removed or
rewritten to reduce coverage.

## Paired-brand rehearsal

Two temporary detached worktrees were created at the tested feature SHA. For
each brand, the normal generator ran with `--write`, a subsequent `--check`
reported all eight emitters `OK`, and the brand merge gate passed:

- OTTO: 8/8 emitter checks, `TESTED_BRAND_SHA=dee1904f6556a73a7182bedb2781b4e306f14fc5`;
- LOOP24: 8/8 emitter checks, `TESTED_BRAND_SHA=dee1904f6556a73a7182bedb2781b4e306f14fc5`.

Both detached worktrees were removed. No OTTO, LOOP24, base, release, or other
branch ref was created or updated.

## Plan deviations and accepted notes

- The approved plan initially applied execution-strict compatibility to the
  loader/list path. It was corrected to established v3.0.1 visibility:
  integrity stays atomic and fail-closed, while per-scenario environment
  incompatibility remains visible and honest. CLI execution and API admission
  remain strict.
- A proposed scheduling reclassification was withdrawn. It remains CLI-only
  for the architectural reason that schedule creation/ownership lives in the
  wrapper, not the admitted package.
- The plan's rehearsal commands said “materialize” but invoked bare
  `generate.mjs <brand>`, which is check-only. Execution stopped on that
  contradiction. With maintainer approval the plan was corrected to
  `--write`, followed by an explicit 8/8 `--check`, before each brand gate.
- The full gate exposed two stale v3.0.1 change-detector assertions. They were
  converted to relationship/invariant assertions rather than weakening or
  deleting coverage.
- The first adversarial review found a verified-byte/parser TOCTOU gap and a
  post-materialization tree-entry bound. Task 8.5 reproduced both with RED
  tests and corrected them before restarting the completion review.
- `concurrency_key = "showcase:<id>"` shares the user-authored concurrency-key
  namespace. Deliberate collision can cause contention only, not source
  confusion or incorrect execution; this remains accepted awareness.

The v3.0.2 backlog retains background scheduling, the AI-consent/architecture
pass, and rich Desktop inputs. After this release, 2 of 5 showcases are
Desktop-runnable and all 5 are visible.
