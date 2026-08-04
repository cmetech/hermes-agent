# Task 12 Final Quality and Security Closure Review

## Verdict

**PASS.** I found no Critical, Important, or Minor issues in the complete final
Task 12 candidate. The generic seam is narrowly origin-bound, fails closed at
the parent protocol boundary, preserves operational failures and existing
callers, and does not implement Task 13 recovery policy or widen the model-tool
surface.

The authenticated scope is
`65dcb957286314787b1c0143f6a4c54eb86f3f63..b9c57e31cd42bc77685a31ce0f7bd9808deb6d1e`.
The refs resolve to the declared base and HEAD, their merge base is the declared
base, and final HEAD resolves to tree
`fda7669405741e23c19a68a06bb939f794d857b9`. The pre-report worktree was clean.
The package contains four commits and exactly seven changed files: the two
plugin-agent modules, the generic SessionDB module, three focused test files,
and the workflow customization ledger. `git diff --check` is clean.

## Prior-Finding Disposition

### 1. Important — Public exception could forge zero-attempt worker origin

**CLOSED.** The canonical missing frame is constructed only by
`_persistent_session_missing_failure()` at the exact atomic database result
branch in `agent/plugin_agent_worker.py:1293-1298`. That branch runs before MCP
configuration/discovery, runtime resolution, structured-output negotiation,
agent construction, plugins, tools, or provider calls. The process-wide generic
converter at `agent/plugin_agent_worker.py:1213-1233` explicitly reserves
`persistent_session_missing`; a later public
`PluginAgentSessionMissingError` is demoted to the ordinary exception-type
failure shape and cannot carry canonical zero counters.

The parent independently requires shared context, failed status, empty
response/session/provider/model, no interaction/usage/structured evidence, an
exact four-field audit, the expected plugin ID, and exact non-boolean integer
zeros (`agent/plugin_agent.py:881-911`). Thus worker-origin evidence cannot be
confused with a later public exception or a malformed/spoofed frame.

### 2. Important — Race test bypassed the real subprocess protocol/lifecycle

**CLOSED.** The replacement regression performs a real parent preflight,
deletes the real profile-local session at the exchange boundary, launches the
real worker subprocess, parses its emitted JSON frame through the production
exchange path, and verifies process reaping, exact correlated output, privacy,
no row recreation, and no request-MCP launch
(`tests/agent/test_plugin_agent.py:1098-1193`). Separate direct-worker coverage
verifies the early SessionDB close and the retained post-start MCP/global
cleanup matrix (`tests/plugins/workflow/test_node_mcp.py:966-1011` and
`:1715-1848`).

### 3. Minor — Mocked database failures and incomplete forgery matrix

**CLOSED.** The suite now uses real invalid SQLite bytes, a directory in place
of the database, POSIX permission denial where reproducible, and—at final
HEAD—a successfully opened/read real SessionDB whose `sessions` table is then
dropped to force the actual unchanged `get_session()` query to raise
`sqlite3.OperationalError` (`tests/agent/test_plugin_agent.py:931-1060`). In
each parent path the operational exception remains distinct, the worker does
not start, and cleanup is asserted. The adversarial frame matrix covers unknown
and missing audit fields, nonzero and boolean counters, wrong plugin identity,
raw response/session/provider/model content, usage, pending interaction,
structured evidence, and fresh-context claims
(`tests/agent/test_plugin_agent.py:1340-1544`).

## Final Candidate Audit

- **Missing versus empty:**
  `SessionDB.get_existing_session_conversation()` uses one parameterized
  `sessions LEFT JOIN messages` statement. No row means absent; a session row
  with no active message ID means an existing empty/inactive-only history; and
  active rows are returned in message-ID order (`hermes_state.py:6519-6553`).
- **SQLite and decoding parity:** The atomic projection contains the same
  active conversation fields as the established single-session loader and
  delegates to the same `_rows_to_conversation()` decoder with the established
  default lineage/alternation flags. This preserves sanitization, byte-fidelity
  API content, tool/reasoning fields, display metadata, timestamp behavior, and
  background-review stripping. A single SQLite statement supplies one coherent
  read snapshot, eliminating the former existence/history TOCTOU window.
- **Operational failures:** Parent preflight raises the typed exception only on
  exact `get_session(...) is None`; construction/open/read/access/corruption
  exceptions are not caught or rewritten and the connection closes in
  `finally` (`agent/plugin_agent.py:1355-1367`). Worker atomic-read errors take
  the ordinary sanitized failure path, never the privileged missing frame.
- **Zero-attempt authority and ordering:** The canonical counters are exact
  zeros because the sole construction branch precedes every provider-capable or
  request-service operation. Later failures cannot claim this failure kind.
- **Privacy and protocol correlation:** The canonical frame exposes only the
  already-correlated plugin ID and fixed classification/count fields. It carries
  no session ID, history, exception text, provider/model, usage, pending
  interaction, or structured payload. Unknown result/audit fields and
  contradictory evidence fail closed.
- **Cleanup:** The early return remains inside `_run()`'s outer `try/finally`;
  SessionDB closes. MCP is not started on the missing path. Existing/fresh and
  post-start failures retain callback, secret/sudo/approval, inline-tool, MCP,
  loader, timeout, registry-generation, hook, middleware, process-tree, and
  provider-authority cleanup.
- **Exception hierarchy and callers:**
  `PluginAgentSessionMissingError` remains `ValueError`-compatible and has no
  instance payload. Exact-symbol search found no production construction or
  special handling outside the parent preflight. Existing generic plugin and
  workflow callers therefore retain their prior `ValueError` behavior; choosing
  workflow recovery remains intentionally deferred.
- **Prompt/history/cache invariants:** The change loads the same active history
  through the shared decoder and does not alter prompts, past messages,
  toolsets, system prompts, or cache policy.
- **Scope and ledger:** The new ledger entry is adjacent to the historical
  `plugin-agent-runner` entry, leaves that entry and its expected subject
  untouched, copies the upstream identity, accurately owns the new exception,
  parent correlation, worker frame, atomic SessionDB seam, invariants, and
  tests, and records the required upstream/removal guidance. No workflow
  executor, recovery/fresh-session policy, Task 13 behavior, core tool schema,
  config, dependency, or unrelated surface changed.

## STRIDE / Security Non-Findings

- **Spoofing/tampering/elevation:** A malformed or plugin-mismatched worker
  result cannot acquire missing-session authority; exact correlation rejects it.
- **Information disclosure:** The privileged frame and parent exception omit
  private session and provider material.
- **Denial of service/resource leakage:** The missing path performs one bounded,
  indexed SQLite lookup and unwinds the database/process lifecycle; it starts no
  MCP/provider work.
- **Repudiation:** The accepted audit is minimal but exact and binds the plugin,
  failure kind, and zero attempt/model-call evidence without trusting additive
  worker fields.

## Severity Summary

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

## Cannot-Verify Items

Per dispatch, I did not rerun focused tests, broad suites, live customization
gates, or the base merge gate. All reported 121/863/4,098 Python, installed-
distribution, Desktop, strict-ledger, permission-denial, and cleanup execution
results remain unverified runtime claims. Static inspection confirms the tests
exercise the stated paths and the final evidence-only package changes tests
only; it cannot independently prove those commands passed on the implementer's
machine.
