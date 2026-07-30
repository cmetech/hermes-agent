# Workflow Language Phase 2: Structured Data and Typed Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Archon `output_format` and `output_type` enforceable, canonical, atomic, recoverable contracts while preserving the exact `hermes-legacy` behavior.

**Architecture:** Add a generic structured-output contract beside provider/runtime resolution, thread it through the isolated plugin-agent and tested transports, and let the workflow plugin bind that contract at admission and execution. Successful Archon nodes produce one immutable primary-output candidate; `RunStore.complete_node()` verifies the active winner and publishes the content/metadata bundle before journaling and projection. Evidence, authenticated artifact APIs, and Desktop consume only bounded, backend-confirmed publication descriptors.

**Tech Stack:** Python 3.11+, immutable dataclasses, `jsonschema` Draft 2020-12 validation, OpenAI Chat Completions/Responses and Anthropic transport adapters, SQLite + JSONL journal + atomic filesystem publication, FastAPI/Pydantic, Electron React/TypeScript, TanStack Query, Vitest/Testing Library, repository test runner.

## Global Constraints

- All new workflow semantics are gated by `WorkflowLanguageProfile.ARCHON_2026_07`; `HERMES_LEGACY` remains byte- and behavior-compatible.
- Keep `WORKFLOW_NORMALIZER_VERSION = 1` readable for admitted runs and introduce version 2 for newly normalized Archon definitions; never reinterpret a version-1 snapshot.
- Never add schema content to a live system prompt or mutate past conversation messages. Prompt adaptation appends a deterministic block to the initial user message only.
- Provider native support is opt-in and transport-tested. API-mode resemblance, custom endpoints, aggregators, and community model metadata cannot promote a route to native support.
- `jsonschema` stays optional under the existing extras policy. Workflows without `output_format` work without it; structured workflows fail closed with the established install guidance.
- Repair is one fresh, one-turn, action-free isolated request. It has no tools, hooks, MCP, skills, subagents, delegation, fallback, history, system override, persistent session, or fresh budget allowance.
- Canonical JSON is a complete single value, UTF-8, sorted keys, compact separators, finite numbers only, no trailing newline, and no more than 500,000 bytes.
- Typed publication is winner-only and store-owned. The scheduler/executor may create an attempt-local candidate, but only `RunStore.complete_node()` may publish it.
- Publication paths use opaque IDs; API callers never provide filesystem paths.
- Use `scripts/run_tests.sh` for every Python test command; never invoke `pytest` directly.
- Preserve user changes outside this feature, especially `docs/reviews/` in the shared checkout.
- Commit each task atomically with the commit message specified below.

## File Map

### New files

- `agent/structured_output.py` — generic immutable schema contract, capability decision, parser, validator, canonicalizer, and prompt adapter.
- `plugins/workflow/output_resolution.py` — Archon primary-output candidate and shared downstream resolver with legacy compatibility adapters.
- `tests/agent/test_structured_output.py` — generic schema, capability, parsing, validation, and canonicalization contracts.
- `tests/plugins/workflow/test_structured_output_language.py` — Archon normalization, bounds, references, and static field-reference checks.
- `tests/plugins/workflow/test_typed_publication.py` — winner-only bundles, all output-producing node kinds, and mirror behavior.
- `tests/plugins/workflow/test_typed_publication_recovery.py` — injected crash boundaries, orphan cleanup, corroborated rebuild, and integrity failure.
- `apps/desktop/src/app/workflows/typed-artifact-view.tsx` — backend-confirmed typed artifact metadata, preview, and download UI.
- `apps/desktop/src/app/workflows/typed-artifact-view.test.tsx` — typed rendering and compatibility/failure tests.

### Modified generic agent/provider files

- `providers/base.py`
- `plugins/model-providers/anthropic/__init__.py`
- `hermes_cli/runtime_provider.py`
- `agent/plugin_agent.py`
- `agent/plugin_agent_worker.py`
- `agent/agent_init.py`
- `agent/chat_completion_helpers.py`
- `agent/transports/chat_completions.py`
- `agent/transports/codex.py`
- `agent/transports/anthropic.py`
- `agent/codex_responses_adapter.py`
- `agent/anthropic_adapter.py`

### Modified workflow/store/API/Desktop/docs files

- `plugins/workflow/language.py`
- `plugins/workflow/language_schema.py`
- `plugins/workflow/schema.py`
- `plugins/workflow/compat.py`
- `plugins/workflow/runner_binding.py`
- `plugins/workflow/executors/base.py`
- `plugins/workflow/executors/ai.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/store.py`
- `plugins/workflow/evidence.py`
- `plugins/workflow/dashboard/plugin_api.py`
- `apps/desktop/src/hermes.ts`
- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/src/app/workflows/run-inspector.tsx`
- `apps/desktop/src/app/workflows/index.test.tsx`
- `apps/desktop/src/hermes.test.ts`
- `website/docs/user-guide/features/workflow-yaml-reference.md`
- `skills/software-development/workflow-builder/references/portable-schema.md`

---

## Task 1: Build the bounded generic JSON Schema contract

**Files:**

- Create: `agent/structured_output.py`
- Create: `tests/agent/test_structured_output.py`
- Test: `tests/agent/test_plugin_agent.py`

- [x] Add failing tests for Draft 2020-12 schema normalization, canonical equivalence, and every approved bound.

  Cover 65,536 canonical schema bytes, depth 32, 4,096 traversed nodes, 1,024 properties, 256 local refs, 1,024 bytes per regex, 16,384 total regex bytes, 1,024 enum values, non-finite numerics, invalid regex, external/unresolved/cyclic refs, `$dynamicRef`, `$id`, and anchors that change resolution scope. Assert a missing `jsonschema` import raises `StructuredOutputValidatorUnavailable` only when validation is requested.

  Run: `scripts/run_tests.sh tests/agent/test_structured_output.py`

  Expected: FAIL because `agent.structured_output` does not exist.

- [x] Implement the immutable generic value objects and constants in `agent/structured_output.py`.

  Use this public shape:

  ```python
  class StructuredOutputStrategy(str, Enum):
      NATIVE_JSON_SCHEMA = "native_json_schema"
      NATIVE_JSON_MODE = "native_json_mode"
      PROMPT_JSON_SCHEMA = "prompt_json_schema"
      UNSUPPORTED = "unsupported"

  @dataclass(frozen=True, slots=True)
  class StructuredOutputSchema:
      canonical_schema: Mapping[str, object]
      schema_fingerprint: str
      canonical_schema_bytes: bytes
      dialect: str = "https://json-schema.org/draft/2020-12/schema"

  @dataclass(frozen=True, slots=True)
  class StructuredOutputRequest:
      schema: StructuredOutputSchema
      strategy: StructuredOutputStrategy
      adapter_version: int
      output_bytes_limit: int = 500_000
      canonicalization_version: int = 1

  @dataclass(frozen=True, slots=True)
  class StructuredOutputValue:
      value: object
      canonical_bytes: bytes
      sha256: str
      media_type: str = "application/json"
      canonicalization_version: int = 1
  ```

  Freeze nested schema structures, reject booleans where integer bounds are expected, walk iteratively with explicit counters, validate local JSON Pointers below `$defs`, and compile every pattern before accepting the schema.

- [x] Add failing parser/canonicalizer tests for prose, fenced JSON, two values, trailing non-space content, NaN/Infinity, refusal text, truncation, and outputs over 500,000 bytes.

  Run: `scripts/run_tests.sh tests/agent/test_structured_output.py`

  Expected: FAIL on the new complete-value and canonicalization cases.

- [x] Implement `parse_validate_canonicalize(response: str, request: StructuredOutputRequest) -> StructuredOutputValue` and `validation_summary(...)`.

  Decode exactly one full JSON value with `json.JSONDecoder.raw_decode`, require only trailing whitespace, reject non-finite numbers with `parse_constant`, validate using `Draft202012Validator`, then encode using `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`, and `allow_nan=False`. Bound diagnostics to 16,384 UTF-8 bytes without persisting the raw response.

- [x] Run the focused generic suite and commit.

  Run: `scripts/run_tests.sh tests/agent/test_structured_output.py tests/agent/test_plugin_agent.py`

  Commit: `feat(agent): add bounded structured output contract`

## Task 2: Normalize Archon schemas and prove direct field references

**Files:**

- Modify: `plugins/workflow/language.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/schema.py`
- Create: `tests/plugins/workflow/test_structured_output_language.py`
- Modify: `tests/plugins/workflow/test_language.py`
- Modify: `tests/plugins/workflow/test_language_schema.py`
- Modify: `skills/software-development/workflow-builder/references/portable-schema.md`

- [x] Add failing tests that `archon-2026-07` accepts valid `output_format` and `output_type`, stores the normalized schema/fingerprint in the language snapshot, and stops emitting `archon_output_format_unavailable` and `archon_output_type_unavailable`.

  Also assert legacy still emits `legacy_output_format_post_validation` and `legacy_output_type_not_published`, and that a version-1 admitted snapshot remains readable.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_structured_output_language.py tests/plugins/workflow/test_language.py tests/plugins/workflow/test_language_schema.py`

  Expected: FAIL because Phase 1 blocks both Archon fields.

- [x] Introduce normalizer version 2 without changing version 1.

  Set the current authoring version to 2, retain `SUPPORTED_NORMALIZER_VERSIONS = frozenset({1, 2})`, and dispatch normalization by version. Version 1 must remain identity-only. Version 2 replaces each Archon node's `output_format` option with its canonical thawed mapping and fills `WorkflowLanguageMetadata.structured_outputs`, an immutable node-ID mapping of canonical schemas, fingerprints, and canonicalization versions. Include that mapping in `normalized_definition_digest` and `semantic_fingerprint` without adding new YAML surface.

  Extend `WorkflowLanguageSnapshot` with `structured_outputs`. `read_language_snapshot()` must accept the exact four-field legacy shape only for normalizer version 1 and require the new bounded field for version 2. `make_language_snapshot()` copies the immutable mapping so the canonical schema and fingerprint are sealed in `resources.json` rather than re-derived from mutable provider state.

- [x] Add failing static-reference tests for `$producer.output.field`.

  Test closed objects that prove a field impossible, optional declared properties, `additionalProperties: true`, `anyOf`/`oneOf` branches where one branch permits the field, schemaless producers, nested field paths, and references to nodes that are not dependencies.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_structured_output_language.py`

  Expected: FAIL because static schema-aware reference analysis is absent.

- [x] Implement conservative `prove_output_path_impossible(schema, path_parts) -> bool` in `plugins/workflow/language.py`.

  Resolve only the already-normalized local `$defs` graph. Return `True` only when every applicable branch is closed and excludes the requested path. Emit stable blocking code `structured_output_field_impossible`; do not change runtime missing-field behavior.

- [x] Update the dependency-neutral field inventory and generated contracts.

  Mark Archon `output_format` and `output_type` as supported in Phase 2, keep legacy codes unchanged, and update the examples, descriptions, codes, and bounds in `skills/software-development/workflow-builder/references/portable-schema.md`. Verify the dynamic CLI contract directly with `.venv/bin/hermes workflow schema --profile archon-2026-07 --json`; there is no checked-in generated JSON schema file.

- [x] Run language and schema suites and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_language.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_structured_output_language.py`

  Commit: `feat(workflow): normalize archon structured schemas`

## Task 3: Declare and seal truthful provider capability decisions

**Files:**

- Modify: `providers/base.py`
- Modify: `plugins/model-providers/anthropic/__init__.py`
- Modify: `hermes_cli/runtime_provider.py`
- Modify: `plugins/workflow/runner_binding.py`
- Modify: `plugins/workflow/compat.py`
- Modify: `tests/providers/test_provider_profiles.py`
- Modify: `tests/plugins/workflow/test_runner_binding.py`
- Modify: `tests/plugins/workflow/test_provider_compat.py`
- Modify: `tests/plugins/workflow/test_compat_matrix.py`
- Modify: `tests/plugins/workflow/test_admission.py`
- Modify: `tests/plugins/workflow/test_doctor.py`

- [x] Add failing matrix tests for direct OpenAI Responses, direct OpenAI Chat Completions, direct Anthropic Messages, custom endpoints, OpenRouter/aggregators, unknown Hermes-managed loops, and delegated runtimes.

  Assert only explicit direct declarations resolve native; custom and aggregator routes resolve `prompt_json_schema`; unknown Hermes-managed routes resolve `prompt_json_schema`; delegated routes resolve `unsupported`; community `structured_output: true` metadata cannot promote a route.

  Run: `scripts/run_tests.sh tests/providers/test_provider_profiles.py tests/plugins/workflow/test_runner_binding.py tests/plugins/workflow/test_provider_compat.py tests/plugins/workflow/test_compat_matrix.py`

  Expected: FAIL because runtime capabilities currently contain only API mode and managed-loop status.

- [x] Add the provider declaration and central resolver.

  Extend `ProviderProfile` with `structured_output_strategy: str | None = None`, where `None` means undeclared and an explicit `"unsupported"` means the provider forbids adaptation. Extend `ExecutionRuntimeCapabilities` with provider identity, normalized base URL trust class, and declared structured-output strategy. Implement:

  ```python
  @dataclass(frozen=True, slots=True)
  class StructuredOutputCapabilityDecision:
      strategy: StructuredOutputStrategy
      effective_provider: str
      model: str
      api_mode: str
      declaration_source: str
      adapter_version: int
      schema_fingerprint: str
      rationale: str
  ```

  `resolve_structured_output_capability(...)` must cap rationale length and apply the authority/default rules from the approved design. Declare native Anthropic support in `plugins/model-providers/anthropic/__init__.py`; declare the trusted direct OpenAI API-key route in the built-in runtime classifier. Do not declare the ChatGPT subscription `openai-codex` profile native unless its backend contract is separately proved by a transport test.

- [x] Seal the decision into workflow admission and scheduled revalidation identity.

  Add the decision to `ExecutionCapabilityContext.identity_digest`, pass it into `assess_compatibility`, emit `structured_output_strategy_unsupported` when required, and store the complete immutable decision in run metadata for Archon AI nodes. Catalog summaries expose only strategy/provider/api-mode/adapter-version, not schemas.

  Extend workflow doctor coverage so a missing validator emits the existing structured-output extra-install guidance and a schemaless workflow does not require the validator.

- [x] Add admission drift tests.

  Change provider config between admission and execution and assert the worker-facing sealed decision stays fixed, scheduled revalidation detects the identity change, and no provider request occurs when the resolved runtime cannot honor it.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_schedule_revalidation.py tests/plugins/workflow/test_runner_binding.py`

- [x] Run the capability suites and commit.

  Run: `scripts/run_tests.sh tests/providers/test_provider_profiles.py tests/plugins/workflow/test_runner_binding.py tests/plugins/workflow/test_provider_compat.py tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_schedule_revalidation.py tests/plugins/workflow/test_doctor.py`

  Commit: `feat(providers): declare structured output capabilities`

## Task 4: Extend the isolated plugin-agent protocol

**Files:**

- Modify: `agent/plugin_agent.py`
- Modify: `agent/plugin_agent_worker.py`
- Modify: `agent/agent_init.py`
- Modify: `run_agent.py`
- Modify: `tests/agent/test_plugin_agent.py`
- Modify: `tests/plugins/workflow/test_provider_failures.py`

- [x] Add failing protocol tests for structured request round trips, contradictory strategies, oversized schema/output limits, immutable nested values, unknown fields, and additive old-client compatibility.

  Run: `scripts/run_tests.sh tests/agent/test_plugin_agent.py`

  Expected: FAIL because the request/result protocol has no structured-output field.

- [x] Add `structured_output: StructuredOutputRequest | None` to `PluginAgentRunRequest` and structured evidence to `PluginAgentRunResult`.

  Serialize with explicit `to_wire()`/`from_wire()` helpers so enums, bytes, and immutable mappings do not depend on `asdict()` accidents. Preserve `_PROTOCOL_VERSION = 1` by making fields additive and optional. Validate schema/request bounds before spawning the worker.

- [x] Thread the request through `AIAgent.__init__` and `agent_init.init_agent` as an immutable per-run value.

  Do not expose provider wire fields through `request_overrides`. Keep the value stable for the worker lifetime and reject attempts to combine it with arbitrary `response_format`, `text.format`, or `output_config.format` overrides.

- [x] Resolve actual runtime capability before constructing `AIAgent`.

  In `plugin_agent_worker.py`, compare the resolved provider/model/API-mode decision with the admitted decision. Return `structured_output_capability_drift` with zero provider attempts when it cannot honor the admission. Record exact `provider_attempts`, `model_calls`, strategy, adapter version, schema fingerprint, and declaration source in bounded result audit data.

- [x] Add prompt-adapter message tests.

  Assert `prompt_json_schema` adds one deterministic bounded instruction block to the initial user message; it does not modify the system prompt, history, tools, or role alternation. Assert `unsupported` fails before a provider request.

- [x] Run the isolated-agent suites and commit.

  Run: `scripts/run_tests.sh tests/agent/test_plugin_agent.py tests/plugins/workflow/test_provider_failures.py`

  Commit: `feat(agent): carry structured output through isolated runs`

## Task 5: Emit only tested native transport wire contracts

**Files:**

- Modify: `agent/chat_completion_helpers.py`
- Modify: `agent/transports/chat_completions.py`
- Modify: `agent/transports/codex.py`
- Modify: `agent/transports/anthropic.py`
- Modify: `agent/codex_responses_adapter.py`
- Modify: `agent/anthropic_adapter.py`
- Modify: `tests/agent/transports/test_chat_completions.py`
- Modify: `tests/agent/transports/test_codex_transport.py`
- Modify: `tests/agent/test_codex_responses_adapter.py`
- Modify: `tests/agent/test_anthropic_adapter.py`
- Modify: `tests/agent/transports/test_transport.py`

- [ ] Add failing exact-kwargs tests for the three approved native shapes.

  Required wire values:

  ```python
  # Direct OpenAI Chat Completions
  response_format={
      "type": "json_schema",
      "json_schema": {"name": "hermes_output", "schema": schema, "strict": True},
  }

  # Direct OpenAI Responses
  text={
      "format": {
          "type": "json_schema",
          "name": "hermes_output",
          "schema": schema,
          "strict": True,
      }
  }

  # Direct Anthropic Messages
  output_config={"format": {"type": "json_schema", "schema": schema}}
  ```

  Assert prompt/native-JSON-mode/unsupported strategies never leak these fields, and custom/aggregator endpoints cannot receive them.

  Run: `scripts/run_tests.sh tests/agent/transports/test_chat_completions.py tests/agent/transports/test_codex_transport.py tests/agent/test_codex_responses_adapter.py tests/agent/test_anthropic_adapter.py`

  Expected: FAIL on missing wire fields.

- [ ] Pass `structured_output` through `build_api_kwargs()` to the selected transport and add transport-owned builders.

  Native builders must verify the exact admitted strategy plus trusted provider identity before emitting a wire field. `ChatCompletionsTransport` owns `response_format`; `CodexResponsesTransport` owns `text.format`; the Anthropic adapter owns `output_config.format`.

- [ ] Preserve existing transport fields while merging.

  Add `text` to the Codex Responses allowlist with strict nested validation. Merge Anthropic `output_config.format` with existing `output_config.effort` rather than replacing either member. Add regression tests for adaptive thinking, xhigh downgrade, streaming, request sanitization, and existing provider extras.

- [ ] Add native JSON-mode support only if an existing direct declared route has a separately documented and tested JSON-mode parameter.

  If none does, keep the enum/resolver branch but ship no native-JSON-mode provider declaration. Do not infer or probe it at runtime.

- [ ] Run transport suites and commit.

  Run: `scripts/run_tests.sh tests/agent/transports/test_transport.py tests/agent/transports/test_chat_completions.py tests/agent/transports/test_codex_transport.py tests/agent/test_codex_responses_adapter.py tests/agent/test_anthropic_adapter.py tests/agent/test_anthropic_kwargs_sanitize.py`

  Commit: `feat(agent): enforce native structured output transports`

## Task 6: Enforce canonical Archon output and one isolated repair

**Files:**

- Modify: `plugins/workflow/executors/base.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `tests/plugins/workflow/test_ai_executor.py`
- Modify: `tests/plugins/workflow/test_ai_e2e.py`
- Modify: `tests/plugins/workflow/test_retry.py`
- Modify: `tests/plugins/workflow/test_node_mcp.py`
- Modify: `tests/plugins/workflow/test_node_skills.py`
- Modify: `tests/plugins/workflow/test_node_hooks.py`
- Modify: `tests/plugins/workflow/test_node_agents.py`

- [ ] Add failing executor tests for canonical success and invalid terminal outcomes.

  Cover equivalent provider JSON encodings, native refusal/truncation/prose/fence/multiple values/oversize, prompt-adapted invalid output, aggregate usage, exact provider attempts, and the schema fingerprint in the AI cache fingerprint.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_ai_e2e.py tests/plugins/workflow/test_retry.py`

  Expected: FAIL because Archon AI execution still uses legacy post-validation and scheduler retries.

- [ ] Add `PrimaryOutputCandidate` to the executor boundary.

  Define it in `plugins/workflow/output_resolution.py` and add `primary_output: PrimaryOutputCandidate | None = None` to `NodeExecutionResult`. The candidate contains the attempt-local relative path, media type, byte size, digest, parsed structured value when applicable, schema fingerprint, canonicalization version, and `output_type`; it is not a published artifact.

- [ ] Bind the admitted structured request in `AgentNodeExecutor`.

  For Archon nodes, obtain the normalized schema and sealed capability decision from `NodeExecutionContext`, add the schema fingerprint/strategy/adapter version to `_fingerprint`, send the generic structured request, validate the returned audit identity, and write canonical bytes once to an attempt-local regular file. Leave the legacy branch unchanged.

- [ ] Add failing repair-isolation tests.

  Capture the second `PluginAgentRunRequest` and assert: `context_mode="fresh"`, `session_id=None`, `allowed_tools=()`, `enabled_toolsets=()`, delegation denied, `hooks=()`, `mcp_servers=None`, `skills=()`, `inline_agents={}`, `fallback_model=None`, `ephemeral_system_prompt=None`, `max_iterations=1`, no original task/history, and a prompt containing only canonical schema, bounded invalid excerpt, and bounded diagnostics.

- [ ] Implement exactly one eligible prompt repair with shared accounting.

  Charge the first result's exact provider/model attempts before constructing repair. Pass only remaining wall/provider/model allowance. Aggregate usage and audits. Skip repair for native strategies, outward nodes, uncertain effects, cancellation, exhausted attempts, exhausted wall time, or output too large to bound. Persist only digest/size/diagnostic summary and repair disposition.

- [ ] Make `structured_output_invalid` terminal only for Archon.

  Add `archon_terminal_failure: True` to result metadata and make `_persist_result()` bypass retry only when both the admitted profile is Archon and that trusted metadata is present. Do not add the code to the global `never_retry` set because that would mutate legacy behavior.

- [ ] Run AI, retry, and extension-isolation suites and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_ai_e2e.py tests/plugins/workflow/test_retry.py tests/plugins/workflow/test_node_mcp.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_node_hooks.py tests/plugins/workflow/test_node_agents.py`

  Commit: `feat(workflow): enforce archon structured output`

## Task 7: Centralize Archon output resolution without Phase 3 semantics

**Files:**

- Create: `plugins/workflow/output_resolution.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `tests/plugins/workflow/test_scheduler.py`
- Modify: `tests/plugins/workflow/test_resources.py`
- Modify: `tests/plugins/workflow/test_language.py`

- [ ] Add failing tests showing every Archon downstream consumer uses the same immutable output value.

  Cover condition lookup, prompt rendering, shell/script variable rendering, evidence projection, and nested field access. Assert all consumers see identical canonical bytes, parsed value, deterministic text, media type, digest, producer node, and winning attempt.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_resources.py`

  Expected: FAIL because `_output_values`, `_variables`, and evidence independently inspect artifacts and parse text.

- [ ] Implement `ResolvedNodeOutput` and `resolve_node_output(...)`.

  ```python
  @dataclass(frozen=True, slots=True)
  class ResolvedNodeOutput:
      canonical_bytes: bytes
      value: object
      text: str
      media_type: str
      sha256: str
      node_id: str
      attempt_id: str
      publication_id: str | None
  ```

  The Archon resolver trusts the winner descriptor and candidate/publication digest; it never reparses raw provider output. The legacy resolver delegates to the exact current scanning/parsing implementation.

- [ ] Route scheduler/resource consumers through the shared resolver.

  Keep Phase 2 compatibility adapters for missing fields, condition coercion, comparison precedence, Bash quoting, and large values. Add explicit comments and tests showing those outcomes stay unchanged until Phase 3.

- [ ] Run scheduler/resource regression suites and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_language.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_script_executor.py`

  Commit: `refactor(workflow): centralize archon output resolution`

## Task 8: Publish winner-only typed artifact bundles atomically

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/scheduler.py`
- Create: `tests/plugins/workflow/test_typed_publication.py`
- Modify: `tests/plugins/workflow/test_store.py`
- Modify: `tests/plugins/workflow/test_parallel_scheduler.py`
- Modify: `tests/plugins/workflow/test_approval.py`
- Modify: `tests/plugins/workflow/test_loop_executor.py`
- Modify: `tests/plugins/workflow/test_script_executor.py`
- Modify: `tests/plugins/workflow/test_bash_e2e.py`

- [ ] Add failing bundle tests for all successful output-producing node kinds.

  Cover `command`, `prompt`, `bash`, `script`, `loop`, and `approval`; assert `cancel` never publishes. Cover empty text, Markdown media type/filename, canonical JSON media type/filename, case-sensitive open `output_type`, opaque publication IDs, and metadata size at or below 65,536 bytes.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py`

  Expected: FAIL because `RunStore.complete_node()` only registers executor artifact refs.

- [ ] Add immutable publication types to `plugins/workflow/store.py`.

  ```python
  @dataclass(frozen=True, slots=True)
  class TypedPublicationCandidate:
      attempt_relative_path: str
      output_type: str
      media_type: str
      size_bytes: int
      sha256: str
      schema_fingerprint: str | None
      canonicalization_version: int
      session_id: str | None

  @dataclass(frozen=True, slots=True)
  class TypedPublicationRef:
      publication_id: str
      content_name: str
      output_type: str
      media_type: str
      size_bytes: int
      sha256: str
      metadata_sha256: str
  ```

  Add `typed_publication: TypedPublicationCandidate | None = None` to `complete_node()`. Keep `NodeExecutionResult.primary_output` as the single executor boundary from Task 6; the scheduler converts that value to the store-owned candidate immediately before completion.

- [ ] Implement the atomic bundle sequence under the run lock.

  After validating the active claim, verify the attempt-local file with the existing contained regular-file primitives, size, and digest. Create a private same-filesystem staging directory, write `content.json` or `content.md` and `metadata.json`, fsync both files and the staging directory, rename to `publications/<opaque-id>`, fsync the parent, then append the completion event and replace `run.json`. Reject existing final paths rather than overwrite.

- [ ] Add stale-claim and concurrent-winner tests.

  Race two completions and assert only the active attempt can create staging/final content, only its descriptor is journaled/projected, and losing content is never named in metadata. Inject a stale completion before staging and assert no filesystem mutation.

- [ ] Run store/node-kind suites and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_bash_e2e.py`

  Commit: `feat(workflow): publish atomic typed artifact bundles`

## Task 9: Recover publications and complete persistent-session mirrors

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/sessions.py`
- Create: `tests/plugins/workflow/test_typed_publication_recovery.py`
- Modify: `tests/plugins/workflow/test_crash_recovery.py`
- Modify: `tests/plugins/workflow/test_fault_injection.py`
- Modify: `tests/plugins/workflow/test_shutdown_recovery.py`
- Modify: `tests/plugins/workflow/test_persisted_sessions.py`
- Modify: `tests/plugins/workflow/test_retention.py`
- Modify: `tests/plugins/workflow/test_security_boundaries.py`

- [ ] Add failure-injection tests for every publication boundary.

  Inject before/after content write, metadata write, staging-directory fsync, directory rename, journal append, and projection replace. Assert incomplete staging is removed; unjournaled final bundles are removed; journaled bundles missing from projection are restored; journal authority remains monotonic.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_fault_injection.py`

  Expected: FAIL because recovery does not know publication bundles.

- [ ] Replay publication descriptors from the checked journal.

  Extend journal projection rebuild to validate publication ID, winning attempt ID, content name, size, content digest, metadata digest, and schema fingerprint. Reconstruct a missing/corrupt journaled bundle only from the corroborated winning-attempt candidate with the same digest. Otherwise record stable `typed_publication_integrity` repair/reconciliation evidence and never invent success.

- [ ] Add path/security/retention tests.

  Cover symlinks, reparse points, traversal, non-regular files, digest mismatch, quota overflow, bundle-as-one-retention-unit, archive/restore, and profile isolation. Assert cleanup never deletes content while retaining metadata or vice versa.

- [ ] Add immutable profile-scoped mirror storage and obligation journal events.

  Store content by hash below the effective profile's Hermes home. Store immutable entries containing workflow, node, operator scope, run, attempt, output type, and hash. Atomically update a scope index under a dedicated lock. Journal `typed_mirror_required` before releasing the run lock and `typed_mirror_completed` after the index points at the immutable entry. Recovery may finish only from a verified run bundle; pending/unverified mirrors are invisible to cold-session recovery.

- [ ] Add concurrent mirror tests.

  Complete two persistent-session runs concurrently for one scope. Assert immutable history retains both entries, the scope index points atomically to one complete entry, no mutable provider-session path is stored, and recovery is idempotent after crashes on either side of index replacement.

- [ ] Run recovery/security/session suites and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_retention.py tests/plugins/workflow/test_security_boundaries.py`

  Commit: `feat(workflow): recover typed publications and mirrors`

## Task 10: Expose bounded evidence, preview, and download

**Files:**

- Modify: `plugins/workflow/evidence.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `tests/plugins/workflow/test_evidence_api.py`
- Modify: `tests/plugins/workflow/test_api_runtime.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_workflow_detail_api.py`

- [ ] Add failing artifact-evidence tests.

  Assert the existing `artifacts` evidence kind adds bounded publication ID, output type, media type, size, SHA-256, producer, winning attempt, schema fingerprint, produced time, session ID, and integrity/recovery status while omitting body and filesystem path. Assert old raw artifact entries still sanitize identically.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py`

  Expected: FAIL because artifact evidence contains only current projection entries.

- [ ] Add a store-owned publication lookup by `(run_id, publication_id, operator_scope)`.

  The lookup must authorize the run first, find the journal/projection-confirmed descriptor, and open only its known `content.json`/`content.md` using `_read_contained_regular_file`. Recheck regular-file containment, recorded size, and digest on every access.

- [ ] Add authenticated preview and download endpoints.

  Implement:

  ```text
  GET /runs/{run_id}/artifacts/{publication_id}/preview
  GET /runs/{run_id}/artifacts/{publication_id}/download
  ```

  Preview returns bounded JSON/text plus `bytes_returned`, `size_bytes`, and `truncated`; it never partially parses JSON. Download uses a server-resolved path/descriptor, streams the verified regular file, sets the recorded safe media type, and uses an ASCII-safe `Content-Disposition` filename derived from the opaque ID and canonical content name.

- [ ] Add ownership and attack tests.

  Cover unauthenticated, wrong profile/operator scope, unknown publication ID, path-like IDs, symlink swaps, size/digest mismatch, unknown media types, JSON/text truncation, and response bounds. Catalog/coordinator list tests must prove they do not open artifact bodies.

- [ ] Run API/evidence suites and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_security_boundaries.py`

  Commit: `feat(workflow): expose typed artifact preview and download`

## Task 11: Add the Desktop typed-artifact inspector

**Files:**

- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/hermes.ts`
- Modify: `apps/desktop/src/hermes.test.ts`
- Create: `apps/desktop/src/app/workflows/typed-artifact-view.tsx`
- Create: `apps/desktop/src/app/workflows/typed-artifact-view.test.tsx`
- Modify: `apps/desktop/src/app/workflows/run-inspector.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`

- [ ] Add failing client/type tests for typed metadata, preview, and download URLs.

  Define `WorkflowTypedArtifact` and `WorkflowArtifactPreview` interfaces. Keep `WorkflowEvidencePage.items` compatible with generic records so a new Desktop still handles an older backend. URL-encode both run and opaque publication IDs.

  Run: `cd apps/desktop && npm test -- src/hermes.test.ts src/app/workflows/typed-artifact-view.test.tsx`

  Expected: FAIL because the types/client/view do not exist.

- [ ] Implement `TypedArtifactView` from backend-confirmed evidence only.

  Render output/media type, producer/winning attempt, size/hash, optional schema fingerprint/time/session, and integrity status. Fetch preview only after user selection, format canonical JSON only when the backend marks it JSON and supplies a complete preview, render other text as bounded plain text, and expose an explicit download link/action.

- [ ] Add old/new compatibility and failure tests.

  Generic old-backend entries continue through `EvidenceItems`. Unknown media types remain download-only. Missing new fields do not crash. Preview metadata/body failures render a non-destructive error inside the artifact tab and do not unmount or impair the run inspector.

- [ ] Integrate the component into `RunInspector` without changing the primary chat/TUI surface.

  The artifacts tab selects the typed view only when at least one backend item has a valid `publication_id`; otherwise retain the existing generic JSON evidence view.

- [ ] Run Desktop unit, type, lint, and formatting checks and commit.

  Run: `cd apps/desktop && npm test -- src/hermes.test.ts src/app/workflows/typed-artifact-view.test.tsx src/app/workflows/index.test.tsx`

  Run: `cd apps/desktop && npm run typecheck`

  Run: `cd apps/desktop && npm run lint`

  Run: `cd apps/desktop && npx prettier --check 'src/**/*.{ts,tsx}' 'electron/**/*.ts' 'vite.config.ts'`

  Commit: `feat(desktop): inspect typed workflow artifacts`

## Task 12: Prove the full Archon workflow path and installed distribution

**Files:**

- Modify: `tests/plugins/workflow/test_portable_compatibility_e2e.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `tests/plugins/workflow/test_workflow_language_desktop_e2e.py`
- Modify: `tests/plugins/workflow/test_showcase_ai_e2e.py`
- Modify: `tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py`

- [ ] Add an end-to-end Archon workflow fixture with structured AI output, a downstream field reference, `output_type`, evidence preview/download, and Desktop projection.

  Assert one canonical value/digest flows from worker validation through downstream resolution, winning publication, journal rebuild, evidence, API preview, download, and Desktop types. Assert MCP and skills remain per-node options on the prompt/command node and do not appear as node kinds.

- [ ] Add legacy parity coverage in the same E2E boundary.

  Run an equivalent `hermes-legacy` workflow and assert its post-hoc validation, output paths, retry behavior, and downstream parsing remain unchanged.

- [ ] Add validator-absent installed-distribution coverage.

  In a temporary installation and temporary `HERMES_HOME`, assert a schemaless workflow runs, a structured Archon workflow fails closed before provider execution with the existing extra-install guidance, and installing the declared extra makes the same workflow runnable.

- [ ] Run the focused E2E suites and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_portable_compatibility_e2e.py tests/plugins/workflow/test_installed_distribution_e2e.py tests/plugins/workflow/test_workflow_language_desktop_e2e.py tests/plugins/workflow/test_showcase_ai_e2e.py tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py`

  Commit: `test(workflow): prove phase 2 structured data end to end`

## Task 13: Update the language contract and user documentation

**Files:**

- Modify: `website/docs/user-guide/features/workflow-yaml-reference.md`
- Modify: `skills/software-development/workflow-builder/references/portable-schema.md`
- Modify: `website/docs/developer-guide/model-provider-plugin.md`
- Modify: `website/docs/developer-guide/provider-runtime.md`
- Modify: `website/docs/user-guide/features/workflows.md`
- Modify: `website/docs/user-guide/desktop.md`
- Modify: `apps/desktop/README.md`

- [ ] Update the workflow YAML reference with an Archon `output_format` + `output_type` example.

  Document Draft 2020-12/self-contained ref rules, bounds, canonical JSON, declared native versus prompt-adapted enforcement, one-repair action-safety boundary, validator extra requirement, atomic publication/recovery, preview/download, and the Phase 3 timeout/retry/condition/reference boundaries.

- [ ] Explicitly document the existing extension taxonomy.

  List the seven supported node kinds and state that MCP and skills are already per-node options for `command`/`prompt`, not separate node kinds or new Phase 3 work.

- [ ] Update provider and Desktop operational guidance.

  Explain that native support is declaration-driven, custom/aggregator routes default to prompt adaptation, capability drift fails before a request, and Desktop renders only backend-confirmed artifacts with generic fallback for older backends.

- [ ] Run documentation/contract checks and commit.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_workflow_detail_api.py`

  Run: `cd website && npm run typecheck`

  Run: `cd website && npm run lint:diagrams`

  Run: `cd website && npm run build`

  Commit: `docs(workflow): document phase 2 structured data`

## Task 14: Complete regression, customization, and merge-readiness gates

**Files:**

- Modify only code/tests needed to fix concrete failures discovered by these gates.
- Do not combine unrelated cleanup with this task.

- [ ] Run the complete focused Python matrix.

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/agent/test_structured_output.py \
    tests/agent/test_plugin_agent.py \
    tests/agent/transports/test_transport.py \
    tests/agent/transports/test_chat_completions.py \
    tests/agent/transports/test_codex_transport.py \
    tests/agent/test_codex_responses_adapter.py \
    tests/agent/test_anthropic_adapter.py \
    tests/plugins/workflow
  ```

  Expected: PASS with no skipped Phase 2 contract cases.

- [ ] Run the complete Desktop checks.

  Run: `cd apps/desktop && npm test`

  Run: `cd apps/desktop && npm run typecheck`

  Run: `cd apps/desktop && npm run lint`

  Run: `cd apps/desktop && npx prettier --check 'src/**/*.{ts,tsx}' 'electron/**/*.ts' 'vite.config.ts'`

- [ ] Run the canonical full Python suite.

  Run: `scripts/run_tests.sh`

  Expected: PASS. Record the final test count and elapsed time in the implementation handoff.

- [ ] Validate customization and brand-sensitive changes.

  Run: `.venv/bin/python scripts/check_upstream_customizations.py --strict --base-ref HEAD`

  Run: `scripts/test_workflow_merge_gate.sh --phase base`

  Inspect changes to generic agent/runtime/transport files against OTTO and LOOP24 overlays. Do not update the ledger merely to silence a failure; record only intentional generic seams.

- [ ] Rehearse upstream and brand merges in temporary worktrees.

  Fetch current refs, then run the repository's bounded rehearsal without publishing refs:

  ```bash
  git fetch origin --prune
  scripts/test_workflow_upstream_merge.sh \
    --upstream-ref origin/main \
    --base-ref HEAD \
    --brand-ref otto \
    --brand-ref loop24
  ```

  The script creates and removes disposable worktrees and records conflicts/gate results. Literal `main` is used only as the upstream synchronization input, never as a feature base.

- [ ] Review the final diff for contract safety.

  Confirm no new `HERMES_*` non-secret config, no new core model tool, no raw response in run/evidence/API data, no schema in system prompts, no duplicated MCP/skills node types, no unbounded API payload, no path-taking artifact endpoint, and no legacy semantic change.

- [ ] Commit any gate-only fixes atomically, then produce the implementation handoff.

  Commit: `fix(workflow): close phase 2 verification gaps` only if concrete gate fixes were required. Otherwise make no empty commit.

  The handoff must include commits, test evidence, merge-rehearsal results, known environmental limitations, and the explicit next boundary: Phase 3 strict conditions/references/timeouts/retries/Bash semantics, with MCP and skills excluded because they are already supported.
