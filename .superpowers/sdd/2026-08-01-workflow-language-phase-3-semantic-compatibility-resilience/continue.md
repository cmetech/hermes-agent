# Continue — Phase 3 / Task 12

## Last action

Closed Task 11 at `3d1f9f05a498c1ef025ced9e7e2de5b6c5d9dd87`: final
implementation `49ffbccfe4b9424f3b6542cfdf9df4bc9ef537e0` passed fresh
independent specification and quality rereviews with 0 Critical, 0 Important,
and 0 Minor findings. The exact closure set passed 1,799 tests; strict
customization passed; the canonical base gate passed 4,070 Python tests, one
installed-distribution test, and 155 Desktop tests.

## Next action

Read `AGENTS.md`, the approved Phase 3 design and plan, this handoff, and the
final Task 11 rereviews; verify the exact branch/HEAD/tree and clean worktree;
then begin Task 12 with parent-preflight tests in
`tests/agent/test_plugin_agent.py` using a real temporary profile-local
`SessionDB`. Prove RED through `scripts/run_tests.sh` with retries disabled
before editing production.

## Why

Task 12 adds only the generic typed classification seam needed by Task 13:
confirmed absence is distinct from database/open/read/ambiguous failures. It
must not choose workflow recovery or fresh-session behavior.

## Open threads

- Tasks 12–16 remain pending; Task 13 recovery must wait for Task 12 closure.
- Task 12 owns `PluginAgentSessionMissingError`, parent preflight, sanitized
  worker `persistent_session_missing` framing/correlation, tests, and one new
  customization-ledger entry adjacent to historical `plugin-agent-runner`.
- The shared `base` checkout remains at `5b974a53593fc880d18417ee2fc0e5eaff5599f4`
  with unrelated user-owned changes.

## Do not

- Do not run direct `pytest`; use `scripts/run_tests.sh` and
  `HERMES_TEST_FILE_RETRIES=0` for authoritative gates.
- Do not add workflow imports or fresh-session policy to the generic agent
  layer; do not change prompts, toolsets, history, or existing callers.
- Do not expose session IDs, history, provider responses, raw exceptions, or
  spoofable attempt counts in the worker frame.
- Do not begin Task 13, Phase 4 loops/includes, MCP/skills node kinds, or new
  artifact/provider surfaces.
- Do not modify the shared base checkout, literal `main`, push, publish, or
  delete branches/worktrees.
