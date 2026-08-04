# Task 12 Final Independent Specification Closure Review

## Verdict

**PASS.** The complete Task 12 candidate satisfies the brief. No Critical,
Important, or Minor specification findings remain.

## Authenticated scope

- Base: `65dcb957286314787b1c0143f6a4c54eb86f3f63`
- Final HEAD: `b9c57e31cd42bc77685a31ce0f7bd9808deb6d1e`
- Final tree: `fda7669405741e23c19a68a06bb939f794d857b9`
- The final worktree was clean before this report was written.
- `git diff --check` emitted no diagnostics for the complete range.
- The complete range changes exactly the seven authenticated files: the two
  plugin-agent modules, the authorized generic `hermes_state.py` seam, the
  customization ledger, and the three focused test files.
- The four commits and subjects match the package, including the required
  `feat(agent): classify missing plugin sessions` customization subject.

## Prior-finding disposition

### Prior Critical — split-read child TOCTOU: resolved

`SessionDB.get_existing_session_conversation()` performs exact-session
existence and active-history retrieval in one `sessions LEFT JOIN messages`
statement. Its three states are unambiguous: `None` for absence, `[]` for an
existing session without active messages, and the existing
`_rows_to_conversation()` shape for nonempty history. The worker uses this
single result rather than the former `get_session()` / conversation split.

The deterministic database regression proves the new loader does not enter
the legacy split-read boundary. More importantly, the real runner regression
deletes the row after parent preflight and before child load, traverses the
actual worker subprocess and JSON wire protocol, verifies the worker process
was reaped, verifies request MCP never started, and reads the real database
afterward to prove the session was not recreated. The accepted frame is the
sanitized missing frame with exact integer zero provider/model counts.

### Prior Important — absence ordered after masking branches: resolved

The atomic shared-session load is the first substantive worker operation after
request decoding and validation. It precedes request-MCP interpolation,
finalization and discovery, runtime/provider resolution, structured-output
negotiation, skill/prompt adaptation, tool policy, and `AIAgent` construction.
Focused cases cover both structured-output failure strategies and enabled MCP,
with forbidden-call sentinels confirming that absence wins before those
branches. The established `finally` path still closes the SessionDB and
restores worker-global callbacks/loaders/registry state.

### Prior Minor — real operational read-failure evidence: resolved

The final evidence-only commit constructs and successfully reads a real
profile-local `SessionDB`, then drops the live `sessions` table and directly
proves the unchanged `SessionDB.get_session()` query raises the real
`sqlite3.OperationalError`. The parent receives that same live SessionDB and
invokes its real method; no method is replaced and no preconstructed exception
is injected. The test proves the SQLite error is not converted to
`PluginAgentSessionMissingError`, the worker never starts, and the connection
is closed. Together with the existing invalid-database bytes, unusable
database-path, and POSIX permission-denial cases, this supplies the required
real open/read/corrupt-or-ambiguous-layout/denied distinctions.

## Complete requirement audit

- Parent classification is exact: only `get_session(exact_id) is None` raises
  the exported typed `ValueError`; constructor, open, read, corruption, layout,
  permission, and close exceptions are not collapsed into absence.
- The typed parent outcome carries no instance payload, session ID, history,
  or provider response and exposes class-level exact-zero attempt evidence.
- Existing empty and one-message history-light sessions pass preflight and
  reach the worker.
- The privileged worker result has empty response/session/provider/model,
  empty usage, no interaction or structured payload, and an exact four-field
  audit. The real subprocess regression also proves private session/history
  content does not cross the wire.
- Parent correlation is strict: shared context, failed status, empty payload
  fields, exact plugin identity, exact audit key set, and true integer zeros
  are required. Tests reject unknown or missing fields, booleans, nonzero
  counts, foreign plugin identity, fresh-context claims, raw content, usage,
  interaction, and structured-output evidence.
- Privileged origin is bounded to the exact early database-absence branch.
  Generic failure conversion reserves `persistent_session_missing`, so a later
  public `PluginAgentSessionMissingError` cannot forge the privileged result.
- Existing direct-worker cleanup and public `ValueError` compatibility are
  preserved. Existing prompt construction, tool policy, shared history shape,
  and fresh-context behavior are not changed.
- No workflow import, workflow recovery choice, implicit fresh-session policy,
  new model-tool surface, or Task 13 implementation appears in production.
- The authorized SessionDB seam is minimal and compatible: one new public
  exact-session method, one query, active messages only, existing decoder,
  and no change to existing method signatures, lineage, alternation repair,
  or caller behavior.
- The new Phase 3 customization entry is immediately adjacent to the
  historical runner entry, accurately owns the SessionDB extension and tests,
  records merge/removal guidance and `upstream_candidate: true`, and retains
  expected subject `feat(agent): classify missing plugin sessions`. The
  historical subject remains `feat(workflow): enforce per-node agent
  resources`, and both entries retain the copied upstream identity
  `aaf5691261f12601db845386d650dce1cdfa30f9`.

## Cannot independently verify

Per the review instruction, I did not rerun the reported focused, strict, or
4,098-test base gates. Their pass counts and command output are therefore
implementer claims rather than independently authenticated execution evidence.
This does not block specification closure: the authenticated code and tests
provide the required behavioral evidence, and no unresolved requirement or
new package defect was found.

## Severity summary

| Severity | Open findings |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |
