# Workflow v3.0.3 Revision 7 Adversarial Remediation Report

Tested SHA: `3ca92256955cd3fe73675ca5515f3b612cc59dc8`

Remediation base: `e6155f2060e5049a4dd8213da5bf726cfe1c48e5`

Status: the six accepted adversarial findings are implemented, independently
task-reviewed, and green under the complete automated gate. Release readiness
remains blocked on the seven manual-only configured-Gateway/Electron gates.

## Findings, commits, and TDD evidence

### RT-2 — nested forbid-overlap outbox write

Commit: `26385c6c5bf7c6c006adbf80dfb50dad336a052b`
(`fix(workflow): defer overlap failure notification`).

RED:

```text
.venv/bin/python -m pytest -q tests/plugins/workflow/test_scheduled_runs.py \
  -k forbid_overlap_defers_outbox_until_after_promotion_transaction
AssertionError: outbox record called during promotion transaction
```

The no-I/O fake proved `_append_locked()` attempted
`NotificationOutbox.record()` while promotion held its transaction. GREEN was
`1 passed, 24 deselected`; the complete scheduled/notification selection was
25 plus 12 passing tests. The terminal `run_failed` journal entry remains
atomic, the promotion has zero worker claims, and ordinary journal
reconciliation creates exactly one failure fact/outbox row. A second
reconciliation creates no duplicate.

Fresh task review: spec-compliant and quality approved, Critical 0 /
Important 0 / Minor 0.

### B-1 — leadership-term runtime capability staleness

Commit: `521b214c962ac4ff1bcd8c12baf789e758185bfe`
(`fix(workflow): refresh scheduled runtime capabilities`).

RED: the three-test binding/admission/fire-time selection produced
`3 failed in 0.66s`. One binding returned stale `chat_completions` after a
config change, admission persisted config A instead of current config B, and a
post-admission switch to `codex_app_server` incorrectly succeeded and reached
the trapped runner.

GREEN: the same selection passed `3 passed in 0.50s`; the focused matrix passed
83 tests. Production `execution_context()` now purely reclassifies current raw
config at every consumption. Admission and fire-time use that derivation, and
claim-time entitlement corroboration calls it directly. A post-admission
incapable change terminalizes as `schedule_revalidation_failed` with zero
claims and zero runner/provider requests. Frozen/slotted server authority,
runner objects, and runner capabilities remain unchanged; injected bindings
without a provider retain snapshot behavior.

Fresh task review: spec-compliant and quality approved, Critical 0 /
Important 0 / Minor 0.

### RT-1 — periodic starvation under a saturated due backlog

Commit: `9e3c5baab8556e8f0b58d8ad94425041c0d4d78f`
(`fix(workflow): preserve coordinator sweep progress`).

RED: the real-store regression failed `1 failed in 4.63s` after four bounded
sweeps with `submitted_periodic == []` and cursors
`[None, None, None, None]`. Three selection-contract tests also failed before
the helper existed.

GREEN: five progress/order regressions passed in 5.89 seconds and the focused
coordinator/scheduled suite passed 61 tests. Under saturation, the current
periodic-page head is processed first and the remaining 99 slots keep ordinary
order without duplication. The four running rows progress in periodic-page
order within four deadline-bounded sweeps; cursor advancement remains
prefix-safe. The hard 100-run budget, query/page bounds, and exact unsaturated
global order remain unchanged.

Fresh task review: spec-compliant and quality approved, Critical 0 /
Important 0 / Minor 0.

### RT-4 — admission/fire-time trust-store bound mismatch

Commit: `9acd5e511fd44af1257a883d6073de29ac401a6c`
(`fix(workflow): align scheduled trust bounds`).

RED: a valid trust store above 1 MiB and below 4 MiB produced
`1 failed in 0.27s`; the scheduled run incorrectly failed. GREEN was
`1 passed in 0.29s`, and the complete focused matrix passed 127 tests.
Fire-time revalidation now uses the same canonical 4 MiB read ceiling as
catalog/admission, remains read-only, and creates no trust lock file.

Fresh task review: spec-compliant and quality approved, Critical 0 /
Important 0 / Minor 0.

### RT-3 — whole-index escalation during v13→v14 migration

Commit: `35fdf8f3ff68150762a19aa7a5ce33cd14408436`
(`fix(workflow): scope schedule migration repairs`).

RED: the damaged-row migration regression failed with
`1 failed, 31 deselected`; the admission generation changed from
`3b23c0a82181412da57424bca2cfe97b` to
`172d2a87d94748d88220658708fd4017`, proving whole-index replacement.

GREEN: the identical regression passed, the schedule-identity file passed 32
tests, and the required four-file matrix passed 79. One uncorroborated
published row now keeps derived `scheduled_at=NULL` and enters ordinary
run-scoped `run_evidence_uncorroborated`/`repair_required` reconciliation.
Healthy schedule derivation, database generation/integrity, unrelated
admission, and reserved-row behavior remain unchanged.

Fresh task review: spec-compliant and quality approved, Critical 0 /
Important 0 / Minor 0.

### B-2 — admission clears the authenticated showcase cache

Commit: `3ca92256955cd3fe73675ca5515f3b612cc59dc8`
(`fix(workflow): reuse verified showcase cache`).

RED: the warm-cache regression failed `1 failed, 26 deselected`; it observed
exactly one extra full verification during admission. GREEN passed the same
test and the required matrix passed 260 tests twice.

Admission now shares catalog/detail's signature-checked authenticated cache
entry without replacing it or advancing its generation. Live tree signature
and generation checks still run for every hit; compatibility and risk are
recomputed for each current execution context. Scheduled fire-time
revalidation alone retains forced full verification.

Fresh task review: spec-compliant and quality approved, Critical 0 /
Important 0 / Minor 0.

## Integrated automated verification

Every command below ran at tested SHA
`3ca92256955cd3fe73675ca5515f3b612cc59dc8` with no external network or
billable inference:

- Eight-file integrated resilience gate: **244 passed** in 74.53 seconds.
- Exact Strategy A 22-file Python matrix: **532 passed, 0 failed** in one
  hermetic repository-wrapper run. This is the original 524-test inventory
  plus the eight remediation regressions; no file selection was duplicated.
- Installed-distribution integration: **1 passed**.
- Desktop Workflow UI: **144 passed across 16 files**.
- Renderer and Electron TypeScript compilation: passed.
- Explicit merge-gate inventory: **26 passed**.
- Scoped Ruff: passed.
- Canonical upstream-customization checker: passed with exit code 0.
- `git diff --check e6155f206..HEAD`: clean.
- `git diff --quiet e6155f206..HEAD -- brands/ plugins/model-providers/`:
  clean.

The final documentation commit is not part of the tested production SHA. It
contains no source or test change; its manifest/check and diff hygiene are
verified separately before commit.

## Contract preservation

The remediation production diff is limited to
`api_admission.py`, `coordinator.py`, `runner_binding.py`,
`scheduled_revalidation.py`, and `store.py`. It does not change either
start-digest implementation, legacy metadata construction, REST input shape,
scheduled-time authority, MCP worker lifecycle, entitlement classification,
generated brand, or model-provider surface.

- Existing unscheduled and non-AI digest behavior stays byte-identical under
  the Strategy A entitlement/golden/idempotency coverage.
- No request or caller-supplied runner/capability authority was added.
- `run_metadata.schedule_at` remains canonical; SQLite `scheduled_at` remains a
  derived index field.
- Required node MCP still fails before provider traffic; cron's optional
  profile-global MCP behavior and `codex_app_server` incompatibility are
  untouched.
- Journal authority and one-use promotion authorization remain intact.
- RT-1 now guarantees bounded periodic progress under saturation.
- RT-2 performs no nested outbox transaction and reconciles exactly once.
- B-1 refreshes current runtime classification for admission, fire-time, and
  claim-time context consumption.

## Deferred and pre-existing review items

All non-remediated findings and their rationale are retained in
`docs/backlog/v3.0.3-release-review-deferrals.md`. In summary:

- Lower-confidence/hardening: RT-5, RT-6, B-3, SK-RT-02, SK-CC-A, SK-CC-B.
- Disputed/unverified: CC-2 provenance, RT-6 fix shape, restart-time duplicate
  notification delivery, and the unavailable cross-provider review lane.
- Nits/decisions: CC-4, CC-6, CC-7, SK-CC-C, SB-02, SB-3.
- Pre-existing: CC-1, CC-2, CC-3, CC-5, and the self-healing
  non-`RuntimeError` claim leak.

None is silently treated as fixed by this remediation.

## Outstanding manual-only release gates

No mock substitutes for these operator-owned gates:

1. Configured-Gateway structured `tool_calls` round trip for OTTO.
2. Configured-Gateway structured `tool_calls` round trip for LOOP24.
3. Electron laptop-diagnostic approval and rejection.
4. Electron ai-extensions through the configured Gateway to success.
5. Electron Run later → Scheduled → due success.
6. Electron cancel-before-fire.
7. Electron approval-gate regression.

The automated remediation is green; release readiness remains blocked until an
authorized operator completes all seven gates.
