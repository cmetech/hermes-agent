# Task 12 Quality and Security Review

## Verdict

**CHANGES REQUIRED.** The parent-side exact-`None` preflight and canonical-frame validation are narrow and generally fail closed, but the child classification does not prove that the exported typed exception originated at the session lookup. The added tests also do not exercise the real worker wire boundary or the required real database failure modes.

Reviewed authenticated range `65dcb957286314787b1c0143f6a4c54eb86f3f63..2d4529924581a5b46178f1bb7dbf2becef12052b`; the checked-out tree matched `6154894cffcf247b2dd05477806d7e2aa7282ae0`. Scope was four changed files. No production, test, index, commit, or branch state was modified.

## Findings

### Important — The child classification trusts a forgeable exception type as origin evidence

**Evidence:** `agent/plugin_agent.py:82-87`, `agent/plugin_agent.py:885-911`, `agent/plugin_agent.py:1439-1444`, `agent/plugin_agent_worker.py:1214-1216`, `agent/plugin_agent_worker.py:1690-1696`, `agent/plugin_agent_worker.py:1883-1889`.

`PluginAgentSessionMissingError` is public and exported. At the worker boundary, `_worker_failure_result()` converts *any* instance of that public type into the canonical `persistent_session_missing` frame with hard-coded zero attempts. `main()` applies this conversion at a process-wide `except BaseException`, and the ordinary unstructured agent path re-raises exceptions from `run_conversation()`. Therefore a plugin/tool/provider path, or later unrelated core code, can raise the exported exception after other work or provider attempts and the parent will accept it as a genuine child-load race because the resulting frame exactly satisfies `_correlate_persistent_session_result()`.

This violates the requirement that the typed missing classification be impossible to spoof or confuse with an operational failure. It also makes the zero-attempt evidence asserted rather than derived; a later recovery policy could incorrectly start fresh after a real operational/provider failure.

Use origin evidence that only the exact child `SessionDB.get_session(...) is None` branch can produce. For example, raise a private worker-only sentinel subclass from that branch and have the boundary recognize only that sentinel, while retaining the public `ValueError`-compatible parent exception. Alternatively, tag the exception with a private identity token at the lookup and require that identity at conversion. Add a regression where a late agent/provider/tool path raises the public exception after recording an attempt and verify that it is not converted to `persistent_session_missing` or zero counts.

### Important — The race test hand-builds the wire result instead of exercising the worker protocol boundary

**Evidence:** `tests/agent/test_plugin_agent.py:1008-1090`, especially `tests/agent/test_plugin_agent.py:1045-1059`; production boundary at `agent/plugin_agent_worker.py:1857-1890`.

The test deletes the session, calls `worker._run()` in-process, catches the exception itself, calls `_worker_failure_result()` itself, and constructs the result frame itself. It does not execute `main()`, `_emit()`, JSON serialization/parsing, a child process, or the real `_exchange_worker()` lifecycle. Consequently it can remain green if the actual worker boundary stops catching, sanitizing, or emitting the typed race correctly. It also does not establish genuine parent/child timing or process/MCP cleanup through the wire path.

Add a real worker protocol test with a deterministic synchronization hook: let the parent preflight complete, delete the real temporary session before the child lookup, then consume the emitted subprocess frame through `_exchange_worker()`. Assert the sanitized wire shape, strict parent acceptance, zero calls, process reaping, SessionDB closure, and MCP cleanup on that real path.

### Minor — Required adversarial database and forgery cases are represented by mocks or omitted

**Evidence:** `tests/agent/test_plugin_agent.py:930-969` and `tests/agent/test_plugin_agent.py:1093-1114`.

The operational/corrupt/denied matrix constructs a real DB but then replaces its `get_session()` method with a lambda that throws a prebuilt exception and replaces `SessionDB` with a factory returning that object. This proves only that `PluginAgentRunner.run()` does not catch those mocked exceptions; it does not exercise real open/read corruption or access-denial behavior from a profile-local `SessionDB`, as the brief requires. The forged-frame matrix tests integer `1` but omits boolean `True` for both counters, and it omits nonempty `model`, `pending_interaction`, and `structured_output` variants. The implementation appears to reject these through exact-type/equality checks, but the specified security invariants are not directly verified.

Use real temporary database artifacts and permissions/corruption where the platform permits, with a narrowly marked fallback only when permissions cannot be reproduced. Extend the forgery matrix with `True` for each count, missing audit keys, nonempty model, pending interaction, and structured-output evidence.

## Severity Summary

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 2 |
| Minor | 1 |

## Positive Observations

- Parent preflight classifies only exact `None` and closes its profile-local `SessionDB` in `finally`; database exceptions are not caught or rewritten.
- Parent correlation requires shared context, a failed/empty result, no interaction/usage/structured output, an exact audit key set, matching plugin ID, and exact integer zeros, so booleans and string/nonzero counts are rejected by implementation.
- The typed frame contains no session ID, history, exception text, provider/model value, usage, or structured-output data.
- Worker cleanup remains on `_run()`'s existing `finally` path, including callback restoration, SessionDB close, inline-tool deregistration, MCP shutdown, loader restoration, timeout restoration, and registry generation restoration.
- No workflow import was added to agent core, no Task 13 recovery policy appears, and no new model-tool surface, prompt, toolset, or history behavior was added.
- The ledger entry is adjacent to the historical entry, preserves its subject and baseline identity, and the two commits read as a generic seam followed by a focused compatibility correction.

## Verification and Cannot-Verify Notes

- `git diff --check` passed for the authenticated range, and the worktree was clean before this designated report was created.
- Per the review dispatch, I did not rerun broad test suites or live gates. The implementer-reported 4,086-test base gate, focused runs, and live customization-gate results remain unverified claims in this review.
- I did not reproduce real filesystem permission denial or SQLite corruption because the submitted tests do not create those conditions and the review was restricted to read-only inspection plus this report.
