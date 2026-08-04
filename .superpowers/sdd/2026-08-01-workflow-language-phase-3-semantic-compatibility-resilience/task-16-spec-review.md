# Task 16 Independent Final Specification Review

## Review identity

- Phase 3 implementation base: `cffc23cecd801d3aed08ba66d596bec4a365a43a`
  (tree `cd1edca5985a94dc9710136ddb13a0d5daeefdeb`)
- Reviewed production HEAD: `8a1fe704484bf63e0e84f536f7fb690a2f024ccf`
- Reviewed production tree: `94f4fd4572b63ba6dd496213b603e67748b41b46`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Verdict: **PASS**
- Findings: **0 Critical / 0 Important / 0 Minor**

The assigned HEAD and tree matched exactly before and after this review. The
worktree was clean before this retained report was written.

## Scope and user override

This review is limited to ordinary functional specification compliance. It
goal-backward checks the approved Phase 3 design and plan, the active Task 16
brief, retained task closure evidence, the Phase 3 implementation range, and
the final Task 16 corrections.

The user's override is absolute. I did not perform threat-model analysis,
security-boundary review, exploit/adversarial validation, privacy/security
assessment, shell-injection review, API-authorization review, or any
security-focused testing or file inspection. No test selected for one of those
purposes was run or used as evidence. This review also does not use either the
discarded broad-suite attempt or the discarded generic-ledger rehearsal output
recorded in the Task 16 report.

## Evidence reviewed

I read the approved Phase 3 design and implementation plan, the Task 16 brief
and report, the retained progress ledger, the final specification closure
reports for the functional workstreams, and the ordinary published-contract,
API/Desktop, lifecycle, recovery, compatibility, and customization evidence
needed for the final goal-backward trace. I inspected the ordinary production
diff from the approved plan baseline through the requested final tree, with
particular attention to the six retained Task 16 corrections.

The earlier task closures converge as follows:

- Tasks 1-2 close profile-specific v3 normalization, exact legacy selection,
  immutable requested/effective execution semantics, admission parity, and
  restart behavior.
- Tasks 3-7 close static direct-dependency admission, one typed/rendered output
  resolver, typed conditions, bounded durable output waits, and strict
  substitution across the existing consumers.
- Tasks 8-9 close per-attempt timeouts and one non-multiplying combined attempt
  ledger.
- Tasks 10-11 close the generic bounded child-descriptor seam and the functional
  32,768/32,769-byte Bash value behavior without adding a core model tool.
- Tasks 12-13 close missing-session classification and confirmed-missing
  cross-run recovery, including durable selection, completion/CAS obligation,
  bounded retry wake, restart, and no-provider-replay behavior.
- Tasks 14-15 close bounded backend/Desktop projections and the generated
  schema, documentation, workflow-builder, portable, and installed contracts.

Every cited final specification closure reports PASS/approved with zero open
Critical, Important, or Minor findings.

## Goal-backward specification verification

| Contract | Final disposition |
|---|---|
| Profile/version selection | PASS. New Archon runs select normalizer v3; unversioned and `hermes-legacy` remain v2; admitted v1/v2 versions remain readable. `node_semantics` exists only for v3. |
| Immutable requested/effective semantics | PASS. The exact v3 execution projection is sealed in `resources.json`, participates in manifest identity, is loaded rather than recomputed on resume, and preserves profile ceilings/config-change behavior. |
| Timeout contract | PASS. Bash/script values are milliseconds with an omitted 120,000 ms request, AI idle values are milliseconds, effective values are capped once, and deadlines are per claimed workflow attempt. Legacy seconds/defaults remain on the legacy path. |
| Retry contract | PASS. AI defaults to two retries; deterministic nodes retain one total attempt unless explicitly configured; authored `max_attempts` means retries after the initial attempt; the sealed combined charge remains at most five; exact additional provider calls are charged once, with conservative bounded fallback. |
| References and conditions | PASS. V3 uses one direct-dependency grammar and one immutable typed/rendered resolver. Conditions use canonical typed values. False conditions skip, typed failures terminate without an executor attempt, and only transient reads enter the bounded durable wake protocol. Legacy adapters remain separate. |
| Existing substitution consumers | PASS. Prompt/command, inline script, approval, and already-existing loop prompt/`until_bash` surfaces use the strict v3 facade. The loop change adds no loop field or loop execution semantic; it only freezes the same resolved values across the existing consumer. |
| Large Bash values | PASS on the ordinary functional contract. The 32,768-byte inline and 32,769-byte contained-value boundary, exact content, bounded evidence, lifecycle cleanup, and legacy compatibility are covered. No excluded review dimension was assessed. |
| Persistent-session lifecycle | PASS. Only a confirmed missing cross-run registry session can select fresh execution. Same-run and unconfirmed failures do not. Selection precedes provider launch; successful completion and its registry obligation are journaled together; restart applies/observes the generation-CAS outcome without replay; repeated registry failure becomes bounded `recovery_pending`. Legacy/v1/v2 behavior remains unchanged. |
| API and Desktop | PASS. Existing backend routes project bounded v3 language, retry/error, migration, and recovery truth. Desktop accepts additive fields and renders backend-authored values through the existing inspector; older/partial shapes retain generic fallbacks. No renderer-side parser, resolver, retry calculator, session probe, alternate workflow authority, or new path-taking endpoint was added. |
| Published contract | PASS. The generated contract, website reference, workflow-builder references, portable fixtures, and installed wheel agree on v3 units/defaults, direct dependencies, typed conditions, the Bash boundary, recovery behavior, durable-code authority, and legacy v2 behavior. |
| Prompt cache and narrow waist | PASS. No `model_tools.py`, `toolsets.py`, or tool-registry change exists in the Phase 3 range. Strict values are resolved before the isolated request; the retained invariants show no live system-prompt mutation, history transplant, or role-alternation change. |
| Phase boundary | PASS. MCP and skills remain options rather than node kinds. Existing loop consumers gained no new loop syntax. Include expansion remains Phase 4; `maxBudgetUsd`, model aliases, portable hook normalization, and sandbox guarantees remain Phase 5 blockers/documented non-guarantees. |

## Final Task 16 correction audit

The final six commits preserve rather than widen the approved contract:

- `812f9c1c7` makes cancellation respect the already-recorded provider release
  linearization point.
- `b2f0d3476` isolates changing live model-catalog fixtures and is test-only.
- `6d7150c6c` restores the journal projection load fast path while leaving full
  rebuild validation at its existing boundary.
- `e799ea7d7` restores positional compatibility by appending new execution
  callbacks after the established `NodeExecutionContext` fields; its other
  changes are regression assertions.
- `e56425981` lets scheduled revalidation retain only the exact bounded names
  of recovery-owned root artifacts and removes the excluded suite from the
  active base-gate selection.
- `8a1fe7044` routes only exact `workflow schema` startup candidates around the
  early recovery mutation. Global version/one-shot precedence, normal startup,
  update startup, profile handling, help, and parse-error authority remain
  intact.

These corrections add no Phase 4/5 behavior, core tool, prompt surface, public
endpoint, or alternate schema/runtime authority.

## Ordinary final-gate evidence

Only retained, non-discarded evidence is used:

- Focused Phase 3 allowlist: **9 files / 1,577 passed / 0 failed**, retries
  disabled.
- Exact final base gate at `8a1fe704...`: **56 explicitly listed Python files /
  3,804 passed / 0 failed**, installed-distribution **1 passed**, Desktop **11
  files / 159 passed**, and Desktop typecheck passed.
- Final schema-startup adjacent allowlist: **4 files / 717 passed / 0 failed**,
  retries disabled.
- Schema/installed/customization/merge/Desktop harness allowlist: **6 files /
  1,033 passed / 0 failed**.
- Strict customization checker: exit 0. Merge contract: **49 passed / 0
  failed**.
- Scoped Desktop verification: typecheck passed, focused UI tests **114 passed /
  0 failed**, scoped ESLint had zero errors, and scoped Prettier passed.
- The manual integration-only rehearsal resolved exact upstream/candidate/OTTO/
  LOOP24 refs, completed all three disposable merges and both brand generations,
  preserved candidate ancestry/runtime bytes, and removed every disposable
  worktree without changing live refs.

A fresh read-only schema probe at the reviewed tree returned:

```text
Archon: profile=archon-2026-07 normalizer=3 compact CLI bytes=233438
        compatibility codes=39 extension-options=present
Legacy: profile=hermes-legacy normalizer=2 compact CLI bytes=225133
        compatibility codes=9 extension-options=absent
```

The byte counts include the CLI newline and remain below the fixed 256,000-byte
contract ceiling. The retained Task 15 report records older, larger byte-count
measurements; those historical counts are not used as exact final-tree
evidence. The semantic inventory and required bound agree, so this is an
evidence-note deviation rather than a product finding.

## Customization and integration decisions

The strict checker accepted the eight explicit Phase 3 overlap decisions:

```text
desktop-workflow-test-gate=adapt
plugin-agent-request-mcp-lifecycle=adapt
workflow-language-admission-pinning=preserve
workflow-language-schema-cli=adapt
workflow-language-desktop-status=preserve
workflow-language-desktop-capability-skew=preserve
workflow-language-regression-gates=adapt
workflow-parser-backed-symbol-ownership=preserve
```

The decisions retain the generic seams and downstream contracts, adapt only
where current upstream/startup/gate behavior requires it, and do not claim any
entry is upstream-equivalent. The integration-only rehearsal did not advance
the ledger baseline or any live branch/ref.

## Deviations from the original Task 16 plan

The active Task 16 brief records the user's later override, which supersedes
the original broad-suite and standard generic-rehearsal wording. Accordingly:

- no canonical discovered-file suite was accepted as evidence;
- no generic ledger-stage completion was claimed; and
- the retained integration evidence comes from the bounded manual
  integration-only rehearsal, while ordinary behavior comes from the explicit
  focused/base/schema/Desktop/customization allowlists above.

This is the required safe execution of the active brief, not a waived product
requirement. The discarded attempts remain only a deviation record and have no
effect on this verdict.

## Final disposition

The exact production tree `94f4fd4572b63ba6dd496213b603e67748b41b46`
delivers the approved Phase 3 ordinary functional contract, preserves legacy
behavior and published compatibility surfaces, retains the established
lifecycle/recovery invariants, and does not enter Phase 4 or Phase 5. There are
no remaining specification findings.
