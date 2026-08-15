# Desktop Tool-Choice Dedicated RPC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/tool-choice required --otto-v1` a first-class Hermes Desktop command that configures the exact backend session through a dedicated RPC and ship the correction in both branded v5.8.1 releases.

**Architecture:** Add `tool_choice.configure` beside the existing TUI gateway tool-choice helper and keep `OneShotToolChoice` as backend-owned session state. Register `/tool-choice` as a Desktop RPC command, retain the established missing-method fallback to `slash.exec`, and repair the adjacent error-unmasking matcher so `command.dispatch` routing noise cannot hide the original execution failure.

**Tech Stack:** Python 3.11, JSON-RPC registry in `tui_gateway`, React/TypeScript, Vitest, pytest, GitHub Actions branded release workflows.

## Global Constraints

- Start and finish development from this fork's `base`; literal `main` remains synchronization-only.
- Preserve all unrelated tracked and untracked files and stage only exact task files.
- Keep `OneShotToolChoice` session-scoped and consume it exactly once through the existing prompt lifecycle.
- Do not append messages, mutate the system prompt/history, change selected models, or alter the OTTO/Gateway v1 wire contract.
- `tool_choice.configure` is intentionally distinct from the unrelated `tools.configure` toolset endpoint.
- Keep the existing `/tool-choice` `slash.exec` handler for compatibility.
- Follow strict RED/GREEN TDD for every production change.
- Publish production v5.8.1 for every descriptor-backed brand only after base and brand gates pass.

---

### Task 1: Add the Session-Scoped Tool-Choice RPC

**Files:**
- Modify: `tui_gateway/methods_tools.py` beside `_handle_tool_choice_control` and before `slash.exec`
- Test: `tests/tui_gateway/test_tool_choice_control.py`

**Interfaces:**
- Consumes: `_sess_nowait(params, rid)`, `_handle_tool_choice_control(session, arguments)`, `_ok`, and `_err`
- Produces: JSON-RPC method `tool_choice.configure` accepting `{"session_id": str, "arguments": str}` and returning `{"output": str}`

- [ ] **Step 1: Write failing RPC behavior tests**

Add tests that register two session dictionaries, invoke the real method registry, and prove the requested session alone receives the one-shot value:

```python
def test_tool_choice_configure_rpc_scopes_and_consumes_one_shot():
    from tui_gateway.methods_prompt import _consume_session_tool_choice
    from tui_gateway import server

    server._sessions["desktop-a"] = {}
    server._sessions["desktop-b"] = {}
    try:
        response = server._methods["tool_choice.configure"](
            "rpc-1",
            {
                "session_id": "desktop-a",
                "arguments": "required --otto-v1",
            },
        )

        assert response["result"] == {
            "output": "Next turn tool choice: required with OTTO v1."
        }
        first = _consume_session_tool_choice(server._sessions["desktop-a"])
        assert first.policy.mode == "required"
        assert first.otto_contract_version == "v1"
        assert _consume_session_tool_choice(server._sessions["desktop-a"]) is None
        assert _consume_session_tool_choice(server._sessions["desktop-b"]) is None
    finally:
        server._sessions.pop("desktop-a", None)
        server._sessions.pop("desktop-b", None)
```

Add a validation test asserting invalid arguments return code `4004` with the parser's exact message and do not arm the session:

```python
def test_tool_choice_configure_rpc_preserves_parser_validation():
    from tui_gateway.methods_prompt import _consume_session_tool_choice
    from tui_gateway import server

    server._sessions["desktop-invalid"] = {}
    try:
        response = server._methods["tool_choice.configure"](
            "rpc-2",
            {"session_id": "desktop-invalid", "arguments": "named"},
        )

        assert response["error"] == {
            "code": 4004,
            "message": "Usage: /tool-choice named <tool> [--otto-v1]",
        }
        assert _consume_session_tool_choice(server._sessions["desktop-invalid"]) is None
    finally:
        server._sessions.pop("desktop-invalid", None)
```

- [ ] **Step 2: Run the focused RED test**

Run:

```bash
scripts/run_tests.sh tests/tui_gateway/test_tool_choice_control.py -q
```

Expected: FAIL because `_methods` has no `tool_choice.configure` entry.

- [ ] **Step 3: Implement the minimal RPC**

Add the method beside the existing helper:

```python
@method("tool_choice.configure")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err

    arguments = params.get("arguments", "")
    if not isinstance(arguments, str):
        return _err(rid, 4004, "tool choice arguments must be a string")
    try:
        output = _handle_tool_choice_control(session, arguments)
    except ValueError as exc:
        return _err(rid, 4004, str(exc))
    return _ok(rid, {"output": output})
```

- [ ] **Step 4: Run focused and adjacent GREEN tests**

Run:

```bash
scripts/run_tests.sh tests/tui_gateway/test_tool_choice_control.py tests/tui_gateway/test_protocol.py -q
python -m compileall -q tui_gateway agent/tool_choice_control.py
```

Expected: all selected tests pass and compilation exits zero.

- [ ] **Step 5: Inspect and commit only the RPC slice**

Run:

```bash
git diff -- tui_gateway/methods_tools.py tests/tui_gateway/test_tool_choice_control.py
git diff --check
git add -- tui_gateway/methods_tools.py tests/tui_gateway/test_tool_choice_control.py
git commit -m "feat(tui): add tool choice configuration RPC"
```

---

### Task 2: Route Desktop Tool Choice Through the Dedicated RPC

**Files:**
- Modify: `apps/desktop/src/lib/desktop-slash-commands.ts`
- Test: `apps/desktop/src/lib/desktop-slash-commands.test.ts`
- Test: `apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx`

**Interfaces:**
- Consumes: Task 1's `tool_choice.configure` RPC and the existing `rpc(...)`/`runRpc(...)` Desktop surface
- Produces: first-class `/tool-choice` command with alias `/tool_choice`, mixed argument mode, and RPC parameters `{session_id, arguments}`

- [ ] **Step 1: Write the failing Desktop registry test**

Add this assertion to the curation suite:

```typescript
it('routes /tool-choice through its dedicated session RPC', () => {
  const surface = resolveDesktopCommand('/tool-choice')?.surface

  expect(surface?.kind).toBe('rpc')
  if (surface?.kind !== 'rpc') return

  expect(surface.rpc).toBe('tool_choice.configure')
  expect(
    surface.buildParams({
      arg: 'required --otto-v1',
      command: '/tool-choice required --otto-v1',
      name: 'tool-choice',
      sessionId: 'desktop-session'
    })
  ).toEqual({
    arguments: 'required --otto-v1',
    session_id: 'desktop-session'
  })
  expect(resolveDesktopCommand('/tool_choice')?.name).toBe('/tool-choice')
  expect(desktopSlashCommandArgumentMode('/tool-choice')).toBe('mixed')
  expect(isDesktopSlashSuggestion('/tool-choice')).toBe(true)
  expect(isDesktopSlashSuggestion('/tool_choice')).toBe(false)
})
```

- [ ] **Step 2: Write failing current-backend and fallback routing tests**

In the prompt-actions suite, use the existing `Harness` and `renderedSeedTexts` helpers. The current-backend test must assert the exact call order and forbid generic routes:

```typescript
it('configures one-shot tool choice through the dedicated RPC', async () => {
  const calls: { method: string; params?: Record<string, unknown> }[] = []
  const seeds: Record<string, unknown>[] = []
  const requestGateway = vi.fn(async (method: string, params?: Record<string, unknown>) => {
    calls.push({ method, params })
    if (method === 'tool_choice.configure') {
      return { output: 'Next turn tool choice: required with OTTO v1.' } as never
    }
    throw new Error(`unexpected method: ${method}`)
  })

  let handle: HarnessHandle | null = null
  await actRender(
    <Harness
      onReady={h => (handle = h)}
      onSeedState={s => seeds.push(s)}
      refreshSessions={async () => undefined}
      requestGateway={requestGateway}
    />
  )

  await handle!.submitText('/tool-choice required --otto-v1')

  expect(calls).toEqual([
    {
      method: 'tool_choice.configure',
      params: {
        arguments: 'required --otto-v1',
        session_id: expect.any(String)
      }
    }
  ])
  expect(renderedSeedTexts(seeds)).toContainEqual(
    expect.stringContaining('Next turn tool choice: required with OTTO v1.')
  )
})
```

Add a compatibility test where `tool_choice.configure` throws `method not found`, `slash.exec` returns the confirmation, and `command.dispatch` is never called.

- [ ] **Step 3: Run the focused Desktop RED tests**

Run:

```bash
npm --prefix apps/desktop test -- --run src/lib/desktop-slash-commands.test.ts src/app/session/hooks/use-prompt-actions/index.test.tsx
```

Expected: registry and dedicated-path assertions fail because `/tool-choice` is still treated as an extension and routed to `slash.exec`.

- [ ] **Step 4: Add the minimal Desktop registry row**

Add this entry among backend RPC commands:

```typescript
{
  name: '/tool-choice',
  description: 'Set one-shot tool policy for the next turn',
  aliases: ['/tool_choice'],
  surface: rpc('tool_choice.configure', ctx => ({
    arguments: ctx.arg,
    session_id: ctx.sessionId
  })),
  argumentMode: 'mixed'
},
```

Do not add a new dispatcher branch; the existing `runRpc` and `renderRpcResult` paths own execution and rendering.

- [ ] **Step 5: Run focused and adjacent Desktop GREEN tests**

Run:

```bash
npm --prefix apps/desktop test -- --run src/lib/desktop-slash-commands.test.ts src/app/session/hooks/use-prompt-actions/index.test.tsx src/app/chat/composer/hooks/use-slash-completions.test.tsx
npm --prefix apps/desktop run typecheck
```

Expected: selected Vitest files and all three TypeScript projects pass.

- [ ] **Step 6: Inspect and commit only the Desktop RPC slice**

Run:

```bash
git diff -- apps/desktop/src/lib/desktop-slash-commands.ts apps/desktop/src/lib/desktop-slash-commands.test.ts apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx
git diff --check
git add -- apps/desktop/src/lib/desktop-slash-commands.ts apps/desktop/src/lib/desktop-slash-commands.test.ts apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx
git commit -m "fix(desktop): route tool choice through dedicated RPC"
```

---

### Task 3: Preserve the Original Slash Execution Error

**Files:**
- Modify: `apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts`
- Test: `apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx`

**Interfaces:**
- Consumes: `slashExecError` captured by `runExec` and the backend's current `not a quick/plugin/bundle/skill command` message
- Produces: compatibility matcher accepting both old and current routing-noise strings while rendering the original `slash.exec` error

- [ ] **Step 1: Change only the existing test fixture to the current backend text**

Update the existing test title and mocked `command.dispatch` error:

```typescript
it('surfaces slash.exec failure when command.dispatch only adds current routing noise', async () => {
  // Existing setup remains unchanged.
  if (method === 'command.dispatch') {
    throw new Error('not a quick/plugin/bundle/skill command: debug')
  }
  // Existing assertions continue requiring "slash worker timed out" and
  // forbidding the command.dispatch message.
})
```

- [ ] **Step 2: Run the focused RED test**

Run:

```bash
npm --prefix apps/desktop test -- --run src/app/session/hooks/use-prompt-actions/index.test.tsx -t "current routing noise"
```

Expected: FAIL because the rendered output contains `not a quick/plugin/bundle/skill command` instead of `slash worker timed out`.

- [ ] **Step 3: Extend the matcher minimally**

Replace the stale matcher with one accepting both backend generations:

```typescript
if (slashExecError && /not a quick\/plugin\/(?:bundle\/)?skill command/i.test(dispatchMessage)) {
```

- [ ] **Step 4: Run focused and full prompt-action GREEN tests**

Run:

```bash
npm --prefix apps/desktop test -- --run src/app/session/hooks/use-prompt-actions/index.test.tsx -t "current routing noise"
npm --prefix apps/desktop test -- --run src/app/session/hooks/use-prompt-actions/index.test.tsx
```

Expected: the focused regression and complete prompt-action file pass.

- [ ] **Step 5: Inspect and commit only the error-unmasking slice**

Run:

```bash
git diff -- apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx
git diff --check
git add -- apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx
git commit -m "fix(desktop): preserve slash execution failures"
```

---

### Task 4: Verify, Merge, and Publish v5.8.1

**Files:**
- Verify only: all changed files from Tasks 1-3
- Release inputs: `brands/otto.json`, `brands/loop24.json`, and each releases-only repository's `release.yml`

**Interfaces:**
- Consumes: tested feature SHA, repository merge gates, brand generator, OTTO/LOOP24 release workflows
- Produces: pushed `base`, `otto`, and `loop24` refs plus production `v5.8.1` releases with verified assets and digests

- [ ] **Step 1: Run focused cross-surface verification**

Run:

```bash
scripts/run_tests.sh tests/tui_gateway/test_tool_choice_control.py tests/tui_gateway/test_protocol.py -q
npm --prefix apps/desktop test -- --run src/lib/desktop-slash-commands.test.ts src/app/session/hooks/use-prompt-actions/index.test.tsx src/app/chat/composer/hooks/use-slash-completions.test.tsx
npm --prefix apps/desktop run typecheck
npm --prefix apps/desktop run lint
.venv/bin/python -m compileall -q agent gateway hermes_cli tui_gateway
git diff --check
git status --short --branch
```

Expected: every command exits zero and only the known unrelated untracked files remain.

- [ ] **Step 2: Run the complete Desktop and repository gates**

Run:

```bash
npm --prefix apps/desktop test
scripts/run_tests.sh
PYTHON_BIN="$PWD/.venv/bin/python" scripts/test_workflow_merge_gate.sh --phase base
```

Expected: all suites pass and the base gate prints the same SHA returned by `git rev-parse HEAD` as `TESTED_BASE_SHA`.

- [ ] **Step 3: Review the final feature range and merge into base**

Run:

```bash
HERMES_V581_BASE_SHA="$(git rev-parse HEAD)"
git log --oneline base.."$HERMES_V581_BASE_SHA"
git diff --stat base..."$HERMES_V581_BASE_SHA"
git switch base
git merge --ff-only fix/desktop-tool-choice-rpc-v5.8.1
git push origin base
test "$(git rev-parse origin/base)" = "$HERMES_V581_BASE_SHA"
```

Verify the pushed `origin/base` SHA equals the tested feature SHA before continuing.

- [ ] **Step 4: Merge tested base into every descriptor-backed brand and gate each**

Use the existing clean brand worktrees and the tested base SHA captured in Step 3:

```bash
HERMES_V581_ROOT="/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent"
HERMES_V581_OTTO_WT="$HERMES_V581_ROOT/.worktrees/release-otto-v5.6.0"
HERMES_V581_LOOP24_WT="$HERMES_V581_ROOT/.worktrees/release-loop24-v5.6.0"

git -C "$HERMES_V581_OTTO_WT" status --short --branch
git -C "$HERMES_V581_OTTO_WT" merge --no-edit "$HERMES_V581_BASE_SHA"
(cd "$HERMES_V581_OTTO_WT" && node scripts/brand/generate.mjs otto --write)
(cd "$HERMES_V581_OTTO_WT" && node scripts/brand/generate.mjs otto --check)
(cd "$HERMES_V581_OTTO_WT" && PYTHON_BIN="$HERMES_V581_ROOT/.venv/bin/python" scripts/test_workflow_merge_gate.sh --phase brand --brand otto --tested-base-sha "$HERMES_V581_BASE_SHA")
git -C "$HERMES_V581_OTTO_WT" diff --check
git -C "$HERMES_V581_OTTO_WT" status --short --branch

git -C "$HERMES_V581_LOOP24_WT" status --short --branch
git -C "$HERMES_V581_LOOP24_WT" merge --no-edit "$HERMES_V581_BASE_SHA"
(cd "$HERMES_V581_LOOP24_WT" && node scripts/brand/generate.mjs loop24 --write)
(cd "$HERMES_V581_LOOP24_WT" && node scripts/brand/generate.mjs loop24 --check)
(cd "$HERMES_V581_LOOP24_WT" && PYTHON_BIN="$HERMES_V581_ROOT/.venv/bin/python" scripts/test_workflow_merge_gate.sh --phase brand --brand loop24 --tested-base-sha "$HERMES_V581_BASE_SHA")
git -C "$HERMES_V581_LOOP24_WT" diff --check
git -C "$HERMES_V581_LOOP24_WT" status --short --branch
```

Regeneration is expected to be idempotent. If it changes files, inspect the exact names and stage only those names before a brand-specific restamp commit. Push `otto` and `loop24` only after both gates print their exact `TESTED_BRAND_SHA` values:

```bash
HERMES_V581_OTTO_SHA="$(git -C "$HERMES_V581_OTTO_WT" rev-parse HEAD)"
HERMES_V581_LOOP24_SHA="$(git -C "$HERMES_V581_LOOP24_WT" rev-parse HEAD)"
git -C "$HERMES_V581_OTTO_WT" push origin otto
git -C "$HERMES_V581_LOOP24_WT" push origin loop24
```

- [ ] **Step 5: Dispatch and monitor production v5.8.1 workflows**

Run with the exact tested brand SHAs:

```bash
gh workflow run release.yml -R cmetech/otto -f ref="$HERMES_V581_OTTO_SHA" -f version=5.8.1 -f stamp_branch=otto -f prerelease=false
gh workflow run release.yml -R cmetech/loop24 -f ref="$HERMES_V581_LOOP24_SHA" -f version=5.8.1 -f stamp_branch=loop24 -f prerelease=false
```

Capture the two returned run URLs, extract their numeric run IDs, and monitor those exact IDs with `gh run watch` and `--exit-status`.

- [ ] **Step 6: Verify releases and restore the checkout**

For both repositories, require:

- tag `v5.8.1`;
- `draft=false` and `prerelease=false`;
- release body names the exact OTTO or LOOP24 SHA captured above and the correct self-update branch;
- seven uploaded assets matching the established macOS ARM64 DMG/ZIP plus blockmaps and Windows x64 EXE/MSI plus blockmap set;
- nonzero sizes and `sha256:` digests for every asset.

Then delete the merged local feature branch, switch the primary checkout to `base`, and verify:

```bash
git branch --show-current
git status --short --branch
git ls-remote origin refs/heads/base refs/heads/otto refs/heads/loop24
```

Expected: current branch is exactly `base`, tracked state is clean, all unrelated untracked content remains, and remote refs equal the tested SHAs.
