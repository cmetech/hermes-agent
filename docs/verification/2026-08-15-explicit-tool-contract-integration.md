# Explicit Per-Turn Tool Contract — Coordinated Integration Verification

**Date:** 2026-08-15
**Hermes branch:** `feat/explicit-per-turn-tool-contract-v1`
**Gateway release:** `v3.5.0` at `8013aaabbe0b0be248c147c218d075104e702176`
**Status:** PASS — coordinated released-binary integration and repository-wide Hermes gates completed

## Release identity

- Gateway main CI completed successfully on the release commit:
  `https://github.com/cmetech/otto-gateway/actions/runs/31901170238`.
- Gateway release workflow completed successfully on the same commit:
  `https://github.com/cmetech/otto-gateway/actions/runs/31901846355`.
- Published release: `https://github.com/cmetech/otto-gateway/releases/tag/v3.5.0`.
- The Apple Silicon archive used for this gate matched its published SHA-256:
  `b8191cf26b6931c50016d8896919ab4e71a89a4c97da58f3ab1d51eed6f8f795`.
- The released binary reported `v3.5.0` before testing.

The archive was extracted into a temporary directory and the Gateway was
started on loopback with a temporary home, a single worker, static fixture
credentials, and a deterministic ACP peer. No installed Gateway, user
configuration, source Gateway file, external model credential, or remote
deployment was modified.

## Test integrity

The same Hermes integration test was first run against released Gateway
`v3.4.0`. The request succeeded but the response lacked the v1 echo, producing
the expected RED assertion:

```text
1 failed: expected X-Otto-Tool-Contract v1, observed no echo
```

Changing only `OTTO_GATEWAY_INTEGRATION_BIN` to the checksum-verified v3.5.0
binary produced:

```text
15 passed in 18.20s
```

The fixture records only bounded event categories and prompt counts. It does
not record prompt text, model output, tool schemas, arguments, tool results,
credentials, raw headers, session identifiers, connector identifiers, or
private project identifiers.

## Coordinated scenario matrix

| Scenario | Released-binary evidence |
|---|---|
| Exact v1 success echo | HTTP success carried the exact v1 echo. |
| Typed validation error echo | The native HTTP 400 response carried the exact v1 echo. |
| Required initial policy | Hermes emitted required, primary, v1. |
| Named outer dispatcher | Hermes emitted the validated named function object, primary, v1. |
| Narrated hidden wrapper | Gateway withheld the first attempt and issued one correction. |
| Corrected outer call | Hermes received and executed exactly one outer dispatcher call; the hidden name was never executed directly. |
| Post-tool lifecycle | The next Hermes request used auto, post_tool, and retained v1. |
| Final prose | The logical operation completed after the post-tool response. |
| Streaming/non-streaming | Required and named lifecycle cases passed in both modes. |
| Optional documentation prose | One prompt, no correction, no tool execution. |
| Auto model | One prompt, no selected-model correction. |
| Second invalid mandatory attempt | Native `selected_model_tool_protocol_failed`, HTTP 502, exact v1 echo. |
| Provenance refusal | One post-tool correction, then final prose; no second tool requirement. |
| Operational correction failure | Native `selected_model_tool_result_provenance_failed`; Hermes treated it as terminal. |
| Missing/wrong echo | Hermes rejected both before tool execution in streaming and non-streaming modes. |
| Correction timeout | Bounded native protocol error with exact v1 echo. |
| Client cancellation | ACP cancellation was observed and Gateway remained healthy. |

## Transport and isolation coverage

The released Gateway was exercised only through its configured OpenAI Chat
Completions HTTP surface. Direct provider mappings remain covered by transport
fakes for OpenAI Chat Completions, Responses/Codex, Anthropic Messages, Gemini,
and Bedrock Converse. Unsupported combinations are asserted as typed failures.

Hermes unit/composition suites separately pin retry identity, concurrent
operation isolation, title/compression/auxiliary defaults, fallback behavior,
prompt-cache identity, stable tool ordering, terminal protocol errors, and
missing/wrong echo before delivery or execution.

## Final repository gates

Fresh verification against the exact candidate produced:

- Required policy, lifecycle, contract, and API suites: 58 passed.
- Provider and transport suites: 311 passed.
- Conversation-loop retry/fallback/concurrency selector: 7 passed, 3 skipped.
- Title, compression, auxiliary, fallback, and prompt-cache selector: 579
  passed, 3 skipped.
- Hermes CLI title/compression selector: 7 passed.
- API-server and streaming suites: 140 passed.
- CLI, Gateway, and TUI contract-control suites: 11 passed.
- Terminal protocol-error selector: 9 passed, 3 skipped.
- Released Gateway v3.5.0 integration suite: 15 passed.
- Canonical `scripts/run_tests.sh -q`: 2,826 files, 33,459 tests passed,
  0 failed. The runner retried two flaky files; uncontended reruns then passed
  cleanly with 517 passed and 104 passed/1 skipped, respectively.
- Python compilation, Ruff lint/format, `git diff --check`, production privacy
  scan, and stable prompt/tool-prefix comparison: passed.

A literal monolithic `pytest -q` invocation remains unusable in this checkout
because pytest imports two pre-existing files named `test_doctor.py` under the
same module name during collection. Neither colliding file is modified by this
feature. The repository's canonical per-file subprocess runner avoids that
known collection collision and completed with zero failures as recorded above.

The system-prompt builder and stable bridge-tool definition/ordering surfaces
are byte-unchanged from the feature branch's merge base with `base`. Concurrent
operation isolation and auxiliary-call fresh-context tests passed.

No live provider output or customer connector data was used. No push, merge,
deployment, tag, branded release, or release-branch mutation occurred during
this gate.
