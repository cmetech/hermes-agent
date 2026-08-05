# Workflow Language Phase 4: Ordinary Loops and Immutable Includes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Awaiting user approval

**Goal:** Add normalizer v4 with durable ordinary-loop confirmation and compile-time immutable workflow includes while preserving every admitted v1-v3 behavior.

**Architecture:** Capture one bounded catalog snapshot, parse source packages without granting child policy authority, expand includes into one namespaced DAG, apply cumulative v2-v4 normalization, bind resources to package origins, and seal snapshot format 2 before admission. Extend the existing loop executor and compare-and-set interaction store so a signal-bearing result can be accepted without replay or continued with bounded feedback. The existing scheduler, evidence stream, API, Gateway, and Desktop remain the only runtime surfaces.

**Tech Stack:** Python 3.11+, immutable dataclasses and mappings, PyYAML safe loading, canonical JSON and SHA-256 manifests, SQLite plus JSONL run journals, FastAPI/Pydantic, Electron React/TypeScript with nanostores, Vitest/Testing Library, and `scripts/run_tests.sh`.

## Global Constraints

- Phase 4 is externally atomic: keep `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` at 3 through every pre-activation gate; only Task 14 may activate v4 after loops, includes, surfaces, distribution, Desktop, and defensive invariants are green.
- `LATEST_NORMALIZER_VERSION` becomes 4 and `SUPPORTED_NORMALIZER_VERSIONS` becomes `{1, 2, 3, 4}` in Task 1 so tests can request v4 explicitly during staged implementation.
- New unversioned and `hermes-legacy` packages stay on v2. Admitted v1-v3 snapshots remain byte-compatible and retain their recorded behavior.
- A v4 loop is effectively interactive only when root `interactive: true` and `loop.interactive: true` are both present.
- `signal_completes` defaults to false for effectively interactive v4 loops and true otherwise. Explicit false without effective interactivity is blocking.
- Exactly one of `loop.prompt` and `loop.command` is required. `loop.command` is a sealed Markdown command resource, not a shell command.
- Include targets are literal discovered workflow names. There are no paths, URLs, variables, expressions, `with`, deep child addressing, runtime subruns, or includes inside `loop_group`.
- Hard closure limits are: depth 3, 64 distinct dependencies, 512 expanded nodes, 4,096 edges, 2 MiB selected source definitions, 2 MiB canonical expanded definition, 512 authenticated files, 1 MiB per authenticated file, and 8 MiB authenticated bytes.
- The root sidecar is the only active policy. Child sidecars are authenticated, snapshotted, digested, diagnosed as ignored, and never executed.
- Never resolve a dependency, command, script, or MCP resource from live source after admission. Resume uses snapshot format 1 or 2 according to the sealed run.
- Keep prompt prefixes byte-stable. Do not mutate prior messages, add a core model tool, widen the tool schema, or inject a synthetic outer-loop user message.
- Public diagnostics are bounded and contain logical names, relative locations, digests, and counts—not prompt/resource bodies, feedback, secrets, provider responses, or unnecessary absolute paths.
- Run Python tests only through `scripts/run_tests.sh`; do not invoke pytest directly.
- Keep commits atomic. Do not push, merge to `base`, delete branches/worktrees, publish, or propagate brand branches without separate authorization.
- Defensive invariant tests are mandatory. Attempt the bounded defensive security review once; if a Codex platform gate stops it, record `BLOCKED_BY_PLATFORM_GATE` and do not attempt to evade the gate.

## Approval and Execution Boundary

- The approved design is `docs/superpowers/specs/2026-08-05-workflow-language-phase-4-ordinary-loops-immutable-includes-design.md` at commit `dcf41a2db`.
- Execute on `feat/workflow-language-phase-4-ordinary-loops-immutable-includes`, which started from `base`. Never use literal `main`.
- This plan is planning-only until the user selects an execution mode after reviewing it.
- Preserve unrelated user-owned changes. Stop before editing an overlapping dirty file that cannot be safely incorporated.
- If Subagent-Driven execution is selected, each worker owns only its named task files, must not revert other agents, and receives independent specification and quality review before the next task.
- If Inline execution is selected, use the executing-plans checkpoints and apply the same RED/GREEN, atomic-commit, and review gates without spawning unrequested subagents.

## File Structure

### New production modules

- `plugins/workflow/compilation.py` — immutable catalog snapshot, compile orchestration, and the `WorkflowCompilation` admission contract.
- `plugins/workflow/includes.py` — bounded closure traversal, namespace/edge/reference expansion, include aliases, and node provenance.
- `plugins/workflow/dependency_manifest.py` — exact snapshot-format-2 dependency/resource manifest codecs and composite digest.

### New Python test modules

- `tests/plugins/workflow/test_phase4_language.py`
- `tests/plugins/workflow/test_phase4_compilation.py`
- `tests/plugins/workflow/test_phase4_includes.py`
- `tests/plugins/workflow/test_phase4_references.py`
- `tests/plugins/workflow/test_phase4_dependency_manifest.py`
- `tests/plugins/workflow/test_phase4_snapshot.py`
- `tests/plugins/workflow/test_phase4_loops.py`
- `tests/plugins/workflow/test_phase4_loop_interactions.py`
- `tests/plugins/workflow/test_phase4_surfaces.py`
- `tests/plugins/workflow/test_phase4_defensive_invariants.py`
- `tests/plugins/workflow/test_phase4_code_catalog.py`

### Principal modified Python modules

- `plugins/workflow/models.py`
- `plugins/workflow/language.py`
- `plugins/workflow/language_schema.py`
- `plugins/workflow/schema.py`
- `plugins/workflow/discovery.py`
- `plugins/workflow/catalog_api.py`
- `plugins/workflow/compat.py`
- `plugins/workflow/resources.py`
- `plugins/workflow/bash_rendering.py`
- `plugins/workflow/trust.py`
- `plugins/workflow/execution_semantics.py`
- `plugins/workflow/store.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/scheduled_revalidation.py`
- `plugins/workflow/actions.py`
- `plugins/workflow/evidence.py`
- `plugins/workflow/notifications.py`
- `plugins/workflow/executors/ai.py`
- `plugins/workflow/executors/bash.py`
- `plugins/workflow/executors/script.py`
- `plugins/workflow/executors/loop.py`
- `plugins/workflow/cli.py`
- `plugins/workflow/api_admission.py`
- `plugins/workflow/gateway_command.py`
- `plugins/workflow/dashboard/plugin_api.py`

### Desktop, generated contracts, and documentation

- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/src/app/workflows/run-inspector.tsx`
- `apps/desktop/src/app/workflows/attention-inbox.tsx`
- `apps/desktop/src/app/workflows/index.test.tsx`
- `apps/desktop/src/app/workflows/workflow-operations.e2e.test.tsx`
- `apps/desktop/src/i18n/ar.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/zh-hant.ts`
- `apps/desktop/src/i18n/zh.ts`
- `website/docs/user-guide/features/workflow-yaml-reference.md`
- `website/docs/user-guide/features/workflows.md`
- `skills/software-development/workflow-builder/references/portable-schema.md`
- `skills/software-development/workflow-builder/references/authoring-checklist.md`
- `docs/upstream-customizations/workflow-orchestration.yaml`

Adding another production module requires a concrete dependency-cycle or single-responsibility reason recorded in the task commit. Do not create a general hook or extension surface without a Phase 4 consumer.

---

### Task 1: Make language versions cumulative and admit explicit v4 tests

**Files:**

- Create: `tests/plugins/workflow/test_phase4_language.py`
- Create: `tests/plugins/workflow/test_phase4_code_catalog.py`
- Modify: `plugins/workflow/language.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/bash_rendering.py`
- Modify: `plugins/workflow/conditions.py`
- Modify: `plugins/workflow/trust.py`
- Modify: `plugins/workflow/execution_semantics.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/executors/bash.py`
- Modify: `plugins/workflow/executors/script.py`
- Modify: `plugins/workflow/executors/loop.py`
- Test: `tests/plugins/workflow/test_phase3_language.py`
- Test: `tests/plugins/workflow/test_language_snapshot.py`
- Test: `tests/plugins/workflow/test_phase3_execution_semantics.py`
- Test: `tests/plugins/workflow/test_strict_output_references.py`

**Interfaces:**

- Produces: `supports_structured_outputs(profile, normalizer_version) -> bool`
- Produces: `supports_phase3_semantics(profile, normalizer_version) -> bool`
- Produces: `supports_phase4_semantics(profile, normalizer_version) -> bool`
- Preserves: current Archon new-run version 3 until Task 14

- [ ] **Step 1: Add RED tests for the cumulative version matrix.**

  Add table-driven assertions proving that v4 is supported only for Archon, inherits v2/v3 semantics for a Phase-3-only workflow, and does not change v1-v3 serialization. Keep new-run Archon selection at v3 for staged work.

  ```python
  @pytest.mark.parametrize(
      ("version", "structured", "phase3", "phase4"),
      [(1, False, False, False), (2, True, False, False),
       (3, True, True, False), (4, True, True, True)],
  )
  def test_archon_capabilities_are_cumulative(version, structured, phase3, phase4):
      profile = WorkflowLanguageProfile.ARCHON_2026_07
      assert supports_structured_outputs(profile, version) is structured
      assert supports_phase3_semantics(profile, version) is phase3
      assert supports_phase4_semantics(profile, version) is phase4
  ```

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_language_snapshot.py`

  Expected: FAIL because v4 and cumulative predicates do not exist.

- [ ] **Step 2: Add the single version-capability authority.**

  Implement these exact helpers in `language.py`, using profile identity plus the minimum version instead of latest-version equality:

  ```python
  def supports_structured_outputs(profile, normalizer_version):
      return profile is WorkflowLanguageProfile.ARCHON_2026_07 and normalizer_version >= 2

  def supports_phase3_semantics(profile, normalizer_version):
      return profile is WorkflowLanguageProfile.ARCHON_2026_07 and normalizer_version >= 3

  def supports_phase4_semantics(profile, normalizer_version):
      return profile is WorkflowLanguageProfile.ARCHON_2026_07 and normalizer_version >= 4
  ```

  Set `LATEST_NORMALIZER_VERSION = 4` and support `{1, 2, 3, 4}`, but leave the Archon entry in `CURRENT_NORMALIZER_BY_PROFILE` at 3.

- [ ] **Step 3: Convert production `== 3`/`!= 3` feature branches to the predicates.**

  Preserve version-exact parsing only where a historical snapshot shape genuinely differs. Normalization must call v2, then v3 for `>= 3`, then an initially identity-preserving `_normalize_v4()` for version 4.

  ```python
  if supports_phase3_semantics(selection.effective_profile, normalizer_version):
      normalized_definition, structured_outputs, node_semantics = _normalize_v3(...)
  if supports_phase4_semantics(selection.effective_profile, normalizer_version):
      normalized_definition, structured_outputs, node_semantics = _normalize_v4(...)
  ```

  Run: `rg -n 'normalizer_version\s*(==|!=)\s*3' plugins/workflow`

  Expected: only explicit historical codec assertions remain, each covered by a v3 snapshot test.

- [ ] **Step 4: Prove v4 executes inherited Phase 3 behavior before adding Phase 4 syntax.**

  Explicitly load the same Archon definition as v3 and v4 and assert equal strict-reference, retry, timeout, Bash, structured-output, and missing-session semantics except for the recorded normalizer and semantic digest.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_phase3_bash_substitution.py tests/plugins/workflow/test_persistent_session_recovery.py`

  Expected: PASS after every inherited feature path uses the cumulative authority.

- [ ] **Step 5: Extend the durable code catalog for v4 applicability.**

  Add profile/version metadata without freezing a count. The behavior-linked completeness test must reject a v4 code registered without a real emitter test.

  ```python
  assert catalog["include_cycle"].minimum_normalizer_version == 4
  assert catalog["include_cycle"].effective_profile == "archon-2026-07"
  ```

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_code_catalog.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_language_schema.py`

- [ ] **Step 6: Commit the cumulative version foundation.**

  Run: `git diff --check`

  Commit: `refactor(workflow): make language capabilities cumulative`

---

### Task 2: Separate bounded source parsing from workflow compilation

**Files:**

- Create: `plugins/workflow/compilation.py`
- Create: `tests/plugins/workflow/test_phase4_compilation.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/discovery.py`
- Modify: `plugins/workflow/catalog_api.py`
- Test: `tests/plugins/workflow/test_schema.py`
- Test: `tests/plugins/workflow/test_catalog_api.py`
- Test: `tests/plugins/workflow/test_catalog_cli.py`

**Interfaces:**

- Produces: `parse_workflow_source_bytes(...) -> WorkflowSourceDocument`
- Produces: `WorkflowCatalogSnapshot.capture(sources: Iterable[WorkflowSourceDocument]) -> WorkflowCatalogSnapshot`
- Produces: `WorkflowCatalogSnapshot.select(name, catalog_source=None) -> WorkflowSourceDocument`
- Produces: `compile_workflow(root, catalog, normalizer_version=None) -> WorkflowCompilation`
- `WorkflowCompilation.package` remains the normalized `WorkflowPackage` consumed by compatibility and read-only projections.

- [ ] **Step 1: Add RED tests for source parsing without premature child authority.**

  Cover safe YAML, exact integer bounds, source lines, self-trust rejection, definition/sidecar byte capture, and parsing a child whose sidecar says legacy while the root requests v4.

  ```python
  source = parse_workflow_source_bytes(
      workflow_path,
      workflow_bytes=b"name: child\ndescription: Child\nnodes:\n  - id: x\n    prompt: hi\n",
      sidecar_bytes=b"language_compatibility: hermes-legacy\n",
      source="project",
      precedence=1,
  )
  assert source.name == "child"
  assert source.sidecar["language_compatibility"] == "hermes-legacy"
  assert source.nodes[0].source_line is not None
  ```

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_compilation.py tests/plugins/workflow/test_schema.py`

  Expected: FAIL because parsing and normalization are one operation.

- [ ] **Step 2: Add immutable source and compilation models.**

  Add frozen `WorkflowSourceNode`, `WorkflowSourceDocument`, and `WorkflowNodeOrigin` records in `models.py`. Define in `compilation.py`:

  ```python
  @dataclass(frozen=True, slots=True)
  class WorkflowCatalogSnapshot:
      selected: Mapping[str, WorkflowSourceDocument]
      ambiguous_names: frozenset[str]
      signatures: Mapping[str, str]

  @dataclass(frozen=True, slots=True)
  class WorkflowCompilation:
      package: WorkflowPackage
      definition_bytes: bytes
      active_policy_bytes: bytes
  ```

  Freeze nested values and reject absolute logical locations or unbounded metadata in `__post_init__`.

- [ ] **Step 3: Extract the safe source parser and preserve old public loaders.**

  Reuse `_WorkflowSafeLoader`, byte ceilings, source-line extraction, sidecar parsing, and portable name validation. `load_workflow()` and `load_workflow_snapshot()` call the new parser internally but return exact legacy/v1-v3 results.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_compilation.py tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_language.py`

- [ ] **Step 4: Capture catalog candidates once and compile no-include roots with parity.**

  Both discovery scanners retain their existing traversal/capacity/failure-isolation rules, parse their bounded candidate files into source documents, then pass those documents into the shared snapshot builder. Do not merge the scanners or weaken Desktop catalog isolation.

  ```python
  source_documents = tuple(
      parse_workflow_source_bytes(
          candidate.path,
          workflow_bytes=candidate.workflow_bytes,
          sidecar_bytes=candidate.sidecar_bytes,
          source=candidate.source,
          precedence=candidate.precedence,
      )
      for candidate in bounded_candidates
  )
  snapshot = WorkflowCatalogSnapshot.capture(source_documents)
  compiled = compile_workflow(snapshot.select("report"), snapshot, normalizer_version=3)
  assert compiled.package == load_workflow(report_path)
  ```

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_compilation.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_catalog_cli.py tests/plugins/workflow/test_discovery.py`

- [ ] **Step 5: Make parse-cache invalidation closure-ready.**

  Cache raw source documents by definition and sidecar content identity. Cache compiled roots by the ordered selected-source signatures supplied by later closure resolution; never cache solely by the root file.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_compilation.py tests/plugins/workflow/test_catalog_api.py`

- [ ] **Step 6: Commit the source/compiler boundary.**

  Run: `git diff --check`

  Commit: `refactor(workflow): separate source parsing from compilation`

---

### Task 3: Resolve and expand bounded include closures

**Files:**

- Create: `plugins/workflow/includes.py`
- Create: `tests/plugins/workflow/test_phase4_includes.py`
- Modify: `plugins/workflow/compilation.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/language_schema.py`
- Test: `tests/plugins/workflow/test_performance_bounds.py`

**Interfaces:**

- Produces: `WorkflowCompilationLimits`
- Produces: `ExpandedWorkflowSource`
- Produces: `expand_workflow_source(root, catalog, limits=DEFAULT_COMPILATION_LIMITS) -> ExpandedWorkflowSource`
- `ExpandedWorkflowSource.include_aliases` maps each include-instance ID to ordered entry IDs, sink IDs, and first sink.

- [ ] **Step 1: Add RED tests for the exact include authoring shape.**

  Accept only `id`, literal `include`, `depends_on`, and `trigger_rule`. Reject paths, URLs, variables, expressions, `with`, `when`, execution options, empty included graphs, and includes under a `loop_group` source shape.

  ```python
  root_path = workflow_writer(tmp_path / "root", name="root", nodes=[
      {"id": "build", "bash": "true"},
      {"id": "checks", "include": "reusable-checks", "depends_on": ["build"]},
  ])
  child_path = workflow_writer(
      tmp_path / "child", name="reusable-checks",
      nodes=[{"id": "lint", "bash": "true"}],
  )
  root = parse_workflow_source_bytes(
      root_path, workflow_bytes=root_path.read_bytes(),
      sidecar_bytes=b"language_compatibility: archon-2026-07\n",
      source="project", precedence=1,
  )
  child = parse_workflow_source_bytes(
      child_path, workflow_bytes=child_path.read_bytes(), sidecar_bytes=None,
      source="project", precedence=1,
  )
  expanded = expand_workflow_source(
      root, WorkflowCatalogSnapshot.capture((root, child)),
  )
  assert [node.id for node in expanded.nodes] == ["build", "checks__lint"]
  ```

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_includes.py tests/plugins/workflow/test_language_schema.py`

  Expected: FAIL because `include` is not a source directive.

- [ ] **Step 2: Add compile-only include metadata without adding a runtime node kind.**

  Keep `NODE_TYPES` as scheduler-executable kinds. Add a separate source-directive inventory so generated authoring contracts expose `include`, while final graph validation refuses any surviving include.

  ```python
  EXECUTABLE_NODE_TYPES = ("command", "prompt", "bash", "script", "loop", "approval", "cancel")
  COMPILE_DIRECTIVE_TYPES = ("include",)
  SOURCE_NODE_TYPES = (*EXECUTABLE_NODE_TYPES, *COMPILE_DIRECTIVE_TYPES)
  ```

- [ ] **Step 3: Implement bounded depth-first closure traversal.**

  Use authored order, a package-key active stack, depth root=0/direct=1, and deduplicated dependency records. Repeated package instances are allowed outside the active stack.

  ```python
  DEFAULT_COMPILATION_LIMITS = WorkflowCompilationLimits(
      max_include_depth=3,
      max_dependencies=64,
      max_nodes=512,
      max_edges=4096,
      max_source_bytes=2 * 1024 * 1024,
      max_expanded_bytes=2 * 1024 * 1024,
  )
  ```

  Emit `include_not_found`, `include_ambiguous`, `include_cycle`, `include_depth_exceeded`, and `include_dependency_limit` with bounded logical include chains.

- [ ] **Step 4: Implement namespace, entry, sink, and dependency rewrites.**

  Namespace recursively with `__`, calculate entries and sinks in definition order, attach include parent dependencies/trigger rule to entries, and replace downstream include dependencies with all sinks.

  ```python
  assert expanded.include_aliases["checks"].sinks == (
      "checks__unit", "checks__lint",
  )
  assert by_id["publish"].depends_on == ("checks__unit", "checks__lint")
  ```

  Reject any final authored/generated ID collision rather than banning authored `__`.

- [ ] **Step 5: Prove every traversal and graph limit at and above the boundary.**

  Add exact boundary tests for depth, dependencies, nodes, edges, source bytes, and canonical expanded bytes. Assert the resolver aborts while traversing and does not first materialize an oversized graph.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_includes.py tests/plugins/workflow/test_performance_bounds.py`

- [ ] **Step 6: Compile the expanded raw graph through root-profile v4 normalization.**

  Only root name, description, top-level defaults, language selection, and sidecar remain authoritative. Child top-level options and sidecars stay attached to provenance, not the expanded definition.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_includes.py tests/plugins/workflow/test_phase4_compilation.py tests/plugins/workflow/test_schema.py`

- [ ] **Step 7: Commit include graph expansion.**

  Run: `git diff --check`

  Commit: `feat(workflow): expand bounded include graphs`

---

### Task 4: Rewrite references and preserve source provenance

**Files:**

- Create: `tests/plugins/workflow/test_phase4_references.py`
- Modify: `plugins/workflow/includes.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/bash_rendering.py`
- Modify: `plugins/workflow/models.py`
- Test: `tests/plugins/workflow/test_strict_output_references.py`
- Test: `tests/plugins/workflow/test_structured_output_language.py`

**Interfaces:**

- Produces: `rewrite_reference_tokens(template, tokens, rename_node) -> str`
- Produces: `rewrite_expanded_node(node, namespace, include_aliases) -> WorkflowSourceNode`
- Preserves: final `WorkflowNode.source_index/source_line` plus logical `WorkflowNodeOrigin`

- [ ] **Step 1: Add RED tests for syntax-aware reference rewrites.**

  Cover prompt/command bodies, inline Bash/script, named scripts, `when`, loop prompt/`until_bash`/gate message, approval/rejection prompts, and existing hook/agent template fields. Include adjacent dollars, quotes, typed paths, Unicode literals, and malformed candidates.

  ```python
  template = "Use $producer.output.value and $$HOME"
  rewritten = rewrite_reference_tokens(
      template,
      tuple(iter_output_references(template, normalizer_version=4)),
      lambda node_id: f"checks__{node_id}",
  )
  assert rewritten == "Use $checks__producer.output.value and $$HOME"
  ```

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_strict_output_references.py`

  Expected: FAIL because strict reference functions only accept version 3 and no rewrite API exists.

- [ ] **Step 2: Generalize the inherited strict grammar to v4 through capability predicates.**

  Keep the exact ASCII grammar and spans. Accept normalizer 3 or 4 when `supports_phase3_semantics()` is true. Do not introduce a second regex or escape syntax.

- [ ] **Step 3: Implement reversed-span token rewriting.**

  Rebuild strings by replacing parsed node-ID spans from right to left. For Bash, use the existing Bash lexer-admitted spans. For `when`, rewrite only parsed LHS operands; quoted RHS remains literal.

  ```python
  for token in reversed(tokens):
      replacement = f"${rename_node(token.node_id)}.output" + (
          "." + ".".join(token.path) if token.path else ""
      )
      template = template[:token.start] + replacement + template[token.end:]
  ```

- [ ] **Step 4: Resolve include aliases and direct-child references.**

  `$checks.output` becomes the ordered first sink; `$checks.output.field` becomes that sink's typed path. `$checks.lint.output` and escaping child references fail `include_reference_invalid`. Re-run inherited direct-dependency and impossible-path validation on the final DAG.

- [ ] **Step 5: Preserve original logical provenance in diagnostics.**

  Attach origin package, source, precedence, logical definition path, original node index/line, include-instance path, and final ID. Validation errors report the root include site and child location without absolute paths.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_phase4_includes.py tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_structured_output_language.py`

- [ ] **Step 6: Expand root sidecar outward references.**

  Root executable IDs remain direct. A root include ID fans out to every expanded node in that instance. Deep child and unknown references are blocking. Child sidecar node references remain ignored provenance.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_trust_policy.py`

- [ ] **Step 7: Commit reference and provenance expansion.**

  Run: `git diff --check`

  Commit: `feat(workflow): rewrite included graph references`

---

### Task 5: Bind dependency resources and compute composite trust identity

**Files:**

- Create: `plugins/workflow/dependency_manifest.py`
- Create: `tests/plugins/workflow/test_phase4_dependency_manifest.py`
- Create: `tests/plugins/workflow/test_phase4_defensive_invariants.py`
- Modify: `plugins/workflow/compilation.py`
- Modify: `plugins/workflow/includes.py`
- Modify: `plugins/workflow/trust.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/compat.py`
- Test: `tests/plugins/workflow/test_trust_policy.py`
- Test: `tests/plugins/workflow/test_security_boundaries.py`

**Interfaces:**

- Produces: `WorkflowDependencyRecord`
- Produces: `WorkflowResourceBinding`
- Produces: `WorkflowDependencyManifest.to_dict()/from_dict()`
- Produces: `composite_workflow_digest(manifest) -> str`
- Extends: `WorkflowCompilation` with exact manifest, sealed files, covered paths, and composite digest

- [ ] **Step 1: Add RED codec tests for exact bounded dependency manifests.**

  Test canonical ordering, exact fields, SHA-256 shapes, logical paths, ignored sidecars, node origins, resource bindings, counts, bounds, and rejection of absolute/backslash/parent paths or extra fields.

  ```python
  manifest = WorkflowDependencyManifest.from_dict(raw)
  assert manifest.to_dict() == raw
  assert manifest.dependencies[0].sidecar_status == "authenticated_ignored"
  assert manifest.resources[0].snapshot_path.startswith("packages/")
  ```

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_dependency_manifest.py`

  Expected: FAIL because no format-2 manifest exists.

- [ ] **Step 2: Implement exact immutable manifest records.**

  Use schema version 1 and canonical JSON. Include root/dependency package digests, source/precedence, logical locations, include edges, ignored-policy fields, origins, resource source/compiled digests, snapshot paths, expansion counts, and digest inputs.

  Extend the Task 2 compilation contract with these exact fields:

  ```python
  @dataclass(frozen=True, slots=True)
  class WorkflowCompilation:
      package: WorkflowPackage
      definition_bytes: bytes
      active_policy_bytes: bytes
      dependency_manifest: WorkflowDependencyManifest
      sealed_files: Mapping[str, bytes]
      composite_digest: str
      covered_relative_paths: tuple[str, ...]
  ```

- [ ] **Step 3: Authenticate each package independently under one aggregate budget.**

  Extend `compute_package_digest()` or a focused helper to accept source-package bytes and origin. Cover definition, sidecar, command, named script, MCP resources, and loop-command resources. Reuse `WorkflowResourceReadBudget`; after `seal()`, any cache miss or changed identity fails.

  ```python
  budget = WorkflowResourceReadBudget(
      max_file_bytes=1 * 1024 * 1024,
      max_total_bytes=8 * 1024 * 1024,
      max_files=512,
  )
  ```

- [ ] **Step 4: Materialize collision-proof resource bindings.**

  Assign deterministic `packages/<package-key>/<relative-path>` paths using logical source/precedence/name plus digest. Rewrite final command/script/MCP/loop-command references to those paths. For command and named-script bodies containing child references, store authenticated source digest and rewritten compiled digest.

  ```python
  assert root_binding.snapshot_path != child_binding.snapshot_path
  assert root_binding.source_relative_path == child_binding.source_relative_path
  assert compilation.sealed_files[child_binding.snapshot_path] == rewritten_bytes
  ```

- [ ] **Step 5: Compute and test the exact composite digest.**

  Hash schema version, root package digest, sorted dependencies, expanded definition digest, node-origins digest, resource-bindings digest, and active root-policy digest. Exclude timestamps, inodes, absolute paths, and raw contents.

  Seed `test_phase4_defensive_invariants.py` with the independent containment suite: symlink escape rejection, manifest completeness for every authenticated path, changed-byte/digest rejection, same-name resource origin separation, and no absolute-path disclosure.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_dependency_manifest.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_trust_policy.py tests/plugins/workflow/test_security_boundaries.py`

- [ ] **Step 6: Extend trust/risk summaries for compilations.**

  `build_risk_summary()` accepts `compilation=` and uses its composite digest plus final expanded nodes. Project per-origin tools, skills, MCP, providers, shell/script nodes, outward nodes, and ignored child policy. Individual child trust never authorizes a new composition.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_dependency_manifest.py tests/plugins/workflow/test_trust_policy.py tests/plugins/workflow/test_compat_matrix.py`

- [ ] **Step 7: Commit immutable dependency identity.**

  Run: `git diff --check`

  Commit: `feat(workflow): seal include dependency identity`

---

### Task 6: Publish snapshot format 2 and revalidate complete closures

**Files:**

- Create: `tests/plugins/workflow/test_phase4_snapshot.py`
- Modify: `tests/plugins/workflow/test_phase4_defensive_invariants.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/scheduled_revalidation.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/api_admission.py`
- Modify: `plugins/workflow/gateway_command.py`
- Modify: `plugins/workflow/catalog_api.py`
- Modify: `plugins/workflow/showcase.py`
- Test: `tests/plugins/workflow/test_language_snapshot.py`
- Test: `tests/plugins/workflow/test_schedule_revalidation.py`
- Test: `tests/plugins/workflow/test_scheduled_runs.py`
- Test: `tests/plugins/workflow/test_crash_recovery.py`

**Interfaces:**

- Extends: `RunStore.prepare_run_snapshot(..., compilation: WorkflowCompilation | None = None)`
- Produces: `resolve_workflow_catalog_compilation(...) -> WorkflowCompilation | None`
- Produces: `load_snapshot_format2(...) -> WorkflowPackage`
- Preserves: format-1 writer/reader for v1-v3 runs

- [ ] **Step 1: Add RED snapshot-layout and tamper tests.**

  Admit explicit v4 and assert canonical expanded `definition.yaml`, root-only `policy.yaml`, `dependencies.json`, origin package files, format version 2, manifest digest, complete sealed paths, and composite public `definition_digest`.

  ```python
  run = store.load_run(run_id)
  assert run["snapshot_format_version"] == 2
  assert run["definition_digest"] == compilation.composite_digest
  assert (store.run_directory(run_id) / "dependencies.json").is_file()
  ```

  Tamper each authoritative file independently and assert fail-closed snapshot mismatch before execution.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_language_snapshot.py`

  Expected: FAIL because only format 1 exists.

- [ ] **Step 2: Extend snapshot preparation without changing legacy writes.**

  When `compilation is None`, preserve current bytes and format 1. For v4, write compiled definition/policy/manifest/sealed files from cached bytes, include manifest identity in `resources.json`, and calculate the existing sealed snapshot digest over every path.

  ```python
  prepared = store.prepare_run_snapshot(
      compilation.package,
      compilation=compilation,
      trusted_package_digest=WorkflowPackageDigest(
          compilation.composite_digest,
          compilation.covered_relative_paths,
      ),
      execution_limits=limits,
  )
  ```

- [ ] **Step 3: Implement exact format-2 reload in the scheduler.**

  Authenticate resources and dependencies first, load canonical definition with recorded normalizer 4 and root policy bytes, verify normalized/origin/resource/composite digests, and root resource resolution at the run directory. Never call discovery.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py`

- [ ] **Step 4: Migrate every admission boundary to carry `WorkflowCompilation`.**

  Add `_resolve_compilation()` beside CLI `_resolve()` and use compilation-aware catalog resolution in CLI run, API, Gateway, showcase, background, and schedule creation. Read-only callers may continue consuming `.package`.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_api_runtime.py tests/hermes_cli/test_authenticated_plugin_commands.py tests/plugins/workflow/test_notification_delivery.py tests/plugins/workflow/test_scheduled_runs.py tests/plugins/workflow/test_showcase_schedule_e2e.py`

- [ ] **Step 5: Recompute the exact closure for future scheduled admission.**

  Re-select from the recorded exact catalog root/source, compile once, compare composite package/risk/execution identities, and require existing trust flow on any definition, ignored sidecar, resource, source, or precedence change. Already-started runs continue from snapshot.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_schedule_revalidation.py tests/plugins/workflow/test_scheduled_runs.py`

- [ ] **Step 6: Prove source deletion and shadowing behavior.**

  Admit a root+child run, delete all source packages, restart, and complete from snapshot. Separately change child precedence before a future scheduled occurrence and assert revalidation blocks it. Mirror the no-live-read and shadowing assertions in the defensive suite so these properties remain independently gated.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_schedule_revalidation.py tests/plugins/workflow/test_crash_recovery.py`

- [ ] **Step 7: Commit snapshot format 2.**

  Run: `git diff --check`

  Commit: `feat(workflow): add sealed dependency snapshots`

---

### Task 7: Normalize v4 ordinary loops and sealed command prompts

**Files:**

- Create: `tests/plugins/workflow/test_phase4_loops.py`
- Modify: `plugins/workflow/language.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/compilation.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/trust.py`
- Modify: `plugins/workflow/compat.py`
- Test: `tests/plugins/workflow/test_loop_executor.py`
- Test: `tests/plugins/workflow/test_language_snapshot.py`

**Interfaces:**

- Extends v4 loop fields with `command` and `signal_completes`
- Produces exact `node_semantics[node_id]["loop"]`
- Produces sealed command binding consumable by `LoopExecutor`

- [ ] **Step 1: Add RED schema/default matrix tests.**

  Cover exactly-one prompt/command, booleans, gate message, root+loop interactivity, default and explicit signal completion, maximum 1/100, command existence, and v1-v3 unchanged behavior.

  ```python
  assert semantics["loop"] == {
      "prompt_source": "command",
      "command_binding": "packages/child/commands/refine.md",
      "effective_interactive": True,
      "signal_completes": False,
  }
  ```

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_loop_executor.py`

  Expected: FAIL because v4 loop fields and semantic projection do not exist.

- [ ] **Step 2: Add exact Phase 4 field inventory and compatibility metadata.**

  Make `prompt` conditionally required rather than globally required, add `command` and `signal_completes` at phase 4, and keep v1-v3 source rules exact. Remove only the now-implemented Phase 4 blockers.

- [ ] **Step 3: Implement `_normalize_v4()` loop semantics.**

  Calculate effective interactivity from root options plus loop value. Reject explicit false without an operator path. Store only prompt source, sealed binding, effective interactive, and effective signal completion.

  ```python
  effective_interactive = (
      definition.options.get("interactive") is True
      and loop.get("interactive") is True
  )
  signal_completes = loop.get("signal_completes", not effective_interactive)
  ```

- [ ] **Step 4: Resolve and parse `loop.command` during compilation.**

  Use the node origin package and the existing command resolver/parser. Reject missing, unreadable, invalid frontmatter, or empty body before admission. Store the rewritten body at its sealed binding; do not expose it in semantic metadata.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_phase4_dependency_manifest.py tests/plugins/workflow/test_security_boundaries.py`

- [ ] **Step 5: Round-trip and tamper-test v4 loop semantics.**

  Read/write exact field sets, reject mismatched binding/resource digests, and prove source command edits/deletion after admission do not change the run.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_phase4_snapshot.py`

- [ ] **Step 6: Commit v4 loop normalization.**

  Run: `git diff --check`

  Commit: `feat(workflow): normalize phase 4 ordinary loops`

---

### Task 8: Add durable signal-confirmation store actions

**Files:**

- Create: `tests/plugins/workflow/test_phase4_loop_interactions.py`
- Modify: `tests/plugins/workflow/test_phase4_defensive_invariants.py`
- Modify: `plugins/workflow/actions.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/models.py`
- Test: `tests/plugins/workflow/test_approval.py`
- Test: `tests/plugins/workflow/test_approval_races.py`
- Test: `tests/plugins/workflow/test_run_queries.py`

**Interfaces:**

- Adds pending interaction type `loop_signal_confirmation`
- Reuses `approve_run()` for acceptance and `provide_loop_input()` for feedback
- Produces `approve`, conditional `provide-input`, and `cancel` from `available_actions()`

- [ ] **Step 1: Add RED action and compare-and-set tests.**

  Construct paused signal interactions with result artifact identity. Assert ordinary iteration offers approve/provide-input/cancel, final iteration omits provide-input, stale/wrong/cross-run IDs mutate nothing, and duplicate decisions have one winner.

  ```python
  assert available_actions("paused", pending) == [
      "status", "events", "approve", "provide-input", "cancel",
  ]
  final_pending = {**pending, "iteration": 5, "max_iterations": 5}
  assert "provide-input" not in available_actions("paused", final_pending)
  ```

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_approval_races.py tests/plugins/workflow/test_run_queries.py`

  Expected: FAIL because the interaction type is unknown.

- [ ] **Step 2: Validate the exact pending interaction shape.**

  Bound message/path sizes and require type, SHA-256 interaction, iteration, maximum, result path, and result digest. Bind identity to run/node/iteration/result digest/gate message.

- [ ] **Step 3: Extend `approve_run()` to finalize the existing result.**

  Identify the matching interaction under the run lock. For signal confirmation, authenticate the recorded result artifact, clear pending state, mark the paused attempt/node succeeded, journal `loop_signal_accepted`, and request downstream scheduling. Do not load a mutable definition or re-enter an executor.

  ```python
  if pending_type == "loop_signal_confirmation" and decision == "approved":
      _verify_pending_loop_result(directory, projection, node, pending)
      node["state"] = "succeeded"
      node["attempts"][-1]["state"] = "succeeded"
  ```

- [ ] **Step 4: Extend `provide_loop_input()` for signal feedback.**

  Require nonempty bounded UTF-8, reject final-iteration feedback, write the existing input artifact form, clear pending signal, make ready, and journal `loop_feedback_provided`. Preserve current `loop_input` behavior, including its historical empty-string rule if tests require it.

- [ ] **Step 5: Make approval definition loading snapshot-version-aware.**

  Existing workflow approval/rejection paths must load sealed definition/policy bytes with the recorded normalizer rather than `load_workflow(directory / "definition.yaml")`. Keep signal acceptance independent of definition parsing.

  Add the stale-ID, cross-run-ID, final-iteration feedback, and concurrent-decision cases to the defensive suite as well as the feature tests.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_approval_races.py tests/plugins/workflow/test_phase4_snapshot.py`

- [ ] **Step 6: Commit durable signal actions.**

  Run: `git diff --check`

  Commit: `feat(workflow): add durable loop signal decisions`

---

### Task 9: Execute and recover the v4 loop state machine

**Files:**

- Modify: `plugins/workflow/executors/loop.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/evidence.py`
- Modify: `tests/plugins/workflow/test_phase4_loops.py`
- Modify: `tests/plugins/workflow/test_phase4_loop_interactions.py`
- Modify: `tests/plugins/workflow/test_phase4_defensive_invariants.py`
- Test: `tests/plugins/workflow/test_loop_executor.py`
- Test: `tests/plugins/workflow/test_crash_recovery.py`
- Test: `tests/plugins/workflow/test_shutdown_recovery.py`
- Test: `tests/plugins/workflow/test_parallel_scheduler.py`

**Interfaces:**

- `LoopExecutor.execute()` consumes sealed v4 loop semantics and command body
- `record_loop_iteration()` remains the pre-decision artifact/evidence boundary
- Paused result metadata carries the exact `loop_signal_confirmation`

- [ ] **Step 1: Add RED outcome-matrix tests with a counted provider runner.**

  Prove v3 immediate signal behavior, v4 immediate/default confirmation, explicit true, ordinary input, `until_bash`, final hard failure, marker removal, and zero extra calls after acceptance.

  ```python
  paused = scheduler.advance(run_id, max_nodes=1)
  assert calls == 1
  store.approve_run(run_id, expected_state_version=paused["state_version"],
                    interaction_id=paused["pending_interaction"]["interaction_id"])
  assert calls == 1
  assert store.get_run_status(run_id)["status"] == "succeeded"
  ```

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_loop_executor.py`

  Expected: FAIL because a signal always completes before interactive handling.

- [ ] **Step 2: Load inline or command prompt from sealed semantics.**

  Inline uses authored text. Command reads only the authenticated snapshot binding and parses/uses the already validated body. A missing binding during resume is snapshot integrity failure, not live fallback.

- [ ] **Step 3: Implement the exact iteration order.**

  Commit and clean the artifact, call `record_iteration`, then handle signal; only without a signal run `until_bash`. If another iteration remains, pause or continue. On the final non-completing iteration, return `loop_max_iterations` without an unusable input pause.

  ```python
  if completed and not signal_completes:
      return NodeExecutionResult(
          "paused", tuple(artifacts),
          metadata={"loop_state": state, "pending_interaction": interaction},
      )
  ```

- [ ] **Step 4: Restore prior output for feedback-driven resume.**

  Authenticate and read the previous output artifact for both ordinary interactive input and signal feedback, pass it as `$LOOP_PREV_OUTPUT`, consume `$LOOP_USER_INPUT` once, and preserve shared/fresh session rules.

- [ ] **Step 5: Add crash-window and coordinator-takeover tests.**

  Cover crashes before/after iteration journal, pause publication, approval, feedback readiness, and downstream scheduling. Assert no duplicate provider calls/artifacts and one authoritative concurrent approve/feedback/cancel winner. Put a counted-provider acceptance/restart invariant in the defensive suite so replay protection is not dependent on UI or feature fixtures.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_parallel_scheduler.py`

- [ ] **Step 6: Project bounded loop evidence.**

  Include iteration, completion mechanism, result artifact identity, interaction ID, and decision actor/channel where already supported. Exclude prompt, result body, and feedback.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_evidence_api.py`

- [ ] **Step 7: Commit the v4 runtime state machine.**

  Run: `git diff --check`

  Commit: `feat(workflow): execute confirmed ordinary loops`

---

### Task 10: Expose Phase 4 through CLI, Gateway, REST, evidence, and diagnostics

**Files:**

- Create: `tests/plugins/workflow/test_phase4_surfaces.py`
- Modify: `tests/plugins/workflow/test_phase4_defensive_invariants.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/gateway_command.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `plugins/workflow/actions.py`
- Modify: `plugins/workflow/evidence.py`
- Modify: `plugins/workflow/notifications.py`
- Modify: `plugins/workflow/catalog_api.py`
- Test: `tests/plugins/workflow/test_cli.py`
- Test: `tests/hermes_cli/test_authenticated_plugin_commands.py`
- Test: `tests/plugins/workflow/test_notification_delivery.py`
- Test: `tests/plugins/workflow/test_desktop_api.py`
- Test: `tests/plugins/workflow/test_evidence_api.py`
- Test: `tests/plugins/workflow/test_workflow_detail_api.py`

**Interfaces:**

- Reuses wire actions `approve`, `provide-input`, and `cancel`
- Adds bounded dependency/expansion projections to existing show/doctor/detail responses
- Adds Gateway `/workflow provide-input`

- [ ] **Step 1: Add RED cross-surface action parity tests.**

  For one paused signal run, assert CLI, Gateway, REST, attention, notifications, and evidence agree on interaction type, version, ID, and valid actions. Assert conflicts return current bounded public state.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_cli.py tests/hermes_cli/test_authenticated_plugin_commands.py tests/plugins/workflow/test_notification_delivery.py tests/plugins/workflow/test_desktop_api.py`

  Expected: FAIL because only approval/rejection Gateway decisions and generic loop input exist.

- [ ] **Step 2: Add Gateway feedback dispatch.**

  Extend the verified parser with `provide-input RUN --interaction-id ... --expected-version ... --value ...`, pass verified actor/channel/scope to `provide_loop_input()`, and preserve prompt command response bounds.

  ```python
  feedback = actions.add_parser("provide-input", add_help=False)
  feedback.add_argument("run_id")
  feedback.add_argument("--interaction-id", required=True)
  feedback.add_argument("--expected-version", type=int, required=True)
  feedback.add_argument("--value", required=True)
  ```

- [ ] **Step 3: Extend REST and attention handling for the new pending type.**

  Keep `ActionRequest` and mutation URLs unchanged. Add `loop_signal_confirmation` to attention/notification classification and rely on backend `next_actions`; do not derive actions in the client.

- [ ] **Step 4: Add compilation diagnostics.**

  `validate/show/doctor/catalog detail` expose normalizer/snapshot versions, dependencies, sources/precedence, counts, composite digest, ignored policies, logical origins, and per-origin risk. Use existing truncation envelopes and stable codes.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_doctor.py`

- [ ] **Step 5: Verify redaction and old-client action vocabulary.**

  Assert JSON and text outputs contain no temp-root absolute path, prompt/command body, feedback, secret value, or provider response. Assert action strings remain the old known verbs. Add the same disclosure assertions to `test_phase4_defensive_invariants.py` and run that module here.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_evidence_api.py`

- [ ] **Step 6: Commit Phase 4 backend surfaces.**

  Run: `git diff --check`

  Commit: `feat(workflow): expose phase 4 workflow state`

---

### Task 11: Add Desktop signal confirmation and dependency inspection

**Files:**

- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/app/workflows/run-inspector.tsx`
- Modify: `apps/desktop/src/app/workflows/attention-inbox.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`
- Modify: `apps/desktop/src/app/workflows/workflow-operations.e2e.test.tsx`
- Modify: `apps/desktop/src/i18n/ar.ts`
- Modify: `apps/desktop/src/i18n/en.ts`
- Modify: `apps/desktop/src/i18n/ja.ts`
- Modify: `apps/desktop/src/i18n/types.ts`
- Modify: `apps/desktop/src/i18n/zh-hant.ts`
- Modify: `apps/desktop/src/i18n/zh.ts`

**Interfaces:**

- Consumes backend `pending_interaction` and `next_actions`
- Sends existing `approve` and `provide-input` mutations with `expected_version` and `interaction_id`
- Does not parse workflow YAML or manifests

- [ ] **Step 1: Add RED renderer tests for interaction-aware labels.**

  A signal confirmation shows **Accept result**, **Continue with feedback**, and **Cancel**. A final-iteration confirmation has no feedback control. An ordinary `loop_input` retains **Provide input**.

  ```tsx
  expect(screen.getByRole('button', { name: 'Accept result' })).toBeEnabled()
  expect(screen.getByRole('button', { name: 'Continue with feedback' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Feedback'), { target: { value: 'Tighten it' } })
  expect(screen.getByRole('button', { name: 'Continue with feedback' })).toBeEnabled()
  ```

  Run: `cd apps/desktop && npm test -- src/app/workflows/index.test.tsx src/app/workflows/workflow-operations.e2e.test.tsx`

  Expected: FAIL because action labels are interaction-agnostic.

- [ ] **Step 2: Add a narrow interaction type guard.**

  Keep the public transport type forward-compatible. Add a local guard that checks only bounded fields needed for presentation; unknown shapes fall back to generic action labels.

  ```typescript
  interface LoopSignalConfirmation {
    interaction_id: string
    iteration: number
    max_iterations: number
    type: 'loop_signal_confirmation'
  }
  ```

- [ ] **Step 3: Render existing wire actions with Phase 4 labels.**

  Map `approve` to accept and `provide-input` to feedback only for the signal type. Keep double-submit disabling, 409 refresh, background attention, and terminal pane behavior unchanged.

- [ ] **Step 4: Present bounded dependency details from backend projections.**

  Show source/precedence, counts, composite digest, and ignored-policy badges in existing workflow detail/overview structures. Do not expose or fetch filesystem paths and do not create a second YAML/compiler implementation.

- [ ] **Step 5: Verify old/new skew and i18n completeness.**

  Test a new interaction on generic old action vocabulary, an unknown future interaction, a backend without dependency fields, and every locale key.

  Run: `cd apps/desktop && npm test -- src/app/workflows/index.test.tsx src/app/workflows/workflow-operations.e2e.test.tsx src/app/workflows/view-workflow-dialog.test.tsx`

  Run: `cd apps/desktop && npm run typecheck`

- [ ] **Step 6: Commit Desktop support.**

  Run: `git diff --check`

  Commit: `feat(desktop): support confirmed workflow loops`

---

### Task 12: Publish Phase 4 contracts, docs, and explicit-v4 installed flows

**Files:**

- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/compat.py`
- Modify: `website/docs/user-guide/features/workflow-yaml-reference.md`
- Modify: `website/docs/user-guide/features/workflows.md`
- Modify: `skills/software-development/workflow-builder/references/portable-schema.md`
- Modify: `skills/software-development/workflow-builder/references/authoring-checklist.md`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `tests/plugins/workflow/test_portable_compatibility_e2e.py`
- Modify: `tests/plugins/workflow/test_showcase_distribution_e2e.py`
- Test: `tests/agent/test_workflow_builder_skill.py`
- Test: `tests/skills/test_workflow_operator_behavior.py`

**Interfaces:**

- Explicit-v4 generated contracts become the staged authoritative Phase 4 syntax inventory
- Default generated contracts and new unversioned admissions remain v3 until Task 14

- [ ] **Step 1: Add RED generated-contract relationship tests.**

  Assert include is compile-only, loop prompt/command exactly-one is represented, signal defaults and interaction requirements are documented, stable codes have behavior coverage, and Phase 4 blockers disappear without brittle enumeration counts.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_code_catalog.py tests/plugins/workflow/test_language_schema.py tests/agent/test_workflow_builder_skill.py tests/skills/test_workflow_operator_behavior.py`

- [ ] **Step 2: Make contract generation version-explicit and update operator documentation.**

  Allow tests and installed validation to request the supported v4 contract without changing the current-profile mapping. Document authoring examples, root-policy authority, child ignored sidecars, exact bounds, entry/sink behavior, first-sink output, immutable resources, signal actions, final-iteration behavior, diagnostics, and deliberate differences from later live Archon features.

- [ ] **Step 3: Add installed-wheel end-to-end fixtures.**

  Build/install outside the repository, create a temporary `HERMES_HOME` with root+child packages and a loop command, validate/trust/admit, remove source, resume from snapshot, approve the signal, and inspect evidence. Assert new modules and documentation assets are in the wheel.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py tests/plugins/workflow/test_portable_compatibility_e2e.py tests/plugins/workflow/test_showcase_distribution_e2e.py`

  Expected before activation: explicit v4 works but new Archon packages still select v3.

- [ ] **Step 4: Run the complete focused Phase 4 gate before activation.**

  Run: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase4_compilation.py tests/plugins/workflow/test_phase4_includes.py tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_phase4_dependency_manifest.py tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_phase4_code_catalog.py`

  Expected: all PASS with Archon current version still 3.

- [ ] **Step 5: Commit staged contracts without activating v4.**

  Run: `git diff --check`

  Commit: `feat(workflow): publish phase 4 language contracts`

---

### Task 13: Complete defensive, distribution, and base verification gates

**Files:**

- Modify: `tests/plugins/workflow/test_phase4_defensive_invariants.py`
- Create: `docs/reviews/2026-08-05-workflow-language-phase-4-validation.md`

**Interfaces:**

- Produces exact verification evidence and exclusions
- Does not change feature semantics unless a failing mandatory invariant proves a defect

- [ ] **Step 1: Complete and run the mandatory benign defensive invariants.**

  Test symlink/containment, manifest completeness, source/compiled digests, same-name resource origins, no live reads after admission, every exact bound, shadowing, admitted-source deletion, stale/cross-run interactions, no provider replay, and redacted diagnostics.

  In this test module, add local `admit_composed_workflow()` and
  `restart_scheduler_and_complete()` helpers around the existing
  `workflow_writer`, `RunStore`, and `RunScheduler` fixtures. Track live source
  reads with a monkeypatch-owned `live_source_open_attempts` list that records
  only paths under the removed dependency root.

  ```python
  def test_admitted_run_never_reopens_deleted_dependency(...):
      admitted = admit_composed_workflow(...)
      shutil.move(child_root, quarantined_child_root)
      terminal = restart_scheduler_and_complete(admitted.run_id)
      assert terminal["status"] == "succeeded"
      assert live_source_open_attempts == []
  ```

  Run: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_performance_bounds.py tests/plugins/workflow/test_approval_races.py tests/plugins/workflow/test_crash_recovery.py`

- [ ] **Step 2: Run the full Python base gate without file retries.**

  Run: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh`

  Record exact file/test counts, duration, failures, retries, and exclusions in the validation report.

- [ ] **Step 3: Run complete Desktop gates.**

  Run: `cd apps/desktop && npm run typecheck`

  Run: `cd apps/desktop && npm test`

  Run: `cd apps/desktop && npm run lint`

  Record exact Vitest file/test counts and any pre-existing lint baseline separately from Phase 4 changes.

- [ ] **Step 4: Run distribution, schema, merge, and customization gates.**

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_installed_distribution_e2e.py tests/plugins/workflow/test_showcase_distribution_e2e.py tests/scripts/test_check_upstream_customizations.py tests/scripts/test_workflow_merge_gate.py tests/scripts/test_workflow_upstream_merge.py tests/test_desktop_workflow_test_gate.py`

- [ ] **Step 5: Run functional adversarial review and resolve real findings.**

  Review normalizer inheritance, include premise/intent, closure determinism, snapshot resume, signal replay, client skew, and scope. Classify findings Critical/High/Medium/Low, reproduce each against current branch, fix the bug class with a RED test, and commit each coherent fix atomically. No Critical or High finding may remain unresolved.

- [ ] **Step 6: Attempt the bounded defensive security review once.**

  Limit the request to containment, digest completeness, trust revalidation, stale-action fencing, disclosure, and crash/replay properties using the already-written benign tests. If Codex stops at its platform gate, do not rephrase to evade it. Record:

  ```text
  Result: BLOCKED_BY_PLATFORM_GATE
  Attempted scope: containment, digest coverage, trust, CAS actions, disclosure, replay
  Mandatory defensive suite: <actual command and result>
  Exclusion: adversarial model review did not complete and is not reported as passed
  ```

  A failing deterministic invariant remains blocking; the documented platform gate alone does not.

- [ ] **Step 7: Record the pre-activation verification evidence.**

  Record the exact commands, counts, durations, findings, exclusions, and security-review result. State explicitly that Archon current remains v3 and Phase 4 is ready for—not yet through—activation.

- [ ] **Step 8: Commit pre-activation verification evidence.**

  Run: `git diff --check`

  Run: `git status --short`

  Commit: `docs(workflow): verify phase 4 before activation`

  Expected state: all pre-activation gates green, no unresolved Critical/High findings, security review either PASS or explicitly `BLOCKED_BY_PLATFORM_GATE`, current Archon version still 3, and no push/merge performed.

---

### Task 14: Atomically activate v4 and produce the final handoff

**Files:**

- Modify: `plugins/workflow/language.py`
- Modify: `tests/plugins/workflow/test_phase4_language.py`
- Modify: `tests/plugins/workflow/test_phase3_language.py`
- Modify: `tests/plugins/workflow/test_language_schema.py`
- Modify: `tests/plugins/workflow/test_language_snapshot.py`
- Modify: `tests/plugins/workflow/test_cli.py`
- Modify: `tests/plugins/workflow/test_portable_compatibility_e2e.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `docs/reviews/2026-08-05-workflow-language-phase-4-validation.md`
- Modify: `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/continue.md`
- Modify: `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/progress.md`

**Interfaces:**

- Changes only new Archon admissions from normalizer v3 to v4
- Preserves `hermes-legacy` v2 and sealed v1-v3 resume behavior
- Produces final post-activation evidence and an honest branch handoff

- [ ] **Step 1: Verify the activation preconditions and add the RED current-version test.**

  Require the committed Task 13 report to show every mandatory pre-activation gate green and no unresolved Critical/High finding. Change current-version assertions to require v4 for new Archon packages while retaining v2 legacy and explicit/sealed v3 behavior.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_language_snapshot.py`

  Expected: FAIL only on current Archon selection/contract assertions because the mapping is still 3.

- [ ] **Step 2: Activate v4 with the one public mapping change.**

  ```python
  CURRENT_NORMALIZER_BY_PROFILE = MappingProxyType({
      WorkflowLanguageProfile.HERMES_LEGACY: 2,
      WorkflowLanguageProfile.ARCHON_2026_07: 4,
  })
  ```

  Do not change legacy selection, supported snapshot readers, or compatibility behavior in this step.

- [ ] **Step 3: Prove new-source, old-snapshot, trust, and installed behavior.**

  Test existing source YAML under the new current runtime, sealed v1-v3 resume, trust identity transition, generated contract v4, and an installed-wheel default Archon admission.

  Run: `scripts/run_tests.sh tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_trust_policy.py tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_portable_compatibility_e2e.py tests/plugins/workflow/test_installed_distribution_e2e.py`

- [ ] **Step 4: Re-run the complete focused and Python base gates after activation.**

  Run: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase4_compilation.py tests/plugins/workflow/test_phase4_includes.py tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_phase4_dependency_manifest.py tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_phase4_code_catalog.py`

  Run: `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh`

  The activation is not complete if either gate fails. Fix the bug class with a RED test and rerun both gates.

- [ ] **Step 5: Commit the atomic activation.**

  Run: `git diff --check`

  Commit: `feat(workflow): activate phase 4 language semantics`

- [ ] **Step 6: Reconcile final validation and handoff artifacts.**

  Append exact post-activation counts, durations, commit IDs, and exclusions to the validation report. Correct stale Phase 3 integration text, record the Phase 4 branch/commits/gates, and leave no claim that integration to `base` occurred unless Git proves it. Do not edit unrelated SDD histories.

  Run: `git diff --check`

  Commit: `docs(workflow): record phase 4 activation evidence`

- [ ] **Step 7: Confirm the final branch state.**

  Run: `git status --short`

  Run: `git branch --show-current`

  Expected handoff: clean `feat/workflow-language-phase-4-ordinary-loops-immutable-includes`, all mandatory post-activation gates green, new Archon admissions on v4, old snapshots resumable, security review either PASS or explicitly `BLOCKED_BY_PLATFORM_GATE`, no unresolved Critical/High findings, and no push/merge performed.

---

## Plan Self-Review Checklist

- Every normalizer, loop, include, dependency, trust, snapshot, action, surface, Desktop, documentation, distribution, and defensive requirement from the approved specification maps to a numbered task.
- V4 remains explicitly test-only through Task 13; only Task 14 changes the current Archon new-run version after all pre-activation gates pass.
- `WorkflowCompilation`, dependency-manifest, capability-predicate, and action names are consistent across producer and consumer tasks.
- Every production task starts with a failing behavior test, names a focused command, ends with GREEN verification, and has an atomic commit.
- No task asks an executor to discover includes or read live source after admission.
- No child sidecar field becomes active, and its authenticated ignored status participates in composite identity.
- The plan adds no core tool, new scheduler, speculative hook, runtime child workflow, or Phase 5/6 behavior.
- Mandatory defensive evidence is independent of the optional adversarial model review and remains blocking when it fails.
