# Task 10 report: expose Phase 4 workflow state

Status: DONE WITH UPSTREAM TEST CONCERNS

## Outcome

Exposed the normalizer-v4 interaction and compilation state through the existing
workflow operator surfaces without activating v4. A paused
`loop_signal_confirmation` now has one backend-owned interaction/version/action
contract across CLI status, REST run detail, attention, notification history, and
evidence. Gateway adds the verified `provide-input` mutation, forwards the adapter
principal/channel/operator scope to the existing compare-and-set store operation,
and returns a bounded current public projection on version conflict.

`show`, `validate`, `doctor`, and catalog detail now retain the authenticated Phase 4
compilation and expose a bounded, body-free projection containing profile and schema
versions, selected dependency sources and precedence, expanded counts/include depth,
the composite digest, ignored child policy fields, logical node/resource origins,
and per-origin risk. Doctor reports stable sealed-closure and future-admission
revalidation findings and reads MCP environment references from sealed compilation
bytes instead of rediscovering included resources beneath the root package.

The public action vocabulary remains the existing `status`, `events`, `approve`,
`reject`, `provide-input`, `resume`, `retry`, `reconcile`, `cancel`, `abandon`,
`archive`, and `restore` verbs. The Archon current normalizer remains pinned to v3.

## Files changed

- `plugins/workflow/actions.py`: named the stable wire-action vocabulary.
- `plugins/workflow/gateway_command.py`: added verified Gateway feedback dispatch and
  bounded conflict refreshes.
- `plugins/workflow/dashboard/plugin_api.py`: classified signal confirmations as
  attention and allowed catalog-detail compilation diagnostics; invalid REST
  transitions now include current public state.
- `plugins/workflow/notifications.py`: classified signal confirmations and attached
  the authoritative interaction/version/action facts to direct and reconciled
  notification payloads.
- `plugins/workflow/evidence.py`: attached the current state version and backend
  `next_actions` to pending interaction evidence.
- `plugins/workflow/cli.py`: added bounded Phase 4 compilation diagnostics to
  show/validate/doctor, stable doctor findings, sealed MCP inspection, and a logical
  public package location.
- `plugins/workflow/catalog_api.py`: retained the selected Phase 4 compilation for
  workflow detail and preserved catalog capacity classification.
- `tests/plugins/workflow/test_phase4_surfaces.py`: added cross-surface parity,
  verified Gateway authority, compilation diagnostics, old-vocabulary, and JSON/text
  disclosure tests.
- `tests/plugins/workflow/test_phase4_defensive_invariants.py`: duplicated the
  absolute-path/body/feedback/secret/provider-response disclosure canaries.

## TDD evidence

Every Python test command used `scripts/run_tests.sh`.

1. Cross-surface action parity RED:
   - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_surfaces.py`
   - Valid RED: 0 passed, 1 failed because the signal confirmation was missing from
     attention. The earlier background fixture attempt was discarded because it
     failed admission setup before reaching a production boundary.
   - GREEN: the focused module now passes 2 tests.
2. Compilation diagnostics RED:
   - Same focused command.
   - RED: `show_package()` rejected the new authenticated `compilation` argument.
   - GREEN: show/validate/doctor/catalog detail agree on the bounded closure facts.
3. Disclosure RED:
   - Same focused command with private canaries.
   - RED: structured doctor output disclosed the temporary package root.
   - GREEN: the public doctor payload uses a logical package component and all JSON
     and text canaries remain absent.

## Final verification

- Disclosure/evidence gate:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_evidence_api.py`
  - 46 passed, 0 failed.
- Catalog/detail/doctor gate:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_doctor.py`
  - 153 passed, 1 pre-existing/stale failure described below.
- CLI/Gateway/notification/Desktop gate:
  - `scripts/run_tests.sh tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_cli.py tests/hermes_cli/test_authenticated_plugin_commands.py tests/plugins/workflow/test_notification_delivery.py tests/plugins/workflow/test_desktop_api.py`
  - Task 10 surface, authenticated-command, and notification files passed. The gate
    retained 5 packaged-schema and 18 Task 9 Desktop failures described below.
- Ruff on every touched Python file: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07] == 3`: verified.

## Concerns outside Task 10 scope

- `test_workflow_catalog_degrades_unrepresentable_workflow_name_per_entry` expects a
  forged 129-character package name created with `dataclasses.replace`. Task 9's
  `WorkflowCompilation.__post_init__` now correctly rejects that forged package graph
  before catalog code can observe it, so the safe invalid entry uses its authenticated
  source name (`placeholder`) instead of forged text. Weakening that integrity check
  would violate the Phase 4 defensive invariant.
- Five packaged-schema read-only tests create `SOUL.md`, cache, cron, and related
  startup files in their temporary Hermes home. The same five failures were present
  in the first exact-gate attempt before Task 10 production edits; the remaining 81
  CLI tests pass.
- Eighteen Desktop API tests fail in Task 9 admission/compilation paths outside the
  assigned files. Representative failures are monkeypatched
  `assess_package_execution` callables that do not accept Task 9's `compilation`
  keyword, a 513-node legacy fixture now rejected by the exact dependency-manifest
  bound, and sealed-resource admission fixtures returning 500. No Task 10 production
  file appears in those failure traces.

No Task 11 security-review/UI work or Task 12 normalizer activation was included.

## Fix round 1 — 2026-08-06

Status: DONE WITH VERIFIED PRE-EXISTING GATE FAILURES

### Review findings addressed

1. Human `show`, `validate`, and `doctor` output now uses one shared bounded
   compilation renderer. In addition to the profile/schema versions and composite
   digest, each command prints dependency entries, source/precedence entries,
   expansion counts and include depth, ignored child policies, logical node origins,
   logical resource origins, per-origin risks, and an explicit truncation status.
   Every item passes through the existing body-free sanitizer and every section is
   capped at 200 entries.
2. Gateway `provide-input` now maps a wrong interaction ID and an invalid run state
   to the stable `invalid_transition` code and stable body-free message. Both
   responses include the scoped, bounded current public run projection, matching the
   existing stale-version refresh contract instead of exposing `ValueError`.
3. Disclosure canaries are now authentic runtime values. The provider-response
   canary is written to the loop result artifact and the feedback canary is applied
   through the verified Gateway mutation and read back from the private feedback
   artifact. CLI, Gateway success/conflict/invalid-transition, REST detail/conflict,
   attention, notification history, and evidence projections are checked both before
   and after the mutation; none exposes either body. The defensive test independently
   stores and reads back both private artifacts before checking status,
   notifications, and evidence.
4. The three failing broad-suite files were rerun at exact base commit
   `50002bda88493be80f68891502de3a776e6687d5` in a detached temporary worktree using
   the same virtual environment and clean test runner. The isolated baseline was:
   `test_catalog_api.py` 75 passed / 2 failed, `test_cli.py` 81 passed / 5 failed,
   and `test_desktop_api.py` 139 passed / 18 failed. The temporary worktree was then
   removed. Current-head results are 76/1, 81/5, and 139/18 respectively: Task 10
   improved one catalog capacity case and introduced no failure delta.

### Fix-round TDD evidence

- RED, before production edits:
  `.venv/bin/python -m pytest -q tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_phase4_defensive_invariants.py`
  produced 2 failed / 16 passed. The failures were the missing human diagnostic
  sections and Gateway returning `ValueError` instead of `invalid_transition`. The
  authentic stored-artifact disclosure regression already passed, confirming the
  production redaction boundary did not need weakening or replacement.
- GREEN, after the shared renderer and Gateway transition mapping:
  the same focused command produced 18 passed / 0 failed.

### Fix-round verification

- Disclosure/evidence gate:
  `scripts/run_tests.sh tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_evidence_api.py`
  — 47 passed / 0 failed.
- Catalog/detail/doctor gate:
  `scripts/run_tests.sh tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_doctor.py`
  — 153 passed / 1 baseline failure. All Task 10, detail, doctor, and evidence tests
  passed; the sole failure is the base-reproduced forged-name assertion.
- CLI/Gateway/notification/Desktop gate:
  `scripts/run_tests.sh tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_cli.py tests/hermes_cli/test_authenticated_plugin_commands.py tests/plugins/workflow/test_notification_delivery.py tests/plugins/workflow/test_desktop_api.py`
  — 244 passed / 23 baseline failures. Task 10 surfaces, authenticated commands, and
  notifications passed; the five CLI and eighteen Desktop failures exactly match the
  isolated base counts and failure identities.
- Ruff over every Task 10-touched Python file: passed.
- `git diff --check`: passed.
- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07] == 3`: verified.

### Remaining concerns

The two broad planned gates cannot honestly report zero failures without changing
out-of-scope tests or weakening Task 9 invariants. Exact-base reproduction establishes
that their remaining failures are not caused by Task 10 or this fix round. The
pre-existing concerns documented above remain unchanged; no test was weakened or
edited merely to force a green aggregate result.
