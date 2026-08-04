# Task 12 Fix Round 1 Independent Specification Rereview

## Verdict

**FAIL — one prior minor specification-evidence gap remains.** The fix package addresses the Critical TOCTOU defect and the Important ordering defect without introducing new breakage. It also replaces the old mocked operational-failure matrix with real profile-local corruption/open/path and permission failures, but it still does not exercise a real operational conversation/session **read** failure after a database has opened successfully, which the task brief and original finding explicitly required.

## Authenticated Scope

- Fix base: `2d4529924581a5b46178f1bb7dbf2becef12052b`
- HEAD: `c026d1ef569720f7e47fbc9792b5d1b9378c9ec4`
- Tree: `8dd3931ec5ae9269bbbaa40fc6082f1498db3be6`
- Worktree was clean, and `git diff --check` for the supplied range emitted no diagnostics.
- Review scope was the supplied six-file fix package. I did not inspect an unchanged caller and did not rerun the claimed focused or broad test gates.

## Prior-Finding Verdicts

### 1. CRITICAL — child split-read TOCTOU: **ADDRESSED**

`SessionDB.get_existing_session_conversation()` performs existence and active-message retrieval with one `sessions LEFT JOIN messages` statement under the database lock (`hermes_state.py`, new method near line 6512). The worker calls that method once for shared context and treats only its exact `None` result as privileged persistent-session absence (`agent/plugin_agent_worker.py`, new block near line 1289). It no longer performs `get_session()` followed by `get_messages_as_conversation()`.

The three result states are coherent and explicit: no joined row returns `None`, the session-only LEFT JOIN row returns `[]`, and message rows go through the existing `_rows_to_conversation()` decoder. The new method does not change the signatures or behavior of either existing SessionDB API. The real-worker regression deletes after parent preflight, runs the subprocess protocol, proves request MCP did not launch, proves the child was reaped, and finally proves the deleted row was not recreated. No `AIAgent` construction or implicit fresh run is reachable from the exact missing observation.

### 2. IMPORTANT — missing classification after masking branches: **ADDRESSED**

The shared-session atomic load is now the first substantive operation inside the worker's established `try`, before request MCP configuration/interpolation/discovery, runtime resolution, tool policy, structured-output capability negotiation, skills, or agent construction. Request decoding/validation remains outside and ahead of this block.

The fix adds both structured-output strategies (`NATIVE_JSON_SCHEMA` drift and `UNSUPPORTED`) with forbidden runtime resolution, plus an enabled-MCP case with forbidden MCP finalization/discovery and verified SessionDB close. Exact absence therefore wins over all masking branches named by the original finding and retains the sanitized zero-attempt result.

### 3. MINOR — real operational database failure coverage: **NOT ADDRESSED**

The fix materially improves coverage: invalid SQLite bytes and a directory at `state.db` exercise real constructor/open failures against the profile-local path, and the POSIX mode-zero case exercises real access denial when the current platform/user cannot bypass it. Corrupt state and denied/open state are no longer represented by a lambda throwing a preconstructed exception.

However, both parametrized broken layouts fail during SessionDB construction/open, and the permission case also targets construction/open. No added test opens a real SessionDB successfully and then causes the actual parent `get_session()` read to fail operationally. Consequently the brief's separate “open/read error” requirement—and the original review's explicit realistic read-failure case—still lacks real-path evidence. A focused real SQLite read-failure regression must also prove the operational exception remains distinct and `_exchange_worker` never starts.

## Generic Seam, Origin, and Compatibility Audit

- The new SessionDB method is narrow: one exact session ID, active messages only, one SQL statement, and no lineage/alternation-repair expansion.
- It preserves `None` absent / `[]` existing-empty / decoded nonempty history and reuses `_rows_to_conversation()` with the existing single-session defaults.
- Existing SessionDB public methods and signatures are untouched; the only new public surface is the authorized method.
- The privileged wire frame is produced only by the exact early database `None` branch. `_worker_failure_result()` reserves `persistent_session_missing`, so later public `PluginAgentSessionMissingError` or other plugin/provider exceptions cannot acquire the privileged origin/count shape.
- Fresh-context behavior remains on the pre-existing path; no recovery policy, workflow import, prompt/tool/history mutation, or Task 13 implementation appears in the fix package.

## Customization Ownership

The Phase 3 ledger entry accurately adds `hermes_state.py`, `SessionDB.get_existing_session_conversation`, the atomic snapshot/exact-origin invariants, `tests/test_hermes_state.py`, and `tests/plugins/workflow/test_node_mcp.py`, while retaining `tests/agent/test_plugin_agent.py`. The historical adjacent entry is untouched, the expected Task 12 subject remains `feat(agent): classify missing plugin sessions`, `upstream_candidate: true` and removal/merge guidance remain present, and `last_verified_upstream` remains the copied historical identity.

## New Breakage Assessment

No new breakage was found in the fix package.

| Severity | New findings |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

The sole blocking item is prior Finding 3's still-incomplete real read-error evidence; implementer gate claims remain unverified by this rereview.
