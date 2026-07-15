# Archon-Compatible Workflow Orchestration Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Use subagents only when the user explicitly authorizes delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a production-grade, Archon-shaped workflow runtime for Co-worker that reuses Hermes agents, skills, tools, MCP, hooks, approvals, and cron; provides first-class workflow/run discovery and status without a visual editor; and keeps Hermes-core customization small, generic, and mergeable.

**Architecture:** An additive `workflow` plugin owns compatibility, discovery, graph execution, durable state, resources, and operator commands. Generic `workflow` and `workflow-builder` skills provide chat activation and authoring. A narrowly scoped `PluginContext.agent` facade plus a generic managed-process-tree primitive are the only planned upstream-Hermes core seams; they run each agent request in a fresh host-owned worker process with bounded deadlines, shutdown, escalation, and reaping so workflow concurrency cannot mutate the parent process's global tool, MCP, hook, or working-directory state or leave orphaned descendants. Every core touch is recorded and merge-tested.

**Tech Stack:** Python 3.11+, PyYAML, dataclasses, `jsonschema` from Hermes' existing `mcp`/`all` install path, Hermes plugin/skill/cron/MCP infrastructure, pytest, Node test runner for capability vendoring and brand generation.

## Global Constraints

- No Pi-framework code, state, manifests, or extension APIs may be imported or copied.
- The portable YAML shape follows Archon's `nodes:` DAG format; the removed sequential `steps:` format is rejected.
- Existing Ericsson workflow YAML is replaced; no deployed-run migration or dual-schema runtime is required.
- The parent conversation's system prompt and tool schema remain byte-stable. `context: fresh` always uses an isolated node session.
- `context: shared` requires an exact cache fingerprint match and reuses the snapshotted system prompt/tool schemas byte-for-byte; a cache-affecting mismatch must fail validation with guidance to use `fresh`.
- No new model-facing core tool is added.
- Behavioral settings live in `config.yaml`; credentials alone may use secret environment storage.
- The plugin is opt-in through existing `plugins.enabled`; there is no workflow-specific loader exception. Ericsson capability staging enables it, while general profiles receive the existing plugin-enable remediation.
- A lean install lacking `jsonschema` must fail closed before any `output_format` or per-node MCP work and report how to install Hermes' existing `mcp` extra. Schema validation is never silently skipped.
- Workflow execution must be bounded by concurrency, timeout, retry, iteration, output, and artifact limits.
- AI idle timeout, hard node wall deadline, provider-request timeout, deterministic-process timeout, parent/child wait deadline, shutdown grace, and kill/reap grace are distinct. Omitted configuration resolves to bounded defaults; `None` never means wait forever.
- A lease heartbeat proves ownership only and never resets semantic idle time. Every child deadline is no later than its parent's remaining deadline or the workflow global deadline.
- Every spawned worker/descendant is coordinator-owned, identity-guarded, and reaped. Owning Hermes shutdown must stop admission, persist interruption, terminate process trees, and exit within a bounded deadline.
- Provider failure, network loss, parent/child IPC loss, laptop suspend/wake, PID reuse, and forced restart must produce explicit recoverable states rather than silent success or indefinite waiting.
- Retry backoff holds no worker or scheduler slot and obeys one combined provider/workflow attempt budget.
- Per-worker process-tree memory, descendants, output, artifacts, event/run storage, open descriptors/handles, and retention are bounded and observable.
- State transitions must be race-safe across threads and processes on POSIX and Windows-supported paths.
- No lock may be held while model, tool, hook, MCP, shell, or script work executes.
- AI nodes never run as parallel threads in the parent Hermes process. Each uses a bounded worker process started without forking a live multithreaded runtime.
- Every task uses test-first development, focused regression tests, and a separately reviewable commit.
- Core-seam commits remain separate from plugin, skill, capability, and workflow commits.
- Existing upstream-file modifications are budgeted to `tools/registry.py`, `hermes_cli/plugins.py`, and `tools/process_registry.py`. The latter consumes one new generic `tools/managed_process.py` primitive so workflow code reuses Hermes' existing process-identity, tree-termination, escalation, and reaping behavior. Any additional upstream-file touch requires an explicit design and customization-ledger amendment before coding.
- Every modified upstream-owned core file is listed in `docs/upstream-customizations/workflow-orchestration.yaml` with tests, merge guidance, and a removal condition.
- A task is not complete until focused tests, static checks, and `git diff --check` pass.

---

## Delivery Roadmap

- [ ] **S01: Public plugin agent runner** `risk:high` `depends:[]`
  > After this: an enabled test plugin can run a fresh Hermes tool-using worker process with enforced model/tool policy and distinct idle/wall/provider deadlines through a documented host facade; timeout, cancel, coordinator loss, and shutdown terminate and reap its process tree without mutating caller state.
- [ ] **S02: Archon package discovery and validation** `risk:high` `depends:[]`
  > After this: `hermes workflow list|show|validate` discovers and explains project/profile/global packages, their topology/requirements, and exact portable, mapped, and unsupported fields without making model or network calls.
- [ ] **S03: Durable bash DAG tracer** `risk:high` `depends:[S02]`
  > After this: `hermes workflow run` executes and resumes a two-node bash DAG with snapshots, artifacts, journaled state, and cross-process-safe claims; `runs|status|events` exposes active/recent progress and sanitized diagnostics from the materialized store.
- [ ] **S04: Command and prompt AI nodes** `risk:high` `depends:[S01,S03]`
  > After this: an Archon command template and inline prompt execute through isolated Hermes agents with variable substitution, structured output validation, artifacts, and fresh/shared context.
- [ ] **S05: Parallel scheduling, retries, and crash recovery** `risk:high` `depends:[S03,S04]`
  > After this: independent nodes run with bounded parallelism while duplicate schedulers, stale workers, provider/network stalls, coordinator loss, shutdown, suspend/wake, host crashes, trigger rules, retries, and resume converge without duplicate completion, leaked workers, or occupied backoff slots.
- [ ] **S06: Script, loop, and cancel nodes** `risk:medium` `depends:[S04,S05]`
  > After this: named and inline scripts, bounded AI loops, `until_bash`, interactive loop pauses, and cancel nodes follow Archon-shaped semantics.
- [ ] **S07: Durable approval and rejection rework** `risk:high` `depends:[S05,S06]`
  > After this: approval decisions survive restart, race safely, capture responses, and perform bounded `on_reject` rework before re-pausing.
- [ ] **S08: Per-node tools, skills, hooks, MCP, and provider policy** `risk:high` `depends:[S01,S04,S05]`
  > After this: a node receives only its declared tools, skills, hook policy, and MCP servers, with explicit diagnostics for unsupported provider-specific fields and guaranteed cleanup.
- [ ] **S09: Chat, gateway, desktop, and cron activation** `risk:medium` `depends:[S07,S08]`
  > After this: natural chat, `/workflow`, `hermes workflow`, and scheduled jobs all discover workflows; explain what they do; list active/recent/waiting runs; inspect status/failure; and operate the same run/approval lifecycle without adding a permanent model tool.
- [ ] **S10: Workflow authoring and compatibility doctor** `risk:medium` `depends:[S02,S04,S06,S07,S08]`
  > After this: the builder skill creates a complete Archon-shaped package and the doctor explains every resource, mapping, warning, and execution blocker before a run starts.
- [ ] **S11: Ericsson package conversion and branded distribution** `risk:medium` `depends:[S09,S10]`
  > After this: the ticket and inbox workflows are portable Archon-shaped packages staged atomically for OTTO and LOOP24 with Ericsson-only policy outside the portable YAML.
- [ ] **S12: Production and upstream-merge release gate** `risk:high` `depends:[S05,S06,S07,S08,S09,S10,S11]`
  > After this: fault, race, shutdown, soak/leak, storage-retention, security, performance, cross-platform, operator-surface, end-to-end, and upstream-merge rehearsal gates pass before the feature reaches either branded branch.

## Boundary Map

- `S01` produces `PluginAgentRunner`, `PluginAgentRunRequest`, `PluginAgentRunResult`, a fresh worker-process protocol, `ManagedProcessTree`, deadline/termination policies, and enforced name-level tool filtering. `S04`, `S05`, `S06`, and `S08` consume them.
- `S02` produces immutable workflow definitions, discovery precedence, compatibility reports, and resolved package roots. Every later workflow slice consumes them.
- `S03` produces `RunStore`, `RunScheduler`, `NodeClaim`, event records, artifact metadata, bash execution, catalog-independent run queries, stable status JSON, and bounded retention/cleanup. `S04`–`S12` extend rather than bypass these contracts.
- `S04` produces AI-node execution, command resolution, structured outputs, and node session lineage. `S05`, `S06`, `S08`, and `S10` consume them.
- `S05` makes scheduling and claims production-safe. Approval, loop, cron, and distribution work cannot ship before it.
- `S06` produces script/loop/cancel executor contracts. `S07` reuses the loop pause model for rejection rework.
- `S07` produces compare-and-set approval decisions and resumable gates. `S09` exposes them to users.
- `S08` produces scoped execution workers and field-level compatibility mapping. `S10` surfaces those mappings during authoring.
- `S09` produces scope-authorized natural-language/slash/CLI entry points for catalog inspection, run inspection, actions, and cron lifecycle behavior. `S11` enables them for branded capability packages.
- `S10` produces package authoring and doctor output. `S11` uses both to convert Ericsson fixtures.
- `S12` consumes the assembled system and proves it against real process boundaries and the branch topology.

## Planned File Structure

```text
agent/
├── plugin_agent.py
└── plugin_agent_worker.py
tools/
└── managed_process.py
plugins/workflow/
├── __init__.py
├── plugin.yaml
├── cli.py
├── compat.py
├── discovery.py
├── locks.py
├── models.py
├── resources.py
├── scheduler.py
├── schema.py
├── store.py
└── executors/
    ├── __init__.py
    ├── ai.py
    ├── approval.py
    ├── base.py
    ├── bash.py
    ├── cancel.py
    ├── loop.py
    └── script.py
skills/productivity/workflow/
└── SKILL.md
skills/software-development/workflow-builder/
├── SKILL.md
└── references/
docs/upstream-customizations/
├── README.md
└── workflow-orchestration.yaml
tests/plugins/workflow/
├── fixtures/
└── test_*.py
```

## Task 1: Public Plugin Agent Runner and Customization Ledger

**Files:**
- Create: `agent/plugin_agent.py`
- Create: `agent/plugin_agent_worker.py`
- Create: `tools/managed_process.py`
- Create: `tests/agent/test_plugin_agent.py`
- Create: `tests/tools/test_managed_process.py`
- Create: `docs/upstream-customizations/README.md`
- Create: `docs/upstream-customizations/workflow-orchestration.yaml`
- Create: `scripts/check_upstream_customizations.py`
- Create: `tests/scripts/test_check_upstream_customizations.py`
- Modify: `tools/registry.py`
- Modify: `tools/process_registry.py`
- Modify: `hermes_cli/plugins.py`

**Interfaces:**
- Produces: `PluginAgentRunRequest`, `PluginAgentRunResult`, `PluginAgentRunner.run(request)`, and `PluginContext.agent`.
- Produces: `ProcessIdentity`, `TerminationPolicy`, and `ManagedProcessTree`, with identity-guarded cross-platform terminate/escalate/wait/reap semantics shared with the existing terminal `ProcessRegistry`.
- Produces: `ToolRegistry.scoped_names(allowed_names=None, denied_names=())`, a reversible process-worker scope covering discovery, lookup, Tool Search, and dispatch with generation-based cache invalidation.
- Produces: `check_upstream_customizations.py --manifest PATH --diff RANGE` for ledger coverage and optional `--upstream-diff RANGE` overlap reporting against the ledger-owned core surface.
- Consumes: Hermes runtime provider resolution, `AIAgent`, `SessionDB`, skill payload loading, and existing callbacks.

- [ ] **Step 1: Write failing tool-filter and runner contract tests**

```python
def test_allowed_and_denied_tools_are_enforced_before_first_call(fake_registry):
    with fake_registry.scoped_names(
        allowed_names={"read_file", "terminal"},
        denied_names={"terminal"},
    ):
        assert fake_registry.get_entry("read_file") is not None
        assert fake_registry.get_entry("terminal") is None


def test_plugin_runner_returns_usage_without_exposing_credentials(fake_runtime):
    result = PluginAgentRunner("test-plugin").run(
        PluginAgentRunRequest(prompt="Use read_file once", allowed_tools=("read_file",))
    )
    assert result.status == "completed"
    assert result.session_id
    assert "api_key" not in result.audit
```

Add process-boundary tests that run two workers with disjoint tool policies concurrently, verify denied deferred tools never appear through Tool Search, time out one worker with descendants, and assert the parent process's `_last_resolved_tool_names`, registry generation, working directory, hooks, and environment are unchanged. Force a dangerous command and prove it pauses or denies rather than taking Hermes' bare non-interactive auto-approval path.

Add managed-process tests for PID/start-identity capture, process-group/job ownership, parent IPC EOF, cooperative cancel, TERM then KILL escalation, mandatory `wait()`/reap, descendant cleanup, PID reuse refusal, already-exited children, spawn failure, and idempotent repeated cleanup. Cover POSIX with real child processes and Windows behavior with focused simulation of `taskkill /T /F` and handle cleanup. A deadline omission must resolve before spawn and no public wait/cleanup path may interpret `None` as infinite.

- [ ] **Step 2: Run the focused tests and confirm the missing contracts fail**

Run: `python3 -m pytest tests/agent/test_plugin_agent.py tests/tools/test_managed_process.py -q`

Expected: FAIL because `agent.plugin_agent`, the managed-process primitive, and name-level tool filters do not exist.

- [ ] **Step 3: Add immutable runner request/result types and host-owned resolution**

```python
@dataclass(frozen=True)
class PluginAgentRunRequest:
    prompt: str
    provider: str | None = None
    model: str | None = None
    context_mode: Literal["fresh", "shared"] = "fresh"
    session_id: str | None = None
    enabled_toolsets: tuple[str, ...] | None = None
    allowed_tools: tuple[str, ...] | None = None
    denied_tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    workdir: Path | None = None
    max_iterations: int = 90
    idle_timeout_seconds: float = 300.0
    wall_timeout_seconds: float = 1800.0
    provider_request_timeout_seconds: float = 300.0


class PluginAgentRunner:
    def run(self, request: PluginAgentRunRequest) -> PluginAgentRunResult:
        """Run a host-owned agent worker and return sanitized output."""
```

The facade validates and size-bounds the request, then starts `sys.executable -m agent.plugin_agent_worker` without a shell and exchanges versioned, length-bounded JSON frames. The child revalidates the request, resolves credentials, constructs `AIAgent`, and returns sanitized progress/result frames. Cancellation and timeout terminate the worker process group and descendants. No raw credential is serialized across IPC.

Install explicit worker callbacks for dangerous-tool approval, clarification, sudo, and skill secret capture. Approval/clarification events are sanitized and digest-bound; no-handler behavior is fail-closed. A pause terminates the worker and returns `status="paused"` plus a bounded pending-interaction descriptor. Resume grants only an exact one-shot action digest or supplies a stored clarification answer. Sudo and secret values are never persisted or exposed to the plugin; unresolved requests return standard setup guidance. Preserve existing hardline blocks, deny rules, approval mode, and `approvals.cron_mode`.

Reject empty prompts, unknown tools, invalid shared session IDs, non-directory workdirs, non-positive budgets, and plugin-disallowed provider/model overrides before starting billable work. `denied_tools` takes precedence over `allowed_tools` when both contain the same name.

- [ ] **Step 4: Extract and adopt a generic managed-process-tree primitive**

Move the reusable, behavior-preserving subset of process identity, PID-reuse protection, POSIX child-first tree termination, Windows `taskkill /T /F`, bounded TERM-to-KILL escalation, and wait/reap logic from `tools/process_registry.py` into `tools/managed_process.py`. Keep terminal background-process checkpointing, output buffering, watchers, and session UX in `ProcessRegistry`; change it to consume the generic primitive and pass all existing `tests/tools/test_process_registry.py` unchanged before workflow code uses it.

`ManagedProcessTree.spawn()` accepts argv only, uses no shell, creates a new process group/job boundary, records identity before returning, and installs explicit stdout/stderr/IPC bounds. `terminate(reason)` is idempotent and follows cooperative cancel -> bounded grace -> TERM tree -> bounded grace -> KILL tree -> `wait()`/reap. `close()` cannot return with an unreaped owned child. The worker protocol holds a coordinator-lifeline descriptor; coordinator EOF causes child-side cancellation and cleanup, while coordinator shutdown enumerates and closes every tracked tree. Startup failure and a child that exits between identity capture and monitoring both produce terminal typed outcomes.

- [ ] **Step 5: Verify and commit the generic process extraction independently**

```bash
python3 -m pytest tests/tools/test_managed_process.py tests/tools/test_process_registry.py -q
git diff --check
git add tools/managed_process.py tools/process_registry.py tests/tools/test_managed_process.py
git commit -m "refactor(tools): extract managed process tree"
```

This commit contains no workflow or Ericsson naming and can be proposed/rebased upstream independently of the plugin-agent facade.

- [ ] **Step 6: Add a generic scoped registry view and enforce the final agent scope**

Add `ToolRegistry.scoped_names()` under the registry's existing reentrant lock. It distinguishes `None` from an empty allowlist, applies deny last, preserves registry order, affects snapshots/get-entry/dispatch, generation-bumps on enter/exit, restores in `finally`, and rejects overlapping incompatible scopes. Registrations and MCP refreshes may continue, but newly registered names remain hidden unless the active predicate permits them.

The generic worker accepts only final Hermes tool names, enters the registry scope before constructing `AIAgent`, keeps it active for the entire run, and then verifies/prunes the final `agent.tools` and `agent.valid_tool_names` for any agent-owned non-registry schemas before the first model call. The workflow plugin resolves Archon aliases before it constructs the request in Task 8. Because Tool Search and unwrap query the scoped registry, no model-tools or tool-executor signature change is required.

- [ ] **Step 7: Expose the runner as a lazy `PluginContext.agent` property**

Mirror `PluginContext.llm`; the plugin ID is fixed at facade construction and the facade never returns raw `AIAgent`, credentials, or global plugin-manager state.

- [ ] **Step 8: Add the machine-readable core-customization record and validator**

```yaml
schema_version: 1
feature: workflow-orchestration
core_changes:
  - id: managed-process-tree
    files:
      - tools/managed_process.py
      - tools/process_registry.py
    tests:
      - tests/tools/test_managed_process.py
      - tests/tools/test_process_registry.py
    upstream_candidate: true
    merge_guidance: Reconcile process identity, process-tree termination, escalation, and wait/reap behavior before replaying the extraction.
    removal_condition: Remove when Hermes upstream exposes an equivalent generic managed-process-tree primitive used by ProcessRegistry.
  - id: plugin-agent-runner
    files:
      - agent/plugin_agent.py
      - agent/plugin_agent_worker.py
      - tools/registry.py
      - hermes_cli/plugins.py
    tests:
      - tests/agent/test_plugin_agent.py
    upstream_candidate: true
    merge_guidance: Reconcile ToolRegistry generation/dispatch behavior and PluginContext facade construction before replaying this commit.
    removal_condition: Remove when Hermes upstream exposes a trusted-plugin agent runner with isolated name-scoped registry execution.
```

The checker validates schema shape, unique IDs, repository-contained paths, existing files/tests, and coverage of protected core paths in the supplied diff range.

- [ ] **Step 9: Run focused and neighboring tests**

Run:

```bash
python3 -m pytest tests/agent/test_plugin_agent.py tests/tools/test_managed_process.py tests/scripts/test_check_upstream_customizations.py -q
python3 -m pytest tests/tools/test_process_registry.py tests/tools/test_registry.py tests/test_model_tools.py tests/test_get_tool_definitions_cache_isolation.py tests/tools/test_tool_search.py tests/hermes_cli/test_plugins.py -q
git diff --check
```

Expected: all selected tests pass and the checker reports the planned core changes as covered.

- [ ] **Step 10: Commit the isolated generic plugin-agent seam**

```bash
git add agent/plugin_agent.py agent/plugin_agent_worker.py tools/registry.py hermes_cli/plugins.py tests/agent/test_plugin_agent.py docs/upstream-customizations scripts/check_upstream_customizations.py tests/scripts/test_check_upstream_customizations.py
git commit -m "feat(plugins): expose scoped host agent runner"
```

## Task 2: Archon Package Discovery, Models, Validation, and CLI

**Files:**
- Create: `plugins/workflow/__init__.py`
- Create: `plugins/workflow/plugin.yaml`
- Create: `plugins/workflow/models.py`
- Create: `plugins/workflow/schema.py`
- Create: `plugins/workflow/discovery.py`
- Create: `plugins/workflow/compat.py`
- Create: `plugins/workflow/cli.py`
- Create: `tests/plugins/workflow/conftest.py`
- Create: `tests/plugins/workflow/fixtures/portable/.archon/workflows/minimal.yaml`
- Create: `tests/plugins/workflow/test_schema.py`
- Create: `tests/plugins/workflow/test_discovery.py`
- Create: `tests/plugins/workflow/test_compat_matrix.py`
- Create: `tests/plugins/workflow/test_catalog_cli.py`
- Create: `tests/plugins/workflow/test_cli.py`

**Interfaces:**
- Produces: `WorkflowDefinition`, `WorkflowNode`, `WorkflowPackage`, `ValidationIssue`, `CompatibilityReport`.
- Produces: `load_workflow(path)`, `discover_workflows(workdir, hermes_home, user_home)`, and `validate_package(package)`.
- Produces: side-effect-free plugin CLI commands `hermes workflow list`, `show`, `validate`, and `doctor --compat-report` with stable human and JSON catalog contracts.
- Consumes: `PluginContext.register_cli_command` only; no agent or network calls.

- [ ] **Step 1: Add failing fixtures for exact one-of node validation and precedence**

Cover all seven node types, mutual exclusivity, removed `steps:`, duplicate IDs, cycles, missing dependencies, invalid trigger rules, invalid retry bounds, path traversal, project-over-profile precedence, same-level duplicates, `persist_sessions`/`persist_session`, every published workflow/node option, every hook event/response field, Archon tool aliases, and provider-specific compatibility classification.

Catalog tests prove `list` returns name, description, source/precedence, compatibility, and runnable state, while `show` adds argument hints, compact textual topology, node-type counts, approvals/outward-action points, required tools/skills/MCP/providers/runtimes, related Hermes cron schedules, and blocking findings. Full command/prompt bodies and resolved secrets must be absent.

```python
def test_node_requires_exactly_one_archon_type(tmp_path):
    path = write_workflow(tmp_path, node={"id": "bad", "prompt": "x", "bash": "echo x"})
    with pytest.raises(WorkflowValidationError, match="exactly one node type"):
        load_workflow(path)
```

- [ ] **Step 2: Run tests and confirm the plugin contracts are absent**

Run: `python3 -m pytest tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_discovery.py -q`

Expected: FAIL on missing `plugins/workflow` modules.

- [ ] **Step 3: Implement immutable parsed models and deterministic validation**

Use frozen dataclasses and preserve source locations for diagnostics. Unknown top-level or node fields become compatibility issues; fields that could alter execution fail strict validation rather than being ignored.

- [ ] **Step 4: Implement recursive discovery with explicit precedence**

Resolve explicit path, project `.archon`, `$HERMES_HOME/workflows`, and `~/.archon` in that order. Sort normalized paths before loading. Cache only successful parses by path, size, mtime-ns, and SHA-256; provide deterministic invalidation.

- [ ] **Step 5: Implement field-level compatibility reporting**

```python
class CompatibilityLevel(StrEnum):
    PORTABLE = "portable"
    MAPPED = "mapped"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CompatibilityFinding:
    path: str
    level: CompatibilityLevel
    message: str
    blocking: bool
```

- [ ] **Step 6: Register the plugin CLI and keep inspection side-effect free**

`list`, `show`, `validate`, and `doctor` must not initialize MCP, providers, or `AIAgent`. JSON output is available with `--json`; human output is stable and redacts environment values. `show` renders graph topology as compact text, not a visual editor. Cron linkage is a read-only join against existing profile-local Hermes cron definitions and does not mutate schedules.

- [ ] **Step 7: Run focused plugin and plugin-discovery tests**

```bash
python3 -m pytest tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_discovery.py tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_catalog_cli.py tests/plugins/workflow/test_cli.py -q
python3 -m pytest tests/hermes_cli/test_plugins.py -q
git diff --check
```

- [ ] **Step 8: Commit the validation tracer**

```bash
git add plugins/workflow tests/plugins/workflow
git commit -m "feat(workflow): discover and validate Archon packages"
```

## Task 3: Durable Bash DAG Execution Tracer

**Files:**
- Create: `plugins/workflow/locks.py`
- Create: `plugins/workflow/store.py`
- Create: `plugins/workflow/scheduler.py`
- Create: `plugins/workflow/executors/__init__.py`
- Create: `plugins/workflow/executors/base.py`
- Create: `plugins/workflow/executors/bash.py`
- Create: `tests/plugins/workflow/test_store.py`
- Create: `tests/plugins/workflow/test_scheduler.py`
- Create: `tests/plugins/workflow/test_bash_e2e.py`
- Create: `tests/plugins/workflow/test_run_queries.py`
- Create: `tests/plugins/workflow/test_retention.py`
- Modify: `plugins/workflow/cli.py`

**Interfaces:**
- Produces: `RunStore.create_run`, `load_run`, `append_event`, `claim_node`, `complete_node`, and `release_or_expire_claim`.
- Produces: `RunStore.list_runs`, `get_run_status`, `tail_events`, and `cleanup_runs`, with profile/conversation authorization filters and bounded pagination/retention.
- Produces: `NodeExecutor.execute(context) -> NodeExecutionResult`.
- Produces: `RunScheduler.advance(run_id)` and CLI `run`, `runs`, `status`, `events`, `resume`, `cancel`, `abandon`, and `cleanup` foundations, including a durable pause envelope for worker interactions.
- Consumes: validated `WorkflowPackage` from Task 2.

- [ ] **Step 1: Write failing run-store, locking, and bash end-to-end tests**

The E2E fixture has two dependent bash nodes. Assert definition snapshot, sequential execution, stdout artifact, journal sequence, atomic projection, status output, and resume without re-running the completed first node.

Add query tests for active/recent filtering, workflow/status/limit filters, deterministic newest-first ordering, unknown/unauthorized run IDs, sanitized event tails, and stable JSON. The run summary contains `action`, `run_id`, `workflow`, `status`, `started_at`, `updated_at`, `elapsed_ms`, `current_nodes`, `progress`, `attempts`, `next_retry_at`, `pending_interaction`, `last_error`, `artifacts`, `warnings`, and `next_actions`, but no full prompts, reasoning, secrets, or unrestricted tool arguments.

- [ ] **Step 2: Add a two-process race test before implementation**

Spawn two processes that attempt to claim the same ready node. Exactly one receives a `NodeClaim`; the other observes the active lease. Include simulated Windows lock operations in a unit test.

- [ ] **Step 3: Implement bounded cross-process locking**

Follow cron's reentrant in-process plus `fcntl`/`msvcrt` pattern. Use non-blocking acquisition with monotonic timeout. The lock file contains one byte on Windows and is never deleted during an active run.

- [ ] **Step 4: Implement snapshot, journal, projection, and artifacts**

```python
@dataclass(frozen=True)
class NodeClaim:
    run_id: str
    node_id: str
    attempt_id: str
    owner_id: str
    lease_expires_at: datetime


@dataclass(frozen=True)
class ArtifactRef:
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
```

Use unique temporary projection files, flush, `fsync`, and atomic replace. Append journal records under lock and verify sequence continuity on load. Define run states `queued`, `running`, `waiting_retry`, `paused`, `interrupted`, `succeeded`, `failed`, `cancelled`, and `abandoned`; define node states `pending`, `ready`, `claimed`, `running`, `waiting_retry`, `paused`, `succeeded`, `failed`, `skipped`, `cancelled`, and `interrupted`. Reject transitions outside the explicit state machine.

- [ ] **Step 5: Implement scheduler readiness and the bash executor**

The scheduler claims under lock, releases the lock, executes, then compare-and-set completes the same attempt. The bash executor uses Hermes terminal environment conventions, sanitized environment, timeout, bounded stdout/stderr files, and process-group termination.

- [ ] **Step 6: Add CLI execution, run inspection, and retention output**

`hermes workflow run PATH --arguments TEXT` returns a run ID immediately before work starts, then exits with distinct codes for completed, paused, cancelled, and failed. `runs [--workflow NAME] [--status STATE] [--limit N] --json` lists authorized active/recent summaries; `status RUN_ID --json` exposes detailed node/attempt state; `events RUN_ID [--tail N] --json` exposes a sanitized bounded diagnostic tail. `status` with no run ID returns the same active/recent summary as `runs` for conversational convenience.

`abandon RUN_ID` makes an interrupted/failed/paused run terminal without deleting audit evidence. `cleanup [--older-than 7d] [--dry-run] [--json]` defaults to Archon's seven-day cleanup window, never removes active runs, uses per-run locks plus rename-to-quarantine before bounded deletion, tolerates concurrent readers/restarts, and reports reclaimed files/bytes. Initial configurable defaults are 512 MiB per run and 2 GiB for all workflow runs in one profile; writes reserve space before commit and fail with a typed quota error instead of partially exceeding either cap.

- [ ] **Step 7: Run durability and race tests repeatedly**

```bash
python3 -m pytest tests/plugins/workflow/test_store.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_run_queries.py tests/plugins/workflow/test_retention.py -q
git diff --check
```

The scheduler race test itself runs the claim/complete contention scenario 20 times without an optional pytest plugin.

Expected: every internal repetition produces exactly one claim and one completion event per attempt.

- [ ] **Step 8: Commit the first executable vertical slice**

```bash
git add plugins/workflow tests/plugins/workflow
git commit -m "feat(workflow): execute durable bash DAG runs"
```

## Task 4: Command and Prompt AI Nodes

**Files:**
- Create: `plugins/workflow/resources.py`
- Create: `plugins/workflow/sessions.py`
- Create: `plugins/workflow/executors/ai.py`
- Create: `tests/plugins/workflow/fixtures/portable/.archon/commands/investigate.md`
- Create: `tests/plugins/workflow/test_resources.py`
- Create: `tests/plugins/workflow/test_ai_executor.py`
- Create: `tests/plugins/workflow/test_ai_e2e.py`
- Create: `tests/plugins/workflow/test_persisted_sessions.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`

**Interfaces:**
- Produces: `ResourceResolver`, `VariableContext`, `AgentNodeExecutor`, structured artifact validation, node-session lineage, cache-fingerprint enforcement, and a profile/conversation-scoped persistent-node-session registry.
- Consumes: `PluginContext.agent` from Task 1, package roots from Task 2, and run claims from Task 3.

- [ ] **Step 1: Write failing resource containment and substitution tests**

Verify local command beats global command; command names cannot traverse; frontmatter preserves `description`/`argument-hint`; and `$ARGUMENTS`, `$USER_MESSAGE`, `$1`/`$2`, `$ARTIFACTS_DIR`, `$WORKFLOW_ID`, `$BASE_BRANCH`, `$DOCS_DIR`, `$CONTEXT`, `$LOOP_USER_INPUT`, `$REJECTION_REASON`, `$node.output`, and JSON dot references follow Archon's substitution order. Secret environment values are not general prompt variables. Bash-node references use compatible shell quoting and spill large values to contained temporary artifacts without command injection.

- [ ] **Step 2: Write failing fresh/shared session and structured-output tests**

Use a deterministic fake provider and a real registered test tool. Assert fresh nodes receive no predecessor conversation; shared nodes receive the chosen predecessor session with byte-identical snapshotted system prompt and tool schemas; and ambiguous joins or changes to provider/model, tool policy/schema, MCP schema, profile, workdir prompt state, or reasoning configuration fail validation with guidance to use fresh context. Assert strict message-role alternation, valid JSON Schema output, and failure for invalid output.

- [ ] **Step 3: Implement resource resolution and variable context**

Command frontmatter is parsed separately from body. Substitution is a single deterministic pass; substituted output is never re-expanded. Artifact and node references are length-bounded before prompt construction. Snapshot command and selected skill content at run start; combine applicable skill instructions and node instructions into one new user message rather than mutating the worker system prompt.

- [ ] **Step 4: Implement AI execution through the host facade**

```python
request = PluginAgentRunRequest(
    prompt=resolved_prompt,
    provider=node.provider or workflow.provider,
    model=node.model or workflow.model,
    context_mode=node.context_mode,
    session_id=shared_session_id,
    workdir=run.workdir,
    max_iterations=limits.max_agent_iterations,
    idle_timeout_seconds=node_idle_timeout,
    wall_timeout_seconds=node_wall_timeout,
    provider_request_timeout_seconds=min(provider_timeout, node_wall_timeout),
)
```

Persist only sanitized provider/model identity, session ID, usage, final output artifact, and audit fields.

- [ ] **Step 5: Validate structured output and typed conditions**

Parse JSON without accepting surrounding prose unless the provider mapping explicitly uses a repair path. Validate with `jsonschema`; if the package declares `output_format` and enforcement is unavailable, validation blocks execution rather than silently degrading.

- [ ] **Step 6: Implement persistent node sessions and guarded reset**

Key records by canonical workflow identity, node ID, conversation/scope key, provider, and profile. `persist_sessions` supplies the workflow default; `persist_session` overrides it for `command`/`prompt`; `context: fresh` wins. Updates use generation-based compare-and-set under a bounded cross-process lock so concurrent runs cannot clobber a newer session. Reuse only when the provider supports resume and the cache fingerprint matches; otherwise start fresh with a visible warning and replace the stale record after success.

Add `hermes workflow reset-sessions NAME [--scope KEY] [--node ID] [--yes]`. Chat always supplies its current scope. A cross-scope reset requires explicit confirmation and cannot cross profiles.

- [ ] **Step 7: Run AI executor, registry, and session tests**

```bash
python3 -m pytest tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_ai_e2e.py tests/plugins/workflow/test_persisted_sessions.py -q
python3 -m pytest tests/agent/test_plugin_agent.py tests/tools/test_registry.py -q
git diff --check
```

- [ ] **Step 8: Commit AI node execution separately from the core seam**

```bash
git add plugins/workflow tests/plugins/workflow
git commit -m "feat(workflow): run Archon command and prompt nodes"
```

## Task 5: Parallel Scheduling, Trigger Rules, Retries, and Crash Recovery

**Files:**
- Create: `tests/plugins/workflow/test_parallel_scheduler.py`
- Create: `tests/plugins/workflow/test_retry.py`
- Create: `tests/plugins/workflow/test_crash_recovery.py`
- Create: `tests/plugins/workflow/test_deadlines.py`
- Create: `tests/plugins/workflow/test_shutdown_recovery.py`
- Create: `tests/plugins/workflow/test_provider_failures.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/locks.py`

**Interfaces:**
- Produces: bounded ready-layer execution, all Archon trigger rules, deadline inheritance, persisted combined retry timing, lease renewal/expiry, coordinated shutdown/reaping, suspend/restart reconciliation, and journal-based projection repair.
- Consumes: executor and claim contracts from Tasks 3 and 4.

- [ ] **Step 1: Add table-driven trigger-rule and skip-propagation tests**

Cover `all_success`, `one_success`, `none_failed_min_one_success`, and `all_done` for succeeded, failed, skipped, cancelled, and running dependencies. Cover Archon's string/numeric operators, JSON dot access, `&&`/`||` precedence, static parse errors, and runtime non-finite/type failures that skip with a journaled warning.

- [ ] **Step 2: Add deterministic retry/backoff tests**

Inject a seeded jitter source. Assert maximum attempts, transient classification, capped delay, persisted `next_attempt_at`, no busy-loop before due time, and no retry for authentication, authorization, credit exhaustion, validation, cancellation, or unknown-side-effect outcomes. Count internal provider retries and workflow attempts against one configured maximum of five by default so the two layers cannot multiply. Prove backoff releases both worker and scheduler capacity and is interruptible by cancel/shutdown.

- [ ] **Step 3: Add parallel and stale-worker fault tests**

Use barriers to prove independent nodes overlap without exceeding `max_parallel_nodes`. Complete a stale claim after a replacement attempt begins and assert compare-and-set rejection. Kill a worker after claim and assert lease expiry produces `interrupted`. Race two persisted-session runs for the same workflow/node/scope and prove generation compare-and-set prevents an older completion from replacing the newer session record.

Add deadline tests that independently stall a provider response, model stream, tool, hook, MCP startup, deterministic subprocess, and child agent. Prove semantic progress resets only the idle timer, heartbeat resets only the lease, and the hard wall deadline still wins. A child receives the minimum of its request, remaining parent time, and workflow cap. Inline `maxTurns` cannot make that child unbounded.

Add shutdown/recovery tests at spawn-before-record, identity-recorded, provider-call, tool-call, child-agent wait, retry backoff, completion-before-persist, and persist-before-reap boundaries. Normal shutdown stops admission, writes a shutdown event, cancels every active tree concurrently, escalates/reaps, marks attempts `interrupted`, releases leases, and returns within the configured total deadline. Coordinator IPC EOF makes workers self-terminate. Simulated suspend/wake and wall-clock jumps reconcile from monotonic/UTC gaps. A stale PID with a different start identity is never signalled.

- [ ] **Step 4: Implement topological ready layers with a bounded executor**

Do not create one thread/process per node. Submit at most the configured capacity, replenish after completion, and stop scheduling new work after cancellation, shutdown, or terminal failure rules require it. Track every submitted worker and descendant in one coordinator registry; no callback may discard the final process handle before `wait()`/reap and durable outcome reconciliation.

- [ ] **Step 5: Implement deadline, lease, retry, shutdown, and resume semantics**

Resolve all lifecycle configuration before spawn. Initial `config.yaml` defaults are `max_parallel_nodes: 4`, AI semantic idle `300s`, AI hard wall `1800s`, provider request `300s`, deterministic subprocess `120s`, heartbeat `5s`, lease `30s`, cooperative shutdown `5s`, TERM grace `5s`, KILL/reap grace `2s`, combined retries `5`, process-tree RSS `2048 MiB`, and descendants per node `32`. Values are schema-bounded and may be tightened by the workflow/sidecar; no timeout accepts infinity or a non-positive value. The parent/child effective deadline is calculated from monotonic absolute deadlines, not by restarting relative timers. Resource polling is bounded and stops with the worker; unsupported platform metrics fail closed for enforcement-required configurations and are called out by `doctor`.

Lease renewal writes a compact heartbeat event no more frequently than the configured interval. Heartbeat does not count as semantic worker progress. Provider disconnect/stall and network errors map to typed transient or fatal outcomes before retry classification. Shutdown cleanup runs for active trees concurrently within the global shutdown deadline, then persists `interrupted`; it never serially spends the full grace budget per worker. Resume uses completed cached results unless `always_run: true`; rerun events retain prior artifacts and attempt lineage. Unknown external-side-effect outcomes pause for reconciliation rather than retrying.

- [ ] **Step 6: Implement projection rebuild and corruption handling**

On projection decode or shape failure, quarantine it and replay the checksum-verified journal. A sequence gap, malformed event, or digest mismatch stops recovery with a diagnostic and preserves both files.

- [ ] **Step 7: Run stress and neighboring cron lock tests**

```bash
python3 -m pytest tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_retry.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_deadlines.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_provider_failures.py -q
python3 -m pytest tests/cron/test_jobs_crossprocess_lock.py tests/cron/test_ticker_stall_60703.py -q
git diff --check
```

- [ ] **Step 8: Commit concurrency and recovery behavior**

```bash
git add plugins/workflow tests/plugins/workflow
git commit -m "feat(workflow): add bounded parallel resume and retries"
```

## Task 6: Script, Loop, and Cancel Nodes

**Files:**
- Create: `plugins/workflow/executors/script.py`
- Create: `plugins/workflow/executors/loop.py`
- Create: `plugins/workflow/executors/cancel.py`
- Create: `tests/plugins/workflow/test_script_executor.py`
- Create: `tests/plugins/workflow/test_loop_executor.py`
- Create: `tests/plugins/workflow/test_cancel_node.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/scheduler.py`

**Interfaces:**
- Produces: named/inline Bun and uv script execution, loop iteration state, interactive loop pause, and cancel propagation.
- Consumes: resource containment, agent runner, claim state, and Task 1's `ManagedProcessTree`; it does not implement a second subprocess supervisor.

- [ ] **Step 1: Add script runtime, dependency, timeout, and traversal tests**

Cover `.py`/`uv`, `.js`/`bun`, runtime-extension mismatch, uv dependency argument construction without shell interpolation, missing runtime, named-script precedence, timeout descendant cleanup, stdout JSON, and stderr artifacts.

- [ ] **Step 2: Add loop completion and safety tests**

Cover mandatory `max_iterations`, completion signal, `until_bash` exit 0, failure at the hard limit, fresh versus shared iteration sessions, `$LOOP_PREV_OUTPUT`, interactive pause, and resume with `$LOOP_USER_INPUT`.

- [ ] **Step 3: Add cancellation race tests**

Cancel while a script process tree and an AI loop iteration are active. Assert no new iteration starts, the process group is terminated, the active attempt records cancellation, and a late success cannot replace cancelled state.

- [ ] **Step 4: Implement script execution without shell-built argv**

Construct argv lists for `uv run --with ...` and Bun. Shell text is allowed only for the explicit `bash` and `until_bash` fields and always uses the existing terminal approval/sanitization policy.

- [ ] **Step 5: Implement loop and cancel executors**

Each iteration receives an attempt child ID and artifact directory. Persist output before evaluating the completion signal. Interactive loops return a typed pause result consumed by the scheduler.

- [ ] **Step 6: Run focused executor and process cleanup tests**

```bash
python3 -m pytest tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_cancel_node.py -q
python3 -m pytest tests/tools/test_process_registry.py tests/tools/test_terminal_tool.py -q
git diff --check
```

- [ ] **Step 7: Commit deterministic node coverage**

```bash
git add plugins/workflow tests/plugins/workflow
git commit -m "feat(workflow): support script loop and cancel nodes"
```

## Task 7: Durable Approval, Capture, and Rejection Rework

**Files:**
- Create: `plugins/workflow/executors/approval.py`
- Create: `tests/plugins/workflow/test_approval.py`
- Create: `tests/plugins/workflow/test_approval_races.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`

**Interfaces:**
- Produces: `approve_run`, `reject_run`, `ApprovalDecision`, exact-action one-shot worker grants, captured gate output, and bounded `on_reject` rework.
- Consumes: pause state from Task 6 and AI execution from Task 4.

- [ ] **Step 1: Write restart, duplicate-decision, and race tests**

Start a run, park at approval, create a new runtime instance, approve, and complete downstream work. Race approve versus reject from separate processes; exactly one transition succeeds and the loser receives an already-decided result.

Also park on a worker tool-approval interaction, approve its sanitized action digest, and prove resume consumes the grant once. If the model proposes a different command or tool payload, it must pause again. Sudo and secret values must never appear in the journal, projection, artifact, resume token, or callback payload exposed to the plugin.

- [ ] **Step 2: Write capture and `on_reject` tests**

Assert `capture_response: true` exposes the trimmed comment as gate output. With `on_reject`, substitute `$REJECTION_REASON`, run one AI rework attempt, and re-pause. Enforce the 1–10 attempt bound and cancel after exhaustion.

- [ ] **Step 3: Implement compare-and-set approval transitions**

Approval decisions require the current gate node, pause generation, and undecided status to match under lock. Decision events include actor/channel identifiers when supplied, but not bearer tokens or raw platform credentials.

- [ ] **Step 4: Add CLI approve/reject commands with stable exit codes**

```text
hermes workflow approve <run-id> [--comment TEXT]
hermes workflow reject <run-id> [--reason TEXT]
```

Both commands resume immediately only when the caller requested `--continue`; chat skill orchestration controls conversational continuation.

- [ ] **Step 5: Run approval and existing approval-tool tests**

```bash
python3 -m pytest tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_approval_races.py -q
python3 -m pytest tests/tools/test_approval.py -q
git diff --check
```

- [ ] **Step 6: Commit durable human gates**

```bash
git add plugins/workflow tests/plugins/workflow
git commit -m "feat(workflow): add durable approval and rejection gates"
```

## Task 8: Per-Node Tools, Skills, Hooks, MCP, and Provider Policy

**Files:**
- Create: `tests/plugins/workflow/fixtures/mcp/echo_server.py`
- Create: `tests/plugins/workflow/test_node_tool_policy.py`
- Create: `tests/plugins/workflow/test_node_skills.py`
- Create: `tests/plugins/workflow/test_node_hooks.py`
- Create: `tests/plugins/workflow/test_node_mcp.py`
- Create: `tests/plugins/workflow/test_node_agents.py`
- Create: `tests/plugins/workflow/test_provider_compat.py`
- Modify: `agent/plugin_agent.py`
- Modify: `agent/plugin_agent_worker.py`
- Modify: `plugins/workflow/compat.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml` only if an upstream-owned file changes

**Interfaces:**
- Extends: `PluginAgentRunRequest` with scoped hook/MCP worker inputs without exposing secrets.
- Produces: enforced `allowed_tools`, `denied_tools`, selected skill injection, isolated MCP lifecycle, bounded inline Hermes child agents, declarative hook mapping, Archon tool aliases, and provider capability diagnostics.
- Consumes: host runner and AI executor.

- [ ] **Step 1: Add fail-closed tool and skill-scope tests**

Assert denied tools are absent from schemas, Tool Search, and dispatch; `allowed_tools: []` provides no built-in tools; denied applies after allowed; unknown Archon/Hermes tool names block before billing; published Archon tool aliases resolve deterministically; only listed skills enter node context; and no skill content leaks to the next fresh node.

- [ ] **Step 2: Add hook behavior tests**

Use a table covering every published Archon hook event. Prove the mapped events' exact matcher/response translations, including PreToolUse deny/allow/ask, updated input, additional context, post-success/failure output, stop, permissions, MCP elicitation, lifecycle, inline-agent completion, and instructions loaded. Prove unsupported/conditional events block strict execution, and cover timeout, invalid regex, malformed response, unknown fields, and mismatched `hookEventName`.

- [ ] **Step 3: Add local MCP lifecycle and cleanup tests**

Run a local stdio echo MCP fixture. Assert only the node sees its tool, environment references expand without logging values, the server stops after success/failure/cancel, and two parallel nodes with different configs cannot see each other's tools.

- [ ] **Step 4: Add bounded inline-agent tests**

Define Archon `agents` with description, prompt, model, tools, disallowedTools, skills, and maxTurns. Assert an ordinary node has no `delegate_task` or `workflow_agent`; a node with declarations receives only the ephemeral `workflow_agent`. Prove raw Hermes `delegate_task` is never dispatched, because its current thread-based/background top-level semantics can outlive the ephemeral node worker and share process-global scope.

Assert each `workflow_agent` call is synchronous from the parent node's perspective and executes the child through a separately spawned `PluginAgentRunner` worker owned and accounted for by the coordinator. The parent must not complete or exit until all requested children have returned, failed, or been cancelled. Cover bounded result/artifact return, nested progress events, provider/model/tools/skills isolation, approval brokering, and process-tree cleanup.

Enforce kebab-case IDs, child policy, combined parent/child token and cost budgets, total descendants, hard spawn depth, weighted concurrency admission, and deadline inheritance. Before starting a parent node, reserve one execution slot plus its declared maximum simultaneous children; prove multiple parent nodes cannot consume every slot and deadlock while waiting for children. The effective child wall/provider/idle deadlines never exceed the parent's remaining wall deadline or workflow caps; `maxTurns` remains an iteration bound only. Hermes' global `delegation.*` settings may tighten but never raise workflow limits. The tool must never register permanently or let a child escape its declared scope.

- [ ] **Step 5: Extend the scoped worker protocol for node resources**

Extend Task 1's always-isolated worker protocol with declarative MCP and hook inputs. Initialize them only inside the child, pass no resolved secret back over IPC, propagate cancellation and coordinator-lifeline EOF, cap startup and total wall time, and always terminate/reap MCP/hook descendants through `ManagedProcessTree` in `finally`. Parallel nodes must prove registry and environment isolation from each other and the parent.

- [ ] **Step 6: Implement field-by-field provider capability mapping**

The compatibility table names exact support for provider/model, reasoning effort, thinking, fallback model, budget, sandbox, hooks, MCP, skills, agents, and web execution fields. Strict execution blocks unsupported behavior that changes security or correctness; observational options may warn when omission cannot alter the result contract.

- [ ] **Step 7: Run scoped execution and MCP regression tests**

```bash
python3 -m pytest tests/plugins/workflow/test_node_tool_policy.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_node_hooks.py tests/plugins/workflow/test_node_mcp.py tests/plugins/workflow/test_node_agents.py tests/plugins/workflow/test_provider_compat.py -q
python3 -m pytest tests/tools/test_mcp_tool.py tests/agent/test_shell_hooks.py -q
python3 scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml --diff HEAD~1..HEAD
git diff --check
```

- [ ] **Step 8: Commit scoped execution separately**

```bash
git add agent/plugin_agent.py agent/plugin_agent_worker.py plugins/workflow tests/plugins/workflow docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): enforce per-node agent resources"
```

## Task 9: Chat, Gateway, Desktop, and Cron Activation

**Files:**
- Create: `skills/productivity/workflow/SKILL.md`
- Create: `tests/agent/test_workflow_skill_command.py`
- Create: `tests/cron/test_workflow_cron.py`
- Create: `tests/gateway/test_workflow_skill_dispatch.py`
- Create: `tests/tui_gateway/test_workflow_skill_dispatch.py`
- Create: `tests/plugins/workflow/test_operator_scope.py`
- Create: `apps/desktop/src/lib/workflow-skill-command.test.ts`
- Modify: `plugins/workflow/cli.py`

**Interfaces:**
- Produces: the `/workflow` skill command and conversational list/show/runs/status/events/run/approve/reject/resume/cancel/abandon/cleanup/reset-sessions instructions.
- Consumes: normal Hermes skill command injection, workflow CLI, durable approvals, and cron skill/provider/model/toolset/workdir/delivery fields.

- [ ] **Step 1: Write explicit skill-command dispatch tests on every chat surface**

Assert `/workflow run demo`, `/workflow list`, `/workflow show demo`, `/workflow runs`, and `/workflow status RUN_ID` load the skill as a user message in CLI/gateway/TUI/desktop catalog paths, do not register a new model tool, and do not mutate the system prompt or global tool list. Skill/quick-command discovery remains visible in the desktop slash palette rather than being removed by built-in curation.

Also assert that a disabled workflow plugin produces an actionable `hermes plugins enable workflow` response, while an Ericsson-staged profile has the plugin enabled through the existing `plugins.enabled` config path.

- [ ] **Step 2: Write natural-language activation description tests**

Verify the skill description contains run, schedule, list, describe/show, active/recent runs, status/progress, failure diagnostics, approval, reject, resume, cancel, cleanup, reset sessions, automation, and workflow intent terms while remaining concise enough for the stable skill index.

Exercise natural-language requests: “What workflows can I run?”, “What does supplier review do?”, “Which workflows are running?”, “Show workflows waiting for approval”, “How far is run X?”, “Why did it fail?”, and “Cancel the inbox workflow.” Assert each maps to the corresponding read-only/action CLI JSON contract and that ambiguous destructive actions ask for selection/confirmation rather than guessing a run.

- [ ] **Step 3: Write cron completion and paused-approval tests**

Create a real temp-home cron job with `skills=["workflow"]`. Assert it runs the same workflow store, delivers final output, and on either a workflow gate or an inner Hermes tool-approval pause delivers run ID/instructions then exits without holding a worker. A later approval resumes the run exactly once. Existing `approvals.cron_mode` remains authoritative.

- [ ] **Step 4: Author the operational workflow skill**

The skill treats `hermes workflow` as the control plane, never edits run/session state directly, never changes graph order, and never auto-approves outward action. It distinguishes interactive continuation from background notification and always scopes chat `runs`, `status`, `events`, actions, and `reset-sessions` to the authenticated profile plus current conversation/user identity. An explicit run ID still passes authorization; it is not an enumeration bypass. The local CLI remains profile-scoped.

For catalog questions, the skill uses `list --json` or `show NAME --json` and explains description, runnable/compatibility state, arguments, compact topology, approvals/outward actions, requirements, and schedules. For execution questions, it uses `runs`, `status`, or `events --tail` and explains progress, current nodes, elapsed time, retry/approval state, sanitized error, artifacts, and `next_actions`. It never reads raw run files, full prompts, hidden reasoning, secret material, or unrestricted tool arguments.

- [ ] **Step 5: Add machine-readable CLI output required by the skill**

Every invoked CLI command supports `--json`. Catalog records have stable `action`, `workflow`, `description`, `source`, `precedence`, `compatibility`, `runnable`, `topology`, `requirements`, `approvals`, `schedules`, `warnings`, and `next_actions` fields as applicable. Run records use the Task 3 summary contract and detailed status adds node/attempt state. Secret values, credentials, full prompt/command bodies, reasoning, and unrestricted tool arguments are excluded.

- [ ] **Step 6: Run cross-surface and cron tests**

```bash
python3 -m pytest tests/agent/test_workflow_skill_command.py tests/gateway/test_workflow_skill_dispatch.py tests/tui_gateway/test_workflow_skill_dispatch.py tests/cron/test_workflow_cron.py tests/plugins/workflow/test_operator_scope.py -q
cd apps/desktop && npx vitest run src/lib/workflow-skill-command.test.ts src/lib/desktop-slash-commands.test.ts
git diff --check
```

- [ ] **Step 7: Commit user activation**

```bash
git add skills/productivity/workflow plugins/workflow/cli.py tests/agent/test_workflow_skill_command.py tests/gateway/test_workflow_skill_dispatch.py tests/tui_gateway/test_workflow_skill_dispatch.py tests/cron/test_workflow_cron.py tests/plugins/workflow/test_operator_scope.py apps/desktop/src/lib/workflow-skill-command.test.ts
git commit -m "feat(workflow): activate runs from chat and cron"
```

## Task 10: Workflow Builder Skill and Compatibility Doctor

**Files:**
- Create: `skills/software-development/workflow-builder/SKILL.md`
- Create: `skills/software-development/workflow-builder/references/archon-schema.md`
- Create: `skills/software-development/workflow-builder/references/authoring-checklist.md`
- Create: `tests/agent/test_workflow_builder_skill.py`
- Create: `tests/plugins/workflow/test_doctor.py`
- Modify: `plugins/workflow/compat.py`
- Modify: `plugins/workflow/cli.py`

**Interfaces:**
- Produces: guided package authoring and `hermes workflow doctor PACKAGE --json`.
- Consumes: exact schema, discovery, resource resolution, provider compatibility, installed skills/tools/MCP, and cron.

- [ ] **Step 1: Write builder-contract and doctor snapshot-invariant tests**

The builder must create `workflows`, `commands`, `scripts`, and `mcp` resources when referenced; validate before offering execution; avoid unsupported fields; and keep Ericsson policy outside portable YAML. Doctor tests assert relationships and issue codes rather than freezing complete prose.

- [ ] **Step 2: Implement a structured doctor report**

```python
@dataclass(frozen=True)
class DoctorReport:
    package: str
    workflow: str
    runnable: bool
    findings: tuple[CompatibilityFinding, ...]
    resolved_commands: tuple[str, ...]
    resolved_scripts: tuple[str, ...]
    resolved_mcp_servers: tuple[str, ...]
    resolved_skills: tuple[str, ...]
```

Include missing runtimes, mapped/unknown tool aliases, skills, MCP variables, credentials, output-schema enforcement, persistent-session capability/fingerprint constraints, every configured hook event, inline-agent bounds, worktree/service requirements, and provider-field support. Never connect to remote MCP or call a model in doctor mode.

- [ ] **Step 3: Author the builder skill around whole-package creation**

The skill asks one decision at a time, plays the workflow back in plain language, writes command templates for long prompts, uses Archon field names/tool aliases, inserts approval gates around outward actions unless the user opts out, chooses fresh context when cache fingerprints would differ, and offers run or cron only after a runnable doctor result.

- [ ] **Step 4: Add fixtures generated by the builder contract**

Keep one minimal on-demand package, one scheduled reporting package, and one approval/rework package. Validate all three in tests and run the deterministic portions without a network.

- [ ] **Step 5: Run builder, doctor, schema, and skill-scanner tests**

```bash
python3 -m pytest tests/agent/test_workflow_builder_skill.py tests/plugins/workflow/test_doctor.py tests/plugins/workflow/test_schema.py -q
python3 -m pytest tests/agent/test_skill_commands.py -q
git diff --check
```

- [ ] **Step 6: Commit authoring support**

```bash
git add skills/software-development/workflow-builder plugins/workflow tests/agent/test_workflow_builder_skill.py tests/plugins/workflow/test_doctor.py
git commit -m "feat(workflow): author and diagnose portable packages"
```

## Task 11: Ericsson Conversion, Capability Staging, and Brand Delivery

**Files:**
- Create: `capabilities/workflow-packages/ericsson/.archon/workflows/inbox-digest.yaml`
- Create: `capabilities/workflow-packages/ericsson/.archon/workflows/my-tickets-summary.yaml`
- Create: `capabilities/workflow-packages/ericsson/.archon/commands/collect-inbox.md`
- Create: `capabilities/workflow-packages/ericsson/.archon/commands/summarize-inbox.md`
- Create: `capabilities/workflow-packages/ericsson/.archon/commands/fetch-tickets.md`
- Create: `capabilities/workflow-packages/ericsson/.archon/commands/summarize-tickets.md`
- Create: `capabilities/workflow-packages/ericsson/.archon/workflows/my-tickets-summary.hermes.yaml`
- Delete: `capabilities/workflows/inbox-digest.yml`
- Delete: `capabilities/workflows/my-tickets-summary.yml`
- Delete: `skills/ericsson/workflow-orchestrator/`
- Delete: `skills/ericsson/workflow-builder/`
- Modify: `capabilities/ericsson.json`
- Modify: `hermes_cli/capability_staging.py`
- Modify: `scripts/vendor-ericsson.mjs`
- Modify: `tests/hermes_cli/test_capability_staging.py`
- Modify: `tests/hermes_cli/test_baked_seed.py`
- Modify: `scripts/__tests__/vendor-ericsson.test.mjs`
- Modify: `brands/otto.json`
- Modify: `brands/loop24.json`

**Interfaces:**
- Produces: atomic `workflowPackages` capability staging and two portable Ericsson packages.
- Consumes: generic workflow plugin/skills and existing brand/capability startup.

- [ ] **Step 1: Write failing complete-package staging tests**

Assert a versioned capability update stages workflows, commands, scripts, MCP files, and sidecars into one temporary directory and atomically swaps it into the profile. Simulate interruption before swap and prove the prior package remains usable. Reject traversal and symlink escape.

- [ ] **Step 2: Extend the capability manifest without adding workflow runtime logic**

```json
{
  "plugins": ["plugins/workflow", "plugins/ericsson-jira", "plugins/ericsson-teams"],
  "workflowPackages": ["capabilities/workflow-packages/ericsson/.archon"]
}
```

Baked and remotely staged capabilities enable the declared workflow plugin through the existing `plugins.enabled` configuration merge.

- [ ] **Step 3: Convert the inbox package**

Use Archon `command` nodes. Move `since` and `limit` to `$ARGUMENTS` conventions, write artifacts under `$ARTIFACTS_DIR`, and keep Outlook service requirements in capability configuration rather than workflow YAML.

- [ ] **Step 4: Convert the ticket package**

Use command templates, an Archon approval object with captured response, and a send node that depends on approval. Put Ericsson delivery defaults and configured-service requirements in `my-tickets-summary.hermes.yaml`.

- [ ] **Step 5: Remove the obsolete schema and Ericsson-only orchestrator skills**

The generic skills replace them. No alias or migration loader remains because there are no deployed installations. Validation rejects the old `kind:` schema with an actionable conversion message.

- [ ] **Step 6: Update vendoring and brand descriptors**

Vendor complete package roots from the Ericsson source, preserve `vendoredFrom`, and ensure base owns shared content while `otto` and `loop24` only select/brand it. Do not duplicate runtime code into either brand branch.

- [ ] **Step 7: Run package, staging, and brand tests**

```bash
python3 -m pytest tests/hermes_cli/test_capability_staging.py tests/hermes_cli/test_baked_seed.py tests/plugins/workflow/test_doctor.py -q
node --test scripts/__tests__/vendor-ericsson.test.mjs scripts/brand/__tests__/descriptor.test.mjs scripts/brand/__tests__/equivalence.test.mjs
node scripts/brand/generate.mjs --brand otto --check
git diff --check
```

- [ ] **Step 8: Commit capability conversion separately**

```bash
git add capabilities skills/ericsson hermes_cli/capability_staging.py scripts/vendor-ericsson.mjs tests/hermes_cli scripts/__tests__/vendor-ericsson.test.mjs brands
git commit -m "feat(ericsson): ship Archon-compatible workflow packages"
```

## Task 12: Production Quality and Upstream-Merge Release Gate

**Files:**
- Create: `tests/plugins/workflow/test_archon_portable_e2e.py`
- Create: `tests/plugins/workflow/test_fault_injection.py`
- Create: `tests/plugins/workflow/test_performance_bounds.py`
- Create: `tests/plugins/workflow/test_process_lifecycle_soak.py`
- Create: `tests/plugins/workflow/test_operator_e2e.py`
- Create: `tests/plugins/workflow/test_security_boundaries.py`
- Create: `scripts/test_workflow_merge_gate.sh`
- Create: `scripts/test_workflow_upstream_merge.sh`
- Create: `tests/scripts/test_workflow_merge_gate.py`
- Create: `docs/workflow-orchestration.md`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Modify: `.github/workflows/ci.yml` only if repository CI lacks a suitable existing job

**Interfaces:**
- Produces: the lightweight offline live-merge gate, release evidence bundle, isolated merge rehearsal, operator documentation, and CI gate.
- Consumes: all previous slices and the `main → base → otto/loop24` branch topology.

- [ ] **Step 1: Add an unmodified portable Archon end-to-end fixture**

The fixture includes command/frontmatter variables, prompt, bash, uv script, parallel join, typed/compound condition, structured/typed output, tool aliases/restrictions, skills, mapped hooks, local MCP, inline agents, retry, persistent sessions, loop, approval/rejection, and cancel paths. Run with a deterministic fake provider plus local MCP. Interrupt after multiple nodes, restart the process, approve, and finish without repeating completed work; invoke it again under the same conversation scope to prove persistent-node-session reuse.

- [ ] **Step 2: Add concurrency and fault-injection stress tests**

Exercise 100 duplicate scheduler starts, 20 simultaneous approval decisions, worker termination at every persistence boundary, coordinator death/IPC EOF, shutdown during every lifecycle phase, laptop suspend/wake gap, wall-clock jump, PID reuse, projection corruption, journal corruption, lock timeout, provider DNS/TLS/disconnect/stalled-stream/model errors, MCP startup failure, hook timeout, and cancellation while descendants are active. Assertions focus on invariants: one winning claim, monotonic events, bounded completion, no leaked/zombie process, mandatory reaping, no worker held during backoff, and no silent success.

Run 100 fast spawn/success/cancel/idle-timeout/wall-timeout/provider-failure cycles in normal CI and 500 cycles in the release gate. Sample live process/child counts, threads, descriptors on POSIX or handles on Windows, coordinator/worker RSS, run-directory bytes, and quarantine contents before, during, and after. After bounded cleanup and garbage-collection settling, require zero live workflow-owned process identities, zero unreaped owned children, no retained scheduler/reader threads, no retained descriptors/handles, no quarantine leak, and no statistically upward resource trend across equal-size batches.

- [ ] **Step 3: Add security boundary tests**

Cover YAML aliases/depth limits, oversized documents, traversal, symlink escape, command injection, unsafe uv dependency tokens, secret redaction, MCP environment expansion, unauthorized provider override, hook input mutation, artifact quota, output quota, approval-digest tampering/replay, and proof that no secret or sudo value can enter durable state or plugin-visible IPC.

- [ ] **Step 4: Add measurable performance tests**

```python
def test_thousand_node_projection_load_is_bounded(completed_run_1000, count_journal_reads):
    started = time.perf_counter()
    with count_journal_reads() as journal_reads:
        result = completed_run_1000.store.load_run(completed_run_1000.run_id)
    elapsed = time.perf_counter() - started
    assert len(result.nodes) == 1000
    assert journal_reads.count == 0
    assert elapsed < 2.0  # generous ceiling, calibrated on CI before release


def test_scheduler_never_exceeds_parallel_limit(runtime):
    runtime.config.max_parallel_nodes = 4
    runtime.run("twenty-independent-nodes")
    assert runtime.observed_peak_workers <= 4


def test_agent_workers_leave_parent_process_state_unchanged(runtime):
    before = runtime.capture_parent_process_state()
    runtime.run("four-disjoint-tool-scopes")
    assert runtime.capture_parent_process_state() == before
```

Measure cold worker startup latency and peak resident memory at concurrency 1 and 4. Store baseline timing in test output, not a brittle committed machine-specific number. Enforce algorithmic/resource invariants plus a generous CI ceiling derived from three CI runs, and fail on worker/process/thread/descriptor growth after completion. Exceed process-tree RSS, descendant, output, artifact, event, per-run storage, and profile-storage limits one at a time; each must terminate/pause with a typed diagnostic, reap descendants, preserve a valid projection/journal, and allow later cleanup.

Add an operator E2E that installs two workflows and creates running, waiting-retry, paused-for-approval, failed, succeeded, cancelled, interrupted, and abandoned runs. Prove `list`, `show`, `runs`, `status`, and `events` agree across CLI JSON, `/workflow`, and natural-language skill paths; compact topology and next actions are correct; current-scope authorization is enforced; and prompt/reasoning/secret/tool-argument canaries never appear.

- [ ] **Step 5: Add an isolated upstream-merge rehearsal script**

First add `scripts/test_workflow_merge_gate.sh` with `--phase base` and `--phase brand --brand SLUG` modes. The base mode runs the customization checker and focused generic runner/workflow tests. The brand mode performs workflow discovery, package validation, and generic-surface checks without a provider, model call, credentials, or network. Test missing counterparts, invalid phases/brands, base failures, per-brand failures, and successful no-network execution in `tests/scripts/test_workflow_merge_gate.py`.

The script creates temporary worktrees, fetches no network by default, merges the supplied local upstream ref into a temporary base branch, runs the customization checker and focused Python/Node tests, then merges temporary base into temporary OTTO and LOOP24 branches and runs brand equivalence checks. It never mutates real `base`, `otto`, or `loop24` refs.

```bash
scripts/test_workflow_upstream_merge.sh \
  --upstream-ref main \
  --base-ref base \
  --brand-ref otto \
  --brand-ref loop24
```

- [ ] **Step 6: Integrate the existing merge skill at its owning location**

Update `/Users/coreyellis/code/github.com/cmetech/otto_hermes/.claude/skills/otto-upstream-merge/SKILL.md` without changing its `main` to `base` to discovered-brand flow. Add a Stage-0 overlap report for ledger-owned core files, then run `check_upstream_customizations.py` plus focused offline workflow tests in the Stage-1 gate before branded propagation. After each brand restamp, run a cheap discovery/validation smoke that performs no model or network call. If the workflow manifest/checker is absent before the feature lands, report that the optional gate is not installed; once either workflow runtime or manifest is present, a missing counterpart fails closed.

Do not call `test_workflow_upstream_merge.sh` from inside the real merge skill. That script repeats the entire branch graph in temporary worktrees and belongs in CI, release verification, or an explicitly requested preflight. Test the skill change against a temporary clone and record its version or commit in release evidence. The skill remains external and is not vendored into Hermes.

- [ ] **Step 7: Document operations and compatibility**

Document package layout, discovery precedence, `list/show/runs/status/events` examples, natural-language equivalents, textual topology, status/state meanings, cron, approvals, resume/cancel/abandon/cleanup, artifacts, config limits, renderer-versus-owner shutdown behavior, provider/network failures, orphan/restart recovery, storage retention, compatibility levels, security/authorization model, how the merge skill invokes the lightweight checker/smoke gates, and how CI or an explicit preflight invokes the full rehearsal script.

- [ ] **Step 8: Run the full release gate**

```bash
python3 -m pytest tests/agent/test_plugin_agent.py tests/tools/test_managed_process.py tests/tools/test_process_registry.py tests/plugins/workflow tests/cron/test_workflow_cron.py tests/gateway/test_workflow_skill_dispatch.py tests/tui_gateway/test_workflow_skill_dispatch.py -q
python3 -m pytest tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_performance_bounds.py tests/plugins/workflow/test_process_lifecycle_soak.py tests/plugins/workflow/test_operator_e2e.py -q
python3 -m pytest tests/scripts/test_workflow_merge_gate.py -q
cd apps/desktop && npx vitest run src/lib/workflow-skill-command.test.ts src/lib/desktop-slash-commands.test.ts
cd ../.. && node --test scripts/__tests__/vendor-ericsson.test.mjs scripts/brand/__tests__/*.test.mjs
python3 scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml --diff main..HEAD
scripts/test_workflow_upstream_merge.sh --upstream-ref main --base-ref base --brand-ref otto --brand-ref loop24
git diff --check
```

Expected: all tests pass; no leaked/zombie processes, scheduler threads, descriptors/handles, or quarantine entries remain; resource slopes stay bounded; the customization ledger covers every workflow-related core change; temporary branch rehearsals preserve both brands.

- [ ] **Step 9: Request security and code review before branded propagation**

Run the repository security-review and code-review skills against the full feature diff. Resolve every high-severity finding and re-run the release gate. Record commands, results, timing, and platform coverage in the PR description.

- [ ] **Step 10: Commit the production gate and documentation**

```bash
git add tests/plugins/workflow tests/scripts/test_workflow_merge_gate.py scripts/test_workflow_merge_gate.sh scripts/test_workflow_upstream_merge.sh docs/workflow-orchestration.md docs/upstream-customizations/workflow-orchestration.yaml .github/workflows/ci.yml
git commit -m "test(workflow): enforce production and merge gates"
```

## Requirement Coverage

| Requirement | Slices |
|---|---|
| Exact Archon-shaped YAML and commands | S02, S04, S10, S11 |
| Fresh/shared context | S01, S04 |
| Durable state, locking, race safety | S03, S05, S07, S12 |
| Parallel DAG, triggers, retries, resume | S05 |
| Bash, script, loop, approval, cancel | S03, S06, S07 |
| Structured output and artifacts | S03, S04 |
| Tools, skills, hooks, MCP, provider mapping | S01, S08 |
| Workflow catalog, description, topology, requirements, and schedules | S02, S09, S12 |
| Active/recent runs, detailed status, sanitized diagnostics, and cleanup | S03, S09, S12 |
| Natural chat and `/workflow` | S09, S12 |
| Cron scheduling and delivery | S09 |
| Workflow developer authoring assistance | S10 |
| Ericsson conversion | S11 |
| Minimal upstream-core surface | S01, S08 |
| Repeatable upstream merges and branded propagation | S01, S11, S12 |
| Deadlines, shutdown, orphan prevention, restart/suspend recovery | S01, S03, S05, S06, S08, S12 |
| Retry/provider/network failure and resource/storage bounds | S03, S05, S06, S08, S12 |
| Security, performance, and production quality | S01, S03, S05, S06, S07, S08, S12 |

## Definition of Done

- All twelve slices are complete in dependency order.
- The design acceptance criteria are mapped to passing tests.
- No old `kind:` workflow schema or Ericsson-only orchestrator runtime remains.
- The portable E2E fixture runs without YAML edits.
- Linux/macOS tests and Windows-specific lock/process simulations pass. Native Windows CI must pass before claiming Windows workflow support; otherwise the release is blocked or Windows workflow support is explicitly disabled and documented.
- No unbounded worker, retry, loop, output, artifact, lock wait, or subprocess path remains.
- Natural language, `/workflow`, and `hermes workflow` can list/show workflows, list active/recent/waiting runs, inspect detailed status and sanitized failures, and report actionable next steps from the same catalog/store contracts.
- Catalog/status authorization prevents cross-profile and cross-conversation/user disclosure, including when an explicit run ID is supplied.
- Shutting down an owning Hermes process stops admission, terminates and reaps every workflow process tree within the configured deadline, and leaves active attempts durably `interrupted`; renderer-only closure behavior is documented and tested separately.
- Parent loss, provider/network stalls, PID reuse, laptop suspend/wake, and forced restart have deterministic bounded outcomes and never infer success.
- Retry backoff holds no worker, nested retry layers share one budget, and fatal/unknown-side-effect errors do not retry.
- The 100-cycle CI soak and 500-cycle release soak leave zero workflow-owned processes/zombies, retained scheduler/reader threads, descriptors/handles, or quarantine entries and no upward memory/disk resource trend.
- Run/profile disk quotas and seven-day cleanup/retention are race-safe, restart-safe, and covered by dry-run and concurrent-reader tests.
- Prompt-cache and message-alternation invariants pass existing regressions.
- The core customization ledger and checker cover the final diff.
- The upstream merge rehearsal passes through temporary base, OTTO, and LOOP24 branches.
- Documentation and PR evidence identify every unsupported provider-specific Archon field.
- Security and code review have no unresolved high-severity findings.
