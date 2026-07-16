# Design: `gateway-toolcall-parity` — tool-call-surfacing + identity coverage (v1.2.0)

- **Date:** 2026-07-16
- **Status:** Approved design — ready for implementation plan
- **Skill:** `skills/gateway-toolcall-parity/` (shipped, disabled-by-default, on `base` → `otto` → `loop24`)
- **Prior art:** `docs/plans/2026-07-15-extend-gateway-toolcall-parity-skill.md` (the two-suite extension)

## Motivation — a real gap the current suite misses

A loop24 desktop chat (OpenAI surface, `plugins/model-providers/otto/__init__.py:38` → gateway
`http://127.0.0.1:18080/v1`) could not run **any** tool from chat. Two live symptoms were
captured:

1. **`[tool: execute]` leaked as prose.** Asking the agent to run `python -c "print(2+2)"`
   produced literal text `"[tool: execute] [tool: execute] …"` followed by a hallucinated
   "shell execution is blocked" and a from-memory answer. This is the **surfacing-gap /
   Track-3b** failure (SKILL.md failure-diagnosis table): kiro's ACP `tool_call` /
   `tool_call_chunk` frames were rendered as `[tool: …]` narration on the OpenAI surface
   instead of being surfaced as structured `tool_calls`, so the Hermes agent's
   `code_execution` tool never fired.
2. **kiro persona bleed.** "List your skills" returned the enabled skill list but the model
   self-identified as "this **Kiro CLI** session" that can help with "coding, file
   operations, **AWS**, and terminal tasks" and claimed "loading and executing Hermes skills
   **requires the Hermes agent**." kiro's built-in identity overrode the OTTO/Hermes framing
   and it hallucinated a capability boundary that does not exist.

**Why the existing suite missed #1:** a full `--suite all` run scored `18/19` with only
`model-normalize` failing — i.e. `toolcall:openai` **passed**. The current tool-call checks
drive a single `get_weather` round-trip with a weather prompt, which kiro surfaces cleanly.
The failing condition uses an **execute/shell-shaped tool** and a "run this" prompt, and the
`[tool: …]` leak is today only a *post-hoc diagnosis* (`run_parity.py` `classify_capture`,
`client_shows_bracket_tool` ~line 553) reached **after** a check has already failed on other
grounds — never a first-class assertion. So a leak that coincides with a structured call, or
a run where the harness's favorable single-tool setup surfaces fine, is not caught.

## Goals

- Reproduce the execute-tool surfacing condition the live chat hit (not just `get_weather`).
- Promote the `[tool: …]` narration leak to a **first-class hard FAIL** on every tool-call
  check, with a clear surfacing-gap detail.
- Add a lightweight, non-brittle guard against the kiro identity/persona bleed.
- Stay within the harness's existing conventions: stdlib-only, loopback-only, one check
  registry, one exit-code gate, both suites (`toolcall` / `conformance`), `--list`/`--suite`/
  `--surface` filters, `flaky`-retry for model-dependent checks. No new dependencies.

## Non-goals

- No gateway changes. The gateway (`otto_app/otto-gateway`) is reference-only from this
  workspace. Its fixes are handed off via a separate LLM prompt (see "Companion deliverable").
- No attempt to reproduce the *full* desktop toolset or system prompt. We add a focused
  execute-tool check, not a faithful desktop replica (YAGNI; would be brittle/flaky).
- No change to the disabled-by-default plumbing or the skill name (keeps
  `curation.skills.disabledByDefault` seeding working).

## The three additions

### 1. `toolcall-exec:<surface>` — execute-tool surfacing check (toolcall suite, flaky)

- **All three surfaces:** `toolcall-exec:anthropic`, `toolcall-exec:openai`,
  `toolcall-exec:ollama`. `flaky=True` (model-dependent), retried. `js=False` (no JS analog;
  keeps it independent of `JS_GW_URL`).
- Offers exactly **one** tool, `run_shell(command: string)` — the code_execution/"execute"-
  shaped tool that leaked. Reuses each surface's request-envelope convention (the `tools`
  block shape already used by the `get_weather` builders).
- **Prompt:** an unambiguous execute intent, e.g. *"Run this command and show me its exact
  output: `echo hi`"*.
- **Single-phase** (assert the request→structured-call step only; no result round-trip). The
  round-trip adds flakiness without adding surfacing signal, and the surfacing gap is fully
  observable in phase one.
- **PASS** iff the surface returns a **structured** tool call whose name is `run_shell`
  (generalized from the get_weather-specific `toolcall_ok`; see "Harness wiring").
- **HARD FAIL** if the assistant text carries the `[tool:` leak (see #2) — checked first, so a
  leak is reported as a surfacing-gap even when a structured call is also present.
- **FAIL** (no leak, no structured call) with a detail that names the observed shape (prose /
  `[tool: …]` / wrong tool), reusing the existing diagnosis vocabulary.

### 2. `[tool: …]` leak guard — first-class assertion on every tool-call check

- New helper `_bracket_tool_leak(resp, surface) -> bool`: extracts the assistant's visible
  text for that surface (via the existing client-observed-text path used by
  `classify_capture`) and returns `True` iff it contains `[tool:` (case-insensitive).
- Wired into **both** the existing `get_weather` tool-call checks (`_toolcall_check`) **and**
  the new `toolcall-exec` checks: if `_bracket_tool_leak` is true → immediate hard FAIL with
  detail `"surfacing-gap: kiro tool call leaked as '[tool: …]' narration instead of
  structured tool_calls"`. This is the assertion that would have caught the live
  `[tool: execute]`.
- The existing `classify_capture` post-hoc diagnosis is unchanged and still runs on failure
  (it adds the ACP-frame Track-3a/3b classification on top of this assertion).

### 3. `identity:openai` — persona-bleed check (conformance suite, flaky)

- One benign non-stream turn on `/v1/chat/completions`: *"Who are you? Answer in one
  sentence."* `flaky=True`, `js=False`.
- **FAIL** iff the reply matches, case-insensitively, any of:
  `kiro cli` · `requires the hermes agent` · `belongs to .*\bagent environment\b`.
- **PASS** otherwise — fail-open on everything else. Only the specific bleed strings trip it,
  so brand/persona wording changes don't cause false positives.
- Rationale: surface-independent defect, but scoped to `openai` (the desktop path) to keep it
  minimal; more surfaces can be added later if needed.

## Harness wiring (`run_parity.py`)

Additive, matching existing conventions:

- **Tool spec:** add a reusable `run_shell` tool schema alongside the inline `get_weather`
  one. Prefer a small `_toolcall_request(surface, tools, prompt)` assembler (or per-surface
  `*_exec_build()` mirroring `*_build1()`) so the exec check shares envelope logic with the
  existing builders rather than duplicating it.
- **Generalized extraction:** the per-surface `*_extract` helpers already return the first
  tool_call; add/extend a normalized accessor that also exposes the call **name** so an
  expected-name assertion (`get_weather` vs `run_shell`) works without cloning each extractor.
- **`_bracket_tool_leak(resp, surface)`** as above, reusing the surface's assistant-text
  extraction (the same path `classify_capture` consumes via the client-observed text).
- **Check fns:** `_exec_check(surface)` factory (parallels `_toolcall_check(surface)`);
  `check_identity_openai(gw_url)`.
- **Registry (`build_registry`):** append `toolcall-exec:{anthropic,openai,ollama}` to the
  toolcall suite and `identity:openai` to the conformance suite, all `flaky=True`,
  `js=False`. Exit-code gating is unchanged (exit 0 iff every selected non-SKIP check passes;
  `flaky` only controls retries + the `FAIL*` marker).

## `selftest.py`

Add in-process OTTO-shaped mock gateways so the new checks are proven with **no real
gateway** and `python selftest.py` still prints `SELFTEST OK`:

- **exec PASS:** mock returns a structured `run_shell` tool call on each surface → asserts
  `toolcall-exec:*` PASS and the leak guard stays quiet.
- **exec FAIL (leak):** mock returns assistant text containing `[tool: run_shell]` and no
  structured call → asserts `toolcall-exec:*` and the existing `toolcall:*` checks hard-FAIL
  with the surfacing-gap detail.
- **identity PASS:** benign "I'm your assistant." reply → `identity:openai` PASS.
- **identity FAIL:** "I am the Kiro CLI; that requires the Hermes agent." → `identity:openai`
  FAIL.

## `SKILL.md` and dev-copy sync

- Document the three checks in the check tables + the "What PASS means" section, and add
  natural-language rows: "check for the `[tool: …]` narration leak" / "run the execute-tool
  surfacing check" → `--suite toolcall`; "check the model's identity / persona" →
  `--suite conformance`.
- Bump `version: 1.1.0 → 1.2.0`.
- Keep the workspace dev copy byte-identical: update
  `.claude/skills/gateway-toolcall-parity/{run_parity.py,selftest.py,SKILL.md}` to match, and
  update the `CLAUDE.md`/`AGENTS.md` customization-surface row for this skill (both files
  byte-identical per the workspace sync rule).

## Branch / merge placement

Brand-neutral fork content → author on **`base`**, then merge `base` into every brand
discovered from `brands/*.json` (`otto`, `loop24` today) and run each brand's gates:

- `node scripts/brand/generate.mjs <brand> --check` → **9/9 OK** (no emitter touched).
- `python selftest.py` → `SELFTEST OK` on each brand tree.
- The skill files are identical across brands (verified: `git diff base otto -- skills/…`
  is empty), so the merge is a clean forward.

## Verification plan

1. `python selftest.py` → `SELFTEST OK` (covers all new checks offline).
2. Against a running gateway started with `ACP_CAPTURE=true`:
   `python run_parity.py --suite all` — new checks appear in the matrix; on the current
   (unfixed) gateway, expect `toolcall-exec:openai`/`:ollama` and/or the leak guard to FAIL
   with the surfacing-gap detail and `identity:openai` to FAIL, formally reproducing the live
   chat symptoms. `toolcall-exec:anthropic` may PASS.
3. `python run_parity.py --suite toolcall --surface openai` a few times to characterize the
   leak as flaky vs. hard.
4. Post-gateway-fix: the same commands read all-PASS (0 `[tool:` leaks; identity clean).

## Caveats (carried into the spec deliberately)

- **Flakiness:** the tool-call and identity checks are model-dependent. A single green run is
  not proof the gap is closed; the leak guard's value is that *any* observed `[tool:` is now a
  hard, classified FAIL rather than a silent pass.
- **Deployed-copy staleness (separate but adjacent):** the loop24 box runs a pre-`c30ad6b2f`
  `run_parity.py` (the `model-normalize` 500 false-FAIL). Refresh
  `%LOCALAPPDATA%\loop24\skills\gateway-toolcall-parity\run_parity.py` from the backend clone
  before running, independent of this change.

## Companion deliverable (not part of the suite code)

A comprehensive LLM prompt for the otto-gateway session to fix the two defects on the
gateway side: (a) convert kiro ACP `tool_call`/`tool_call_chunk` → structured
`tool_calls` on `/v1/chat/completions` (and the OpenAI streaming surface) so nothing leaks as
`[tool: …]`; (b) compose the system prompt so kiro does not self-identify as "Kiro CLI" or
deny capabilities. Includes the two live transcripts as acceptance evidence and
`run_parity.py --suite all` (this extension) as the regression gate. Delivered as a handoff
document; the gateway is not modified from this workspace.
