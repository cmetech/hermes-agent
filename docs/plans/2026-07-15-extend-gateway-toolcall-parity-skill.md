# Task: extend `gateway-toolcall-parity` into a two-suite gateway conformance skill

## Your role
You are extending an existing, shipped hermes-agent skill. Work carefully and additively — the current skill is in production (disabled-by-default) across three brand branches. Read the existing code before changing anything; match its conventions exactly. Do NOT reinvent the harness.

## Mission
Today the skill runs ONE kind of test: a black-box **tool-call parity** check (drive a `get_weather` round-trip through the gateway's Anthropic/OpenAI/Ollama surfaces, assert a STRUCTURED tool call, send a result, assert a coherent final answer). We want the SAME skill to ALSO run a broader **conformance** suite modeled on the legacy JS gateway's `test-v1-messages.mjs` (health, model listing, basic + streaming chat, model normalization/auto, validation errors, tool-call robustness edges). Both must be triggerable **from one skill** via either a **CLI flag** or **natural language** to the co-worker agent, sharing one PASS/FAIL scorecard and one exit-code gate.

## Repos, paths, branches (all local, macOS)
- **Skill (shipped, edit here):** `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/skills/gateway-toolcall-parity/`
  - `SKILL.md` (manifest, hermes frontmatter), `run_parity.py` (664-line stdlib-only harness), `selftest.py` (in-process mock-gateway self-test).
  - Repo: `hermes-agent`, remote `git@github.com:cmetech/hermes-agent.git`. Currently on branch `otto`. **Brand-branch flow used previously: `base` → `otto` → `loop24`** (cherry-pick/merge forward). Confirm the exact flow with the human before pushing.
  - Disabled-by-default plumbing already exists: `hermes_cli/capability_staging.py` `seed_brand_defaults()`, and each brand's `curation.skills.disabledByDefault` lists `gateway-toolcall-parity` (`brands/otto.json`, `brands/loop24.json`). The skill NAME is unchanged, so this plumbing keeps working — do not rename the skill.
  - Workspace-only (NOT in git): a Claude Code dev copy at `.claude/skills/gateway-toolcall-parity/{SKILL.md,run_parity.py,selftest.py}` and a `CLAUDE.md`/`AGENTS.md` customization-surface row (keep those two files byte-identical). Update the dev copy + docs row to match.
- **Legacy JS reference suite (port FROM this, but adapt — see gotchas):** `/Users/coreyellis/code/gitlab.rosetta.ericssondevops.com/loop_24/acp_server/test-v1-messages.mjs` (148 lines, Anthropic-surface-only, ~12 checks).
- **OTTO gateway (the SYSTEM UNDER TEST — its code is the source of truth for real wire shapes, NOT the JS suite):** `/Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway/`. When you need to know what a real response looks like, read the Go adapter/render code and its `_test.go` files there — do not assume JS parity.

## READ THESE FIRST (in order)
1. `skills/gateway-toolcall-parity/SKILL.md` — the manifest + how it's run + the PASS matrix + the ACP diagnosis table.
2. `skills/gateway-toolcall-parity/run_parity.py` — the whole harness. Note its shape: env config (`GW_URL` default `http://127.0.0.1:18080`, `JS_GW_URL`, `GW_TIMEOUT=120`, `TOOL_RESULT`); `_require_loopback` guard; `post`/`get`/`_try_json` HTTP helpers; per-surface `*_build1/extract/build2/final` functions; the `SURFACES` spec list; `toolcall_ok`/`final_answer_ok` assertions; the ACP capture + `classify_capture` diagnosis (Track-3a / Track-3b / surfacing-gap); `run_surface` (2-phase round-trip); `reference_toolcall` (optional JS diff); `run_all`/`print_report`/`main` (exit 0 iff all pass).
3. `skills/gateway-toolcall-parity/selftest.py` — how the harness self-validates with in-process mock gateways (two loopback HTTP servers + direct unit-checks of the extractors). Any new check MUST be coverable by an extended mock so `python selftest.py` still prints `SELFTEST OK` with no real gateway.
4. `test-v1-messages.mjs` — the coverage to fold in.
5. The OTTO gateway routes/adapters (see gotchas) to confirm real shapes.

## The JS suite mapped to OTTO — with the gotchas (this mapping is the heart of the task)
`test-v1-messages.mjs` was written against the **JS** gateway with a hardcoded `qwen3-coder-next` model. OTTO is a different Go implementation fronting `kiro-cli` with `model:"auto"`. Port the INTENT, assert OTTO's REAL shapes. Confirmed OTTO facts (verified in the gateway repo):

| # | JS check | OTTO endpoint | Port verdict / gotcha |
|---|---|---|---|
| 1 | `health ok` (pool.alive>0) | `GET /health` ✅ exists (`internal/server/server.go`) | Port. **Verify OTTO's health JSON shape** — the field names may differ from JS (`pool.alive`). Read the healthHandler. |
| 2 | `tags list model` | Ollama `GET /api/tags`; OpenAI `GET /v1/models` | Port, but OTTO lists kiro/`auto` models, NOT `qwen3-coder-next`. Assert the catalog is non-empty and includes the model the other checks use. |
| 3 | `basic non-stream` | `POST /v1/messages` | Port (text block, `stop_reason:"end_turn"`). |
| 4 | `streaming SSE event order` | `/v1/messages` stream:true | Port. **Add analogs:** OpenAI `/v1/chat/completions` SSE + Ollama `/api/chat` NDJSON stream ordering. |
| 5 | `tool_use emitted` | already covered by the current **toolcall** suite | Do not duplicate — reference the existing check. |
| 6 | `tool_result round-trip` | already covered | Do not duplicate. |
| 7 | `model normalize/fallback` | `/v1/messages` with an arbitrary model id | Port (arbitrary model → 200 + text). Verify OTTO's normalization behavior. |
| 8 | `model auto` | `model:"auto"` | Port. |
| 9 | `count_tokens` | **NONE — OUT OF SCOPE on OTTO** (`internal/adapter/anthropic/adapter.go:15`: "count_tokens is out of scope") | **DO NOT port as a live check.** Either omit, or include as an explicit `SKIP`/`N/A` row that asserts OTTO returns 404/not-implemented (decide with the human). Never a FAIL. |
| 10 | `validation: max_tokens` / `empty messages` (400 + Anthropic error envelope) | `/v1/messages` | Port, but **assert OTTO's actual error envelope** (read the anthropic adapter's error rendering + its `_test.go`) — it follows the public Anthropic error shape but confirm `error.type`/`type` fields against OTTO, not JS. Consider per-surface validation analogs (OpenAI/Ollama error shapes differ). |
| 11 | `Write nested fences` (Write tool, content contains ```` ``` ````) | tool-call surfaces | Port as a **tool-call robustness** case (a Write round-trip whose `input.content` contains a nested fence). This is really a toolcall-suite case. |
| 12 | `invented-name remap` (model invents a name → surfaced as the only offered tool) | tool-call surfaces | Port as a toolcall-suite case. OTTO has `pickBestTool` remap in `internal/engine/coerce.go` — assert the surfaced tool is the single offered tool regardless of the name kiro emits. |

Also note the JS suite's own caveat (line 6-7): tool-emission checks can **flake** when the model returns prose instead of a tool call. Carry that forward — see "flakiness" below.

## Target design

### Two suites, one harness, one scorecard
- **`toolcall`** — the CURRENT behavior: per-surface structured `get_weather` round-trip (phase-1 tool call + phase-2 result→final answer) across Anthropic/OpenAI/Ollama, with the ACP-capture diagnosis on failure. PLUS the two tool-call robustness edges (nested-fence Write, invented-name remap) belong here (they're tool-call-specific).
- **`conformance`** — the NEW v1-messages-style functional coverage that is NOT tool-call-specific: health, model listing, basic non-stream, streaming order (all 3 surfaces), model normalize/auto, validation errors. (count_tokens per the gotcha.)
- **`all`** — run both.

Recommended grouping is above; if you disagree on where a check lands, raise it — don't silently regroup.

### A check registry (refactor, don't fork the file)
Introduce a small registry so checks are declarative and filterable rather than hardcoded in `main`. Each check carries: a stable `name`, the `suite(s)` it belongs to (`toolcall`/`conformance`), the `surface(s)` it applies to (`anthropic`/`openai`/`ollama`/`n/a`), and a callable that returns `(pass: bool, detail: str)`. The existing per-surface round-trip becomes the `toolcall` checks; the new ones register alongside. Reuse the existing `post`/`get`/`_try_json`/`_require_loopback` helpers and the surface `*_build*` functions — do not add a second HTTP layer.

### CLI + env selection
- Add argparse: `--suite {toolcall,conformance,all}` (env fallback `GW_SUITE`; pick a sensible default — recommend `toolcall` to preserve today's zero-arg behavior, OR `all`; **confirm the default with the human**), `--surface {anthropic,openai,ollama,all}` (env `GW_SURFACE`, default `all`), plus keep the existing `GW_URL`/`JS_GW_URL`/`GW_TIMEOUT`/`TOOL_RESULT` envs. Preserve the exit-code contract: **0 iff every selected check passes, non-zero otherwise** (so it still gates releases). `--list` should print the available checks and their suite/surface tags.
- Keep it stdlib-only. Keep the loopback-only guard for BOTH gateway URLs.

### Natural-language dispatch (this is what makes it "one skill, two tests, via NL")
The co-worker agent decides which invocation to run by reading `SKILL.md`. Add an explicit **intent→command** table to `SKILL.md` so the agent maps phrasing to the CLI, e.g.:
- "run the tool-call parity check" / "check structured tool calls" → `python run_parity.py --suite toolcall`
- "run the full gateway conformance / v1-messages suite" / "regression-test the gateway" → `python run_parity.py --suite conformance`
- "run everything / full parity gate" → `python run_parity.py --suite all`
- "…on the Anthropic surface only" → append `--surface anthropic`
- "…and diff against the JS gateway" → set `JS_GW_URL=…` (reference mode).
Keep the descriptions concrete and unambiguous so the agent picks the right one without guessing.

## Requirements (numbered)
1. **Additive & faithful.** Preserve the current `toolcall` behavior and output exactly; the existing three-surface round-trip and its ACP diagnosis must still work identically when `--suite toolcall` (or the chosen default) is selected.
2. **Port the JS cases per the mapping table**, asserting OTTO's real shapes (verify each against the OTTO gateway repo / its adapter `_test.go`, not the JS assertions). Skip count_tokens per the gotcha. Use `model:"auto"` (or OTTO's real model ids from `/api/tags` / `/v1/models`), never `qwen3-coder-next`.
3. **Cross-surface generalization** where it's meaningful: streaming-order checks for all three surfaces (SSE for Anthropic/OpenAI, NDJSON for Ollama); validation-error checks per surface with each surface's real error envelope; model listing via both `/api/tags` and `/v1/models`. Where a JS check is genuinely Anthropic-only, keep it Anthropic-only and say so.
4. **ACP diagnosis** already exists for tool-call failures — keep it wired for the toolcall suite. For conformance failures where an ACP frame would help (e.g., a streaming or tool edge), reuse `classify_capture`; otherwise a clear assertion detail is enough. Don't force ACP diagnosis onto checks it can't explain.
5. **Flakiness discipline:** tool-emission and model-behavior checks can flake (model returns prose). Add a bounded retry (e.g., N attempts) for the model-dependent checks, and print flaky-vs-hard-fail clearly so a single prose response doesn't read as a real regression. Deterministic checks (health, validation, tags, streaming framing) get no retry.
6. **Reference-diff mode:** extend `reference_toolcall`-style JS diffing to the conformance checks that the JS gateway actually implements (health, tags, basic, streaming, model, validation, count_tokens) when `JS_GW_URL` is set — so "parity" covers the broader suite too. Checks with no JS analog just skip the diff.
7. **Constraints (unchanged):** stdlib-only Python (HTTP client + JSON only — no new deps), loopback-only, the skill only exercises the gateway over HTTP and never modifies it, disabled-by-default (do not touch the seed plumbing beyond leaving the skill name intact).
8. **selftest.py:** extend the in-process mock gateways to serve the new endpoints/shapes (`/health`, `/api/tags`, `/v1/models`, streaming frames, validation 400s, count_tokens-not-implemented) and add direct unit-checks for every new assertion helper, so `python selftest.py` prints `SELFTEST OK` with NO real gateway. This is the primary way you prove the extension works locally.
9. **SKILL.md manifest:** update `description`/body to describe BOTH suites, the CLI flags, and the NL intent→command table; keep the hermes frontmatter (`name`, `version` bump, `platforms`, `metadata.hermes.tags` — add `conformance`/`regression`/`v1-messages` tags) and the disabled-by-default note. Update the PASS-matrix and diagnosis sections to cover the new checks.
10. **Dev copy + docs:** mirror all skill changes into the workspace `.claude/skills/gateway-toolcall-parity/` copy, and add one dated row to `CLAUDE.md` AND `AGENTS.md` (kept byte-identical) documenting this extension.

## Acceptance criteria (definition of done)
- `python selftest.py` → `SELFTEST OK`, exit 0, covering every new check via mocks.
- `python run_parity.py --list` prints all checks with suite/surface tags.
- `python run_parity.py --suite toolcall` reproduces today's behavior (same checks, same scorecard semantics, same exit-code gate).
- `python run_parity.py --suite conformance` and `--suite all` run the new coverage; `--surface X` filters; exit code is 0 iff all selected checks pass.
- Every assertion is grounded in OTTO's real response shape (cite the gateway file you verified against in your report). count_tokens is never a hard FAIL.
- SKILL.md's NL intent table is unambiguous enough that a co-worker agent picks the right invocation from a plain-English request.
- Manual live run against a real OTTO gateway (started with a real `kiro-cli`, `ACP_CAPTURE=true`) on `127.0.0.1` — report the scorecard per suite honestly, including any flaky model-dependent checks.

## Decisions to confirm with the human BEFORE building (don't guess)
1. **Default `--suite`** when run with no args — preserve today's zero-arg behavior (`toolcall`), or make it `all`?
2. **count_tokens:** omit entirely, or keep as an explicit `SKIP`/`not-implemented` assertion row?
3. **Cross-surface breadth:** port the Anthropic-only JS checks to OpenAI/Ollama analogs now, or keep conformance Anthropic-first and expand later?
4. **Brand-branch push flow** (`base`→`otto`→`loop24`) and whether to bump the skill `version` minor or major.

## Commit / branch mechanics
- Work on `otto` (or `base` first if that's the brand-flow root — confirm). Keep commits scoped: (a) harness refactor + new checks, (b) selftest extension, (c) SKILL.md + brand/docs. Do NOT commit the `.claude/` dev copy or `CLAUDE.md`/`AGENTS.md` if they live outside the git repo (they're workspace-only saved files) — save them but don't stage.
- Push via the repo's normal remote (`origin` = `git@github.com:cmetech/hermes-agent.git`). Forward-port to the other brand branches per the established flow. Confirm before pushing.

Deliver a short report: what checks you added, the JS→OTTO shape decisions (with the gateway file you verified each against), the selftest output, and the per-suite live scorecard.
