# Phase 3 Semantic Compatibility and Resilience — Plan Rereview 1

**Reviewed HEAD:** `be6cb3a5eaf7c196243f6979893c30fc0d916530`

**Reviewed tree:** `f2943572455d7e99cfcbf7e0ec962919349f8ae0`

**Scope:** Focused independent rereview of the I-1 through I-6 and M-1
dispositions from Plan Review 1. The complete revised plan was reread to detect
contradictions introduced by those remediations; this is not a broader second
plan review.

**Verdict:** Changes required before user approval or implementation.

**Severity count:** 0 Critical, 1 Important, 0 Minor.

## Closure table

| Finding | Status | Focused disposition evidence |
|---|---|---|
| I-1 — Bash replacement contexts | Closed | Task 11 now gives the approved three-row context table: quoted expansion for an unquoted token, bare expansion inside an existing double-quoted token, and the exact quote bridge inside an existing single-quoted token. It also requires real `/bin/sh` argument/content identity tests, including proof that the double-quoted expansion remains inside the original quoted word (plan lines 679–703). |
| I-2 — Generic seams, ledger boundaries, and live gates | **Remains open** | The nonexistent paths were replaced by the real `docs/upstream-customizations/workflow-orchestration.yaml`; Tasks 10 and 12 now own dedicated adjacent extension entries, exact subjects, same-commit ledger changes, and live strict/base gates (lines 583–641 and 761–787). Task 12 runs focused GREEN before commit. Task 10, however, still commits at lines 625–627 before its first focused GREEN suite at lines 629–638, contradicting the required handoff/TDD order at lines 38–45. |
| I-3 — Run Inspector coverage | Closed | Task 14 owns `apps/desktop/src/app/workflows/index.test.tsx`, renders `RunInspector`, selects and requests recovery evidence, covers the persistent-session shape plus older-backend/missing-field, empty, and error behavior, and includes the file in RED, GREEN, ESLint, and Prettier commands (lines 898–943 and 1018–1029). |
| I-4 — Pre-provider recovery boundary | Closed | Task 13 reserves selection, possible winning obligation, and bounded outcomes before allocation; persists selection through an active-claim-fenced callback before launch; and tests crashes immediately before selection, after selection/before launch, and after launch with the required provider/CAS and privacy assertions (lines 814–853). |
| I-5 — Stable Phase 3 code catalog | Closed | Task 1 establishes the dependency-neutral, versioned durable-code authority with bounded metadata, uniqueness and applicability relationships, same-commit registration by later emitters, and behavior-linked completeness rather than source scans. Later tasks and the API/Desktop/docs gates consume that central authority (lines 157–173 and the task-local catalog requirements). |
| I-6 — Final review/gate convergence | Closed | Task 16 makes all focused, canonical, Desktop, customization, and rehearsal gates precede final reviews, records the exact green candidate identity, and requires clean specification and quality rereviews plus invalidated-gate reruns after any fix. The controller verifies that no production path differs from the final rereview tree (lines 1005–1105). |
| M-1 — Exact final commands | Closed | Task 16 supplies exact non-writing Desktop TypeScript/Vitest/ESLint/Prettier commands, the live strict customization checker, the base merge gate, `git fetch origin --prune`, and the exact upstream/OTTO/LOOP24 rehearsal invocation and refs (lines 1018–1060). The referenced repository files exist; the shell harnesses and `../../.venv/bin/python` are executable; and the Desktop `test` and `typecheck` package scripts exist. |

## Important finding

### I-2 remains open — Task 10 commits the generic seam before proving GREEN

**Plan references:** lines 38–45 and 581–641.

The substantive I-2 remediation is otherwise correct: Task 10 isolates the
generic `ManagedProcessTree.spawn()` descriptor contract, uses the actual
customization ledger, preserves the historical entry, defines a dedicated
Phase 3 extension with the exact subject, and keeps the generic implementation
and ledger amendment in one atomic commit. Its live strict checker and base
merge gate also target the resulting commit.

The task's executable sequence is still internally contradictory. It orders:

1. RED tests;
2. production implementation;
3. ledger amendment;
4. commit `feat(process): inherit bounded child descriptors`; then
5. the first focused generic GREEN suite.

That permits an unverified implementation commit and conflicts with the plan's
global RED/GREEN/atomic-commit handoff protocol. Task 12 already demonstrates
the intended order by saying “Run focused verification, then commit.” A later
gate-driven fix commit does not repair the original commit-boundary guarantee.

**Required remediation:** In Task 10, move this focused command before the
atomic commit:

```bash
scripts/run_tests.sh tests/tools/test_managed_process.py tests/tools/test_process_registry.py tests/scripts/test_workflow_merge_gate.py
```

Keep the implementation and ledger amendment together in
`feat(process): inherit bounded child descriptors`. Then, from that clean
commit, run the live strict checker and base merge gate exactly as already
written:

```bash
../../.venv/bin/python scripts/check_upstream_customizations.py --strict --base-ref HEAD
scripts/test_workflow_merge_gate.sh --phase base
```

No other I-1 through I-6 or M-1 disposition remains open in this focused
rereview.
