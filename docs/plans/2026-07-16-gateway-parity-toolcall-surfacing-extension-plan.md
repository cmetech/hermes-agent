# Gateway-parity tool-call-surfacing + identity coverage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the shipped `gateway-toolcall-parity` skill (v1.1.0 → v1.2.0) so it reproduces the two live loop24 defects the current suite misses: kiro tool calls leaking as `[tool: …]` narration (surfacing-gap), and kiro persona bleed.

**Architecture:** Three additive changes to the stdlib-only harness `run_parity.py`: (1) a `[tool: …]` leak predicate wired as a first-class hard-FAIL into every tool-call check; (2) new `toolcall-exec:<surface>` checks that offer a `run_shell` tool + a "run this" prompt; (3) a `identity:openai` conformance check. Each is proven offline by new mock-gateway modes in `selftest.py` — which is the test harness (TDD loop = add selftest mode+assertions → `python selftest.py` fails → implement in `run_parity.py` → passes).

**Tech Stack:** Python 3 stdlib only (`urllib`, `json`, `http.server` in selftest). No new dependencies. No gateway changes.

**Spec:** `docs/plans/2026-07-16-gateway-parity-toolcall-surfacing-extension.md`

## Global Constraints

- **Stdlib-only.** No third-party imports in `run_parity.py` / `selftest.py`. Do NOT add `import re` — use lowercase substring checks (match the existing `_WEATHER_HINTS` / `client_shows_bracket_tool` style).
- **Loopback-only** guard (`_require_loopback`) stays; do not touch it.
- **Additive.** Do not rename existing checks, functions, or the skill. `curation.skills.disabledByDefault` keys off the skill NAME — leave it `gateway-toolcall-parity`.
- **Exit-code gate unchanged:** exit 0 iff every selected non-SKIP check passes; `flaky` only controls retries + the `FAIL*` display marker.
- **All new tool-call / identity checks are `flaky=True`, `js=False`** (model-dependent, no legacy-JS analog).
- **Two byte-identical copies:** the git-tracked `hermes-agent/skills/gateway-toolcall-parity/{run_parity.py,selftest.py,SKILL.md}` and the workspace dev copy `.claude/skills/gateway-toolcall-parity/{…}`. Every change to one is mirrored to the other (Task 5).
- **Branch model:** author on `base`, then merge `base` → every brand in `brands/*.json` (`otto`, `loop24`) and gate each (Task 6). Never author brand-specific.
- **Surface keys** are exactly `("anthropic", "openai", "ollama")` = `SURFACE_KEYS`.
- **Working file:** `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/skills/gateway-toolcall-parity/` (git repo `hermes-agent`, start on branch `base`). Run all commands from that skill directory.

---

## File Structure

- Modify: `skills/gateway-toolcall-parity/run_parity.py` — add leak predicate, exec checks, identity check, registry entries (Tasks 1-3).
- Modify: `skills/gateway-toolcall-parity/selftest.py` — add `leak` / `identity_bleed` mock modes + good-mode `run_shell` handling + assertions (Tasks 1-3).
- Modify: `skills/gateway-toolcall-parity/SKILL.md` — document the three checks; bump version (Task 4).
- Mirror: `.claude/skills/gateway-toolcall-parity/{run_parity.py,selftest.py,SKILL.md}` (Task 5).
- Modify: `CLAUDE.md` + `AGENTS.md` (workspace root) — update the customization-surface row (Task 5).

---

### Task 1: `[tool: …]` leak guard — first-class hard FAIL on every tool-call check

**Files:**
- Modify: `run_parity.py` (add predicate near `final_answer_ok`; edit `run_surface`; edit `check_nested_fence_anthropic` + `check_invented_name_anthropic`)
- Test: `selftest.py` (new `leak` mode + assertion group)

**Interfaces:**
- Produces: `_bracket_tool_leak(text: str) -> bool`; module constants `_LEAK_MARKER`, `_LEAK_DETAIL`; `run_surface` result dict gains key `"observed_text"`.
- Consumes: existing `_client_observed_text(spec, resp)`, `fetch_capture`, `classify_capture`, `format_frame`, `anthropic_final`.

- [ ] **Step 1: Write the failing test — add a `leak` mock mode + assertions to `selftest.py`**

In `selftest.py`, inside `_make_handler`'s `do_POST`, add a `leak`-mode short-circuit right after the existing `if mode == "prose":` block (around line 144):

```python
            if mode == "leak":
                return self._send_json(self._leak(path))
```

Add the `_leak` builder method next to `_prose` (after the `_prose` method, ~line 243):

```python
        # --- leak-mode (surfacing-gap: [tool: …] narration, no structured call) ---
        def _leak(self, path):
            txt = "[tool: execute] I'll run that for you."
            if path == "/v1/messages":
                return {"id": "msg_l", "type": "message", "role": "assistant", "model": "auto",
                        "stop_reason": "end_turn", "content": [{"type": "text", "text": txt}]}
            if path == "/v1/chat/completions":
                return {"id": "cl", "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": txt}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True, "message": {"role": "assistant", "content": txt}}
            return {"error": "not found"}
```

In `main()`, add the server next to the others (after `cipher_srv, cipher_url = _serve("cipher")`, ~line 263):

```python
    leak_srv, leak_url = _serve("leak")
```

Add this assertion group inside the `try:` block, before the `finally:` (after group 7, ~line 375):

```python
        # 8) leak gateway ([tool: …] narration, no structured call) → every
        #    toolcall check hard-FAILs with the surfacing-gap diagnosis, exit 1.
        results, code = rp.run_selected("toolcall", "all", leak_url, "", retries=1)
        try:
            _check(code == 1, "leak/toolcall → exit 1")
            for k in ("anthropic", "openai", "ollama"):
                r = next(x for x in results if x["name"] == f"toolcall:{k}")
                _check("surfacing-gap" in r["detail"],
                       f"leak/toolcall:{k} → surfacing-gap (got: {r['detail'][:70]})")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1
```

Add `leak_srv.shutdown()` to the `finally:` block (next to `cipher_srv.shutdown()`):

```python
        leak_srv.shutdown()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python selftest.py`
Expected: `SELFTEST FAILED` — group 8 fails because the leak detail is currently classified as `track-3a`/`inconclusive`, not `surfacing-gap` (the leak guard doesn't exist yet).

- [ ] **Step 3: Add the leak predicate to `run_parity.py`**

Immediately after `final_answer_ok` (ends ~line 439), add:

```python
_LEAK_MARKER = "[tool:"


def _bracket_tool_leak(text: str) -> bool:
    """True iff assistant text carries a `[tool: …]` tool-call NARRATION leak —
    kiro's tool call rendered as prose instead of structured tool_calls."""
    return _LEAK_MARKER in (text or "").lower()


_LEAK_DETAIL = (
    "surfacing-gap: kiro tool call leaked as '[tool: …]' narration instead of "
    "structured tool_calls"
)
```

- [ ] **Step 4: Wire the leak short-circuit into `run_surface`**

In `run_surface`, add `"observed_text": "",` to the `result` dict initializer (next to `"http": None,`). Then replace the phase-1 extraction block:

```python
    tc = spec["extract"](resp)
    result["tool_call"] = tc
    result["phase1"] = toolcall_ok(tc)

    if not result["phase1"]:
        observed = _client_observed_text(spec, resp)
        enabled, frames, note = fetch_capture(gw_url, cursor)
```

with:

```python
    tc = spec["extract"](resp)
    result["tool_call"] = tc
    observed = _client_observed_text(spec, resp)
    result["observed_text"] = observed

    # A `[tool: …]` leak is a hard surfacing-gap FAIL even if a call is present.
    if _bracket_tool_leak(observed):
        enabled, frames, note = fetch_capture(gw_url, cursor)
        _, _, frame = classify_capture(frames, observed)
        result["phase1"] = False
        result["diagnosis"] = {
            "code": "surfacing-gap",
            "message": _LEAK_DETAIL,
            "capture_note": note,
            "capture_enabled": enabled,
            "first_frame": format_frame(frame) if frame else None,
            "observed_text": (observed or "")[:200],
        }
        return result

    result["phase1"] = toolcall_ok(tc)

    if not result["phase1"]:
        enabled, frames, note = fetch_capture(gw_url, cursor)
```

(Note: `observed` is now computed once above; the fail branch reuses it — remove its old `observed = _client_observed_text(spec, resp)` line, which is the first line of the old `if not result["phase1"]:` body.)

- [ ] **Step 5: Add the leak guard to the two Anthropic robustness edges**

In `check_nested_fence_anthropic`, immediately after the `status, resp, raw = post(...)` line and before `tc = anthropic_extract(resp)`:

```python
    if _bracket_tool_leak(anthropic_final(resp)):
        return False, _LEAK_DETAIL
```

Add the identical two lines in `check_invented_name_anthropic`, after its `post(...)` and before `tc = anthropic_extract(resp)`.

- [ ] **Step 6: Run the test to verify it passes**

Run: `python selftest.py`
Expected: `SELFTEST OK` (group 8 now sees `surfacing-gap` in every `toolcall:*` detail).

- [ ] **Step 7: Commit**

```bash
git add skills/gateway-toolcall-parity/run_parity.py skills/gateway-toolcall-parity/selftest.py
git commit -m "feat(skills): gateway-parity [tool: …] leak guard (first-class surfacing-gap FAIL)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01SLTDpPxSEcgErA7TsaBFh2"
```

---

### Task 2: `toolcall-exec:<surface>` — execute-tool surfacing checks

**Files:**
- Modify: `run_parity.py` (exec tool defs + `_exec_check` factory + registry)
- Test: `selftest.py` (good-mode `run_shell` handling + assertions; reuse Task 1's `leak` mode)

**Interfaces:**
- Consumes: `SURFACE_BY_KEY`, `_client_observed_text`, `_bracket_tool_leak`, `_LEAK_DETAIL`, `capture_cursor`, `fetch_capture`, `classify_capture`, `format_frame`, `post`, `ANTHROPIC_VERSION`, `SURFACE_KEYS`.
- Produces: `_exec_check(surface_key) -> fn`; registry checks `toolcall-exec:{anthropic,openai,ollama}`; constants `_EXEC_TOOL_ANTHROPIC`, `_EXEC_FUNCTION_TOOL`, `EXEC_TOOL_BY_SURFACE`, `EXEC_PROMPT`.

- [ ] **Step 1: Write the failing test — good-mode `run_shell` handling + assertions in `selftest.py`**

In `_make_handler`'s `do_POST`, in the `if tools:` block (~line 171), add a `run_shell` branch BEFORE the `write_file` branch:

```python
            if tools:
                if "run_shell" in tools:
                    return self._send_json(self._exec_toolcall(path))
                if "write_file" in tools:
                    return self._send_json(self._write_toolcall())
```

Add the `_exec_toolcall` builder next to `_write_toolcall` (~line 208):

```python
        def _exec_toolcall(self, path):
            args = {"command": "echo hi"}
            if path == "/v1/messages":
                return {"id": "msg_e", "type": "message", "role": "assistant", "model": "auto",
                        "stop_reason": "tool_use", "content": [
                            {"type": "tool_use", "id": "toolu_e", "name": "run_shell", "input": args}]}
            if path == "/v1/chat/completions":
                return {"id": "ce", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_e", "type": "function", "function": {
                            "name": "run_shell", "arguments": json.dumps(args)}}]}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True, "message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "run_shell", "arguments": args}}]}}
            return {"error": "not found"}
```

In `main()`, extend the good-mode toolcall group (group 2, ~line 286) — after the existing loop that asserts `toolcall:{k}`, add assertions for the exec checks. Replace group 2's body with:

```python
        results, code = rp.run_selected("toolcall", "all", good_url, "", retries=1)
        try:
            _check(code == 0, "good/toolcall → exit 0")
            for k in ("anthropic", "openai", "ollama"):
                r = next(x for x in results if x["name"] == f"toolcall:{k}")
                _check(r["ok"], f"good/toolcall:{k} PASS")
                e = next(x for x in results if x["name"] == f"toolcall-exec:{k}")
                _check(e["ok"], f"good/toolcall-exec:{k} PASS (got: {e['detail'][:60]})")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1
```

Extend Task 1's leak group (group 8) to also assert the exec checks FAIL on leak — add inside its `for k` loop, after the `toolcall:{k}` assertion:

```python
                e = next(x for x in results if x["name"] == f"toolcall-exec:{k}")
                _check("surfacing-gap" in e["detail"],
                       f"leak/toolcall-exec:{k} → surfacing-gap (got: {e['detail'][:70]})")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python selftest.py`
Expected: `SELFTEST FAILED` — `next(...)` for `toolcall-exec:{k}` raises `StopIteration` (the checks aren't registered yet), failing groups 2 and 8.

- [ ] **Step 3: Add exec tool defs + prompt to `run_parity.py`**

After `_WRITE_TOOL_ANTHROPIC` (ends ~line 205), add:

```python
_EXEC_TOOL_ANTHROPIC = {
    "name": "run_shell",
    "description": "Run a shell command and return its output",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}
_EXEC_FUNCTION_TOOL = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": "Run a shell command and return its output",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
}
EXEC_TOOL_BY_SURFACE = {
    "anthropic": _EXEC_TOOL_ANTHROPIC,
    "openai": _EXEC_FUNCTION_TOOL,
    "ollama": _EXEC_FUNCTION_TOOL,
}
EXEC_PROMPT = "Run this command and show me its exact output: echo hi"
```

- [ ] **Step 4: Add the `_exec_check` factory**

After `_toolcall_check` (ends ~line 754), add:

```python
def _exec_check(surface_key):
    """Offer a single run_shell tool + an execute prompt; assert a STRUCTURED
    run_shell call (not `[tool: …]` narration). Reproduces the live desktop
    surfacing gap the get_weather round-trip missed. Single-phase."""
    spec = SURFACE_BY_KEY[surface_key]
    tool = EXEC_TOOL_BY_SURFACE[surface_key]

    def fn(gw_url):
        cursor = capture_cursor(gw_url)
        msg = {"role": "user", "content": EXEC_PROMPT}
        if surface_key == "anthropic":
            path, headers = "/v1/messages", {"anthropic-version": ANTHROPIC_VERSION}
            body = {"model": "auto", "max_tokens": 256, "messages": [msg], "tools": [tool]}
        elif surface_key == "openai":
            path, headers = "/v1/chat/completions", {}
            body = {"model": "auto", "messages": [msg], "tools": [tool]}
        else:  # ollama
            path, headers = "/api/chat", {}
            body = {"model": "auto", "stream": False, "messages": [msg], "tools": [tool]}

        status, resp, raw = post(gw_url + path, body, headers)
        if status == 0:
            return False, f"connection failed: {raw}"

        observed = _client_observed_text(spec, resp)
        if _bracket_tool_leak(observed):
            return False, f"{_LEAK_DETAIL} (HTTP {status}, text={observed[:80]!r})"

        tc = spec["extract"](resp)
        if tc and (tc.get("name") or "").strip().lower() == "run_shell":
            return True, f"structured run_shell call surfaced (args={tc.get('args')})"

        enabled, frames, note = fetch_capture(gw_url, cursor)
        code, human, frame = classify_capture(frames, observed)
        parts = [f"HTTP {status}", f"no structured run_shell call (got {tc})",
                 f"diagnosis[{code}]: {human}"]
        if note:
            parts.append(f"capture: {note}")
        if frame:
            parts.append(f"frame: {format_frame(frame)}")
        return False, " | ".join(parts)

    return fn
```

- [ ] **Step 5: Register the exec checks**

In `build_registry`, immediately after the two `toolcall-*` robustness-edge appends (~line 1030), add:

```python
    # execute-tool surfacing (model-dependent) — reproduces the [tool: …] leak
    for k in SURFACE_KEYS:
        reg.append(Check(f"toolcall-exec:{k}", "toolcall", k, _exec_check(k), flaky=True))
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python selftest.py`
Expected: `SELFTEST OK` (exec checks PASS in good mode, surfacing-gap FAIL in leak mode).

- [ ] **Step 7: Verify the checks are listed**

Run: `python run_parity.py --suite toolcall --list`
Expected: output includes `toolcall-exec:anthropic`, `toolcall-exec:openai`, `toolcall-exec:ollama` with `flaky`.

- [ ] **Step 8: Commit**

```bash
git add skills/gateway-toolcall-parity/run_parity.py skills/gateway-toolcall-parity/selftest.py
git commit -m "feat(skills): gateway-parity toolcall-exec:<surface> execute-tool surfacing checks

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01SLTDpPxSEcgErA7TsaBFh2"
```

---

### Task 3: `identity:openai` — persona-bleed conformance check

**Files:**
- Modify: `run_parity.py` (`_has_persona_bleed`, `check_identity_openai`, registry)
- Test: `selftest.py` (`identity_bleed` mode + assertions)

**Interfaces:**
- Consumes: `post`, `openai_final`.
- Produces: `_has_persona_bleed(text) -> bool`; `check_identity_openai(gw_url) -> (bool, str)`; constant `_IDENTITY_BLEED`; registry check `identity:openai`.

- [ ] **Step 1: Write the failing test — `identity_bleed` mode + assertions in `selftest.py`**

Make good-mode identity clean and bleed-mode dirty by parameterizing `_basic`. Replace the first line of `_basic` (`def _basic(self, path):`) body so it selects the text by mode:

```python
        def _basic(self, path):
            text = ("I am the Kiro CLI; running Hermes skills requires the Hermes agent."
                    if mode == "identity_bleed" else HELLO_REPLY)
```

Then replace the three `HELLO_REPLY` references inside `_basic` with `text`. (The `_basic` method now returns `text` on each surface.)

In `main()`, add the server after `leak_srv` (~line 264):

```python
    identity_srv, identity_url = _serve("identity_bleed")
```

Add this assertion group before `finally:` (after group 8):

```python
        # 9) identity — good gateway PASSES; a "Kiro CLI / requires the Hermes
        #    agent" reply FAILS identity:openai.
        results, _ = rp.run_selected("conformance", "openai", good_url, "", retries=1)
        try:
            r = next(x for x in results if x["name"] == "identity:openai")
            _check(r["ok"], f"good/identity:openai PASS (got: {r['detail'][:60]})")
        except (AssertionError, StopIteration) as e:
            print(f"  FAIL: {e}"); failures += 1

        results, code = rp.run_selected("conformance", "openai", identity_url, "", retries=1)
        try:
            r = next(x for x in results if x["name"] == "identity:openai")
            _check(not r["ok"], "bleed/identity:openai FAIL")
            _check("persona bleed" in r["detail"],
                   f"bleed/identity:openai → persona bleed (got: {r['detail'][:70]})")
        except (AssertionError, StopIteration) as e:
            print(f"  FAIL: {e}"); failures += 1
```

Add `identity_srv.shutdown()` to `finally:` (next to `leak_srv.shutdown()`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `python selftest.py`
Expected: `SELFTEST FAILED` — group 9 fails (`identity:openai` not registered → `StopIteration`).

- [ ] **Step 3: Add the persona-bleed predicate + check to `run_parity.py`**

After `_basic_ollama` / the `_BASIC = {...}` line (~line 881), add:

```python
_IDENTITY_BLEED = ("kiro cli", "requires the hermes agent")


def _has_persona_bleed(text: str) -> bool:
    """True iff the reply leaks kiro's built-in identity or a hallucinated
    capability boundary (fail-OPEN: only these specific signals trip it)."""
    low = (text or "").lower()
    if any(s in low for s in _IDENTITY_BLEED):
        return True
    return "belongs to" in low and "agent environment" in low


def check_identity_openai(gw_url):
    body = {"model": "auto",
            "messages": [{"role": "user", "content": "Who are you? Answer in one sentence."}]}
    status, resp, raw = post(gw_url + "/v1/chat/completions", body)
    if status != 200:
        return False, f"HTTP {status}: {raw[:160]}"
    txt = openai_final(resp)
    if not txt:
        return False, f"200 but empty message.content: {raw[:160]}"
    if _has_persona_bleed(txt):
        return False, f"persona bleed / capability-boundary refusal: {txt[:160]!r}"
    return True, f"identity clean: {txt[:80]!r}"
```

- [ ] **Step 4: Register the identity check**

In `build_registry`, in the conformance section after the `model-normalize:anthropic` append (~line 1043), add:

```python
    # persona / identity bleed (model-dependent, OpenAI surface = desktop path)
    reg.append(Check("identity:openai", "conformance", "openai", check_identity_openai, flaky=True))
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python selftest.py`
Expected: `SELFTEST OK`.

- [ ] **Step 6: Commit**

```bash
git add skills/gateway-toolcall-parity/run_parity.py skills/gateway-toolcall-parity/selftest.py
git commit -m "feat(skills): gateway-parity identity:openai persona-bleed check

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01SLTDpPxSEcgErA7TsaBFh2"
```

---

### Task 4: Document the new checks + bump version (`SKILL.md`)

**Files:**
- Modify: `skills/gateway-toolcall-parity/SKILL.md`

- [ ] **Step 1: Bump the version**

Change the frontmatter line `version: 1.1.0` to `version: 1.2.0`.

- [ ] **Step 2: Document the checks in the "What PASS means" section**

In the `## What PASS means` section, after the **toolcall** paragraph (ends with "…is a **FAIL**."), add:

```markdown
**toolcall-exec** (per surface): a **structured** `run_shell(command)` call in response to an
execute prompt — same shapes as above. A `[tool: …]` narration marker anywhere in the
assistant text is a **hard surfacing-gap FAIL** on this and every tool-call check (the leak
guard), even if a structured call is also present.
```

In the **conformance** paragraph list, append:

```markdown
`identity:openai` sends a benign "who are you?" turn and FAILS if the reply self-identifies as
"Kiro CLI" or claims host tools/skills "require the Hermes agent" (persona bleed).
```

- [ ] **Step 3: Add natural-language → command rows**

In the `## Natural-language → command` table, add these rows before the final row:

```markdown
| "check for the `[tool: …]` narration leak" / "run the execute-tool surfacing check" | `python run_parity.py --suite toolcall` |
| "check the model's identity / persona" / "is it leaking the Kiro persona?" | `python run_parity.py --suite conformance --surface openai` |
```

- [ ] **Step 4: Verify SKILL.md frontmatter still parses**

Run: `python run_parity.py --list`
Expected: runs without error (SKILL.md is not parsed by the harness, but this confirms the skill dir is intact); no traceback.

- [ ] **Step 5: Commit**

```bash
git add skills/gateway-toolcall-parity/SKILL.md
git commit -m "docs(skills): gateway-parity v1.2.0 — document exec + leak-guard + identity checks

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01SLTDpPxSEcgErA7TsaBFh2"
```

---

### Task 5: Sync the workspace dev copy + customization-surface docs

**Files:**
- Overwrite: `.claude/skills/gateway-toolcall-parity/{run_parity.py,selftest.py,SKILL.md}` (workspace root, NOT in git)
- Modify: `CLAUDE.md` + `AGENTS.md` (workspace root) — the gateway-parity surface row

- [ ] **Step 1: Copy the three files to the dev copy**

Run (from the workspace root `/Users/coreyellis/code/github.com/cmetech/otto_hermes`):

```bash
cp hermes-agent/skills/gateway-toolcall-parity/run_parity.py .claude/skills/gateway-toolcall-parity/run_parity.py
cp hermes-agent/skills/gateway-toolcall-parity/selftest.py   .claude/skills/gateway-toolcall-parity/selftest.py
cp hermes-agent/skills/gateway-toolcall-parity/SKILL.md      .claude/skills/gateway-toolcall-parity/SKILL.md
```

- [ ] **Step 2: Verify byte-identical**

Run:

```bash
for f in run_parity.py selftest.py SKILL.md; do
  cmp hermes-agent/skills/gateway-toolcall-parity/$f .claude/skills/gateway-toolcall-parity/$f && echo "$f IN SYNC"
done
```

Expected: three `… IN SYNC` lines.

- [ ] **Step 3: Verify the dev copy self-tests**

Run: `cd .claude/skills/gateway-toolcall-parity && python selftest.py && cd -`
Expected: `SELFTEST OK`.

- [ ] **Step 4: Update the customization-surface row in `CLAUDE.md`**

Find the paragraph documenting the gateway-toolcall-parity skill (search for "two-suite conformance skill (v1.1.0"). Append a sentence recording v1.2.0:

```markdown
**Extended to cover tool-call surfacing + identity (v1.2.0, 2026-07-16):** added
`toolcall-exec:<surface>` (offer a `run_shell` tool + execute prompt, assert a STRUCTURED
call), a first-class `[tool: …]` leak guard that hard-FAILs every tool-call check with a
`surfacing-gap` diagnosis, and `identity:openai` (FAIL on "Kiro CLI" / "requires the Hermes
agent" persona bleed) — reproducing a live loop24 OpenAI-surface gap the get_weather suite
missed. Still stdlib-only, loopback-only, disabled-by-default; selftest.py gains `leak` +
`identity_bleed` mock modes (`SELFTEST OK` with no real gateway). Keep the `.claude/skills/`
dev copy byte-identical. Spec/plan: `docs/plans/2026-07-16-gateway-parity-toolcall-surfacing-extension*.md`.
```

- [ ] **Step 5: Mirror the same edit into `AGENTS.md`**

Apply the identical addition to the workspace-root `AGENTS.md` (same location).

- [ ] **Step 6: Verify the pair is identical**

Run: `cmp CLAUDE.md AGENTS.md && echo "CLAUDE/AGENTS IN SYNC"`
Expected: `CLAUDE/AGENTS IN SYNC`.

- [ ] **Step 7: Commit the tracked docs**

The `.claude/` dev copy is not in the hermes-agent repo (workspace root isn't a git repo) — only `CLAUDE.md`/`AGENTS.md` need no git commit here (workspace root is not a repo). If the workspace root later becomes tracked, commit there. No `hermes-agent` commit is produced by this task; it updates workspace files only.

---

### Task 6: Merge to brands + gate + push

**Files:** none edited — this task merges `base` into each brand and runs gates.

**Interfaces:**
- Consumes: the committed `base` changes from Tasks 1-4.

- [ ] **Step 1: Confirm `base` is green**

Run (from `hermes-agent/skills/gateway-toolcall-parity/`):

```bash
git -C ../.. rev-parse --abbrev-ref HEAD    # expect: base
python selftest.py                          # expect: SELFTEST OK
```

- [ ] **Step 2: Discover the brand list**

Run (from `hermes-agent/`):

```bash
ls brands/*.json | xargs -n1 basename | sed 's/\.json$//' | grep -vE '^(_|schema$)'
```

Expected: `loop24` and `otto`.

- [ ] **Step 3: Merge `base` into each brand and gate**

For each brand `BR` from Step 2, run (from `hermes-agent/`):

```bash
git checkout $BR
git merge base --no-edit
node scripts/brand/generate.mjs $BR --check          # expect: 9/9 OK (no emitter touched)
python skills/gateway-toolcall-parity/selftest.py    # expect: SELFTEST OK
git rev-parse $BR:skills/gateway-toolcall-parity/run_parity.py \
  == $(git rev-parse base:skills/gateway-toolcall-parity/run_parity.py) 2>/dev/null || true
```

Expected per brand: clean merge (or trivial), `9/9 OK`, `SELFTEST OK`. The skill blob should match `base` (identical skill content across brands).

- [ ] **Step 4: Verify no branding regressed on either brand**

For each brand `BR`, run:

```bash
git checkout $BR
node scripts/brand/generate.mjs $BR --check    # 9/9 OK
```

Expected: `9/9 OK` on both.

- [ ] **Step 5: Return to `otto` (workspace end state)**

```bash
git checkout otto
git status --porcelain    # expect: empty (clean tree)
```

- [ ] **Step 6: Push — REQUIRES HUMAN CONFIRMATION (outward-facing)**

Do NOT push automatically. Present the commit list per branch and ask the user to confirm. Only after explicit approval:

```bash
git push origin base
git push origin otto
git push origin loop24
```

Expected: three fast-forward pushes. If any is not fast-forward, STOP and report.

---

## Self-Review

- **Spec coverage:** ✔ `toolcall-exec:<surface>` (Task 2), ✔ `[tool: …]` leak guard on every tool-call check (Task 1: run_surface + both robustness edges; Task 2: exec check), ✔ `identity:openai` (Task 3), ✔ selftest mocks for all (Tasks 1-3), ✔ SKILL.md + version bump (Task 4), ✔ dev-copy + CLAUDE/AGENTS sync (Task 5), ✔ base→otto/loop24 merge + `--check` 9/9 + selftest gates (Task 6). The spec's "single-phase exec" and "fail-open identity" are honored.
- **Placeholder scan:** no TBD/TODO; every code step shows complete code.
- **Type consistency:** `_bracket_tool_leak(text)` used identically in run_surface, `_exec_check`, and both robustness edges; `run_surface` result key `"observed_text"` added in Task 1 and not consumed elsewhere (informational). `_exec_check` uses `SURFACE_BY_KEY`/`EXEC_TOOL_BY_SURFACE` defined in Task 2. `check_identity_openai` uses `openai_final` (existing). Registry names `toolcall-exec:{k}` / `identity:openai` match the selftest `next(...)` lookups exactly.
- **Flakiness caveat** carried from spec: a single green run isn't proof; the leak guard makes any observed `[tool:` a hard classified FAIL.
