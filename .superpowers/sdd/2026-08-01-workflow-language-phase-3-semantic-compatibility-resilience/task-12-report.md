# Task 12 Report — Classify missing isolated sessions

## Status

Complete. Task 12 adds only the generic typed missing-session classification
seam. It does not choose workflow recovery, start a fresh session, import the
workflow package into agent core, or alter prompts, toolsets, or history.

## Commits

- `03ea54b1b` — `feat(agent): classify missing plugin sessions`
- `2d4529924` — `fix(agent): preserve plugin session cleanup`

The second atomic commit fixes the existing direct-worker cleanup contract
found by the first live base gate, as required by the task brief's gate-defect
procedure.

## Implementation

### Parent preflight

- Added exported `PluginAgentSessionMissingError`, a typed `ValueError`
  carrying class-level `provider_attempts = 0`, `model_calls = 0`, and
  `failure_kind = "persistent_session_missing"`.
- `PluginAgentRunner.run()` raises it only when the profile-local
  `SessionDB.get_session(exact_id)` result is exactly `None`.
- The exception message is generic and the exception carries no session ID,
  history, provider response, or instance payload.
- Database construction/read failures continue to propagate unchanged.
  Existing empty and history-light sessions still reach the worker.

### Worker race classification

- A child that observes deletion after the parent preflight raises the same
  typed exception from `_run()`. Keeping it a `ValueError` preserves existing
  direct-worker cleanup/unwinding behavior.
- Only the worker protocol boundary maps that exception to a sanitized failed
  result: empty response/session/provider/model, empty usage, no structured
  payload, and an exact audit containing plugin ID, failure kind, and zero
  provider/model attempts.
- Other worker exceptions retain the existing generic sanitized failure path.

### Parent correlation

- Added strict correlation for `persistent_session_missing` results.
- The parent accepts the frame only for a shared-context request and only when
  status, empty payload fields, plugin identity, exact audit field set, and
  exact integer zero counts all match.
- Unknown audit fields, nonzero or boolean counts, wrong plugin identity,
  fresh-context claims, raw session/exception/provider content, nonempty usage,
  pending interaction, or structured-output evidence are rejected.

### Customization ledger

- Added `plugin-agent-persistent-session-missing-classification` immediately
  after historical `plugin-agent-runner`.
- Preserved the historical entry and subject unchanged.
- Recorded `upstream_candidate: true`, exact subject
  `feat(agent): classify missing plugin sessions`, merge guidance, removal
  condition, owned symbols/invariants/tests, and copied
  `last_verified_upstream: aaf5691261f12601db845386d650dce1cdfa30f9`.

## Files changed

- `agent/plugin_agent.py`
- `agent/plugin_agent_worker.py`
- `tests/agent/test_plugin_agent.py`
- `docs/upstream-customizations/workflow-orchestration.yaml`

No change was required in `tests/plugins/workflow/test_ai_executor.py` or
`tests/scripts/test_workflow_merge_gate.py`. No file outside Task 12 ownership
was modified.

## TDD evidence

All authoritative Python runs used `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`. No direct `pytest` command was run.

### Parent-preflight RED

Command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py
```

Result before parent production edits: **RED**, 98 passed / 1 failed. The sole
expected failure was
`test_missing_shared_session_is_a_typed_zero_provider_preflight`: current code
raised generic `ValueError('session_id does not identify an existing Hermes session')`
instead of the absent `PluginAgentSessionMissingError`. The worker-start guard
remained false.

### Parent-preflight GREEN

Same command after the minimal parent seam: **GREEN**, 99 passed / 0 failed.

### Worker-race/correlation RED

Command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_ai_executor.py
```

Result before worker/correlation production edits: **RED**, 207 passed / 10
failed. Expected failures were the real delete-after-preflight worker race
(generic `ValueError`) plus nine accepted forged/uncorrelated frames: unknown
audit field, spoofed provider/model counts, wrong plugin, raw session,
raw exception, raw provider, nonempty usage, and a fresh-context missing claim.

### Worker-race/correlation GREEN

Same command after the bounded worker result and parent correlation:
**GREEN**, 217 passed / 0 failed.

### Live-gate compatibility RED and corrective GREEN

The first committed live base gate found one genuine existing-caller failure:

```bash
scripts/test_workflow_merge_gate.sh --phase base
```

`tests/plugins/workflow/test_node_mcp.py` failed only at
`test_request_mcp_cleanup_covers_every_post_start_failure[shared_session-ValueError]`
because the initial worker implementation returned early instead of unwinding
the established cleanup path with `ValueError`.

Before corrective production edits, the tightened Task 12 race test and the
existing MCP cleanup file were run with:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_node_mcp.py
```

Result: **RED**, 277 passed / 2 failed. The failures were the new expectation
that `_run()` raise `PluginAgentSessionMissingError` and the existing MCP
cleanup expectation that it raise `ValueError`.

After making the typed exception a backward-compatible `ValueError` and moving
sanitized conversion exclusively to the worker boundary, the same command was
**GREEN**, 279 passed / 0 failed.

## Focused verification

Exact focused command:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_ai_executor.py tests/scripts/test_workflow_merge_gate.py
```

### Pre-commit ledger-boundary deviation

With the final new ledger entry present but uncommitted, this command produced
265 passed / 1 failed. The only failure was
`test_live_customization_ledger_has_one_rehearsable_upstream_baseline`: the
checker deliberately validates owned symbols from committed source revision
`HEAD`, so uncommitted `PluginAgentSessionMissingError` could not exist at that
revision. The checker was not weakened or modified.

With only the new ledger entry temporarily withheld, the exact command proved
all Task 12 behavior and the historical ledger **GREEN**, 266 passed / 0
failed. The entry was restored and committed atomically with the implementation.
From committed `03ea54b1b`, the exact command was **GREEN**, 266 passed / 0
failed. Before and after corrective commit `2d4529924`, the exact command was
again **GREEN**, 266 passed / 0 failed. This ordering was explicitly approved
after the committed-HEAD boundary was demonstrated.

## Live customization gates

Final strict gate from clean committed `HEAD`:

```bash
../../.venv/bin/python scripts/check_upstream_customizations.py --strict --base-ref HEAD
```

Result: **PASS**, exit 0 with no diagnostics.

Final canonical base gate:

```bash
scripts/test_workflow_merge_gate.sh --phase base
```

Result: **PASS**:

- 4,086 Python tests passed across 57 files
- 1 installed-distribution test passed
- 155 Desktop tests passed across 11 files
- `TESTED_BASE_SHA=2d4529924581a5b46178f1bb7dbf2becef12052b`

The previously failing MCP cleanup file passed 170/170 in this final gate.

## Self-review

Reviewed the complete range
`65dcb957286314787b1c0143f6a4c54eb86f3f63..2d4529924581a5b46178f1bb7dbf2becef12052b`.

- Scope is exactly four owned files; no workflow executor, Task 13, prompt,
  history, toolset, model-tool, config, or unrelated file changed.
- Parent classification is limited to exact `None`; operational failures are
  not collapsed into absence.
- No session ID, history, provider response, or raw missing-session exception
  crosses the typed worker frame.
- Parent correlation rejects unknown fields and spoofable evidence rather than
  trusting worker counts.
- Existing direct-worker cleanup semantics are preserved by the typed
  `ValueError` and confirmed by the full base gate.
- Historical ledger subject and shared upstream identity remain unchanged.
- `git diff --check` for the full range is clean.
- Branch remained
  `feat/workflow-language-phase-3-semantic-compatibility-resilience`; no push,
  publish, branch/worktree deletion, literal-main mutation, or shared-base
  checkout mutation occurred.

## Concerns

None remaining. Task 13 recovery/fresh-session policy is intentionally not
implemented here.

## Fix Round 1 — Semantic compatibility and resilience

Fix commit: `c026d1ef5` — `fix(agent): make plugin session loads atomic`.

Independent spec and quality reviews found that the initial worker still used
`get_session()` followed by `get_messages_as_conversation()`, allowing a delete
between those reads to collapse missing state into empty history. They also
found that MCP/runtime/structured-output failures could mask the later missing
check, and that any later public `PluginAgentSessionMissingError` could forge
the privileged zero-attempt result through its public `failure_kind`.

### Authorized scope extensions

The correct TOCTOU fix required a generic SessionDB seam not present in the
original Task 12 ownership. Work stopped before editing, and the parent then
authorized `hermes_state.py` plus `tests/test_hermes_state.py`. The minimal
public method is `SessionDB.get_existing_session_conversation()`:

- one `sessions LEFT JOIN messages` SQLite statement supplies one coherent
  snapshot;
- `None` means the exact session is absent;
- `[]` means it exists without active messages;
- nonempty history is decoded by the existing `_rows_to_conversation()` path,
  preserving `get_messages_as_conversation()` output and signature unchanged.

The parent separately authorized a test-only update to
`tests/plugins/workflow/test_node_mcp.py`. The old fixture encoded the obsolete
post-MCP missing check and exposed only the unsafe split-read methods. Missing
state is now its own pre-MCP case proving the sanitized result, SessionDB close,
and no MCP launch; every remaining post-start cleanup matrix case and assertion
is retained.

The customization entry now owns the exact SessionDB symbol, atomic snapshot
invariant, and both added focused test files. Its historical expected subject
and upstream identity remain unchanged.

### TDD evidence

Every Python test command used `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`; no direct pytest invocation was used.

Generic SessionDB RED:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/test_hermes_state.py -k get_existing_session_conversation
```

Result before production: **RED**, 0 passed / 2 failed, both because the public
method did not exist. The tests cover exact absent/empty/history distinctions
and a deterministic writer deleting at the legacy existence/history boundary.
After the one-query implementation: **GREEN**, 2 passed / 0 failed.

Worker ordering/origin RED:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py -k 'missing_shared_session_precedes or late_public_session_missing'
```

Result before worker production edits: **RED**, 0 passed / 4 failed. Both
structured strategies reached runtime resolution, enabled MCP reached request
configuration, and the late public exception forged
`persistent_session_missing`. Moving the atomic load to the top of `_run()`
made the three ordering cases green. Removing the explicit public-exception
branch exposed its class-level `failure_kind` as a second RED; reserving that
failure kind in the generic converter closed the forgery path. The complete
agent test file later passed 120/120.

Existing MCP compatibility RED:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_node_mcp.py -k 'shared_session or cleanup_covers_every_post_start_failure'
```

Result after the intentional ordering change but before the authorized fixture
update: **RED**, 6 passed / 1 failed because `FakeSessionDB` lacked the new
atomic method. The corrected focused contract then passed 7/7: missing state
is classified before its request MCP starts and closes the DB, while all six
remaining post-start cleanup cases remain green.

Complete affected-file verification:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/test_hermes_state.py tests/agent/test_plugin_agent.py tests/plugins/workflow/test_node_mcp.py
```

Result: **GREEN**, 705 passed / 0 failed:

- `tests/test_hermes_state.py`: 415 passed;
- `tests/agent/test_plugin_agent.py`: 120 passed;
- `tests/plugins/workflow/test_node_mcp.py`: 170 passed.

### Strengthened behavior

- Missing shared state is atomically established before request MCP
  interpolation/finalization/discovery, provider/runtime resolution,
  structured-output drift/unsupported negotiation, or AIAgent construction.
- Only the exact `None` returned by the atomic SessionDB loader constructs the
  privileged sanitized missing frame. Later plugin/provider/tool exceptions,
  including the public typed exception, remain ordinary failures.
- The real race test now uses parent preflight, deletion at the exchange
  boundary, the real worker subprocess and JSON protocol, strict parent
  correlation, a captured reaped process tree, SessionDB close coverage, an
  MCP launch sentinel, and a final real DB read proving no row recreation.
- Parent forgery coverage now includes boolean `True` counts, missing audit
  keys, nonempty model, pending interaction, and structured-output evidence in
  addition to the original malformed cases.
- Parent preflight operational coverage now uses real profile-local SessionDB
  construction/open against invalid database bytes, an unusable DB path, and
  actual POSIX permission denial. The permission test skips only when the
  platform/user can bypass those requested filesystem permissions. In all
  reproduced cases the operational exception remains distinct and no worker
  starts.

Task 13 recovery/fresh-session policy, workflow imports, prompt/tool/history
mutation, and new model-tool surface remain deliberately out of scope.

### Final committed-HEAD verification

From clean commit `c026d1ef569720f7e47fbc9792b5d1b9378c9ec4`, the exact
expanded Task 12 focused gate passed **862/862**:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_ai_executor.py tests/scripts/test_workflow_merge_gate.py tests/test_hermes_state.py tests/plugins/workflow/test_node_mcp.py
```

The strict customization gate passed with exit 0 and no diagnostics:

```bash
../../.venv/bin/python scripts/check_upstream_customizations.py --strict --base-ref HEAD
```

The canonical base gate also passed from clean committed HEAD:

```bash
scripts/test_workflow_merge_gate.sh --phase base
```

- 4,097 Python tests passed across 57 files;
- 1 installed-distribution test passed;
- 155 Desktop tests passed across 11 files;
- `TESTED_BASE_SHA=c026d1ef569720f7e47fbc9792b5d1b9378c9ec4`.

## Fix Round 2 — Real post-open read-failure evidence

Test commit: `b9c57e31c` —
`test(agent): cover plugin session read failures`.

The independent Fix Round 1 specification rereview passed the atomicity,
ordering, origin, compatibility, and customization work, but identified one
remaining test-evidence gap: all real operational database failures occurred
during SessionDB construction/open or access, rather than in the actual
`get_session()` query after a successful open.

One focused regression now:

- constructs a real profile-local `SessionDB` at the configured `state.db`;
- creates and successfully reads a real session first;
- drops the `sessions` table through SQLite on the live connection;
- directly proves the unchanged real `SessionDB.get_session()` method raises
  `sqlite3.OperationalError("no such table: sessions")`;
- supplies that same real instance/path to the parent preflight without
  replacing `get_session()` or injecting a preconstructed exception;
- proves the actual SQLite exception remains distinct from
  `PluginAgentSessionMissingError`, `_exchange_worker` never starts, and the
  connection is closed deterministically by the parent/finally cleanup.

This test passed immediately against the existing production implementation:

```bash
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/agent/test_plugin_agent.py -k real_post_open_read_failure
```

Result: **GREEN**, 1 passed / 0 failed. There is intentionally no production
RED/GREEN cycle in Fix Round 2: the rereview found a specification-evidence
gap, not a production defect. The narrowly controlled real schema fault and
the direct preflight guard demonstrate the test would fail if operational read
errors were collapsed into missing-session classification or allowed worker
startup. No production file changed, and Task 13 remains out of scope.

### Fix Round 2 final verification

From clean commit `b9c57e31cd42bc77685a31ce0f7bd9808deb6d1e`:

- full `tests/agent/test_plugin_agent.py`: **121 passed / 0 failed**;
- expanded Task 12 focused gate: **863 passed / 0 failed** (the prior 862
  plus this new regression);
- strict customization gate: **PASS**, exit 0 with no diagnostics;
- canonical base gate: **4,098 Python tests passed**, **1 installed-
  distribution test passed**, and **155 Desktop tests passed**;
- `TESTED_BASE_SHA=b9c57e31cd42bc77685a31ce0f7bd9808deb6d1e`.

## Independent closure

Fresh independent specification and quality reviewers examined the complete
authenticated range
`65dcb957286314787b1c0143f6a4c54eb86f3f63..b9c57e31cd42bc77685a31ce0f7bd9808deb6d1e`
at tree `fda7669405741e23c19a68a06bb939f794d857b9`.

- Specification closure: **PASS**, 0 Critical, 0 Important, 0 Minor.
- Quality and security closure: **PASS**, 0 Critical, 0 Important, 0 Minor.
- All findings from the initial reviews and Fix Round 1 rereviews were closed.

## Controller closure verification

The controller independently reran the exact expanded focused gate from the
reviewed implementation with retries disabled: **863 passed / 0 failed**.
Ruff, `git diff --check` for the full Task 12 range, and the strict
customization gate all passed. The canonical base merge gate independently
passed **4,098 Python tests**, **1 installed-distribution test**, and **155
Desktop tests**, with
`TESTED_BASE_SHA=b9c57e31cd42bc77685a31ce0f7bd9808deb6d1e`.

No Task 13 implementation was begun during Task 12 closure.
