# Task 12 Independent Specification Review

## Verdict

**FAIL — changes required.** The committed tree is authenticated as requested (`HEAD` `2d4529924581a5b46178f1bb7dbf2becef12052b`, tree `6154894cffcf247b2dd05477806d7e2aa7282ae0`) and the four-file scope matches the review package, but the worker-side race handling does not cover the complete interval between parent preflight and child session load. In one interleaving it can recreate the deleted persistent session and run with empty history, which is fresh-session behavior explicitly reserved for Task 13.

## Findings

### Critical — The child session load has a TOCTOU window that silently selects fresh behavior

**Evidence:** `agent/plugin_agent_worker.py:1513-1520`, `agent/plugin_agent_worker.py:1553-1585`, and `agent/plugin_agent_worker.py:1689-1693`; corroborating unchanged behavior at `hermes_state.py:4811-4818`, `hermes_state.py:6462-6510`, `agent/agent_init.py:1511-1523`, and `run_agent.py:1917-1920`.

The worker first calls `get_session()` and then separately calls `get_messages_as_conversation()`. Those are distinct SQLite statements under distinct lock acquisitions. A concurrent `delete_session()` can commit after `get_session()` returns a row but before the messages query. The messages query then returns `[]`, not an absence signal. The worker continues, constructs `AIAgent` with that empty history and the deleted ID, and calls `run_conversation()`. `AIAgent` marks the supplied DB row as not yet created, and its normal early persistence calls `_ensure_db_session()`, recreating the deleted row before continuing the model turn.

This violates three binding requirements at once: the worker race does not emit `persistent_session_missing`; provider/model attempts are no longer guaranteed to be exactly zero; and generic agent core has implicitly chosen a fresh persistent session despite Task 13 recovery being out of scope. It can also discard the intended prior context while presenting the run as a normal shared-context execution.

**Required correction:** make the existence/history load race-safe and fail with `PluginAgentSessionMissingError` whenever absence is observed before history has been loaded. Add a deterministic regression that pauses between the exact existence read and history read, deletes the session from another connection, and proves that no `AIAgent` is constructed, no row is recreated, and the only accepted result is the sanitized zero-attempt missing frame.

### Important — Missing-session classification is ordered after branches that can mask the race

**Evidence:** `agent/plugin_agent_worker.py:1332-1365` and `agent/plugin_agent_worker.py:1477-1519`.

Before checking the shared session, the child may resolve runtime configuration, start/discover request MCP servers, and return `structured_output_unsupported` or `structured_output_capability_drift`. Therefore a session deleted immediately after parent preflight can produce an MCP/runtime operational failure or a structured-output negotiation result instead of the contractually exclusive sanitized `persistent_session_missing` result. The current race test uses no MCP servers and no structured-output request, so it does not exercise these masking paths.

**Required correction:** perform the exact shared-session load before child branches that can return another result or start request-scoped services, while retaining request validation and established cleanup. Add race cases with structured-output drift/unsupported evidence and an MCP-enabled request to prove absence wins and still reports exact zero model/provider attempts with no raw content.

### Minor — The required operational database scenarios are simulated at the wrong seam

**Evidence:** `tests/agent/test_plugin_agent.py:930-969`.

The new operational-failure test creates a healthy real `SessionDB`, then replaces that instance's `get_session()` with a lambda that throws a preconstructed exception. It does not cover `SessionDB()` open failure at all, and it does not exercise a real corrupt/ambiguous database or an access-denied/open path. The production code presently propagates constructor and read exceptions because it does not catch them, but the brief explicitly requires distinguishing database **open/read**, corrupt/ambiguous, and denied failures with a real temporary profile-local database. The committed tests do not supply that evidence.

**Required correction:** add an explicit constructor/open-failure case and realistic temporary-database corrupt/read/denied cases where portable; keep assertions that the exact operational exception propagates and the worker never starts.

## Requirement Audit

- **Generic seam only / no workflow import / no fresh policy in intended path:** satisfied structurally by the diff, but defeated by the Critical race above.
- **Parent exact profile-local preflight:** satisfied. `SessionDB.get_session(request.session_id) is None` is the only new parent classification condition; constructor/read/close errors are not converted.
- **Existing empty/history-light sessions:** satisfied by implementation and focused tests.
- **Typed outcome sanitization and exact zero evidence:** satisfied for the directly exercised preflight and worker-exception paths. The exception has only generic `args`, an empty instance dictionary, and class-level zero counters; the wire result is empty/sanitized.
- **Strict parent correlation:** satisfied by code. Result decoding rejects unknown top-level fields; missing-result correlation requires the exact audit field set, exact plugin identity, integer (not boolean) zero counters, empty payload/usage, no interaction, and no structured evidence, and rejects fresh-context claims.
- **Preservation of non-workflow callers and cleanup:** the typed exception remains a `ValueError`, and the corrective commit retains unwinding through the existing worker `finally`. No prompt/toolset/history/schema changes are present outside the missing-session additions.
- **v1/v2/legacy and narrow waist:** no model tool, config, workflow import, or recovery policy was added.
- **Customization ledger:** satisfied. The new entry is immediately adjacent to the historical `plugin-agent-runner`; the historical subject remains `feat(workflow): enforce per-node agent resources`; `last_verified_upstream` is copied unchanged; `upstream_candidate: true`, removal/merge guidance, and the expected `feat(agent): classify missing plugin sessions` subject are present. Commit `03ea54b1b` has that exact subject.
- **Task 13 out of scope:** no explicit Task 13 code was added, but the Critical interleaving reaches existing automatic session creation and thus violates the behavioral boundary.
- **Exact scope:** authenticated diff contains only the four declared modified files and passes `git diff --check`.

## Gate-Order and Verification Assessment

The pre-commit ledger failure described in the implementer report is not itself a defect: the ledger checker is committed-HEAD based, the implementation and ledger amendment coexist in commit `03ea54b1b`, and current committed HEAD/tree contain both. I did not pre-judge the deviation.

The claimed focused run, strict customization gate, and 4,086-test base gate are **not independently verifiable from the authenticated diff or repository artifacts**: no authenticated command logs were supplied, and per instruction I did not rerun broad suites. The exact HEAD/tree identities, commit subjects, changed-file scope, and `git diff --check` result were independently verified. Even if every claimed gate result is accurate, the uncovered interleavings above remain specification failures.

## Severity Summary

| Severity | Count |
|---|---:|
| Critical | 1 |
| Important | 1 |
| Minor | 1 |
