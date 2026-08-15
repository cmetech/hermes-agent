# Desktop Tool-Choice Dedicated RPC Design

Date: 2026-08-15
Status: Approved for implementation review
Target release: v5.8.1

## Problem

Hermes v5.8.0 added an explicit, request-scoped tool-choice control and a
`slash.exec` handler for `/tool-choice`. The Desktop renderer did not register
the command as a built-in Desktop surface. It therefore treated the command as
an extension command and routed it through the generic
`slash.exec`/`command.dispatch` fallback chain. That chain succeeds on a
current v5.8.0 backend because `/tool-choice` has a pre-worker fast path, but it
is fragile: when `slash.exec` fails, Desktop falls through to
`command.dispatch`, which does not own built-in tool-choice configuration.

The installed failure combined that registry gap with a second cause on the
active backend path and produced:

```text
/tool-choice error: not a quick/plugin/bundle/skill command: tool-choice
```

The command did not arm the one-shot policy, so submitting the following prompt
would run with ordinary automatic tool choice rather than the coordinated OTTO
v1 contract.

Desktop also has an adjacent error-unmasking defect. Its fallback recognizes
the old `not a quick/plugin/skill command` text, while the backend now emits
`not a quick/plugin/bundle/skill command`. The mismatch replaces the original
`slash.exec` failure with misleading `command.dispatch` routing noise. v5.8.1
will update that matcher and preserve the original failure for every affected
exec-surface command.

## Decision

Desktop will expose `/tool-choice` as a first-class command backed by a
dedicated `tool_choice.configure` JSON-RPC method. The RPC will reuse the
existing backend-owned `OneShotToolChoice` state and `configure_tool_choice`
parser. Desktop will not duplicate policy parsing or store mutable policy in
React.

The snake-case `tool_choice` namespace is intentional: it names the existing
session-scoped policy concept without overloading the unrelated
`tools.configure` toolset endpoint.

The underscore spelling `/tool_choice` remains an alias. The command accepts
the existing forms:

```text
/tool-choice auto
/tool-choice required [--otto-v1]
/tool-choice named <tool> [--otto-v1]
/tool-choice none [--otto-v1]
/tool-choice off
```

Because arguments may combine a mode, a tool name, and `--otto-v1`, Desktop
will use mixed/free-text argument behavior rather than a closed option list.

## Architecture and Data Flow

1. The Desktop command registry resolves `/tool-choice` or `/tool_choice` to a
   dedicated RPC surface.
2. The renderer calls `tool_choice.configure` with the active runtime
   `session_id` and the unmodified argument string following the command.
3. The backend resolves that exact session, creates its `OneShotToolChoice`
   holder when absent, and delegates parsing and validation to
   `configure_tool_choice`.
4. The RPC returns the existing human-readable confirmation, such as:

   ```text
   Next turn tool choice: required with OTTO v1.
   ```

5. Desktop renders the confirmation as command output. The next
   `prompt.submit` for the same session consumes the policy exactly once using
   the existing backend lifecycle.

The existing `slash.exec` handler remains intact for TUI, classic CLI, older
Desktop compatibility paths, and other clients. The dedicated Desktop path
does not call `slash.exec` or `command.dispatch` on a current backend.

## Error Handling and Compatibility

- Invalid modes, missing named-tool arguments, and invalid extra arguments use
  the existing parser's exact validation messages.
- Session lookup failures remain ordinary typed JSON-RPC session errors.
- Desktop's established missing-RPC compatibility behavior may fall back to
  `slash.exec` for an older backend. A current v5.8.1 shell and managed backend
  use the dedicated RPC.
- If that `slash.exec` compatibility path fails and `command.dispatch` only
  reports that the command is not a quick/plugin/bundle/skill command, Desktop
  renders the original `slash.exec` error rather than the routing noise.
- The command does not append a message, alter the system prompt, modify prior
  history, change the selected model, or grant connector authorization.
- A successful confirmation is required before the user submits a contract
  test prompt. An error never arms the policy.

## Testing

Implementation follows strict RED/GREEN TDD:

1. Desktop registry tests prove the canonical command, underscore alias,
   discoverability, mixed argument mode, and dedicated RPC surface.
2. Desktop prompt-action tests prove
   `/tool-choice required --otto-v1` calls only `tool_choice.configure` with
   the correct session and arguments, renders the confirmation, and does not
   call `slash.exec` or `command.dispatch`.
3. A Desktop compatibility test proves a missing dedicated RPC falls back to
   `slash.exec` and renders its successful confirmation.
4. A Desktop error-unmasking test proves the current
   `not a quick/plugin/bundle/skill command` fallback preserves the original
   `slash.exec` failure.
5. Backend RPC tests prove configuration is scoped to the requested session,
   returns the existing confirmation, is consumed exactly once by the prompt
   lifecycle, and preserves validation errors.
6. Focused Desktop tests, adjacent TUI gateway tests, Desktop type-check/build,
   Python compilation, merge gates, and brand-generation gates must pass before
   release.

## Release

After verification, merge the feature branch into this fork's development
`base`, then merge the tested base into every descriptor-backed brand branch.
Regenerate and gate OTTO and LOOP24 independently, push only the tested refs,
and publish production v5.8.1 releases from the exact tested brand SHAs. Verify
both workflows, release metadata, macOS and Windows asset sets, and SHA-256
digests. End with the primary checkout on `base`.

## Non-Goals

- No changes to the OTTO/Gateway v1 wire contract.
- No new policy semantics or process-wide feature flags.
- No model-selection or fallback changes.
- No connector authorization changes.
- No removal of the existing CLI, TUI, gateway-platform, or `slash.exec`
  controls.
