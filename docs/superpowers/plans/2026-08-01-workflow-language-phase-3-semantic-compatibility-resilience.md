# Workflow Language Phase 3: Semantic Compatibility and Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Revised after independent review — awaiting user approval

**Goal:** Make Archon timeout, retry, typed-condition, strict-reference, large Bash substitution, and missing persistent-session semantics explicit, durable, and safe while preserving exact unversioned and `hermes-legacy` behavior.

**Architecture:** Introduce profile-specific normalizer v3 and seal both requested language semantics and effective execution semantics at admission. Route all v3 references through one typed/rendered resolver, let the scheduler own typed conditions, bounded resolution wakes, and one combined attempt ledger, and add a descriptor-authoritative Bash renderer. Missing cross-run sessions use a generic typed classification, then a workflow-owned, atomically journaled registry-update obligation. Existing authenticated API and Desktop surfaces project only bounded backend truth.

**Tech Stack:** Python 3.11+, immutable dataclasses, YAML plus canonical JSON snapshots, SQLite and JSONL run journals, descriptor-relative POSIX filesystem operations, `/bin/sh`, `ManagedProcessTree`, FastAPI/Pydantic, Electron React/TypeScript, Vitest/Testing Library, and the repository test runner.

## Approval and execution boundary

- This document is planning-only. Do not edit production code until the user approves the independently reviewed design and this plan.
- Execute in the dedicated worktree on `feat/workflow-language-phase-3-semantic-compatibility-resilience`, never literal `main`.
- `base` is the development main. Do not push, publish, delete a branch/worktree, propagate brand branches, or merge Phase 3 without separate authorization.
- Preserve all user-owned changes in the shared base checkout. Never stash, commit, delete, overwrite, or reformat them.

## Global implementation constraints

- Gate every new semantic on a newly admitted `WorkflowLanguageProfile.ARCHON_2026_07` snapshot with `normalizer_version: 3`.
- New unversioned and `hermes-legacy` packages remain on normalizer v2. Admitted v1/v2 runs remain readable and execute their recorded behavior exactly.
- Do not add MCP or skills node kinds. They remain options on existing command/prompt nodes.
- Do not add Phase 4 loops/includes or Phase 5 provider-portability behavior.
- Keep prompt prefixes byte-stable. Do not alter historical messages, add a model tool, or widen the core/tool waist.
- Keep API and evidence projections bounded. Never expose raw provider responses, raw substituted values, provider histories, spill paths, registry keys, session IDs, or fingerprints.
- Do not add path-taking artifact, spill, or recovery endpoints.
- The 32,768-byte Bash boundary is measured on resolved UTF-8 bytes. The rendered command bytes passed to `/bin/sh -c` are authoritative.
- Retry authoring counts retries after the initial call, while the sealed combined ledger counts the initial call plus every additional provider/workflow attempt exactly once.
- Timeout is per workflow attempt. A workflow retry receives a new sealed attempt budget; Phase 3 does not invent a cross-retry total-node deadline.
- Run Python tests only through `scripts/run_tests.sh`. Never invoke the test framework directly.
- The user has pre-authorized up to three bounded fix/verification retry rounds. A retry does not authorize scope expansion, destructive Git operations, publication, or silent acceptance of a failing invariant.
- Keep each implementation and fix commit atomic. Keep the Phase 3 worktree clean at every implementer/reviewer/controller handoff.

## Required task handoff protocol

For every implementation task below:

1. Dispatch a fresh implementer subagent with ownership limited to that task's files and tests. State that other agents share the repository and it must not revert unrelated work.
2. The implementer must add the specified failing test first and run the listed command to capture genuine RED before changing production code.
3. After GREEN and an atomic implementation commit, dispatch a fresh independent specification reviewer against the design and task requirements.
4. Then dispatch a different fresh independent quality reviewer for bugs, security, maintainability, bounds, and regression risk.
5. Route findings through at most three bounded fix rounds. Each round gets fresh focused verification and an atomic fix commit. Use superpowers:receiving-code-review before applying findings.
6. The controller reruns the focused command, checks the exact diff/commit, and verifies a clean worktree before the next task.

No task may waive RED because a neighboring task happened to add part of the behavior. If the planned RED is already green, stop and prove whether the behavior is already complete or the test is ineffective before proceeding.

## File map

### New production modules

- `plugins/workflow/execution_semantics.py` — immutable v3 requested/effective timeout and retry projections, canonical parsing, and resume verification.
- `plugins/workflow/conditions.py` — v3 condition lexer, parser, typed evaluator, precedence, and stable failures.
- `plugins/workflow/bash_rendering.py` — bounded shell lexer, inline/spill rendering, inherited-descriptor manifest, and exact command evidence.

### New focused test modules

- `tests/plugins/workflow/test_phase3_language.py`
- `tests/plugins/workflow/test_phase3_execution_semantics.py`
- `tests/plugins/workflow/test_strict_output_references.py`
- `tests/plugins/workflow/test_phase3_conditions.py`
- `tests/plugins/workflow/test_phase3_resolution_waits.py`
- `tests/plugins/workflow/test_phase3_bash_substitution.py`
- `tests/plugins/workflow/test_persistent_session_recovery.py`
- `tests/plugins/workflow/test_phase3_code_catalog.py`

### Principal modified modules

- `plugins/workflow/language.py`
- `plugins/workflow/language_schema.py`
- `plugins/workflow/models.py`
- `plugins/workflow/schema.py`
- `plugins/workflow/compat.py`
- `plugins/workflow/trust.py`
- `plugins/workflow/resources.py`
- `plugins/workflow/output_resolution.py`
- `plugins/workflow/store.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/sessions.py`
- `plugins/workflow/executors/base.py`
- `plugins/workflow/executors/ai.py`
- `plugins/workflow/executors/bash.py`
- `plugins/workflow/executors/script.py`
- `plugins/workflow/executors/approval.py`
- `plugins/workflow/executors/loop.py`
- `plugins/workflow/api_admission.py`
- `plugins/workflow/gateway_command.py`
- `plugins/workflow/cli.py`
- `plugins/workflow/showcase.py`
- `plugins/workflow/evidence.py`
- `plugins/workflow/dashboard/plugin_api.py`
- `agent/plugin_agent.py`
- `agent/plugin_agent_worker.py`
- `tools/managed_process.py`
- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/src/app/workflows/run-inspector.tsx`
- `website/docs/user-guide/features/workflow-yaml-reference.md`
- `skills/software-development/workflow-builder/references/portable-schema.md`
- `docs/upstream-customizations/workflow-orchestration.yaml`

The implementation may omit a listed modified file when tests prove it needs no change. Adding a new production module not listed here requires a concrete ownership or dependency reason recorded in the task report; it must not become speculative infrastructure.

---

## Task 1: Introduce profile-specific normalizer v3 and requested semantics

**Files:**

- Create: `tests/plugins/workflow/test_phase3_language.py`
- Create: `tests/plugins/workflow/test_phase3_code_catalog.py`
- Modify: `plugins/workflow/language.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/compat.py`
- Modify: `plugins/workflow/trust.py`
- Test: `tests/plugins/workflow/test_language.py`
- Test: `tests/plugins/workflow/test_language_snapshot.py`
- Test: `tests/plugins/workflow/test_trust_policy.py`
- Test: `tests/plugins/workflow/test_compat_matrix.py`

- [ ] Add failing tests for profile-specific current versions.

  Assert new unversioned and `hermes-legacy` packages remain byte-for-byte on v2, new `archon-2026-07` packages select v3, and sealed v1/v2 snapshots reload without extra fields or changed digests. Assert explicit v1/v2 reload never upgrades.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_language.py tests/plugins/workflow/test_language_snapshot.py`

  Expected: FAIL because current normalization is globally v2 and no v3 snapshot exists.

- [ ] Implement profile-specific version selection.

  Add `LATEST_NORMALIZER_VERSION = 3`, `CURRENT_NORMALIZER_BY_PROFILE`, and `SUPPORTED_NORMALIZER_VERSIONS = frozenset({1, 2, 3})`. `None` selects after profile resolution; an admitted integer dispatches exactly. Keep v1 and v2 serializers/parsers unchanged.

- [ ] Add failing tests for the exact requested `node_semantics` projection.

  Cover millisecond-to-second conversion; positive finite validation; omitted Archon Bash/script timeout `120.0`; authored timeout/idle values; AI default two retries; deterministic default zero retries; explicit delay/on-error/max-attempts; inapplicable loop/approval/cancel fields; `max_attempts: 5` as requested retries 5 and requested total 6; sorted canonical keys; and bounded snapshot rejection.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_schema.py`

  Expected: FAIL because the v3 requested semantic bundle and field statuses do not exist.

- [ ] Implement `_normalize_v3()` and exact snapshot parsing.

  Normalize units once. Store only applicable requested fields. Keep the Phase 2 `structured_outputs` projection. Include both projections in the normalized-definition digest and semantic fingerprint. Reject booleans, zero, negatives, NaN, infinity, unsupported node kinds, and malformed retry objects with the stable design codes.

- [ ] Add trust and migration RED tests, then bind v3 semantic identity.

  A trusted Archon v2 package with unchanged source must require retrust when normalized as v3; legacy v2 must not drift. Doctor findings must remove implemented Archon blockers, preserve legacy warnings, and explain seconds-to-milliseconds, retries-after-initial, direct dependencies, typed comparisons, and the Bash byte boundary.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_trust_policy.py tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_doctor.py`

  Expected: FAIL because trust risk identity and migration guidance do not include v3 semantics.

- [ ] Implement trust identity and central compatibility inventory updates without duplicating policy outside `language_schema.py`/`compat.py`.

- [ ] Establish the versioned Phase 3 durable-code catalog authority.

  Add bounded code metadata to the existing dependency-neutral
  `plugins/workflow/language_schema.py` authority: stable code, public meaning,
  area, effective profile, normalizer applicability, and whether it may appear
  in compatibility, runtime failure, or evidence projections. Extend
  `compatibility_code_catalog()`, doctor, and editor/API contract generation
  from that authority. Add relationship tests proving uniqueness, bounded
  metadata, and profile/version applicability without asserting a brittle
  enumeration count. Later tasks must register each code in the same commit as
  its real emitter and add a behavior path to the completeness suite; source
  text searches do not count as emitter coverage.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_doctor.py`

  Expected: FAIL because v3 durable runtime/evidence code metadata and
  behavior-linked completeness hooks do not exist.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_language.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_trust_policy.py tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_doctor.py`

  Commit: `feat(workflow): add phase 3 language normalization`

## Task 2: Seal effective execution semantics at every admission boundary

**Files:**

- Create: `plugins/workflow/execution_semantics.py`
- Create: `tests/plugins/workflow/test_phase3_execution_semantics.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/api_admission.py`
- Modify: `plugins/workflow/gateway_command.py`
- Modify: `plugins/workflow/showcase.py`
- Test: `tests/plugins/workflow/test_cli.py`
- Test: `tests/plugins/workflow/test_api_runtime.py`
- Test: `tests/plugins/workflow/test_scheduled_runs.py`
- Test: `tests/plugins/workflow/test_showcase_schedule_e2e.py`
- Test: `tests/plugins/workflow/test_phase3_code_catalog.py`

- [ ] Add failing round-trip tests for exact `phase3_execution_semantics` schema version 1.

  Assert the exact five-field limits object, exact node field set, null applicability, finite-number rules, timeout source, requested/effective wall and idle values, provider ceiling, timeout/retry capped bits, and field-specific attempt ranges. Include the boundary `requested_retries: 5`, `requested_total_attempts: 6`, `effective_total_attempts: 5`.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_execution_semantics.py`

  Expected: FAIL because no effective-semantics codec exists.

- [ ] Implement immutable requested-to-effective projection and canonical verification.

  Normalize the historical `RunExecutionLimits.combined_retries` field once into v3 `combined_total_attempts`. Compute omitted Bash/script effective timeout as `min(120.0, subprocess ceiling)`. Compute command/prompt wall, idle, and provider limits from the resolved admission authority. Never read current config while parsing the sealed projection.

- [ ] Add failing admission parity tests.

  Exercise CLI, API, gateway, showcase, scheduled promotion, and direct-store helpers with the same profile plus authenticated sidecar. Assert identical canonical projection bytes and digest. Direct-store calls without an explicit authority must use one documented default object rather than infer environment state.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_scheduled_runs.py tests/plugins/workflow/test_showcase_schedule_e2e.py`

  Expected: FAIL because admission callers do not pass a resolved authority into snapshot preparation.

- [ ] Pass one resolved authority explicitly into `RunStore.prepare_run_snapshot()` and seal canonical projection bytes in `resources.json` plus `input_manifest_digest`.

- [ ] Add changed-config resume and tamper RED tests.

  Admit under one configuration, change profile defaults, restart scheduler/store, and prove execution consumes the original projection. Tampering requested/effective bytes, field sets, or manifest identity must fail as `workflow_language_snapshot_mismatch` or `workflow_execution_semantics_mismatch` before execution.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_crash_recovery.py`

  Expected: FAIL because the scheduler reconstructs limits from current configuration.

- [ ] Make v3 scheduler load authenticated effective semantics directly. Keep legacy `_run_execution_limits()` behavior unchanged.

- [ ] Register normalization and execution-snapshot mismatch codes in the
  Phase 3 durable-code catalog and exercise each through real load/admission or
  resume behavior in `test_phase3_code_catalog.py`.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_scheduled_runs.py tests/plugins/workflow/test_showcase_schedule_e2e.py tests/plugins/workflow/test_crash_recovery.py`

  Commit: `feat(workflow): seal phase 3 execution semantics`

## Task 3: Close the v3 reference grammar at static admission

**Files:**

- Create: `tests/plugins/workflow/test_strict_output_references.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/trust.py`
- Modify: `plugins/workflow/compat.py`
- Test: `tests/plugins/workflow/test_structured_output_language.py`
- Test: `tests/plugins/workflow/test_security_boundaries.py`
- Test: `tests/plugins/workflow/test_phase3_code_catalog.py`

- [ ] Add failing table-driven lexer and identifier tests.

  Use the exact ASCII node/path grammar. Cover accepted underscore/hyphen IDs and field/index segments; rejected leading digits, dots, Unicode, slash/backslash, empty segments, leading-zero indexes, bracket syntax, and dotted mapping keys. Assert legacy identifier acceptance does not change.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_structured_output_language.py`

  Expected: FAIL because current schema, condition, and runtime patterns accept different languages.

- [ ] Implement one versioned reference token/iterator in the central field inventory and reuse it from schema/admission/resource scanning.

  Do not add an escape syntax. Reject unsafe v3 node IDs with `archon_node_id_not_reference_safe` and unaddressable paths with `output_reference_path_unsupported`.

- [ ] Add failing direct-dependency tests across every authenticated surface.

  Cover `when`, inline prompt/Bash/script, authenticated command bodies, approval messages/rejection prompts, and existing loop prompt/`until_bash` fields. Test direct, transitive-only, unknown, self, downstream, cyclic, and impossible schema paths. Ensure command bytes are scanned only after authenticated resolution and before promotion.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_security_boundaries.py`

  Expected: FAIL because Phase 2 validates fewer surfaces and allows transitive/legacy adapters.

- [ ] Enforce `output_reference_not_declared_dependency` and existing topology errors consistently at v3 admission.

- [ ] Add failing named-script tests.

  A recognized reference in sealed named script bytes must block as `named_script_output_reference_unsupported`; ordinary dollar syntax and reference-free named scripts remain unchanged. Inline scripts remain referencable.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_script_executor.py`

  Expected: FAIL because named scripts are not part of the current reference scan.

- [ ] Scan authenticated named-script bytes and fail explicitly without generating a mutable script copy.

- [ ] Register every static-reference and identifier blocker in the Phase 3
  durable-code catalog in this commit, and extend the behavior-linked
  completeness matrix with the real admission path for each code.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_structured_output_language.py tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_script_executor.py`

  Commit: `feat(workflow): enforce strict phase 3 references`

## Task 4: Add one typed and rendered runtime output resolver

**Files:**

- Modify: `plugins/workflow/output_resolution.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/executors/base.py`
- Modify: `tests/plugins/workflow/test_strict_output_references.py`
- Test: `tests/plugins/workflow/test_typed_publication.py`
- Test: `tests/plugins/workflow/test_typed_publication_recovery.py`
- Test: `tests/plugins/workflow/test_phase3_code_catalog.py`

- [ ] Add failing resolver tests for `ResolvedOutputReference(typed_value, rendered_text)`.

  Cover schemaless whole text, structured root scalars, mappings, arrays, exact keys, canonical indexes, missing fields, scalar descent, schema absence, winning-publication identity, digest/media mismatch, and JSON-looking schemaless text. Assert structured strings render without JSON quote characters while non-strings render as finite canonical JSON.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_typed_publication.py`

  Expected: FAIL because the Phase 2 resolver returns a single adapter value and has no typed error hierarchy.

- [ ] Implement immutable `ResolvedOutputReference` and `WorkflowOutputReferenceError(code, node_id, path)`.

  Conditions receive `typed_value`; prompt/script/Bash/approval substitutions receive `rendered_text`. Never reparse provider text and never turn a strict failure into empty text. Preserve Phase 2/legacy resolver entry points as explicit adapters.

- [ ] Add failing integrity and winning-attempt recovery tests.

  Verify only the successful winning publication can resolve. Descriptor, digest, content, media type, canonicalization version, schema fingerprint, or attempt identity drift must be `output_reference_integrity` and must not poison a reusable cache entry.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py`

  Expected: FAIL on strict error identities and typed/rendered recovery paths.

- [ ] Thread the resolver object through `NodeExecutionContext` and scheduler caches with existing weight/count bounds.

- [ ] Register the strict runtime resolver codes in the Phase 3 durable-code
  catalog and prove each through actual resolution/integrity behavior.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_performance_bounds.py`

  Commit: `feat(workflow): resolve phase 3 outputs strictly`

## Task 5: Evaluate v3 conditions against canonical typed values

**Files:**

- Create: `plugins/workflow/conditions.py`
- Create: `tests/plugins/workflow/test_phase3_conditions.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/schema.py`
- Test: `tests/plugins/workflow/test_scheduler.py`
- Test: `tests/plugins/workflow/test_parallel_scheduler.py`
- Test: `tests/plugins/workflow/test_phase3_code_catalog.py`

- [ ] Add failing lexer/parser tests for exact grammar and precedence.

  Cover `==`, `!=`, `<`, `<=`, `>`, `>=`, `&&`, `||`, whitespace, quoted numeric RHS, short-circuit, malformed tokens, trailing tokens, rejected parentheses/functions/truthiness/arithmetic, and precedence with `&&` tighter than `||`.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_conditions.py`

  Expected: FAIL because current `evaluate_condition()` uses truthiness/reparse adapters.

- [ ] Implement a bounded v3 condition lexer/parser with no general expression evaluation.

  Reuse the strict reference lexer. Bound expression bytes, token count, nesting, and diagnostic bytes using central constants. Do not use Python evaluation or provider text reparsing.

- [ ] Add failing typed comparison matrices.

  Test structured root and field strings, integers, finite decimals, booleans, null, arrays, objects; schemaless numeric text; structured numeric-looking strings; quoted/unquoted RHS numerics; exact string equality; type mismatches; exponents, locale forms, hex, NaN, infinity, empty/partial parses; and decimal ordering without binary-float surprises.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_conditions.py`

  Expected: FAIL because canonical typed values and strict failure codes are not consumed.

- [ ] Implement type-directed equality and finite decimal ordering.

  Only schemaless whole-output text and RHS numeric syntax may parse decimal text. A string field in a declared structured output remains a string. Boolean/null/container operands fail with the stable condition codes.

- [ ] Add scheduler transition RED tests.

  False must atomically produce `pending -> skipped` with `condition_false`. Any typed/reference/syntax error must atomically produce `pending -> failed`, bounded `last_error`, zero executor/provider attempts, and zero retry consumption. No `on_error` policy may retry it.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_conditions.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py`

  Expected: FAIL because condition errors currently collapse to false/skip or executor failure.

- [ ] Dispatch v3 to the new evaluator and legacy to the unchanged current adapter.

- [ ] Register every condition code in the Phase 3 durable-code catalog and
  cover it through a real parser/evaluator/store-transition behavior path.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_conditions.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_compat_matrix.py`

  Commit: `feat(workflow): evaluate typed phase 3 conditions`

## Task 6: Make transient reference reads durably bounded

**Files:**

- Create: `tests/plugins/workflow/test_phase3_resolution_waits.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/output_resolution.py`
- Test: `tests/plugins/workflow/test_coordinator_multiprocess.py`
- Test: `tests/plugins/workflow/test_crash_recovery.py`
- Test: `tests/plugins/workflow/test_performance_bounds.py`
- Test: `tests/plugins/workflow/test_phase3_code_catalog.py`

- [ ] Add failing store tests for the exact resolution-wait state machine.

  The initial transient failure schedules 250 ms. Failed wake observations one through four schedule 500 ms, 1 s, 2 s, and 4 s. The fifth failed wake—the sixth failed observation total—fails as `output_reference_unavailable`. Assert immutable producer publication identity, `resolution_read_count`, and `next_resolution_at` round-trip through restart.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_resolution_waits.py`

  Expected: FAIL because no durable output-resolution wake exists.

- [ ] Implement fenced `RunStore.defer_output_resolution()`, wake selection, success clearing, and terminal exhaustion.

  The transition occurs before claim/executor launch, consumes no workflow/provider attempt, and suppresses ordinary runnable graph evaluation until due.

- [ ] Add multiprocess and crash RED tests.

  Race coordinators at the same due time and prove one CAS records each wake. Restart at every backoff state. Prove there is no cached miss, claim churn, immediate polling, retry charge, provider allocation, or hot loop.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_resolution_waits.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_performance_bounds.py`

  Expected: FAIL because pending nodes are selected repeatedly and transient errors lack a distinct boundary.

- [ ] Route only `output_reference_temporarily_unavailable` through the wait protocol. Convert every other strict resolver error to a terminal zero-attempt node failure.

- [ ] Register transient and exhausted-resolution codes in the Phase 3
  durable-code catalog and prove both through the real wait state machine.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_resolution_waits.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_performance_bounds.py`

  Commit: `feat(workflow): bound phase 3 reference waits`

## Task 7: Thread strict substitution through existing consumers

**Files:**

- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/executors/script.py`
- Modify: `plugins/workflow/executors/approval.py`
- Modify: `plugins/workflow/executors/loop.py`
- Modify: `tests/plugins/workflow/test_strict_output_references.py`
- Test: `tests/plugins/workflow/test_ai_executor.py`
- Test: `tests/plugins/workflow/test_script_executor.py`
- Test: `tests/plugins/workflow/test_approval.py`
- Test: `tests/plugins/workflow/test_loop_executor.py`

- [ ] Add failing cross-surface runtime tests.

  Resolve whole and field outputs in prompt/command, inline script, approval message/rejection prompt, and the already-existing loop prompt/`until_bash` surfaces. Assert direct dependency again at execution, deterministic `rendered_text`, and the same stable failure code at every surface.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py`

  Expected: FAIL because existing consumers independently call `VariableContext` and retain Phase 2 coercions.

- [ ] Introduce a v3 strict rendering facade backed only by resolved immutable objects.

  Keep legacy `VariableContext` rendering intact. Resolve before executor side effects. Command content remains authenticated byte authority; named scripts remain non-interpolated. Do not add loop fields or change loop execution semantics.

- [ ] Add cache and alternation invariants.

  Prove substitution happens only in the initial isolated node request/body, does not change the tool schema/system prompt, does not mutate history, and cannot create same-role message adjacency.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_node_mcp.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_node_hooks.py tests/plugins/workflow/test_ai_extensions_middleware_e2e.py`

  Expected: FAIL until all strict paths share the facade and prompt invariants are asserted.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_node_mcp.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_node_hooks.py tests/plugins/workflow/test_ai_extensions_middleware_e2e.py`

  Commit: `feat(workflow): render strict phase 3 substitutions`

## Task 8: Enforce sealed per-attempt timeout semantics

**Files:**

- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/executors/base.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/executors/bash.py`
- Modify: `plugins/workflow/executors/script.py`
- Test: `tests/plugins/workflow/test_deadlines.py`
- Test: `tests/plugins/workflow/test_ai_executor.py`
- Test: `tests/plugins/workflow/test_bash_e2e.py`
- Test: `tests/plugins/workflow/test_script_executor.py`
- Test: `tests/plugins/workflow/test_shutdown_recovery.py`

- [ ] Add failing timeout normalization-to-execution tests.

  Cover authored fractional milliseconds, omitted Bash/script 120 seconds under ceilings below/equal/above 120, AI wall/idle/provider intersections, exact boundary expiry, and absence of current-config reads on resumed v3 runs.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_deadlines.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_script_executor.py`

  Expected: FAIL because executors still reinterpret node options/current limits.

- [ ] Build one `DeadlineBudget` per claimed workflow attempt from sealed effective values.

  Nested provider requests and repair receive `min(remaining attempt wall, provider ceiling)`. AI idle expiry uses semantic progress only. Bash/script use the sealed attempt wall. Executors must not inspect raw authored milliseconds.

- [ ] Add retry/restart timeout tests.

  Prove retry backoff is outside the prior attempt and a later workflow retry gets a fresh per-attempt budget. Prove active process/claim recovery classifies the in-flight outcome before another claim, so restart cannot silently run two attempts or reset an active one.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_deadlines.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_crash_recovery.py`

  Expected: FAIL until scheduler and executors consume only the sealed attempt contract.

- [ ] Preserve exact legacy timeout defaults, units, and code paths with regression assertions.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_deadlines.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_crash_recovery.py`

  Commit: `feat(workflow): enforce phase 3 attempt timeouts`

## Task 9: Normalize retries into one non-multiplying ledger

**Files:**

- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/executors/base.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `tests/plugins/workflow/test_retry.py`
- Test: `tests/plugins/workflow/test_provider_failures.py`
- Test: `tests/plugins/workflow/test_ai_executor.py`
- Test: `tests/plugins/workflow/test_parallel_scheduler.py`
- Test: `tests/plugins/workflow/test_coordinator_multiprocess.py`

- [ ] Add failing normalization/accounting matrices.

  Cover AI default requested 2/effective total 3, deterministic default total 1, explicit retries 1 and 5, combined caps 1 through 5, and exact requested/effective/capped evidence. Assert legacy still treats `max_attempts` as its existing total-attempt ceiling and retains its delay.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_retry.py`

  Expected: FAIL because current workflow and provider retry layers use independent totals.

- [ ] Implement one sealed grant and durable charge equation.

  Before execution, grant `effective_total_attempts - retry_consumed`. Afterward charge exactly one workflow attempt plus validated additional provider attempts. Convert total provider-call evidence exactly once; conservatively consume the full grant when exact evidence is absent/invalid. Repair and fallback calls draw from the same grant.

- [ ] Add failing provider/workflow composition tests.

  Cover provider-only, workflow-only, mixed failure, structured repair, fallback, unknown provider count, and concurrent coordinator cases. For every row assert total calls never exceed the sealed effective total and no layer subtracts/adds the initial call twice.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_retry.py tests/plugins/workflow/test_provider_failures.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_coordinator_multiprocess.py`

  Expected: FAIL on multiplied or miscounted attempts.

- [ ] Implement stable retry classification.

  Map fatal, transient, unknown-no-effect, and unknown-outcome exactly. Outward, uncertain, potentially completed, cleanup, validation, resource, contract-drift, authentication/authorization, and credit failures never replay. Deterministic retries require both an explicit block and a known retryable/no-effect outcome.

- [ ] Add cancellation RED tests at wake, claim, backoff, provider allocation, and shutdown boundaries.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_retry.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_shutdown_recovery.py`

  Expected: FAIL if cancellation consumes a new attempt or a wake races into launch.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_retry.py tests/plugins/workflow/test_provider_failures.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_shutdown_recovery.py`

  Commit: `feat(workflow): unify phase 3 retry accounting`

## Task 10: Add generic bounded child-descriptor inheritance

**Files:**

- Modify: `tools/managed_process.py`
- Modify: `tests/tools/test_managed_process.py`
- Test: `tests/tools/test_process_registry.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_workflow_merge_gate.py`

- [ ] Add failing POSIX descriptor-inheritance tests before touching the
  workflow plugin.

  Add an explicit bounded `inherited_descriptors` argument to the intended
  `ManagedProcessTree.spawn()` contract in tests. Prove a child reads the exact
  nominated read-only descriptor, unrelated descriptors are closed, standard
  descriptors/duplicates/closed descriptors/over-bound lists are rejected,
  `start_new_session` and process-tree termination remain intact, spawn failure
  closes no caller-owned handle unexpectedly, and a non-empty request fails
  closed on native Windows.

  Run: `scripts/run_tests.sh tests/tools/test_managed_process.py tests/tools/test_process_registry.py`

  Expected: FAIL because `ManagedProcessTree.spawn()` has no explicit bounded
  descriptor contract.

- [ ] Implement the smallest generic primitive.

  Validate at most 64 unique descriptors above standard input/output/error,
  pass exactly those through the POSIX process API with all other descriptors
  closed, and preserve the existing Windows Job Object, process identity,
  termination, escalation, resource, and reap semantics. The generic primitive
  does not know workflow values, spills, shell syntax, or evidence.

- [ ] Add a dedicated Phase 3 extension entry adjacent to the existing
  historical `managed-process-tree` entry in
  `docs/upstream-customizations/workflow-orchestration.yaml`.

  Leave the historical entry and its expected subject intact. The new entry
  records only the inherited-descriptor owned symbol/contract, exact tests,
  merge guidance, `upstream_candidate: true`, removal condition, and expected
  subject `feat(process): inherit bounded child descriptors`. Copy rather than
  advance the existing `last_verified_upstream` identity.

- [ ] Commit the generic primitive and its ledger amendment together.

  Commit: `feat(process): inherit bounded child descriptors`

- [ ] From the resulting clean commit, run the focused generic suite and live
  customization gates.

  Run: `scripts/run_tests.sh tests/tools/test_managed_process.py tests/tools/test_process_registry.py tests/scripts/test_workflow_merge_gate.py`

  Run: `../../.venv/bin/python scripts/check_upstream_customizations.py --strict --base-ref HEAD`

  Run: `scripts/test_workflow_merge_gate.sh --phase base`

  Expected: PASS with the new generic seam isolated at this commit boundary.

  If a gate finds a defect, fix it in a new atomic commit, rerun all three
  commands, and hand off only a clean tree.

## Task 11: Substitute large Bash values through verified descriptors

**Files:**

- Create: `plugins/workflow/bash_rendering.py`
- Create: `tests/plugins/workflow/test_phase3_bash_substitution.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/executors/bash.py`
- Modify: `plugins/workflow/executors/base.py`
- Test: `tests/plugins/workflow/test_bash_e2e.py`
- Test: `tests/tools/test_managed_process.py`
- Test: `tests/plugins/workflow/test_security_boundaries.py`
- Test: `tests/plugins/workflow/test_performance_bounds.py`
- Test: `tests/plugins/workflow/test_phase3_code_catalog.py`

- [ ] Add failing byte-bound and content-preservation tests using real `/bin/sh`.

  Cover 32,767, 32,768, and 32,769 UTF-8 bytes; multibyte splits; empty values; spaces, quotes, dollar signs, backticks, globs, Unicode, terminal `x`, and trailing newlines. Test unquoted, double-quoted, and single-quoted admitted simple-token contexts. Small values stay inline; large values resolve by contents, never path.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_bash_substitution.py tests/plugins/workflow/test_bash_e2e.py`

  Expected: FAIL because current spill substitution reopens a pathname and loses size-independent semantics.

- [ ] Implement a bounded shell lexer and fail-closed contexts.

  Recognize ordinary unquoted/single/double-quoted command-word text, escapes, comments, redirection, and nesting boundaries. Ignore escaped references and comments. Reject references inside heredoc delimiter/body, command substitution, backticks, arithmetic expansion, parameter expansion, or unterminated/ambiguous state as `bash_reference_context_unsupported`.

- [ ] Add failing unsafe-context and evaluation tests.

  Use real shell commands for quoted/unquoted heredocs, comments, escaped delimiters, nested substitutions/expansions, and payloads containing executable metacharacters. Assert rejected contexts never launch and admitted values are data, not syntax.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_bash_substitution.py tests/plugins/workflow/test_security_boundaries.py`

  Expected: FAIL because the current three-state quote walk is incomplete.

- [ ] Implement bounded descriptor-relative spill creation and inherited consumption.

  Enforce NUL rejection, 64 distinct files, 500,000 bytes per value, and 2,000,000 total bytes. Use opaque indexes, safe descriptor traversal, `O_NOFOLLOW` where required, `O_EXCL`, mode `0600`, regular/single-link checks, bounded writes, `fsync`, reopen through verified descriptor chain, identity/size/digest verification, and rewind. Keep only verified read-only descriptors through launch.

  Render one deterministic variable per distinct spill using the verified file descriptor, not a pathname:

  ```sh
  __HERMES_WF_SPILL_abcd=$(command cat <&17; __hermes_rc=$?; printf x; exit "$__hermes_rc") || exit $?
  __HERMES_WF_SPILL_abcd=${__HERMES_WF_SPILL_abcd%x}
  ```

  The sentinel preserves trailing newlines and the captured read status prevents
  `printf` from masking failure. Use the exact context table:

  | Placeholder context | Replacement |
  |---|---|
  | unquoted token | `"${__HERMES_WF_SPILL_abcd}"` |
  | already double-quoted token | `${__HERMES_WF_SPILL_abcd}` |
  | already single-quoted token | `'"${__HERMES_WF_SPILL_abcd}"'` |

  Deduplicate identical resolved values before assigning bounded descriptors.
  Real `/bin/sh` tests must compare exact argument/content identity separately
  in all three surrounding contexts—including spaces, globs, empty strings,
  quotes, and trailing newlines—and prove the double-quoted expansion remains
  inside the original double-quoted word.

- [ ] Consume Task 10's explicit bounded inherited-descriptor argument.

  Pass only spill descriptors; retain start-new-session containment; close parent descriptors after spawn; never make unrelated handles inheritable. On native Windows fail closed for large v3 values before launch and keep existing inline command construction/platform gate.

- [ ] Register every Bash admission/runtime code in the Phase 3 durable-code
  catalog and prove each through a real lexer, bound, integrity, or launch path.

- [ ] Add descriptor race, failure, and evidence RED tests.

  Replace/unlink the pathname between materialization and spawn; attempt symlink/escape swaps; close/corrupt a descriptor; test 64/65 files and total bounds. Assert the shell reads the verified handle or fails, never the replacement. Assert exact `argv[-1]`, template/rendered SHA-256 and byte size, spill count/total/content digests, fixed descriptor manifest, no values/paths, cleanup, and legacy pathname behavior.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_bash_substitution.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_bash_e2e.py tests/tools/test_managed_process.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_performance_bounds.py`

  Expected: FAIL until consumption and process launch share descriptor authority.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_bash_substitution.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_bash_e2e.py tests/tools/test_managed_process.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_performance_bounds.py`

  Commit: `feat(workflow): secure large bash substitutions`

## Task 12: Classify missing isolated sessions without widening core behavior

**Files:**

- Modify: `agent/plugin_agent.py`
- Modify: `agent/plugin_agent_worker.py`
- Modify: `tests/agent/test_plugin_agent.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/plugins/workflow/test_ai_executor.py`
- Test: `tests/scripts/test_workflow_merge_gate.py`

- [ ] Add failing parent-preflight tests for `PluginAgentSessionMissingError`.

  With a real temporary profile-local `SessionDB`, distinguish confirmed missing from database open/read error, corrupt/ambiguous state, denied access, and an existing empty/history-light session. The error carries no history/provider response and reports zero provider attempts.

  Run: `scripts/run_tests.sh tests/agent/test_plugin_agent.py`

  Expected: FAIL because missing persistent sessions are not a typed generic outcome.

- [ ] Add the narrow generic exception and parent preflight.

  Raise only when `SessionDB.get_session(exact_id)` returns `None`. Do not choose fresh behavior in the generic layer and do not add a workflow import to the agent core.

- [ ] Add failing worker-race wire tests.

  Delete the session after parent preflight but before child load. The worker must emit sanitized `failure_kind: persistent_session_missing` with exact zero-provider evidence. Unknown fields, spoofed counts, and raw exception/session content must be rejected or sanitized.

  Run: `scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_ai_executor.py`

  Expected: FAIL because the worker has no typed race frame.

- [ ] Implement the bounded worker classification and strict parent correlation.

  Preserve all existing non-workflow callers and session behavior. Do not change prompts, toolsets, or history.

- [ ] Add a dedicated Phase 3 extension entry adjacent to the existing
  historical `plugin-agent-runner` entry in the real workflow customization
  ledger.

  Leave the historical expected subject intact. The new entry owns only
  `PluginAgentSessionMissingError`, parent preflight, worker
  `persistent_session_missing` framing/correlation, and their tests. Record
  merge guidance, `upstream_candidate: true`, removal condition, and expected
  subject `feat(agent): classify missing plugin sessions`; copy rather than
  advance the existing `last_verified_upstream` identity.

- [ ] Run focused verification, then commit the generic classification and its
  ledger amendment together.

  Run: `scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_ai_executor.py tests/scripts/test_workflow_merge_gate.py`

  Commit: `feat(agent): classify missing plugin sessions`

- [ ] From the resulting clean commit, run both live customization gates.

  Run: `../../.venv/bin/python scripts/check_upstream_customizations.py --strict --base-ref HEAD`

  Run: `scripts/test_workflow_merge_gate.sh --phase base`

  Expected: PASS with the new generic seam isolated at this commit boundary.
  If a gate finds a defect, fix it in a new atomic commit, rerun the focused
  and live commands, and hand off only a clean tree.

## Task 13: Recover missing cross-run sessions with a durable CAS obligation

**Files:**

- Create: `tests/plugins/workflow/test_persistent_session_recovery.py`
- Modify: `plugins/workflow/sessions.py`
- Modify: `plugins/workflow/executors/base.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/evidence.py`
- Modify: `plugins/workflow/language_schema.py`
- Test: `tests/plugins/workflow/test_persisted_sessions.py`
- Test: `tests/plugins/workflow/test_crash_recovery.py`
- Test: `tests/plugins/workflow/test_coordinator_multiprocess.py`
- Test: `tests/plugins/workflow/test_phase3_code_catalog.py`

- [ ] Add failing source-sensitive recovery tests.

  Same-run shared predecessor missing must fail `context_missing_session` and never fresh. Confirmed-missing cross-run registry state may replace a zero-provider shared request once with a fresh request inside the same workflow attempt. DB unavailable/corrupt/ambiguous/denied fails `persistent_session_recovery_unavailable`. Fingerprint mismatch keeps existing warning/fresh behavior and is not classified as missing recovery. Legacy/v1/v2 stay exact.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py`

  Expected: FAIL because the AI executor cannot distinguish source or recover only confirmed absence.

- [ ] Add failing pre-provider reserve, selection, and crash-order tests.

  Before allocating the fresh worker, require journal reserve for the bounded
  selection frame, possible winning registry obligation, and bounded outcome
  frames. Append `persistent_session_missing_fresh_start` plus
  `fresh_start_selected` through an active-claim-fenced store callback before
  provider launch, including `provider_attempts_before_recovery: 0` and only
  digests/bounded identifiers.

  Inject crashes immediately before selection, after selection but before
  provider launch, and just after provider launch. Prove respectively: no
  selection evidence or provider/CAS; durable selection followed by ordinary
  zero-effect interrupted-claim recovery with no provider/CAS; and the existing
  unknown-outcome rules with no silent provider replay. A reserve refusal must
  happen before allocation. No private key, session ID, fingerprint, history,
  path, or provider content may enter journal-derived public projection.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_crash_recovery.py`

  Expected: FAIL because selection evidence and its complete reserve are not a
  durable pre-provider boundary.

- [ ] Implement the pre-provider reserve and active-claim selection callback
  before fresh worker allocation.

- [ ] Return a private `SessionRegistryUpdateCandidate` from successful fresh execution instead of mutating `NodeSessionRegistry` inside the executor.

  Include exact key, expected generation, non-empty new session ID, fingerprint, and winning attempt identity. Keep it out of public metadata/evidence.

- [ ] Add failing atomic-completion tests.

  `RunStore.complete_node()` must validate the claim/winner and atomically journal successful node completion plus one bounded `pending_session_registry_update` obligation. A run with a pending obligation cannot become terminal-complete. A failed fresh execution creates no obligation.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_crash_recovery.py`

  Expected: FAIL because registry CAS currently occurs before store completion.

- [ ] Add the private obligation to the bounded run journal/projection and terminal journal reserve.

  Persist the exact protected candidate only in private store state. Public projections receive digests and bounded identifiers. Extend recovery validation/rebuild rules so damaged or uncorroborated obligations fail closed.

- [ ] Add `compare_and_set_or_observe()` and failing idempotence tests.

  Expected generation writes generation + 1; exact expectation + 1 identity reports already applied; newer/different state is retained. Test real separate registry processes and stale winners.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_coordinator_multiprocess.py`

  Expected: FAIL because current boolean CAS cannot distinguish an already-applied recovery.

- [ ] Implement coordinator reconciliation and exact operational backoff.

  Apply only after durable node completion. Journal outcome and clear obligation when replaced/already-applied/newer-retained. On operational failure wait 1, 2, 4, 8, then 16 seconds. After five failed applications leave the run durably `recovery_pending` with `persistent_session_registry_update_pending` until ordinary resume/operator retry. Never rerun the provider or discard the obligation.

- [ ] Add crash/cancellation RED tests on both sides of every write.

  Crash before completion: no CAS. Crash after completion/before CAS: recover obligation once. Crash after CAS/before outcome journal: observe exact already-applied identity. Cancellation after winning completion resolves the internal obligation before final cancellation publication. Test finalization blocking, no hot loop, no provider replay, scope/profile/provider separation, and bounded history.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py`

  Expected: FAIL until store and registry ordering is recoverably idempotent.

- [ ] Complete sanitized recovery outcome evidence.

  Project the already-durable selection plus outcomes
  `stale_entry_replaced`, `stale_entry_replaced_already_applied`,
  `newer_entry_retained`, `registry_update_deferred`, or
  `fresh_execution_failed`. Include attempt/generation, hashes, source,
  bounded provider/runtime profile, and zero-provider pre-recovery count.
  Exclude raw session IDs, keys, fingerprints, histories, storage paths, and
  provider responses.

- [ ] Register all missing-session selection/failure/pending codes and events
  in the Phase 3 durable-code catalog and exercise each through a real
  preflight, recovery, store, registry, or evidence path.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_evidence_api.py`

  Commit: `feat(workflow): recover missing persistent sessions`

## Task 14: Project bounded Phase 3 truth through API and Desktop

**Files:**

- Modify: `plugins/workflow/evidence.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/app/workflows/run-inspector.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`
- Modify: `apps/desktop/src/app/workflows/review-run-dialog.test.tsx`
- Modify: `apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx`
- Test: `tests/plugins/workflow/test_catalog_api.py`
- Test: `tests/plugins/workflow/test_workflow_detail_api.py`
- Test: `tests/plugins/workflow/test_evidence_api.py`
- Test: `tests/plugins/workflow/test_workflow_language_desktop_e2e.py`
- Test: `tests/plugins/workflow/test_phase3_code_catalog.py`

- [ ] Add failing backend projection tests.

  Accept normalizer v3. Project bounded compatibility/migration summaries and unambiguous requested/effective retry/error fields. Recovery evidence uses `recovery_kind: persistent_session`. Assert exact upper bounds and absence of command text, output values, spill details/paths, raw provider data, pending obligation fields, session IDs, keys, fingerprints, and histories.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_evidence_api.py`

  Expected: FAIL because Pydantic currently caps normalizer version at 2 and recovery projection lacks Phase 3 shapes.

- [ ] Extend existing authenticated models/routes only.

  Keep artifact lookup publication-ID based. Add no endpoint and no filesystem parameter. Preserve operator-scope/profile authorization and all sanitizers.

  Prove API, doctor, and editor code projections derive from the versioned
  Phase 3 durable-code authority, with no hand-maintained duplicate list.

- [ ] Add failing Desktop compatibility/rendering tests.

  A new renderer against a v3 backend displays backend-authored language/findings and generic persistent-session recovery evidence. In `index.test.tsx`, render `RunInspector`, select the recovery tab, assert `kind=recovery` is requested, and render `recovery_kind: persistent_session` through the generic bounded evidence view. A new renderer against an older backend treats missing additive fields as unavailable and preserves usable empty/error states. An older-compatible shape ignores v3 additions. No renderer parser, retry calculator, session probe, or filesystem access is added.

  Run: `cd apps/desktop && npm test -- src/app/workflows/index.test.tsx src/app/workflows/review-run-dialog.test.tsx src/app/workflows/view-workflow-dialog.test.tsx`

  Expected: FAIL on v3 and persistent-session evidence fixtures.

- [ ] Update TypeScript interfaces and the existing Run Inspector projection without creating a second workflow authority.

- [ ] Run backend/Desktop verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_workflow_language_desktop_e2e.py tests/plugins/workflow/test_desktop_api.py`

  Run: `cd apps/desktop && npm test -- src/app/workflows/index.test.tsx src/app/workflows/review-run-dialog.test.tsx src/app/workflows/view-workflow-dialog.test.tsx`

  Run: `cd apps/desktop && npm run typecheck`

  Commit: `feat(workflow): expose bounded phase 3 evidence`

## Task 15: Update generated contracts, operator docs, and installed flows

**Files:**

- Modify: `plugins/workflow/language_schema.py`
- Modify: `website/docs/user-guide/features/workflow-yaml-reference.md`
- Modify: `skills/software-development/workflow-builder/references/portable-schema.md`
- Modify: `skills/software-development/workflow-builder/references/authoring-checklist.md` only if generated guidance requires it
- Create: `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/progress.md`
- Test: `tests/plugins/workflow/test_language_schema.py`
- Test: `tests/plugins/workflow/test_phase3_code_catalog.py`
- Test: `tests/plugins/workflow/test_portable_compatibility_e2e.py`
- Test: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Test: `tests/agent/test_workflow_builder_skill.py`
- Test: `tests/skills/test_workflow_operator_behavior.py`

- [ ] Add failing generated-contract and documentation assertions.

  Generated field metadata must describe millisecond units, 120,000 ms omission, AI/deterministic retry defaults, retries-after-initial, direct dependencies, strict types/errors, safe Bash contexts/bounds, and missing-session recovery. Public stable-code documentation must derive from the registered catalog and the completeness test must pair every registered code with a real behavior path; do not maintain a second prose-only list. It must keep MCP/skills as options and loops/includes as Phase 4.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_language_schema.py tests/agent/test_workflow_builder_skill.py tests/skills/test_workflow_operator_behavior.py`

  Expected: FAIL because generated/editor/author guidance still reflects Phase 2 blockers.

- [ ] Update central descriptors first, verify the dynamic schema/editor contract, and then update prose docs from the same authority.

  There is no checked-in generated workflow JSON Schema. Run `./hermes workflow schema --profile archon-2026-07 --json` and its legacy counterpart, then run the language-schema tests that compare the dynamic contract. Do not invent or hand-maintain a second generated artifact. Do not document a raw environment variable for behavioral configuration.

- [ ] Add representative official Archon fixture tests.

  Cover timeout/retry authoring, typed conditions/references, 32 KiB Bash behavior, and missing cross-run session recovery. Adapt only where the design documents Hermes' stronger safe contract. Preserve provenance comments/links without copying large upstream text.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_portable_compatibility_e2e.py tests/plugins/workflow/test_installed_distribution_e2e.py tests/plugins/workflow/test_showcase_distribution_e2e.py`

  Expected: FAIL until the v3 contract is discoverable and works from an installed temporary `HERMES_HOME`.

- [ ] Update the retained progress document with task commits, RED/GREEN commands, review reports, and deviations. Do not claim final completion.

- [ ] Run focused verification and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_portable_compatibility_e2e.py tests/plugins/workflow/test_installed_distribution_e2e.py tests/plugins/workflow/test_showcase_distribution_e2e.py tests/agent/test_workflow_builder_skill.py tests/skills/test_workflow_operator_behavior.py`

  Commit: `docs(workflow): publish phase 3 language contract`

## Task 16: Complete final regression, review, and customization gates

**Files:**

- Modify only when a verified gate proves an intentional ledger correction is required: `docs/upstream-customizations/workflow-orchestration.yaml`
- Modify only when a verified gate proves selection/harness changes are required: `scripts/test_workflow_merge_gate.sh`
- Modify only when a verified rehearsal defect requires it: `scripts/test_workflow_upstream_merge.sh`
- Create: `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-16-report.md`
- Create: independent final specification and quality review reports in the same SDD directory
- Test: `tests/scripts/test_check_upstream_customizations.py`
- Test: `tests/scripts/test_workflow_merge_gate.py`
- Test: `tests/scripts/test_workflow_upstream_merge.py`
- Test: `tests/test_desktop_workflow_test_gate.py`

- [ ] First bring every focused Phase 3 test green with retries disabled for evidence.

  Run: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_phase3_conditions.py tests/plugins/workflow/test_phase3_resolution_waits.py tests/plugins/workflow/test_phase3_bash_substitution.py tests/plugins/workflow/test_persistent_session_recovery.py tests/agent/test_plugin_agent.py`

  Expected: PASS with zero failed files and no flaky retry.

- [ ] Run the canonical Python suite once from a clean worktree with retries disabled.

  Run: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh`

  Expected: PASS with zero failures and no flaky retry. Record exact
  file/test/pass/skip counts and raw-log SHA-256 in the Task 16 report.

- [ ] Run scoped Desktop gates without rewriting unrelated files.

  Run: `cd apps/desktop && npm run typecheck`

  Run: `cd apps/desktop && npm test -- src/app/workflows/index.test.tsx src/app/workflows/review-run-dialog.test.tsx src/app/workflows/view-workflow-dialog.test.tsx`

  Run: `cd apps/desktop && npx eslint src/types/hermes.ts src/app/workflows/run-inspector.tsx src/app/workflows/index.test.tsx src/app/workflows/review-run-dialog.test.tsx src/app/workflows/view-workflow-dialog.test.tsx`

  Run: `cd apps/desktop && npx prettier --check src/types/hermes.ts src/app/workflows/run-inspector.tsx src/app/workflows/index.test.tsx src/app/workflows/review-run-dialog.test.tsx src/app/workflows/view-workflow-dialog.test.tsx`

  These commands do not write files. Record the established 20 unrelated
  full-Desktop Prettier failures as baseline; do not edit those files.

- [ ] Run schema, installed-distribution, merge-gate, and customization checks.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_installed_distribution_e2e.py tests/scripts/test_check_upstream_customizations.py tests/scripts/test_workflow_merge_gate.py tests/scripts/test_workflow_upstream_merge.py tests/test_desktop_workflow_test_gate.py`

  Run: `../../.venv/bin/python scripts/check_upstream_customizations.py --strict --base-ref HEAD`

  Run: `scripts/test_workflow_merge_gate.sh --phase base`

  Expected: PASS. The live strict checker and base merge gate validate the
  candidate HEAD; harness unit tests are additional evidence, not substitutes.
  Do not edit the ledger merely to silence a failure.

- [ ] Resolve exact refs and rehearse upstream/OTTO/LOOP24 integration.

  Run:

  ```bash
  git fetch origin --prune
  scripts/test_workflow_upstream_merge.sh \
    --upstream-ref origin/main \
    --base-ref HEAD \
    --brand-ref otto \
    --brand-ref loop24
  ```

  Record the resolved 40-hex identities before invocation, plus executable
  invariant/reference counts, failures, flakes, and temporary-worktree cleanup.
  Reading `origin/main` as the synchronization input does not authorize
  switching to or mutating literal `main`; do not push, publish, or propagate
  brand refs.

- [ ] If any gate above requires a production, test, harness, or ledger fix,
  apply the bounded review/fix protocol, commit it atomically, and rerun every
  final gate whose evidence that change invalidated. Do not start final reviews
  until the complete candidate is green and the worktree is clean.

- [ ] Record the exact green candidate HEAD and tree, then run one independent
  final specification review against those identities.

  Review goal-backward against the umbrella design, approved Phase 3 design,
  and every plan task. Require exact legacy behavior, no Phase 4/5, all stable
  failure/evidence contracts, all admission boundaries, and every requested
  workstream.

- [ ] Run one separate independent quality review against the same exact HEAD
  and tree.

  Inspect correctness, concurrency, crash consistency, descriptor safety,
  shell injection, bounds, privacy, API authorization, prompt caching,
  alternation, maintainability, and test quality.

- [ ] Converge reviews and gates on one production tree.

  If either review causes any production/test/harness/ledger fix, commit it
  atomically, rerun the affected focused checks plus every final gate whose
  evidence changed, record the new exact HEAD/tree, and obtain clean
  specification and quality rereviews of that new identity. Up to three
  bounded rounds are already authorized. Stop rather than waive a remaining
  finding or failing invariant.

- [ ] Perform final controller verification.

  Verify the feature branch, final reviewed production HEAD/tree, report-only
  HEAD/tree if different, clean worktree, atomic commit series, 0-finding review
  closure, test evidence hashes, no production path differing from the last
  clean-rereview tree, and preservation of the shared base checkout's
  user-owned changes.

- [ ] Commit only retained report artifacts after convergence.

  Do not defer ledger changes to this report commit; Tasks 10 and 12 own their
  generic-seam ledger entries at the same implementation boundaries. Record
  both the final reviewed production identity and this report-only identity.

  Commit: `docs(workflow): record phase 3 verification`

## Completion handoff

Phase 3 is implementation-complete only when all task checkboxes are satisfied, every independent review is closed, every required suite/gate passes from a clean worktree, and the final report records exact evidence. Completion does not authorize integration, push, publication, branch/worktree deletion, brand propagation, or literal-`main` changes. Use superpowers:finishing-a-development-branch only after the user separately authorizes the next integration action.
