# Task 12 Quality and Security Rereview — Fix Round 1

## Verdict

**APPROVED.** All three prior quality findings are addressed by the scoped fix. I found no new critical, important, or minor breakage in the authenticated fix range `2d4529924581a5b46178f1bb7dbf2becef12052b..c026d1ef569720f7e47fbc9792b5d1b9378c9ec4`.

The checked-out `HEAD` was `c026d1ef569720f7e47fbc9792b5d1b9378c9ec4`, its tree was `8dd3931ec5ae9269bbbaa40fc6082f1498db3be6`, and the worktree was clean before this designated report was written. The fix contains six changed files, 528 insertions, and 94 deletions. No production, test, index, commit, or branch state was modified.

## Prior Finding Verdicts

### 1. IMPORTANT — Forgeable public exception could manufacture zero-attempt missing evidence

**ADDRESSED.**

The worker no longer imports or type-checks `PluginAgentSessionMissingError` at the process-wide exception boundary. `_worker_failure_result()` explicitly reserves `persistent_session_missing` and demotes any exception-supplied instance of that value to its ordinary exception type (`agent/plugin_agent_worker.py:1213-1233`). The canonical zero-attempt frame is now returned only from the exact `history is None` branch immediately following the child SessionDB lookup (`agent/plugin_agent_worker.py:1283-1298`).

That origin is materially bound rather than inferred: the child reads the existing session and its active conversation before MCP configuration/discovery, runtime resolution, structured-output negotiation, agent construction, plugins, tools, or provider calls can run. A later public typed exception therefore reaches the generic failure path with an `error` field and without zero counters, which cannot satisfy the parent's exact canonical-frame correlation. Existing strict parent checks still require empty response/session/provider/model, no pending interaction, usage, or structured output, an exact four-key audit, matching plugin ID, and exact non-boolean integer zeros.

The added regression at `tests/agent/test_plugin_agent.py:1274-1286` verifies the late public exception is ordinary, while the expanded forged-frame matrix verifies the parent rejects boolean counts and additional evidence.

### 2. IMPORTANT — Race test bypassed the real worker protocol and lifecycle

**ADDRESSED.**

The replacement race test performs the parent preflight, deletes the real profile-local session at the `_exchange_worker` boundary, then invokes the real worker subprocess (`tests/agent/test_plugin_agent.py:1048-1143`). It therefore exercises worker `main()`, `_emit()`, JSON serialization/parsing, `_exchange_worker()`, and `ManagedProcessTree`; it asserts one spawned process tree is reaped, the exact correlated sanitized result arrives, prior session/history text is absent from the wire result, the session was not recreated, and the request MCP launch sentinel was never created.

The early-return cleanup contract is independently exercised through direct `_run()` with a tracking SessionDB (`tests/plugins/workflow/test_node_mcp.py:966-1011`), proving the DB closes and pre-MCP globals remain untouched. The existing post-MCP failure matrix retains all six meaningful post-start stages and continues to assert MCP process exit, DB closure, callbacks, loaders, timeout function, registry state, hooks, and middleware restoration (`tests/plugins/workflow/test_node_mcp.py:1715-1848`). This split matches the new ordering: confirmed missing state has no MCP process to clean up, while every actual post-start failure still proves cleanup.

### 3. MINOR — Mocked operational DB cases and incomplete adversarial coverage

**ADDRESSED.**

Parent-preflight coverage now constructs real profile-local SessionDB layouts for invalid SQLite bytes, a database path that is a directory, and POSIX permission denial; each reproduced operational failure must remain distinct from `PluginAgentSessionMissingError` and must not start a worker (`tests/agent/test_plugin_agent.py:937-1011`). The permission case skips only when the platform or user can bypass the requested filesystem permissions.

Forgery coverage now includes `True` for both counters, absent counter keys, nonempty model, pending interaction, and structured-output evidence, in addition to the prior unknown-field, nonzero-count, identity, raw-content, provider, and usage cases (`tests/agent/test_plugin_agent.py:1289-1447`).

## New Breakage Audit

No new findings.

- **Cleanup ordering:** The missing return remains inside `_run()`'s outer `try/finally`; the opened SessionDB is closed. Because the check precedes MCP imports/configuration, no MCP child or mutable MCP/timeout/registry state exists on that path. Existing and fresh sessions continue through the established cleanup path.
- **Atomicity and false-missing classification:** `SessionDB.get_existing_session_conversation()` uses one `sessions LEFT JOIN messages` statement. No rows means the exact session is absent; a left-join row with no message ID means an existing empty/inactive-only session; active message rows mean existing history (`hermes_state.py:6519-6553`). SQLite statement snapshot semantics remove the split-read deletion window.
- **Conversation decoding parity:** The atomic loader selects the same active message fields in message-ID order and calls the existing `_rows_to_conversation()` with the same single-session/default flags as `get_messages_as_conversation(session_id)`. The parity test compares the decoded histories, and the implementation preserves content sanitization, API-content fidelity, tool/reasoning decoding, display metadata, timestamps, and background-review stripping through the shared decoder.
- **Privacy and exact origin binding:** The canonical frame contains no session ID, history, exception, provider/model value, usage, pending interaction, or structured payload. Its only dynamic value is the already-correlated plugin ID. Operational failures remain generic rather than being mislabeled missing.
- **Direct worker callers:** Repository search found no production caller of private worker `_run()` outside the one-shot worker entrypoint. The direct-call tests cover both early DB cleanup and all post-start cleanup stages.
- **Scope discipline:** The production fix changes only the worker ordering/conversion and adds the generic one-query SessionDB API. It adds no workflow import to agent core, recovery/fresh-session policy, prompt, tool, toolset, history mutation, or Task 13 behavior. The ledger amendment accurately owns the added seam and preserves the historical subject and upstream identity.

## Severity Summary

| Severity | New findings |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

## Verification Notes

- `git diff --check` passed for the authenticated fix range.
- Per dispatch, I did not rerun the broad suite or live gates. All implementer-reported focused, 862-test, 4,097-test, Desktop, and customization-gate results remain unverified claims in this rereview.
- Review was limited to the supplied fix and concrete surrounding code needed for the named risks.
