# Workflow language Phase 5 validation and activation

Date: 2026-08-07

Status: **POST-ACTIVATION VALIDATION IN PROGRESS**

This report records the implementation, independent review, and atomic
activation of Workflow Language Phase 5 provider portability. The reviewed
activation candidate now selects normalizer v5 for new `archon-2026-07`
admissions. `hermes-legacy` still selects v2, and explicit or sealed v1-v4
packages retain their recorded behavior. Post-activation regression gates are
still running. No push, merge, rebase, tag, release, brand mutation, or
publication has been performed.

## Scope and activation boundary

Phase 5 adds one backend provider-capability authority, config-only model tiers
and aliases, sealed provider/runtime identity, bounded hook and MCP adapters,
shared inline-agent limits, authoritative hard-cost budgets, truthful
provider-native sandbox handling, and closed backend-authored client
projections. It does not add `loop_group`, runtime child workflows, dynamic
includes, input mapping, a core model tool, synthetic conversation messages,
telemetry, an OS sandbox, or a client-side resolver.

Normalizer v5 remained readable but dormant through every implementation and
pre-activation gate. After independent review returned GO, the sole profile
selection authority `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` changed
from 4 to 5. `LATEST_NORMALIZER_VERSION` is now derived from that mapping so
the two markers cannot drift. Snapshot format remains 2, legacy remains v2,
and v1-v4 readers remain available.

## Pre-activation evidence

All commands used the feature worktree and the repository's shared virtual
environment. File retries were disabled where the plan requires it.

### Focused Phase 5 and compatibility gate

The Task 15 Step 2 command ran 17 files with
`HERMES_TEST_FILE_RETRIES=0`: **221 passed, 0 failed in 4.1s**. It covered the
provider capability/config resolver, all Phase 5 workflow suites, and Phase 3
and Phase 4 language/snapshot/defensive compatibility suites.

### Installed distribution and Desktop

- Installed-wheel integration: **1 passed, 0 failed in 9.6s**.
- Desktop `npm run typecheck`: **passed**.
- Full Desktop Vitest: **498 files passed, 1 skipped; 4,741 tests passed, 2
  skipped in 50.59s**.
- Desktop ESLint: **passed with 0 errors and 163 warnings**. The warnings are
  non-blocking lint diagnostics; no lint error was introduced.

### Ledger and rehearsal contract

The first combined Step 3 run exposed two pre-activation gate defects after
**428 tests passed**: Phase 5 ledger `owned_symbols` contained prose rather than
exact identifiers, and the exhaustive base gate omitted the Phase 5 test
suites. The direct failures were reproduced before edits. The ledger now uses
only machine-locatable symbols, and all eleven Phase 5 workflow test files are
selected by the base release gate.

Focused GREEN for the two failing merge-gate cases was **2 passed, 0 failed in
22.0s**. The complete Step 3 ledger/rehearsal command then passed **430 tests,
0 failed in 98.7s**:

- customization checker: 275 passed;
- merge-gate contract: 49 passed;
- upstream-merge rehearsal contract: 104 passed;
- Desktop workflow test gate: 2 passed.

The live customization checker independently returned verified upstream
baseline `36cb5ae5530a75def7df3195e49b7a4aa2add482`. No
`last_verified_upstream` value was advanced and no retroactive catch-all ledger
entry was added.

The plan's original standalone rehearsal example used the obsolete
`--phase base` spelling and failed before mutation with `unknown argument:
--phase`. The executable plan now uses the script's real explicit
`--upstream-ref`, `--base-ref`, and dynamically enumerated `--brand-ref`
contract. That corrected rehearsal is part of the pending clean-tree gate; the
argument-validation failure is not counted as product-test evidence.

The original Step 4 brand loop also invoked the brand-only gate from the
neutral feature checkout. That gate correctly rejected the missing generated
LOOP24 overlay and changed no state. Step 4 now uses the controlled upstream
rehearsal in a detached temporary worktree once per dynamically validated brand
and compares external state after each run. The neutral-checkout rejection is
not counted as branded regression evidence.

## Independent implementation review

A fresh high-reasoning Sol reviewer examined the complete implementation and
plan contract over multiple rounds. Every reported Critical or Important
finding was first reproduced with a failing regression and then corrected.
The dispositions covered provider-free deterministic nodes, sealed approval
rejection identities, exact hook event/operation semantics, MCP admission and
worker parity, Desktop normalizer projection, approval tool/lifecycle parity,
cancel ordering, v1-v4 approval replay, and ambiguous dual hook reasons.

The final review inspected the clean exact SHA
`9c8653cb812164635967f8540733685c9355733e` and returned **GO: 0 Critical, 0
Important**. That SHA is the reviewed pre-activation implementation; activation
was not performed until after this verdict.

## Atomic activation RED/GREEN

Before the mapping changed, the Task 15 Step 6 command ran four files. It
reported **154 passed and 2 failed**. Both failures were the new activation
contract: current Archon and the default authoring schema still selected v4.
No unrelated assertion failed.

Changing the single Archon profile mapping to v5 made those activation
assertions pass. Historical snapshot tests that intended format-1 or v4
behavior were then pinned to explicit v4 instead of weakening v5's mandatory
format-2 provider authority. The same four-file Step 6 command finished with
**156 passed, 0 failed in 4.6s**. A subsequent affected-surface sweep found and
fixed one generated-schema publication defect: the v5 string/null hook matcher
had inherited a boolean example. The corrected language schema, catalog,
Desktop middleware, and portable-compatibility sweep passed **715 tests, 0
failed in 15.2s**.

## Pending gates

The following evidence will be appended after it exists:

- clean-tree upstream base rehearsal and full no-retry Python suite;
- dynamically enumerated non-publishing brand rehearsals with byte-identical
  pre/post local, remote, tag, release, branch, status, and worktree snapshots;
- full post-activation Steps 2-4 against the exact committed activation SHA;
- final commit IDs, exact Git/worktree state, and retry/exclusion accounting.
