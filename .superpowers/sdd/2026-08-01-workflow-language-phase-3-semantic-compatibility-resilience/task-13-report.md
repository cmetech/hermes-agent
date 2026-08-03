# Task 13 Report: Persistent Session Recovery

## Outcome

Implemented source-sensitive recovery for confirmed-missing cross-run persistent
sessions under the Archon v3 execution contract. Same-run missing shared context
fails closed, ambiguous or operational registry failures never trigger a fresh
provider request, and legacy profiles retain their prior behavior.

Successful execution now returns a private generation-CAS candidate. The store
atomically records the winning node completion and a corroborated private
registry obligation before the coordinator applies it. Pre-provider journal
capacity transfers from the worker claim to that obligation, survives rebuild,
and is released only after a durable reconciliation outcome.

Coordinator reconciliation distinguishes replacement, idempotent prior apply,
and a newer winner; retries operational failures at 1, 2, 4, 8, and 16 seconds;
then leaves the run in `recovery_pending` until ordinary operator resume.
Cancellation and finalization wait for the winning obligation, and provider work
is never replayed.

Public evidence contains bounded identifiers and hashes only. Raw session IDs,
registry keys, fingerprints, histories, paths, and provider content remain out
of public projections and evidence. The Phase 3 compatibility catalog contains
the new missing-session selection, failure, and pending codes.

## TDD Evidence

- RED: confirmed absence/source classification, active-claim selection,
  pre-provider reserve refusal, atomic completion/CAS, exact retry ordering,
  cancellation ordering, and fail-closed rebuild tests failed before their
  respective implementations.
- RED: the obligation-reserve invariant exposed that worker-claim release also
  released post-completion journal capacity; a durable obligation reserve and
  crash rebuild closed the gap.
- RED: a fresh-worker exception left selection evidence at
  `fresh_start_selected`; exception paths now durably record
  `fresh_execution_failed` without creating a registry obligation.

## Verification

Focused recovery suite: 16 passed, 0 failed.

Required ten-file gate via `scripts/run_tests.sh`: 238 passed, 0 failed.

Ruff passed on every changed Python source/test file, and `git diff --check`
reported no whitespace errors.

## Concerns

None blocking. Reconciliation remains deliberately fail-closed: registry
operational errors require bounded retries/operator resume rather than treating
uncertainty as confirmed absence.
