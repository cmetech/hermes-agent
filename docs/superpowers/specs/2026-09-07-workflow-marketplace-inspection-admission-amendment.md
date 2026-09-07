# Workflow Marketplace Inspection Admission Amendment

**Date:** 2026-09-07

**Status:** Design direction approved; written amendment awaiting review

**Amends:** [Workflow Marketplace Lifecycle Recovery Amendment](2026-09-04-workflow-marketplace-lifecycle-recovery-amendment.md)

## 1. Problem and evidence

Task 15B real-backend and real-Desktop testing proved that the layers disagree about a background package inspection:

- Locked backend `PackageState` reports an installed package with `busy: false` because inspection is read-only.
- Desktop `getPackageGate()` deliberately excludes `inspect` records from lifecycle barriers, so an action such as Remove can be enabled from verified local state.
- Desktop `start()` nevertheless rejects that action with local `marketplace_request_conflict` while the same-package inspection remains supervised.
- If Desktop dispatched the action, the backend registry would reject it with `marketplace_operation_conflict` because inspection and lifecycle operations currently reserve the same private exclusive target.
- Task 14F also permits a fresh exact inspection while an older exact inspection survives navigation, but the backend's exclusive target rejects the second inspection.

The observed user-visible failure is an enabled Remove action that sends no removal request and then displays an unconfirmed outcome. No mutation was admitted, so unknown-outcome copy is false and unnecessarily alarming.

This is not a missing public identity field, a stale-card heuristic problem, or the resolved transaction-workspace collision. The operation subject, request ID, actor, profile, principal binding, registry epoch, and native connection generation are already exact. The missing rule is whether a read-only inspection participates in package lifecycle admission exclusivity.

## 2. Authority and non-goals

The backend remains the final authority for admission, current installed state, trust, and transaction recovery. Desktop may decide whether a visible action is eligible to attempt, but a backend conflict wins if another lifecycle operation was admitted first.

An inspection result is historical read evidence for the repository and package state observed during that operation. It is not a lease over current installed state and cannot overrule a later locked `PackageState` result.

This amendment does not:

- add or change a public operation, subject, result, outcome, or error schema;
- expose the backend's private target or conflict bookkeeping;
- weaken exact request replay or actor/profile/connection binding;
- make inspection a source of installed-state authority;
- permit two package lifecycle operations to overlap;
- cancel, adopt, replay, or queue an operation by timestamp, kind, or "latest" ordering;
- persist Desktop watches, cache generations, or preparation tokens;
- change source refresh, all-package update checks, direct-install subjects, transaction locking, or trust semantics.

## 3. Decision: asymmetric observation admission

`inspect` is a non-exclusive package observation. Every other package-subject lifecycle operation is a package lifecycle operation for admission purposes, including package-scoped update check, install/update/remove preparation and confirmation, trust preparation/confirmation/revocation, and any future package operation unless its contract explicitly classifies it as an observation.

The exact same package subject and authority use this matrix:

| Existing nonterminal work | Incoming `inspect` | Incoming package lifecycle operation |
| --- | --- | --- |
| None | Admit | Admit |
| One or more `inspect` operations | Admit | Admit |
| Package lifecycle operation | Reject as known non-admission | Reject as known non-admission |

This rule is intentionally asymmetric. Inspections that were admitted first may finish while a later lifecycle operation runs. Once a lifecycle operation is admitted, no new inspection or second lifecycle operation starts for that exact package until it becomes terminal.

Different package subjects remain independent. Terminal operation history does not occupy the backend active target. Desktop may retain a terminal lifecycle mutation barrier until locked package-state and affected projections reconcile; an exact inspection may run through that terminal barrier to obtain reconciliation evidence, but another lifecycle action remains governed by the existing package gate.

### 3.1 Backend registry

The registry keeps exact operations, capacity accounting, cancellation, actor scoping, receipts, and public projections unchanged. Under its existing admission lock it applies these private rules:

1. Exact receipt lookup still occurs before capacity and conflict checks. A replay returns the original operation and never creates work.
2. A package lifecycle operation reserves the existing exclusive private package target.
3. An inspection checks whether that exclusive lifecycle target is occupied but does not reserve it.
4. A lifecycle operation checks only the exclusive lifecycle target. Earlier inspections therefore do not block it.
5. Terminal cleanup releases only an exclusive target actually owned by that operation.

V1 and V2 inspections share this registry behavior. V1 gains no client replay promise and Desktop continues to start inspection through V2 only.

The lock makes the order deterministic. If inspection admission wins the lock first, the later lifecycle operation may coexist with that already-running observation. If lifecycle admission wins first, the inspection receives a conflict and does not start.

### 3.2 Desktop supervisor

The application-level supervisor mirrors the same matrix before dispatch:

- inspection records never block a package lifecycle start;
- a nonterminal lifecycle barrier blocks a new inspection and every other lifecycle start;
- multiple exact inspection intents remain independently identified and supervised;
- a terminal lifecycle barrier may admit inspection for reconciliation under the existing rules, while the package gate continues to control lifecycle actionability;
- scope, subject, request, operation, selection, connection generation, principal binding, and registry epoch correlation remain exact.

The supervisor does not automatically retry a conflict. A fresh user retry creates a fresh bounded request ID only after normal action eligibility is revalidated. A lost response for an operation that may have been admitted continues through exact receipt replay/list reconciliation; this amendment does not reinterpret it as a conflict.

## 4. Cache and outcome truth

Before a lifecycle action is admitted, the existing exact-package mutation generation advances and creates the current mutation barrier. An inspection captures the earlier generation through the existing query/reconciliation ownership.

If that earlier inspection finishes after lifecycle admission:

- its operation history may truthfully retain the read-only terminal result;
- it must not publish package detail, installed state, trust state, or actionability into a newer generation;
- it must not clear or satisfy the lifecycle mutation barrier;
- locked `PackageState` and successful post-mutation reconciliation remain authoritative;
- a failed post-mutation refetch keeps contradictory actions disabled under the existing barrier policy.

The implementation must prove this behavior rather than relying on completion timestamps.

`marketplace_request_conflict` or `marketplace_operation_conflict` returned before admission is a known non-admission. Desktop releases the attempted action's transient guard, reconciles the exact scope/package, and says that another package action is already running. It must not display "State could not be confirmed" because the rejected request did not mutate package state. Existing ambiguous/lost-response paths remain unchanged when admission might have occurred.

## 5. User-visible behavior

When verified local state is sufficient, a background inspection does not disable Remove or trust actions. Install or Update still require whatever verified candidate detail their existing action gate requires; this amendment does not make stale candidate data actionable.

If the user starts an eligible lifecycle action while an older inspection runs, the action proceeds normally. The inspection is not cancelled and does not own the action dialog. Its later result cannot make removed content appear installed, restore an obsolete version, or undo trust truth.

If another lifecycle action wins admission first, the attempted action remains known unchanged. The UI reports a busy/conflict condition, refreshes exact current state, returns focus under the existing rules, and allows a later explicit retry when ready.

Navigation does not change these rules. The application supervisor continues exact watches while Marketplace or Installed views unmount. On return, the view consumes only exact current-generation projections; it never selects a result by recency or operation kind.

## 6. Failure and lifecycle sequences

### Inspection admitted before Remove

```text
Desktop starts exact inspection A
Backend admits A without reserving the lifecycle target
Desktop verifies locked installed PackageState and enables Remove
User starts exact remove preparation B
Desktop advances the package mutation barrier and admits B past A
Backend reserves the lifecycle target for B and runs A and B
B completes and locked PackageState becomes authoritative
A completes as historical read-only evidence
Desktop rejects A from newer package/action caches
```

### Lifecycle operation admitted before inspection

```text
Lifecycle operation B reserves the exact lifecycle target
Desktop or another client attempts inspection A
Supervisor, or backend in a race, rejects A as known non-admission
B continues unchanged
After B is terminal, an explicit exact inspection may start
```

### Navigation during overlap

```text
Inspection A and lifecycle operation B have exact supervisor records
Marketplace view unmounts; renderer polling pauses as already designed
Backend work continues
View returns; supervisor resumes by exact IDs and binding
B reconciles locked current state
Any older A result remains historical and cannot cross B's generation
```

## 7. Compatibility and migration

No wire fixture or generated TypeScript contract changes because public operation shapes and error codes are unchanged. The private backend active-target implementation changes from "every targeted operation owns exclusivity" to "package lifecycle operations own exclusivity; inspection observes it."

Existing persisted admission receipts remain valid because kind, subject, canonical body, operation ID, and result are unchanged. Existing operation history needs no migration. Desktop state remains memory-only and is reconstructed through existing exact capability, receipt, list, get, and package-state routes after renderer navigation or restart.

Older backends may still reject an inspection/lifecycle overlap. Desktop treats their pre-admission conflict as known unchanged and reconciles; it does not claim the mutation ran. The feature branch's V2 capability version does not change because this correction aligns already-advertised operation behavior rather than adding a callable capability.

## 8. Required tests

Backend registry and authenticated API tests must prove:

1. multiple exact same-package inspections may coexist and retain distinct request/operation IDs;
2. an earlier inspection does not block a later package lifecycle operation;
3. an earlier lifecycle operation blocks a later inspection and another lifecycle operation;
4. different package subjects remain independent;
5. exact replay returns the original inspection or lifecycle operation before conflict checks;
6. capacity, cancellation, terminal release, actor/profile isolation, and active-mutation `PackageState.busy` behavior are unchanged;
7. a real temporary-Git inspection held with event synchronization can overlap removal preparation/confirmation without weakening installed/provenance truth.

Desktop supervisor and rendered tests must prove:

1. the local matrix matches the backend matrix for pending, watching, terminal, unknown, and reconciled records;
2. Remove remains actionable from verified locked state while inspection is pending and its start reaches IPC exactly once;
3. new inspection is rejected behind lifecycle work without an HTTP call;
4. a backend race conflict is rendered as known non-admission, not unknown outcome;
5. navigation keeps both exact watches and never adopts a different operation;
6. a pre-mutation inspection completing after install, update, remove, or trust cannot overwrite newer detail/installed/trust caches or reopen contradictory actions;
7. failed terminal refetch retains the newer mutation barrier;
8. listener, timer, focus, Escape, token, and request-replay guarantees remain unchanged.

The real Desktop Playwright lifecycle must reproduce the discovery sequence through actual install, separate trust, pending inspection, enabled Remove, removal confirmation, and truthful terminal/current state. Response bodies and confirmation tokens remain absent from test evidence and logs.

## 9. Acceptance boundary

This amendment is complete when backend and Desktop use the same matrix, the real removal flow succeeds while an earlier inspection remains held, late inspection results cannot cross the mutation generation, all existing lifecycle conflict/replay/barrier tests remain green, and a fresh reviewer independently checks both admission orders and stale-result suppression.

Any implementation that merely ignores the Desktop conflict, merely relaxes the backend target, automatically queues/replays the user's action, or chooses an operation by recency is incomplete. A need for a new public identity or durable queue must stop as a new design issue.
