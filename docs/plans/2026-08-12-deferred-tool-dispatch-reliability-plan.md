# Deferred Tool Dispatch Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Status:** Draft for discussion; not authorized for execution or release

**Goal:** Make described deferred tools reliably execute through Hermes'
scoped `tool_call` bridge without exposing undeclared tool names or mutating a
conversation's model-facing tool list.

**Architecture:** Strengthen the Hermes-owned describe/dispatch contract, then
add a narrowly gated OTTO Gateway recovery path for textual wrappers that name
a deferred tool directly while an exact `tool_call` dispatcher is declared.
The gateway only constructs the already-authorized outer dispatcher call;
Hermes remains responsible for inner-name scope, bridge/core exclusion,
argument validation, policy, hooks, and execution.

**Tech Stack:** Python 3.11+, pytest, Go 1.24+, Go `testing`, OpenAI-compatible
SSE, Hermes plugin/tool registry, OTTO Gateway canonical tool-call coercion,
PowerShell-based Windows installed UAT.

**Findings:**
[Deferred tool dispatch failure findings](../design/2026-08-12-deferred-tool-dispatch-findings.md)

## Global Constraints

- Preserve per-conversation prompt caching and byte-stable model-facing tool
  prefixes; do not dynamically add a described schema to later turns.
- Keep the implementation generic; no Ericsson or GitLab name checks.
- Do not make arbitrary undeclared wrappers executable without an explicitly
  declared, structurally valid dispatcher.
- Preserve fail-open-to-text behavior when dispatcher recognition fails.
- Preserve Hermes' session-scoped deferred catalog as the final execution
  authority.
- Preserve native structured calls and every protocol-specific wire shape.
- Add no new core model tool, dependency, user-facing environment variable, or
  configuration setting.
- Begin implementation in isolated worktrees. Do not disturb unrelated
  untracked files in the Hermes checkout or the gateway repository's existing
  local commit stack.
- Use `base` as Hermes development main and restore the Hermes checkout to
  `base` after any release workflow.
- Proposed release targets are OTTO Gateway v3.2.1 and branded Hermes v5.5.4;
  release remains blocked until the open decisions in the findings document
  are approved.

---

## File and responsibility map

### Hermes repository

- `tools/tool_search.py` owns the bridge schemas, `tool_describe` response,
  deferred-name classification, and the human/model-facing dispatch contract.
- `model_tools.py` owns unwrapping `tool_call`, enforcing the current session's
  scoped deferred catalog, and invoking the underlying tool.
- `tests/tools/test_tool_search.py` owns unit and integration coverage for
  bridge descriptions, describe results, valid dispatch, and scope rejection.

### OTTO Gateway repository

- `internal/engine/coerce.go` owns extraction and validation of textual
  `{"tool_call":...}` wrappers. It will own exact dispatcher recognition and
  nested-call construction.
- `internal/engine/coerce_test.go` owns extractor-level safety and argument
  preservation tests.
- `internal/adapter/openai/sse_golden_test.go` owns client-visible streaming
  wire behavior for the reproduced failure.
- Ollama's shared-coercion tests verify the extractor change on that adapter;
  Anthropic's adapter suite verifies its intentionally separate native
  `tool_use` path remains unaffected.

## Interfaces

### Hermes `tool_describe` result

`dispatch_tool_describe()` will retain its existing `name`, `description`, and
`parameters` members and add:

```json
{
  "invoke_via": "tool_call",
  "invoke_arguments": {
    "name": "gitlab_list_group_projects",
    "arguments_schema": {"type": "object", "properties": {}}
  },
  "next_step": "Call tool_call with name set to the described exact name and arguments matching arguments_schema. The described tool remains deferred and is not directly callable."
}
```

`arguments_schema` is the same object as the existing `parameters` value. It
describes the nested `arguments` object; it is not itself sent to the target
tool.

### Gateway dispatcher recognition

Add the unexported helper:

```go
func toolCallDispatcher(tools []canonical.ToolSpec) *canonical.ToolSpec
```

It returns the first tool only when all conditions hold:

- `Name == "tool_call"`;
- `Parameters["type"] == "object"`;
- `Parameters["properties"]` is a map;
- property `name` has type `string`;
- property `arguments` has type `object`; and
- `required` contains both `name` and `arguments`.

When a wrapper's name is undeclared and this helper succeeds, the extractor
returns:

```go
canonical.ToolCall{
    Name: "tool_call",
    Arguments: map[string]any{
        "name": originalName,
        "arguments": originalArguments,
    },
}
```

When the helper returns nil, existing invented-name remapping and
fail-open-to-text behavior remain unchanged.

---

### Task 1: Create isolated worktrees and prove both baselines

**Files:** No production changes.

**Interfaces:**

- Consumes: Hermes `base` at `d48f783b254ac2faa93b9f9db7a7ed6098e2172b`;
  gateway `main` at `ca18a6796dc19a13a0c5df60c9a6d0744390f6f1`.
- Produces: clean feature worktrees for independent commits and review.

- [ ] **Step 1: Create the Hermes worktree**

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
git worktree add \
  .worktrees/deferred-tool-dispatch \
  -b fix/deferred-tool-dispatch \
  d48f783b254ac2faa93b9f9db7a7ed6098e2172b
```

Expected: the new worktree is clean on `fix/deferred-tool-dispatch`; the main
checkout remains on `base` with unrelated untracked files untouched.

- [ ] **Step 2: Create the gateway worktree**

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway
git worktree add \
  .worktrees/deferred-tool-dispatch \
  -b fix/deferred-tool-dispatch \
  ca18a6796dc19a13a0c5df60c9a6d0744390f6f1
```

Expected: the new gateway worktree is clean and preserves the root checkout's
existing six-commit local stack.

- [ ] **Step 3: Record task-local paths**

```bash
HERMES_WT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/deferred-tool-dispatch
HERMES_PY=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
GATEWAY_WT=/Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway/.worktrees/deferred-tool-dispatch
git -C "$HERMES_WT" status --short --branch
git -C "$GATEWAY_WT" status --short --branch
```

Expected: both worktrees report only their feature branch header.

- [ ] **Step 4: Run the focused Hermes baseline**

```bash
cd "$HERMES_WT"
"$HERMES_PY" -m pytest -q tests/tools/test_tool_search.py
```

Expected: PASS before adding a regression test.

- [ ] **Step 5: Run the focused gateway baseline**

```bash
cd "$GATEWAY_WT"
go test ./internal/engine ./internal/adapter/openai -count=1
```

Expected: PASS before adding a regression test.

### Task 2: Make the Hermes describe-to-dispatch contract explicit

**Files:**

- Modify: `tools/tool_search.py`
- Modify: `tests/tools/test_tool_search.py`

**Interfaces:**

- Consumes: existing `dispatch_tool_describe(args, current_tool_defs=...)`.
- Produces: backward-compatible describe JSON with `invoke_via`,
  `invoke_arguments`, and `next_step` members.

- [ ] **Step 1: Write the failing describe-result test**

Add this test to `TestBridgeDispatch`:

```python
def test_tool_describe_returns_explicit_dispatch_contract(self):
    from tools.registry import registry
    from tools.tool_search import dispatch_tool_describe

    name = "deferred_fixture_group_projects"
    parameters = {
        "type": "object",
        "properties": {
            "group": {"type": "string"},
            "recursive": {"type": "boolean"},
        },
        "required": ["group"],
    }
    tool_def = {
        "type": "function",
        "function": {
            "name": name,
            "description": "List projects below a group.",
            "parameters": parameters,
        },
    }
    registry.register(
        name=name,
        handler=lambda args, **kwargs: "{}",
        schema=tool_def,
        toolset="deferred-fixture",
    )

    result = json.loads(dispatch_tool_describe(
        {"name": name},
        current_tool_defs=[tool_def],
    ))

    assert result["name"] == name
    assert result["parameters"] == parameters
    assert result["invoke_via"] == "tool_call"
    assert result["invoke_arguments"] == {
        "name": name,
        "arguments_schema": parameters,
    }
    assert "remains deferred" in result["next_step"]
    assert "not directly callable" in result["next_step"]
```

- [ ] **Step 2: Run the test and confirm RED**

```bash
cd "$HERMES_WT"
"$HERMES_PY" -m pytest -q \
  tests/tools/test_tool_search.py::TestBridgeDispatch::test_tool_describe_returns_explicit_dispatch_contract
```

Expected: FAIL with missing `invoke_via`.

- [ ] **Step 3: Add the backward-compatible result members**

In `dispatch_tool_describe`, build the response as:

```python
parameters = fn.get("parameters", {})
return json.dumps({
    "name": name,
    "description": fn.get("description", ""),
    "parameters": parameters,
    "invoke_via": TOOL_CALL_NAME,
    "invoke_arguments": {
        "name": name,
        "arguments_schema": parameters,
    },
    "next_step": (
        f"Call {TOOL_CALL_NAME} with name set to '{name}' and arguments "
        "matching arguments_schema. The described tool remains deferred "
        "and is not directly callable."
    ),
}, ensure_ascii=False)
```

Do not remove or rename the existing response members.

- [ ] **Step 4: Strengthen the stable bridge descriptions**

Change the `tool_describe` description to state:

```python
desc_describe = (
    f"Load the full JSON schema for one tool returned by {TOOL_SEARCH_NAME}. "
    f"This does not add that tool to the active tool list. After describing "
    f"it, invoke the declared {TOOL_CALL_NAME} bridge; never call the "
    "described name directly."
)
```

Retain the existing `tool_call` schema and argument names.

- [ ] **Step 5: Add a description regression test**

Add this test to `TestAssembly`:

```python
def test_tool_describe_bridge_warns_tool_remains_deferred(self):
    from tools.tool_search import assemble_tool_defs, ToolSearchConfig

    name = "mcp_describe_contract"
    self._register_mcp(name)
    result = assemble_tool_defs(
        [_td(name, "Deferred capability description.")],
        context_length=200_000,
        config=ToolSearchConfig.from_raw({"enabled": "on"}),
    )

    assert result.activated
    describe = next(
        td for td in result.tool_defs
        if td["function"]["name"] == "tool_describe"
    )
    description = describe["function"]["description"]
    assert "does not add that tool to the active tool list" in description
    assert "never call the described name directly" in description
```

- [ ] **Step 6: Run the focused Hermes suite and confirm GREEN**

```bash
cd "$HERMES_WT"
"$HERMES_PY" -m pytest -q tests/tools/test_tool_search.py
```

Expected: PASS.

- [ ] **Step 7: Commit the Hermes contract slice**

```bash
cd "$HERMES_WT"
git add tools/tool_search.py tests/tools/test_tool_search.py
git diff --cached --check
git commit -m "fix(tool-search): make deferred dispatch contract explicit"
```

### Task 3: Add exact dispatcher recognition to gateway coercion

**Files:**

- Modify: `internal/engine/coerce.go`
- Modify: `internal/engine/coerce_test.go`
- Modify: `internal/adapter/openai/sse_golden_test.go`

**Interfaces:**

- Consumes: `[]canonical.ToolSpec` declared by the client request.
- Produces: `toolCallDispatcher(tools) *canonical.ToolSpec` and a nested
  canonical call only for the exact declared dispatcher contract, plus the
  client-visible OpenAI streaming regression guard.

- [ ] **Step 1: Add test fixtures for valid and malformed dispatchers**

Add these helpers to `coerce_test.go`:

```go
func deferredDispatcherTool() canonical.ToolSpec {
    return canonical.ToolSpec{
        Name: "tool_call",
        Parameters: map[string]any{
            "type": "object",
            "properties": map[string]any{
                "name": map[string]any{"type": "string"},
                "arguments": map[string]any{"type": "object"},
            },
            "required": []any{"name", "arguments"},
        },
    }
}

func malformedDispatcherTool() canonical.ToolSpec {
    spec := deferredDispatcherTool()
    spec.Parameters["required"] = []any{"name"}
    return spec
}
```

If canonical JSON decoding stores `required` as `[]string` in existing test
fixtures, make `toolCallDispatcher` accept both `[]string` and `[]any` without
accepting non-string members.

Add the equivalent short fixture to the OpenAI package as
`deferredDispatcherRequestTool`; test helpers are package-local and must not be
exported from production code.

- [ ] **Step 2: Write the failing extractor tests**

Add these table cases to `TestExtractToolCallWrappers` or focused sibling
tests:

```go
func TestExtractToolCallWrappers_DeferredDispatcher(t *testing.T) {
    text := `{"tool_call":{"name":"gitlab_list_group_projects","arguments":{"group":"sd-macs-att-rnam-hosting","recursive":true,"max_groups":50,"max_projects":100}}}`

    got := ExtractToolCallWrappers(text, []canonical.ToolSpec{deferredDispatcherTool()})
    if len(got) != 1 {
        t.Fatalf("calls=%d, want 1", len(got))
    }
    if got[0].Name != "tool_call" {
        t.Fatalf("name=%q, want tool_call", got[0].Name)
    }
    if got[0].Arguments["name"] != "gitlab_list_group_projects" {
        t.Fatalf("inner name=%v", got[0].Arguments["name"])
    }
    inner, ok := got[0].Arguments["arguments"].(map[string]any)
    if !ok || inner["group"] != "sd-macs-att-rnam-hosting" || inner["recursive"] != true {
        t.Fatalf("inner arguments=%#v", got[0].Arguments["arguments"])
    }
}

func TestExtractToolCallWrappers_MalformedDispatcherFailsOpen(t *testing.T) {
    text := `{"tool_call":{"name":"gitlab_list_group_projects","arguments":{"group":"g"}}}`
    got := ExtractToolCallWrappers(text, []canonical.ToolSpec{malformedDispatcherTool()})
    if len(got) != 0 {
        t.Fatalf("calls=%#v, want none", got)
    }
}
```

Retain the existing `wrapper_unknown_name_no_overlap` test unchanged. It is the
canary that no-dispatcher requests still fail open to text.

Add focused recognition-boundary cases proving that
`toolCallDispatcher([]canonical.ToolSpec{tc}) == nil` for every malformed
candidate:

```go
cases := []canonical.ToolSpec{
    {Name: "other_dispatcher", Parameters: deferredDispatcherTool().Parameters},
    {Name: "tool_call", Parameters: map[string]any{"type": "string"}},
    {Name: "tool_call", Parameters: map[string]any{
        "type": "object",
        "properties": map[string]any{
            "name": map[string]any{"type": "integer"},
            "arguments": map[string]any{"type": "object"},
        },
        "required": []any{"name", "arguments"},
    }},
}
```

- [ ] **Step 3: Write the exact OpenAI streaming reproduction test**

Add this test to `sse_golden_test.go`:

```go
func deferredDispatcherRequestTool() canonical.ToolSpec {
    return canonical.ToolSpec{
        Name: "tool_call",
        Parameters: map[string]any{
            "type": "object",
            "properties": map[string]any{
                "name": map[string]any{"type": "string"},
                "arguments": map[string]any{"type": "object"},
            },
            "required": []any{"name", "arguments"},
        },
    }
}

func TestStream_DeferredWrapperRoutesThroughDeclaredDispatcher(t *testing.T) {
    defer goleak.VerifyNone(t)
    text := "```json\n" +
        `{"tool_call":{"name":"gitlab_list_group_projects","arguments":{"group":"sd-macs-att-rnam-hosting","recursive":true,"max_groups":50,"max_projects":100}}}` +
        "\n```"
    req := &canonical.ChatRequest{
        Tools: []canonical.ToolSpec{deferredDispatcherRequestTool()},
    }

    body := driveGoldenWithReq(t,
        []canonical.Chunk{{
            Kind: canonical.ChunkKindText,
            Text: &canonical.TextChunk{Content: text},
        }},
        &canonical.FinalResult{StopReason: canonical.StopEndTurn},
        req,
    )
    out := string(body)

    if !strings.Contains(out, `"name":"tool_call"`) {
        t.Fatalf("dispatcher call missing; body=%q", out)
    }
    if !strings.Contains(out, `\"name\":\"gitlab_list_group_projects\"`) {
        t.Fatalf("inner tool name missing; body=%q", out)
    }
    if strings.Contains(out, "```json") || strings.Contains(out, `\"tool_call\":{`) {
        t.Fatalf("raw wrapper leaked as assistant content; body=%q", out)
    }
    if !strings.Contains(out, `"finish_reason":"tool_calls"`) {
        t.Fatalf("tool_calls finish reason missing; body=%q", out)
    }
}
```

- [ ] **Step 4: Run the engine and streaming tests and confirm RED**

```bash
cd "$GATEWAY_WT"
go test ./internal/engine -run 'TestExtractToolCallWrappers_(DeferredDispatcher|MalformedDispatcherFailsOpen)' -count=1 -v
go test ./internal/adapter/openai -run TestStream_DeferredWrapperRoutesThroughDeclaredDispatcher -count=1 -v
```

Expected: the valid-dispatcher extractor returns zero calls and the streaming
response preserves the raw wrapper with `finish_reason:"stop"`.

- [ ] **Step 5: Implement exact dispatcher recognition**

Add `toolCallDispatcher` beside the extractor's existing schema helpers. It
must validate the exact interface defined above and return nil for missing,
malformed, or lookalike schemas.

In `pushWrapper`, preserve the original wrapper name before remapping:

```go
originalName := name
if !toolDeclared(name, tools) {
    if dispatcher := toolCallDispatcher(tools); dispatcher != nil {
        name = dispatcher.Name
        args = map[string]any{
            "name": originalName,
            "arguments": args,
        }
    } else {
        best, score := pickBestTool(args, tools)
        if best == nil || score == 0 {
            return canonical.ToolCall{}, false
        }
        name = best.Name
    }
}
```

Dispatcher routing intentionally precedes invented-name key-overlap remapping:
an explicit hidden name should reach the declared scope-checking dispatcher,
not be guessed into an unrelated visible tool that happens to share one
property.

- [ ] **Step 6: Run coercion-sensitive suites and confirm GREEN**

```bash
cd "$GATEWAY_WT"
go test ./internal/engine ./internal/adapter/openai ./internal/adapter/anthropic ./internal/adapter/ollama -count=1
```

Expected: PASS. The exact streaming reproduction produces a structured outer
`tool_call`; existing undeclared-name cases remain text when no dispatcher is
declared; Anthropic and Ollama retain their protocol-specific shapes.

- [ ] **Step 7: Format and commit the complete gateway slice**

```bash
cd "$GATEWAY_WT"
gofmt -w \
  internal/engine/coerce.go \
  internal/engine/coerce_test.go \
  internal/adapter/openai/sse_golden_test.go
git add \
  internal/engine/coerce.go \
  internal/engine/coerce_test.go \
  internal/adapter/openai/sse_golden_test.go
git diff --cached --check
git commit -m "fix(tools): route deferred wrappers through declared dispatcher"
```

### Task 4: Verify Hermes scope enforcement against the recovered shape

**Files:**

- Modify: `tests/tools/test_tool_search.py`

**Interfaces:**

- Consumes: gateway-produced structured call
  `tool_call(name="deferred_recovered_group_projects", arguments={"group":"g","recursive":true})`.
- Produces: proof that recovery grants no authority outside the existing
  session-scoped deferred catalog.

- [ ] **Step 1: Add an in-scope recovered-call test**

Add this class after `TestHandleFunctionCallIntegration`:

```python
class TestRecoveredDeferredDispatchScope:
    @staticmethod
    def _register():
        from tools.registry import registry

        name = "deferred_recovered_group_projects"
        tool_def = _td(
            name,
            "List projects below a recovered group.",
            {
                "group": {"type": "string"},
                "recursive": {"type": "boolean"},
            },
        )

        def _handler(args, task_id=None, **kwargs):
            return json.dumps(args)

        registry.register(
            name=name,
            handler=_handler,
            schema=tool_def,
            toolset="deferred-recovered-allowed",
        )
        return name

    def test_recovered_call_executes_when_inner_name_is_in_scope(self):
        import model_tools

        name = self._register()
        result = json.loads(model_tools.handle_function_call(
            function_name="tool_call",
            function_args={
                "name": name,
                "arguments": {"group": "g", "recursive": True},
            },
            enabled_toolsets=["deferred-recovered-allowed"],
        ))

        assert result == {"group": "g", "recursive": True}
```

- [ ] **Step 2: Add an out-of-scope recovered-call test**

Add this method to `TestRecoveredDeferredDispatchScope`:

```python
def test_recovered_call_rejects_inner_name_outside_session_scope(self):
    import model_tools

    name = self._register()
    parsed = json.loads(model_tools.handle_function_call(
        function_name="tool_call",
        function_args={
            "name": name,
            "arguments": {"group": "g", "recursive": True},
        },
        enabled_toolsets=["different-toolset"],
    ))

    assert parsed["error"].startswith(
        f"'{name}' is not available in this session"
    )
```

The existing shared `tool_error` envelope is `{"error":"..."}`. Do not weaken
the scope gate or special-case the fixture.

- [ ] **Step 3: Run the focused tests**

```bash
cd "$HERMES_WT"
"$HERMES_PY" -m pytest -q \
  tests/tools/test_tool_search.py::TestRecoveredDeferredDispatchScope \
  tests/tools/test_tool_search.py::TestRegression_ToolsetScoping
```

Expected: PASS.

- [ ] **Step 4: Commit the authority tests**

```bash
cd "$HERMES_WT"
git add tests/tools/test_tool_search.py
git diff --cached --check
git commit -m "test(tool-search): preserve scope for recovered deferred calls"
```

### Task 5: Run repository-level verification and review the security boundary

**Files:** No additional production changes expected.

**Interfaces:**

- Consumes: completed Hermes and gateway feature branches.
- Produces: reviewable evidence that prompt caching, scope, and adapter wire
  contracts remain intact.

- [ ] **Step 1: Run the Hermes focused and integration suites**

```bash
cd "$HERMES_WT"
"$HERMES_PY" -m pytest -q \
  tests/tools/test_tool_search.py \
  tests/hermes_cli/test_ericsson_connector_surfaces.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py
```

Expected: PASS.

- [ ] **Step 2: Run the gateway full test suite**

```bash
cd "$GATEWAY_WT"
go test ./... -count=1
```

Expected: PASS.

- [ ] **Step 3: Run gateway static checks**

```bash
cd "$GATEWAY_WT"
gofmt -w internal/engine/coerce.go internal/engine/coerce_test.go internal/adapter/openai/sse_golden_test.go
git diff --check
go vet ./...
```

Expected: no formatting diff after the committed formatting pass, no
whitespace errors, and no vet findings.

- [ ] **Step 4: Perform the explicit security review**

Review the diff and record evidence for each question in the PR description:

```text
1. Does recovery require a declared function named exactly tool_call?
2. Does recognition validate both required dispatcher arguments and types?
3. Does no-dispatcher behavior remain fail-open-to-text?
4. Can the gateway execute an inner tool itself? Expected: no.
5. Does Hermes still reject out-of-scope, bridge, and core inner names?
6. Are directly declared and native calls unchanged?
```

Expected: all six answers satisfy the stated invariant.

- [ ] **Step 5: Request cross-repository code review**

Present the two branch diffs together so the reviewer can verify the complete
authority chain rather than reviewing either half in isolation.

### Task 6: Build and validate release candidates

**Files:** Release metadata only if required by the existing release scripts.

**Interfaces:**

- Consumes: reviewed gateway and Hermes commits.
- Produces: gateway v3.2.1 Windows artifact and branded Hermes v5.5.4
  candidates suitable for installed UAT.

- [ ] **Step 1: Rebase, verify, and merge the gateway fix after approval**

```bash
cd "$GATEWAY_WT"
git fetch origin main
git rebase origin/main
git status --short --branch
go test ./... -count=1
git log --oneline --decorate -5

cd /Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway
git switch main
git merge --ff-only fix/deferred-tool-dispatch
GATEWAY_RELEASE_SHA=$(git rev-parse HEAD)
git push origin main
```

Expected: clean branch, green suite, and only reviewed commits ahead of its
approved base. Stop before the `git merge` and `git push` lines until the user
explicitly approves remote mutation. If the root gateway checkout's existing
local commit stack prevents a fast-forward, do not rewrite it; reconcile that
stack and rerun the full gateway suite before continuing.

- [ ] **Step 2: Build the gateway release set**

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway
make package-all VERSION=v3.2.1
make test-windows-package-support
(cd dist && shasum -a 256 -c SHA256SUMS-v3.2.1.txt)
```

Expected: all release archives and checksums exist; the Windows package
support test passes.

- [ ] **Step 3: Rebase, gate, and merge the Hermes contract fix into `base` after approval**

```bash
cd "$HERMES_WT"
git fetch origin base
git rebase origin/base
PYTHON_BIN="$HERMES_PY" scripts/test_workflow_merge_gate.sh --phase base

cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
git switch base
git merge --ff-only fix/deferred-tool-dispatch
HERMES_RELEASE_BASE_SHA=$(git rev-parse HEAD)
git push origin base
```

Expected: the committed feature branch passes the base gate and `base`
fast-forwards to that exact tested commit. Stop before the root-checkout merge
and push until the user explicitly approves them.

- [ ] **Step 4: Stamp and gate isolated `otto` and `loop24` candidates**

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
git worktree add .worktrees/release-otto-v5.5.4 otto
git worktree add .worktrees/release-loop24-v5.5.4 loop24

for BRAND_NAME in otto loop24; do
  BRAND_WT="/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-${BRAND_NAME}-v5.5.4"
  git -C "$BRAND_WT" merge --no-edit "$HERMES_RELEASE_BASE_SHA"
  node "$BRAND_WT/scripts/brand/generate.mjs" "$BRAND_NAME" --write
  git -C "$BRAND_WT" add -A
  if ! git -C "$BRAND_WT" diff --cached --quiet; then
    git -C "$BRAND_WT" commit -m "chore(${BRAND_NAME}): restamp v5.5.4 candidate"
  fi
  node "$BRAND_WT/scripts/brand/generate.mjs" "$BRAND_NAME" --check
  (
    cd "$BRAND_WT"
    PYTHON_BIN="$HERMES_PY" scripts/test_workflow_merge_gate.sh \
      --phase brand \
      --brand "$BRAND_NAME" \
      --tested-base-sha "$HERMES_RELEASE_BASE_SHA"
  )
done
```

Expected: each brand candidate contains the exact tested `base` commit, its
generated overlay is clean, and its brand merge gate passes. Do not involve
literal `main` or resolve unrelated conflicts automatically.

- [ ] **Step 5: Push the gated brand SHAs and dispatch prerelease candidates after approval**

```bash
OTTO_WT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v5.5.4
LOOP24_WT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v5.5.4
OTTO_RELEASE_SHA=$(git -C "$OTTO_WT" rev-parse HEAD)
LOOP24_RELEASE_SHA=$(git -C "$LOOP24_WT" rev-parse HEAD)

git -C "$OTTO_WT" push origin otto
git -C "$LOOP24_WT" push origin loop24

gh workflow run release.yml -R cmetech/otto \
  -f ref="$OTTO_RELEASE_SHA" \
  -f stamp_branch=otto \
  -f version=5.5.4 \
  -f prerelease=true
gh workflow run release.yml -R cmetech/loop24 \
  -f ref="$LOOP24_RELEASE_SHA" \
  -f stamp_branch=loop24 \
  -f version=5.5.4 \
  -f prerelease=true

OTTO_RUN_ID=$(gh run list -R cmetech/otto \
  --workflow release.yml \
  --event workflow_dispatch \
  --limit 1 \
  --json databaseId \
  --jq '.[0].databaseId')
LOOP24_RUN_ID=$(gh run list -R cmetech/loop24 \
  --workflow release.yml \
  --event workflow_dispatch \
  --limit 1 \
  --json databaseId \
  --jq '.[0].databaseId')
gh run watch "$OTTO_RUN_ID" -R cmetech/otto --exit-status
gh run watch "$LOOP24_RUN_ID" -R cmetech/loop24 --exit-status
```

Expected: the release workflows build from immutable source SHAs and publish
v5.5.4 as prerelease candidates for installed UAT. Confirm each selected run's
head SHA matches the corresponding immutable source SHA, then record artifact
names and checksums. Stop before every push and workflow dispatch until the
user explicitly approves publication of prerelease assets.

- [ ] **Step 6: Restore the Hermes checkout to `base`**

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
git switch base
test "$(git branch --show-current)" = "base"
```

Expected: checkout is on `base` even if publication is deferred or aborted.

### Task 7: Perform installed Windows UAT with the original prompt

**Files:** No source changes.

**Interfaces:**

- Consumes: OTTO Gateway v3.2.1 and LOOP24 v5.5.4 candidate on the remote
  Windows server.
- Produces: end-to-end evidence that the connector executes and that the
  separate GitLab ordering fix is finally exercised.

- [ ] **Step 1: Verify installed versions**

```powershell
Invoke-RestMethod 'http://127.0.0.1:18080/api/version' |
    ConvertTo-Json -Compress
```

Expected: `{"version":"3.2.1"}`.

Confirm the LOOP24 About/version surface reports 5.5.4 and the agent log shows
the expected Hermes checkout revision.

- [ ] **Step 2: Start a fresh Desktop conversation**

Do not reuse the prior session. This prevents earlier leaked wrapper text or
tool results from influencing the test.

- [ ] **Step 3: Submit the original regression prompt**

```text
Use only the Ericsson GitLab connector tools—do not use terminal, curl,
browser, or generic web tools. I only know the GitLab group
sd-macs-att-rnam-hosting. Explore it recursively and report the visible
subgroups and projects. Keep the request bounded to max_groups=50 and
max_projects=100. If a connector call fails, report its structured error and
do not substitute another access method.
```

- [ ] **Step 4: Verify the tool sequence in `agent.log`**

The same session id must contain, in order:

```text
tool tool_describe completed
tool tool_call completed
tool gitlab_list_group_projects completed
```

The bridge may log the underlying name rather than a separate outer
`tool_call` completion depending on current lifecycle logging. In that case,
the gateway frames must show native structured `tool_call`, and
`agent.tool_executor` must show `gitlab_list_group_projects` invocation.

Failure conditions:

- the raw fenced wrapper appears as the final assistant response;
- the turn ends after only `tool_describe`;
- no underlying connector invocation is logged; or
- an out-of-scope generic tool runs.

- [ ] **Step 5: Verify the GitLab result separately**

If the connector succeeds, confirm the response includes bounded subgroup and
project data with truncation/continuation metadata as applicable.

If the connector returns an error, capture its category. A connector error no
longer indicates deferred-dispatch failure; diagnose it as a separate GitLab
transport/data issue. Specifically verify that the former unsupported
descendant ordering parameter is absent from the outgoing GitLab request.

- [ ] **Step 6: Promote and publish only after UAT passes**

After the structured call and underlying connector execution are both
evidenced, publish the gateway tag and promote both brand prereleases:

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway
git tag -a v3.2.1 "$GATEWAY_RELEASE_SHA" -m "OTTO Gateway v3.2.1"
git push origin v3.2.1

gh release edit v5.5.4 -R cmetech/otto --prerelease=false --latest
gh release edit v5.5.4 -R cmetech/loop24 --prerelease=false --latest
```

Expected: the gateway tag triggers its existing release workflow; both brand
releases become stable only after UAT. These commands require a separate,
explicit publication approval.

- [ ] **Step 7: Restore the Hermes checkout to `base` and verify**

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
git switch base
git branch --show-current
```

Expected: `base`.

---

## Plan self-review

### Findings coverage

- Explicit describe contract: Task 2.
- Exact dispatcher recognition: Task 3.
- Original OpenAI streaming reproduction: Task 3.
- Scoped execution authority: Task 4.
- Cross-adapter regression protection: Task 5.
- Windows installed proof and GitLab ordering verification: Task 7.
- Prompt-cache stability: enforced by Global Constraints; no dynamic schemas
  are introduced in any task.

### Deferred decisions

The plan is executable as written for the recommended two-layer remediation,
but Tasks 3–7 remain discussion-gated. If the team chooses Hermes-only
hardening, execute Tasks 1–2 and the Hermes portions of Tasks 4–5, then run an
unpublished UAT before deciding whether to resume the gateway tasks.

### Type and naming consistency

- Hermes fields are consistently `invoke_via`, `invoke_arguments`,
  `arguments_schema`, and `next_step`.
- Gateway recognition is consistently named `toolCallDispatcher` and produces
  the existing declared outer name `tool_call`.
- Inner dispatcher arguments remain exactly `name` and `arguments` across
  gateway and Hermes.
