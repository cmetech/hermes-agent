# Workflow Orchestration Operator Robustness Design

**Date:** 2026-07-19
**Status:** Approved design, pending written-spec review
**Branch:** `feat/workflow-production-remediation`

## Purpose

This follow-up hardens the low-severity edges that most affect whether workflow orchestration feels dependable to operators and machine callers. Each change converts a silent, confusing, or indefinitely accumulating condition into behavior that is bounded, visible, and recoverable.

The scope is the nine operator-robustness fixes selected from the two adversarial reviews, plus four focused regression-test additions. Five low-risk findings remain recorded debt by explicit decision.

## Architectural constraints

- Workflow behavior remains plugin-owned. Generic host and lifecycle files must not import the workflow plugin.
- Hermes core remains a narrow waist. This work adds no permanent model-facing tool and does not mutate a conversation's system prompt, toolset, or history.
- User-facing non-secret configuration remains in `config.yaml`; no new `HERMES_*` setting is introduced.
- RunStore, journals, the notification outbox, delivery receipts, and route capabilities remain authoritative. CLI, Desktop, Gateway, and dashboard views are projections.
- Background mutations remain bounded and return promptly. No HTTP or Gateway route executes a workflow tail synchronously.
- Missing, empty, corrupt, inconsistent, unreadable, or unsafe evidence fails closed. Cleanup never deletes a run when its evidence cannot be corroborated.
- Outward effects are never replayed while a prior delivery outcome is uncertain.
- Existing authority, destination binding, coordinator fencing, and claim-retention contracts remain intact.
- Cross-machine sharing of a workflow database remains unsupported. Monotonic-clock corroboration is meaningful only within the persisted boot identity.

## Alternatives rejected

### Patch only the visible surface

Patching only the CLI message, attention rendering, or one ingress route would leave sibling APIs and durable state inconsistent. This is specifically rejected because it preserves the drift that produced the reviewed findings. Each fix is made at the narrow authoritative seam and is exercised through its affected real surface.

### Introduce a shared orchestration framework

A new pagination, lease, cleanup, or retention framework would add broad permanent surface to a branch that has already completed multiple review cycles. The selected fixes instead reuse the existing store, cursor, lease-clock, journal, delivery-receipt, and reload seams. Small local helpers are acceptable where they make an invariant explicit, but no new cross-cutting subsystem is introduced.

## Operator-robustness contracts

### 1. NF-L2: attention inbox returns the newest items with a real cursor

The attention endpoint must return a stable, newest-first page rather than sorting oldest-first and truncating at 100.

- Ordering is descending by the item observation timestamp, followed by a deterministic, fully unique tie-break key containing the item kind and its durable identity.
- The endpoint fetches `limit + 1` eligible items and returns a non-null `next_cursor` when another page exists.
- The cursor is opaque, signed, versioned, and bound to the authenticated operator scope and the attention query kind. It carries the snapshot boundary and the complete ordering key needed for keyset continuation.
- Pagination must cover both run-derived attention and notification-derived attention. It must not retain the current hidden 100-item result truncation or a fixed run-scan cap that can hide newer eligible items.
- Source reads remain bounded through attention-specific keyset queries that apply the attention predicate before `LIMIT`. The endpoint merges those bounded source pages and carries both source continuation positions in the signed composite cursor.
- Subsequent pages cannot duplicate or skip an item within the cursor snapshot, even when timestamps collide.
- Existing authorization and response sanitization remain unchanged.

### 2. NF2-L5 and NF-L13: queue-policy starts queue at capacity

A start using queue admission policy is accepted as a durable queued run when execution capacity is full, provided the configured nonterminal, storage, or rate bounds are not themselves exhausted.

- The ingress decision uses the same admission predicates as the central scheduler path.
- A full executing-capacity count alone is not a typed rejection for a queue-policy start.
- Same-lane FIFO and eligible-predecessor ordering remain intact.
- Held or un-actioned work in another lane does not globally freeze admission.
- A `forbid` policy continues to reject when its policy requires it; a permitted bypass continues to bypass only the restriction it is designed to bypass.
- A typed capacity rejection remains correct when the bounded queued/nonterminal population, storage budget, or another actual configured admission ceiling is reached.
- The accepted mutation persists the run as queued, wakes the coordinator, and returns promptly without advancing the run synchronously.

### 3. Foreground adoption is visible to humans and machine callers

When the coordinator adopts a foreground CLI run, the CLI must make the handoff explicit.

- Human mode emits one clear notice: the run was adopted by the background coordinator, it continues running, and it can be watched with `workflow status <run-id>`.
- JSON mode emits no prose outside the canonical envelope.
- The final JSON status payload contains `execution_mode: "background"` and explicit transition evidence naming `foreground_execution_adopted`, so a skill or chat agent can accurately explain the handoff.
- The transition evidence comes from durable run projection or journal evidence, not an inference based solely on the foreground process exiting.
- A normal foreground completion and a genuine foreground failure retain their existing messages and exit semantics.

### 4. NF-L10: all top-level CLI errors honor the JSON contract

Machine callers must receive the same canonical JSON envelope for parse-time errors as for command-handler errors.

- An invalid workflow subcommand used with `--json` exits with the established usage exit code and emits exactly one canonical error envelope on stdout.
- Raw `argparse` usage or error text is not emitted to stderr in JSON mode.
- Human mode retains helpful parser usage and error output.
- `OSError` failures are mapped to a stable, sanitized error message/code. Absolute paths, profile locations, and implementation-specific exception text are not exposed in JSON or human-facing output.
- Valid command dispatch and existing envelope fields remain backward compatible.

### 5. NF-L9: an oversized first repair journal is processed, not skipped

The notification repair cursor must always make progress without creating a permanent blind spot for the first run whose journal exceeds the remaining page-byte budget.

- If the first eligible journal in a repair page exceeds the remaining byte budget, that journal is processed anyway.
- This permits at most a single-row budget overrun, bounded by the journal quota already enforced by the writer.
- The cursor advances past that run only after its terminal facts have been evaluated and any missing notification projection has been durably repaired or durably determined unnecessary.
- Later rows remain subject to the ordinary byte and row budgets.
- Unsafe, unreadable, or corrupt evidence continues to fail closed and is not silently skipped.
- The defining regression assertion is that the oversized run's missing terminal notification is eventually repaired, not merely that the cursor value changed.

### 6. NF-L8: cleanup cannot quarantine an unprojected terminal notification

Cleanup eligibility must account for the crash gap between a terminal journal commit and notification-outbox projection.

- Before a cleanup candidate can be selected or confirmed, the plugin performs bounded notification reconciliation for that candidate or otherwise corroborates that no unprojected terminal notification fact exists.
- A terminal fact requiring a notification blocks cleanup until the outbox projection is durable.
- A candidate that produces a repaired outbox row is excluded from that cleanup execution and can be reconsidered only after the ordinary delivery/retention lifecycle permits it.
- Missing, empty, corrupt, inconsistent, unreadable, oversized, or containment-unsafe journal/admission evidence makes the candidate ineligible for deletion.
- Preview and execution use the same authority-bound eligibility semantics. The existing confirmation token cannot authorize a different candidate set or a different authority.
- Reconciliation is bounded per cleanup request; overflow candidates remain preserved for a later pass rather than being deleted without inspection.

### 7. NF-L6: foreground leases receive monotonic and boot corroboration

Foreground execution leases use the same wall-clock-step protections already established for coordinator leases.

- The schema adds persisted boot identity, monotonic deadline/corroboration, and the duration data needed to validate foreground claim and renewal freshness.
- Foreground claim, renewal, authority validation, and adoption consult the shared lease-clock logic.
- A backward wall-clock step cannot extend a dead foreground lease.
- A forward wall-clock step cannot expire a monotonic-fresh foreground lease.
- A boot identity mismatch prevents the previous boot's monotonic reading from being treated as fresh.
- Legacy rows whose new corroboration columns are `NULL` fall back to the persisted UTC deadline until that deadline passes. They are not expired en masse during migration; after the wall-clock deadline they become adoptable in the normal fail-closed manner.
- The schema migration is cumulative and idempotent. The hash-pinned v2.0.9 fixture is upgraded through the complete current schema twice, with integrity checks, foreign-key checks, manifest verification, and evidence-byte/hash preservation.
- The installed-wheel integration test verifies the same new current schema target, including the new foreground-lease columns.

### 8. NF-L7: concurrent provider reload is a typed conflict with rollback

Two simultaneous provider configuration changes must not leave saved configuration that the running host did not apply.

- The winning reload follows the existing host-controlled reload path.
- A concurrent reload attempt returns HTTP 409 with the stable code `plugin_reload_in_progress`, not a 500.
- If the losing request saved a configuration value before discovering the reload conflict, it atomically restores the prior value before returning.
- Rollback is conditional on the value still being the losing request's write, so it cannot overwrite the winner or a later successful update.
- If rollback itself cannot be proven durable, the response reports a typed configuration-consistency failure and does not claim that the requested provider is active.
- The positive real-path test proving a newly configured provider becomes usable after a successful host reload remains part of the contract.

### 9. NF-L11: bounded retention prunes only safe routes and receipts

Delivery capability and receipt tables receive bounded TTL-based pruning without weakening never-replay evidence or pending delivery authority.

- Each pruning pass has explicit row and time/age bounds and is safe to repeat.
- Only terminal receipts older than the conservative 30-day retention boundary are eligible. Delivered and durable non-retryable outcomes are terminal.
- A `sending` or otherwise outcome-uncertain receipt is never pruned, regardless of age. It remains the durable evidence preventing an outward-effect replay.
- Retryable/nonterminal receipt state is not pruned while it can still govern delivery recovery.
- An unexpired return-route capability is never pruned. Consequently, an unexpired capability referenced by a pending, leased, or dead notification-outbox row necessarily survives pruning.
- An expired route is prunable only when no retained sending/uncertain or other nonterminal receipt still depends on it.
- Route and receipt eligibility is rechecked in the deletion transaction. A concurrent delivery cannot convert an eligible row into protected evidence and then lose that evidence to the pruner.
- Pruning failure is non-destructive and does not block ordinary outbox delivery; the next bounded pass retries it.

The generic Gateway delivery layer may implement generic expiry/receipt retention, but it must not import the workflow plugin. The workflow invariant is satisfied by never pruning unexpired capabilities and by retaining protected receipt states.

## Test-hardening contracts

### NF-L1: Windows evidence-containment fallback

Add a test that forces the Windows containment fallback through hostile link/reparse-point and post-open identity conditions. The test must prove unsafe evidence is rejected and never read or deleted. Platform-neutral seam tests may supplement the native Windows assertion, but they may not replace it or weaken the production fallback.

### NF-L3: cross-process idempotency race

Add a spawn-based, synchronized multi-process test in which independent processes race the same semantic start against a real SQLite database. Exactly one durable run is created, every successful response resolves to that run, and no uniqueness/database-lock error leaks to the caller.

### NF2-L1: concurrent notification drainers

Add a genuinely concurrent test with two drainers using the same real SQLite outbox and delivery-receipt machinery. The per-row lease permits exactly one outward send; the other drainer observes the lease/terminal result and does not replay it.

### NF2-L6: the merge gate tests its own lock

Add `tests/scripts/` to the workflow merge-gate selection so its meta-tests run in the green gate. Preserve the test's nested `FAST` mode to avoid recursively running the full gate. The gate expectation must count the newly included tests rather than loosening or wildcarding its assertions.

## Explicitly deferred findings

The following items remain documented debt and are not changed in this batch:

- **NF-L4, legacy-namespace retry duplication:** requires a pre-upgrade stable-key retry path that did not ship in a fielded build.
- **NF2-L2, upgrade dedup one-shot resend:** depends on a pre-fix build having shipped; it did not.
- **NF-L5, unfenced convergent mutations:** the affected operations are serialized and convergent; the disruptive pressure interrupt is tied to pressure both leaders are expected to observe.
- **NF-L12, multi-profile Gateway delivery:** current fail-closed behavior is acceptable until multi-profile Gateways become a supported topology.
- **NF2-L3, raw capability at rest in `facts.destination`:** all read projections are masked and the local store is permission-restricted; at-rest transformation remains defense-in-depth backlog.

## Schema and migration discipline

The foreground lease change is the only expected schema change in this batch. Its migration must:

1. Advance the schema manifest exactly once.
2. Add nullable corroboration fields so legacy leases preserve their UTC-deadline semantics.
3. Be safe when executed twice.
4. Preserve every evidence blob byte-for-byte and preserve its recorded digest.
5. Pass SQLite integrity and foreign-key checks.
6. Update the copied, hash-pinned v2.0.9 fixture expectation and the installed-distribution migration test to the same current version.

No retention migration deletes existing delivery evidence. Pruning begins only through the new bounded runtime path after the protection predicates are active.

## Failure behavior and observability

- User-visible conflicts and validation failures are typed; they do not surface raw tracebacks, filesystem paths, or parser internals.
- Background repair, cleanup corroboration, and pruning log bounded summaries and protected/skipped counts without logging route capabilities or confirmation tokens.
- Oversized-but-valid repair processing is observable as a budget overrun event naming the run ID and byte count, without including evidence contents.
- A protected cleanup candidate remains in authoritative storage. Failure to reconcile never degrades into deletion.
- A reload conflict leaves the previously active provider configuration authoritative and tells the caller to retry.

## TDD and verification strategy

Each numbered behavior is its own reviewable red/green unit even when several units share an implementation commit.

For every unit:

1. Add the smallest failing behavior test first.
2. Run the focused red command and record that the failure is caused by the named defect, not a fixture or import problem.
3. Implement the minimum complete contract, checking sibling CLI, Desktop, Gateway, dashboard, and API paths implicated by that contract.
4. Run the focused green command and the relevant real SQLite, real middleware, or real-process integration tests.
5. Run the cumulative migration test whenever schema/store behavior is touched.
6. Inspect the diff and run `git diff --check` before staging.

The final verification includes the strengthened workflow merge gate, the installed-distribution integration test, Desktop tests, TypeScript validation where affected, and repository status/diff checks. Completion claims require fresh output from those commands.

## Packaging

The planned history is:

1. This design specification, committed independently.
2. A detailed executable TDD plan, committed independently after written-spec approval.
3. `fix(workflow): harden operator-facing orchestration` for the nine operator-robustness units, with a red/green evidence block recorded for each behavior.
4. `test(workflow): strengthen production invariants` for the four test-hardening units and merge-gate lock.
5. Any required final evidence/review documentation in its own documentation commit.

Only files owned by the current unit are staged. The reviewer-authored follow-up review addendum already present in the worktree is preserved untouched and excluded from these commits unless the reviewer or maintainer explicitly assigns it to this work.

## Acceptance criteria

- Attention pages are newest-first, deterministic, complete across cursors, and authorized.
- Queue-policy starts durably queue at execution capacity unless a real bounded ceiling is reached.
- Foreground adoption is explicit in human output and machine-readable final status evidence.
- Parse-time and `OSError` CLI failures preserve the JSON envelope and redact sensitive paths.
- An oversized first repair journal is actually reconciled and its missing terminal notification is repaired.
- Cleanup cannot remove a run with an unprojected terminal notification or uncorroborated evidence.
- Foreground leases withstand wall-clock steps and preserve legacy UTC-deadline behavior through the cumulative migration.
- Concurrent provider reload returns a typed 409 and leaves saved and active configuration consistent.
- Retention is bounded and never prunes uncertain receipts or unexpired route capabilities.
- Windows evidence containment, cross-process idempotency, and concurrent drainer invariants have real concurrency/platform coverage.
- `tests/scripts/` is enforced by the green workflow merge gate.
- The five consciously deferred findings are recorded without speculative implementation.
