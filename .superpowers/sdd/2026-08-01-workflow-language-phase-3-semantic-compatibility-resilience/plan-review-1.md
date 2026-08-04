# Phase 3 Semantic Compatibility and Resilience — Plan Review 1

**Reviewed HEAD:** `55d028a6d9fef0db2406d79c38a45252bb0b1c09`

**Scope:** Goal-backward review of the complete Phase 3 implementation plan
against the umbrella design, approved Phase 3 design, all retained Phase 3
design reviews, root and Desktop engineering instructions, and current
implementation/test seams.

**Verdict:** Changes required before user approval or implementation.

**Severity count:** 0 Critical, 6 Important, 1 Minor.

The plan correctly preserves the approval boundary, exact legacy/v1/v2
behavior, profile-specific v3 normalization, sealed admission semantics,
bounded APIs/evidence, prompt caching, the narrow tool waist, fresh
implementer/specification/quality handoffs, and the exclusion of Phase 4/5.
The branch diff at the reviewed HEAD contains only the Phase 3 design, plan,
and retained review artifacts; no production work has begun.

## Important findings

### I-1 — Task 10's double-quoted Bash replacement contradicts the approved renderer contract

**Plan references:** lines 549–580.

**Design references:** lines 688–698.

The approved design deliberately assigns different replacements to unquoted
and already-double-quoted placeholders:

- unquoted: `"${__HERMES_WF_SPILL_abcd}"`;
- inside double quotes: `${__HERMES_WF_SPILL_abcd}`; and
- inside single quotes: `'"${__HERMES_WF_SPILL_abcd}"'`.

Task 10 instead instructs the implementer to substitute
`"${__HERMES_WF_SPILL_abcd}"` in both unquoted and double-quoted tokens. In an
already-open double-quoted word, those added quotes close the surrounding
quote before the expansion and reopen it afterward. The variable expansion is
therefore unquoted, allowing field splitting and pathname expansion and
breaking the exact-content guarantee for spaces and globs. The planned
real-shell tests say the correct behavior should be covered, but the production
instruction tells the implementer to build the opposite behavior.

**Required remediation:** Replace line 580 with the exact three-row
context-to-replacement table from the design. Make the real `/bin/sh` RED tests
assert argument/content identity separately for unquoted, double-quoted, and
single-quoted surrounding contexts, including spaces, globs, empty strings,
quotes, and trailing newlines. The double-quoted fixture must prove that the
expansion remains inside the original double-quoted word.

### I-2 — Generic core changes are not ledgered at their commit boundaries, and Task 15 names nonexistent customization files

**Plan references:** lines 536–598, 602–637, and 798–847.

**Umbrella-design references:** lines 769–786.

Task 10 changes `tools/managed_process.py`, and Task 11 changes
`agent/plugin_agent.py` plus `agent/plugin_agent_worker.py`. Those are existing
upstream-owned generic seams. The umbrella contract requires every such change
to have an independent commit boundary and to add or amend its customization
ledger entry in that same commit. The plan instead commits the Task 10 workflow
integration as one commit, commits the Task 11 generic change without a ledger
file, and postpones any possible ledger update to Task 15.

Task 15 then identifies `docs/upstream-customizations.json` and
`.github/workflows/upstream-merge.yml`, but neither file exists. The real
workflow ledger is
`docs/upstream-customizations/workflow-orchestration.yaml`; it already owns
the `managed-process-tree` and `plugin-agent-runner` seams. The repository has
no `upstream-merge.yml`. The final command list also runs tests *about* the
customization harness but does not explicitly run the strict checker and base
merge gate against the candidate HEAD.

**Required remediation:** Amend the actual
`docs/upstream-customizations/workflow-orchestration.yaml` entries in the same
commit as each generic seam, recording the new exact owned symbols/contracts,
tests, merge guidance, expected commit subject, upstreamability, and removal
condition without advancing `last_verified_upstream`. Give the generic
`ManagedProcessTree.spawn()` inherited-descriptor change its own commit and
invariant tests before the plugin integration commit. Include the plugin-agent
classification and its ledger amendment in the Task 11 generic commit. Replace
the nonexistent Task 15 paths with the actual manifest and only real CI/gate
files proven necessary. Run these real gates at each applicable generic commit
and again at final HEAD:

```bash
.venv/bin/python scripts/check_upstream_customizations.py --strict --base-ref HEAD
scripts/test_workflow_merge_gate.sh --phase base
```

Keep the harness unit tests and the separate upstream/OTTO/LOOP24 rehearsal as
additional evidence, not substitutes for validating the candidate ledger.

### I-3 — The Desktop RED/GREEN commands do not exercise the Run Inspector that Task 13 changes

**Plan references:** lines 708–753 and 831–837.

Task 13 names `apps/desktop/src/app/workflows/run-inspector.tsx` as the Desktop
production seam for persistent-session recovery evidence, but its only Vitest
targets are `review-run-dialog.test.tsx` and
`view-workflow-dialog.test.tsx`. Those tests exercise catalog/detail and
preflight dialogs; they do not render `RunInspector`. The existing test that
imports and renders `RunInspector`, drives `getWorkflowEvidence`, and exercises
its evidence tabs is `apps/desktop/src/app/workflows/index.test.tsx`. The Python
`test_workflow_language_desktop_e2e.py` proves backend/middleware projection,
not renderer behavior, and cannot replace a JS-side component test under the
repository test-placement rules.

As written, the claimed Desktop RED can remain green even when the recovery tab
drops, mislabels, or fails to render `recovery_kind: persistent_session`, and
the final Desktop gate would repeat the same ineffective target set.

**Required remediation:** Add `apps/desktop/src/app/workflows/index.test.tsx`
to Task 13's Files and to its RED/GREEN/final commands. Add a real
`RunInspector` fixture that selects the recovery tab, requests `kind=recovery`,
renders generic persistent-session recovery evidence, handles missing additive
fields from an older backend as unavailable, and preserves a usable empty/error
state. Keep the review/view dialog tests for language and compatibility status.

### I-4 — Task 12 omits the pre-provider recovery-selection boundary and its reserved crash proof

**Plan references:** lines 654–704.

**Design references:** lines 824–845 and 865–876.

The approved contract requires an active-claim callback to append
`persistent_session_missing_fresh_start` and its bounded
`fresh_start_selected` evidence **before** the fresh provider request. It also
requires journal capacity for the selection frame, winning obligation, and
bounded outcome frames to be reserved before worker allocation. This creates a
specific crash boundary: after selection but before provider launch, ordinary
zero-effect interrupted-claim recovery applies.

Task 12 says only to "emit selection and outcomes" near the end. Its reserve
step mentions the pending obligation but not all three frame classes, and its
enumerated crash cases jump from "before completion" to "after
completion/before CAS." An implementation can therefore launch the fresh
provider before selection is durable, or discover journal exhaustion only
after a provider effect, while still satisfying the written task steps.

**Required remediation:** Add a dedicated RED step requiring the scheduler to:

1. reserve bounded journal space for selection, obligation, and outcome before
   allocating the fresh worker;
2. append selection through an active-claim-fenced store callback before the
   provider request, with `provider_attempts_before_recovery: 0`; and
3. inject crashes immediately before selection, after selection/before provider
   launch, and after provider launch.

The tests must prove no selection evidence before the callback, durable
selection plus zero-effect claim recovery and no provider/CAS after the middle
crash, existing uncertainty behavior after launch, and no raw protected values
in any journal-derived public projection.

### I-5 — No task owns the complete stable Phase 3 error/evidence catalog contract

**Plan references:** lines 132–152, 255–396, 557–592, 654–700, and 755–796.

**Design references:** lines 966–985.

The design enumerates the durable normalization, reference, condition, Bash,
and session codes and requires them to enter one central
compatibility/evidence catalog with duplicate and completeness tests. The plan
does ask feature tests to assert individual codes and asks Task 14 to describe
"strict types/errors," but it never assigns ownership of the exact central
catalog, version/profile applicability, uniqueness, public meaning, or an
emitter-to-catalog completeness invariant. Task 1's general compatibility
inventory update covers field status and migration guidance; it does not close
the later runtime/evidence code set.

The current code already has a central generated compatibility catalog in
`plugins/workflow/language_schema.py`, fed in part by
`DYNAMIC_LANGUAGE_COMPATIBILITY_CODES` from `language.py`, plus completeness
tests in `test_language_schema.py`. Without an explicit Phase 3 extension,
runtime paths can emit stable-looking codes that never reach `doctor`, the
editor contract, documentation, or a duplicate check.

**Required remediation:** Establish the versioned Phase 3 durable-code catalog
authority in Task 1, then require Tasks 3–12 to register their emitted codes in
the same commit as the emitter. Add behavior-contract tests proving that:

- catalog codes are unique and have bounded metadata plus explicit profile and
  normalizer applicability;
- every actual Phase 3 admission/runtime/evidence emitter code is registered;
- every registered code is exercised by a real behavior path, not a source-text
  scan;
- API/doctor/editor projections derive from that authority; and
- Task 14 documents the registered public codes without a hand-maintained
  second list.

These should be relationship/completeness invariants, not brittle enumeration
counts or source-reading tests.

### I-6 — Final reviews occur before gates that may change production code, with no mandatory rereview of the final HEAD

**Plan references:** lines 811–855.

Task 15 runs the final specification and quality reviews first, then the
focused suite, canonical suite, Desktop checks, customization checks, and merge
rehearsal. The global bounded-fix protocol permits fixes when those later gates
find defects, but Task 15 does not require both final reviewers to inspect the
post-fix HEAD. `review closure` can therefore refer to an earlier tree than the
one declared implementation-complete.

**Required remediation:** Define a convergence order tied to exact identities:

1. bring all focused, canonical, Desktop, schema, installed, customization,
   and rehearsal gates green;
2. run independent final specification and quality reviews against that exact
   HEAD/tree;
3. if either review or any repeated gate causes a production fix, rerun the
   affected gates plus all final gates whose evidence was invalidated, then
   obtain clean rereviews of the new exact HEAD/tree; and
4. allow only retained report-only commits after the final reviewed/tested
   production tree, with the report recording both identities.

The controller must verify that no production path differs from the final
clean-rereview tree.

## Minor finding

### M-1 — Final Desktop formatting and merge-rehearsal steps are not executable commands

**Plan references:** lines 831–847.

The plan gives exact commands almost everywhere, but tells the controller to
run "scoped ESLint and Prettier commands" and to use temporary
branches/worktrees or the rehearsal harness without naming the invocations.
That leaves both evidence scope and the protected known-Prettier baseline to
operator interpretation.

**Required remediation:** List exact non-writing commands over every planned
Phase 3 Desktop file (including `index.test.tsx` after I-3), for example direct
`npx eslint <files...>` and `npx prettier --check <files...>` invocations from
`apps/desktop`. Also restore the repository's explicit bounded rehearsal
command, including exact refs:

```bash
git fetch origin --prune
scripts/test_workflow_upstream_merge.sh \
  --upstream-ref origin/main \
  --base-ref HEAD \
  --brand-ref otto \
  --brand-ref loop24
```

Record the resolved commit identities before running it. Fetching and temporary
rehearsal do not authorize push, publication, brand propagation, or mutation of
literal `main`.

## Required disposition

Resolve all six Important findings and the one Minor finding in the plan, then
request an independent focused rereview at the revised exact HEAD. Do not begin
Phase 3 production implementation before the revised design and plan receive
user approval.
