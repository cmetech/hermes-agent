# Portable Workflow Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Use subagents only when the user explicitly authorizes delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a production-grade, Archon-shaped workflow runtime for Co-worker that reuses Hermes agents, skills, tools, MCP, hooks, approvals, and cron; provides first-class workflow/run discovery, native desktop operational boards, detailed diagnostics, portable text/Mermaid topology, and an offline installed showcase harness without building a visual authoring editor; and keeps Hermes-core customization small, generic, and mergeable.

**Architecture:** An additive `workflow` plugin owns compatibility, digest-bound trust, immutable input capture, idempotent admission, deterministic text/Mermaid topology projection, graph execution, durable state, resources, operator commands, read-only bundled showcase packages, and an authenticated snapshot/delta/action API. Generic `workflow`, `workflow-builder`, and `workflow-showcase` skills provide chat activation, surface-aware presentation, authoring, and guided demonstrations. Desktop renders independent workflow and Kanban read models through one desktop-native activity-board presentation layer; workflow `RunStore` and `kanban_db` remain separate lifecycle authorities. A narrowly scoped `PluginContext.agent` facade plus a generic managed-process-tree primitive are the only planned upstream-Hermes agent-core seams; one separately committed generic `kanban_db` CAS extension protects native Kanban mutations. Every upstream-owned touch is classified, ledgered, invariant-tested, and rehearsed through main → base → each branded branch.

**Tech Stack:** Python 3.11+, PyYAML, dataclasses, FastAPI plugin routes, `jsonschema` from Hermes' existing `mcp`/`all` install path, Hermes plugin/skill/cron/MCP infrastructure, Electron/React, TanStack Query, nanostores, the desktop app's existing design primitives and Mermaid/Streamdown renderer, pytest, Vitest, and Node test runner for capability vendoring and brand generation.

**Status:** Historical milestone plan; remaining production/operator work is superseded and requires review approval

**Amended:** 2026-07-16 — native workflow/Kanban operational boards (option 3), neutral internal package naming, expanded status/diagnostic coverage, idempotent admission, trusted-package execution, cancellation races, generic Kanban CAS (option 2), offline production showcase harness, and overlap-aware upstream merge preservation

**2026-07-18 supersession:** Completed historical tasks remain useful evidence,
but no unfinished task in this document authorizes implementation or release.
Safety remediation, generic plugin background services, durable coordination,
machine contracts, operator UX, and release gates must follow
`docs/superpowers/plans/2026-07-18-workflow-orchestration-operator-experience-plan.md`
in its risk order. That plan and its focused coordinator design supersede any
earlier foreground-owned continuation, cleanup, notification, or touch-budget
assumption.

## Global Constraints

- No Pi-framework code, state, manifests, or extension APIs may be imported or copied.
- The portable YAML shape follows Archon's `nodes:` DAG format; the removed sequential `steps:` format is rejected.
- Existing Ericsson workflow YAML is replaced; no deployed-run migration or dual-schema runtime is required.
- The parent conversation's system prompt and tool schema remain byte-stable. `context: fresh` always uses an isolated node session.
- `context: shared` requires an exact cache fingerprint match and reuses the snapshotted system prompt/tool schemas byte-for-byte; a cache-affecting mismatch must fail validation with guidance to use `fresh`.
- No new model-facing core tool is added.
- No Archon-branded directory is created or used for discovery. Project packages live under `.hermes/workflows/`; profile packages live under `$HERMES_HOME/workflows/`; explicitly supplied external packages retain their portable YAML shape.
- Workflow `RunStore` is the sole workflow lifecycle authority and `kanban_db` is the sole Kanban lifecycle authority. Ordinary workflow nodes are never mirrored into Kanban tasks.
- The desktop activity-board layer is presentation-only. Workflow actions are typed lifecycle operations; workflow cards never support arbitrary drag/drop status mutation.
- Physical Kanban boards remain project/repository/domain queues. No physical board is created per workflow definition or run.
- Every trigger supplies a source-scoped idempotency key. The same key/start digest returns the existing run, a key reused with different workflow/input/policy fails with conflict, and an intentional second run uses a new key plus the configured overlap policy.
- Per-workflow overlap defaults to `queue` with one active run per workflow/concurrency key. `allow` and `forbid` are bounded opt-ins; automatic `replace` is not supported in the first milestone.
- Initial admission defaults are `max_executing_runs: 4`, `max_queued_runs: 100`, `max_paused_runs: 100`, `max_nonterminal_runs: 200`, `max_start_requests_per_minute: 60`, and `max_total_workers: 4` per profile. Per-run `max_parallel_nodes: 4` is additionally constrained by the profile-wide worker budget. Queued, paused/user-wait, and persisted-retry runs consume their appropriate durable/nonterminal quota but hold no worker, process, thread, MCP server, or retry timer.
- Imported or explicitly supplied executable packages are disabled until the user approves a risk summary bound to the digest of YAML, sidecar, commands, scripts, MCP definitions, and other executable resources. Package files cannot grant their own trust; any digest change invalidates trust.
- Untrusted packages may execute only through a configured Hermes execution backend that advertises the required isolation. Local user-privileged shell/script execution is limited to digest-trusted packages and is explicitly not represented as a malicious-code sandbox.
- File/document inputs are validated, size-bounded, read-tested, and copied into the immutable run snapshot before admission; downstream nodes never reopen a mutable source path.
- Desktop status is derived from bounded snapshots and monotonic event cursors. Cursor gaps, schema-version mismatch, backend restart, profile/board switch, or reconnect force a bounded snapshot reload.
- Stale mutations use compare-and-set revisions/state versions and fail with `409 Conflict`; optimistic UI rolls back before reloading the affected source.
- Board progress is graph progress, never an invented elapsed-time percentage. Long indeterminate nodes expose last semantic progress and health separately.
- The board is not the only diagnostic surface: topology, attention reason, event timeline, attempts, retry timing, bounded resource/budget consumption, artifacts, verification evidence, and next actions remain available in the run inspector and CLI/chat contracts.
- Only visible desktop pages refresh; requests/events/cards are bounded and paginated, large columns virtualize, hidden windows stop refresh, and terminal/attention transitions are not lost through cosmetic event coalescing.
- Workflow/Kanban pages are keyboard accessible, usable at laptop width, preserve focus during background updates, and visibly disable mutations while data is stale or disconnected.
- Desktop notifications are limited to deduplicated transitions into user-actionable attention or terminal failure. They never fire for every node/progress event.
- Workflow topology always has a bounded portable text projection. Bounded Mermaid source is generated from the same normalized DAG, never accepted from workflow-authored directives, and is rendered graphically only by an explicitly tested surface.
- Classic CLI, Ink TUI, dashboard-embedded TUI, unknown gateways, and ordinary Markdown-only adapters use text topology. Desktop chat includes the text fallback plus a fenced Mermaid diagram; the Workflows page renders the same bounded projection beside its board and inspector. Generic Markdown support is not treated as Mermaid support.
- Topology limits are exact: 12 × 1,024 UTF-8 bytes of text, 100 Mermaid nodes, 200 Mermaid edges, 80 Unicode code points per Mermaid node label including its ellipsis, and 64 × 1,024 UTF-8 bytes of Mermaid source. Labels truncate at 80 code points; exceeding a graph/source limit returns `null` plus a warning, never an unbounded diagram.
- Behavioral settings live in `config.yaml`; credentials alone may use secret environment storage.
- The plugin is opt-in through existing `plugins.enabled`; there is no workflow-specific loader exception. Ericsson capability staging enables it, while general profiles receive the existing plugin-enable remediation.
- The installed showcase is an edge capability inside the workflow plugin plus a bundled skill. It never adds a model-facing core tool, a second scheduler, a new discovery tier for user workflows, or a brand-specific runtime path.
- The default showcase is provider-free, network-free, integration-free, cross-platform, and based on sanitized fictional laptop evidence. It never inventories or changes the real host. The legacy Windows PowerShell collector is a separate future capability and is not copied or executed by this plan.
- AI/extension and scheduling showcases require explicit opt-in. Missing AI is a typed skip, not a failure of the offline suite; scheduling reuses Hermes' one-shot `repeat=1` claim/auto-delete plumbing, and reset may never match or remove an unowned user job.
- Production showcase scenarios may use only controlled bounded failures. State corruption, forced Hermes termination, resource exhaustion, concurrency floods, and soak tests remain release/CI-only and are not selectable through the installed skill or CLI.
- Showcase claims pass only when supported by normalized definitions plus durable RunStore events, attempts, interactions, cleanup evidence, and verified artifacts. Catalog metadata by itself is never test evidence.
- A lean install lacking `jsonschema` must fail closed before any `output_format` or per-node MCP work and report how to install Hermes' existing `mcp` extra. Schema validation is never silently skipped.
- Workflow execution must be bounded by concurrency, timeout, retry, iteration, output, and artifact limits.
- AI idle timeout, hard node wall deadline, provider-request timeout, deterministic-process timeout, parent/child wait deadline, shutdown grace, and kill/reap grace are distinct. Omitted configuration resolves to bounded defaults; `None` never means wait forever.
- A lease heartbeat proves ownership only and never resets semantic idle time. Every child deadline is no later than its parent's remaining deadline or the workflow global deadline.
- Every spawned worker/descendant is coordinator-owned, identity-guarded, and reaped. Owning Hermes shutdown must stop admission, persist interruption, terminate process trees, and exit within a bounded deadline.
- Completion-versus-cancel, retry-wakeup-versus-cancel, approval-versus-cancel, admission-versus-shutdown, and cleanup-versus-reader use explicit compare-and-set winners; stale completions/actions are rejected and uncertain outward actions become reconciliation-required.
- Provider failure, network loss, parent/child IPC loss, laptop suspend/wake, PID reuse, and forced restart must produce explicit recoverable states rather than silent success or indefinite waiting.
- Retry backoff holds no worker or scheduler slot and obeys one combined provider/workflow attempt budget.
- Per-worker process-tree memory, CPU time where enforceable/measurable, descendants, output, artifacts, event/run storage, open descriptors/handles, and retention are bounded and observable. Admission enforces a free-disk watermark of `max(1 GiB, min(5 GiB, 5% of target-volume capacity))` before snapshot/process allocation.
- State transitions must be race-safe across threads and processes on POSIX and Windows-supported paths.
- No lock may be held while model, tool, hook, MCP, shell, or script work executes.
- AI nodes never run as parallel threads in the parent Hermes process. Each uses a bounded worker process started without forking a live multithreaded runtime.
- Every task uses test-first development, focused regression tests, and a separately reviewable commit.
- Core-seam commits remain separate from plugin, skill, capability, and workflow commits.
- Existing upstream agent-core file modifications are budgeted to `tools/registry.py`, `hermes_cli/plugins.py`, and `tools/process_registry.py`. The latter consumes one new generic `tools/managed_process.py` primitive so workflow code reuses Hermes' existing process-identity, tree-termination, escalation, and reaping behavior.
- This amendment additionally budgets `hermes_cli/kanban_db.py` for generic same-transaction optional mutation preconditions, `plugins/kanban/dashboard/plugin_api.py` for bounded cursor/CAS REST fields, and narrow Desktop route, navigation, page composition, typed API client, and locale changes. It does not authorize changes to `run_agent.py`, model tools, Kanban dispatcher/worker lanes, TUI gateway, Electron backend spawning/preload, or a generic desktop plugin/WebSocket framework.
- The showcase additionally budgets narrow `pyproject.toml` and `MANIFEST.in` package-data entries for `plugins/workflow/showcases/**` plus the matching `tests/test_packaging_metadata.py` invariant. These are ledgered packaging UNION changes, not runtime/core logic; an unconstrained all-plugin asset glob requires a separate size/content review.
- Every modified upstream-owned agent-core or product-surface file is listed in `docs/upstream-customizations/workflow-orchestration.yaml` with change class, owned symbols/contracts, rationale, tests, last verified upstream commit, expected commit boundary, merge guidance, and removal condition. Any additional touch requires an explicit design and ledger amendment before coding.
- The upstream merge procedure may never apply blanket whole-file `ours`/`theirs` to a ledger-owned file. It must classify incoming overlap, preserve a pre-merge customization reference, run the entry's invariant tests after reconciliation, update the verified-upstream baseline only after success, and propagate only that tested `base` commit into branded branches.
- A task is not complete until focused tests, static checks, and `git diff --check` pass.

---

## Delivery Roadmap

- [ ] **S01: Public plugin agent runner** `risk:high` `depends:[]`
  > After this: an enabled test plugin can run a fresh Hermes tool-using worker process with enforced model/tool policy and distinct idle/wall/provider deadlines through a documented host facade; timeout, cancel, coordinator loss, and shutdown terminate and reap its process tree without mutating caller state.
- [ ] **S02: Portable package discovery and validation** `risk:high` `depends:[]`
  > After this: `hermes workflow list|show|validate` discovers and explains explicitly supplied, project-local, and profile-local packages, emits matching bounded text/Mermaid topology projections, reports their requirements plus exact portable/mapped/unsupported fields, and enforces digest-bound trust/risk preflight without making model or network calls.
- [ ] **S03: Durable bash DAG tracer** `risk:high` `depends:[S02]`
  > After this: `hermes workflow run` atomically deduplicates triggers, snapshots validated inputs, queues/admits bounded overlapping runs, and executes/resumes a two-node bash DAG with artifacts, journaled state, and cross-process-safe claims; `runs|status|events` exposes active/recent progress and sanitized diagnostics from the materialized store.
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
- [ ] **S09: Chat, gateway, desktop chat, and cron activation** `risk:medium` `depends:[S07,S08]`
  > After this: natural chat, `/workflow`, `hermes workflow`, and scheduled jobs all discover workflows; explain what they do with portable text and desktop-chat Mermaid topology; list active/recent/waiting runs; inspect status/failure; and operate the same run/approval lifecycle without adding a permanent model tool.
- [ ] **S10: Native desktop workflow and Kanban operations** `risk:high` `depends:[S03,S07,S09]`
  > After this: Desktop has separate Workflows and Kanban pages sharing a presentation-only activity board; portfolio, run, attention, topology, inspector, comments, artifacts, and safe typed actions remain consistent with the authoritative workflow/Kanban stores across reconnects, stale mutations, large boards, and local/remote authentication modes. Kanban mutations use generic same-transaction preconditions in `kanban_db`, not API-side check-then-write logic.
- [ ] **S11: Workflow authoring and compatibility doctor** `risk:medium` `depends:[S02,S04,S06,S07,S08]`
  > After this: the builder skill creates a complete Archon-shaped package and the doctor explains every resource, mapping, warning, and execution blocker before a run starts.
- [ ] **S12: Ericsson package conversion and branded distribution** `risk:medium` `depends:[S09,S11]`
  > After this: the ticket and inbox workflows are portable Archon-shaped packages staged atomically for OTTO and LOOP24 with Ericsson-only policy outside the portable YAML.
- [ ] **S13: Offline production showcase harness** `risk:medium` `depends:[S06,S07,S08,S09,S10,S11]`
  > After this: a production install can explain and run a safe Laptop Diagnostic Tour, controlled resilience modes, an optional AI/extension tour, and an opt-in temporary scheduling tour through chat or `hermes workflow showcase`; every report is backed by the ordinary RunStore, artifacts, interactions, and cleanup evidence.
- [ ] **S14: Production and upstream-merge release gate** `risk:high` `depends:[S05,S06,S07,S08,S09,S10,S11,S12,S13]`
  > After this: fault, race, shutdown, soak/leak, storage-retention, security, performance, cross-platform, installed-showcase, operator-surface, end-to-end, and upstream-merge rehearsal gates pass before the feature reaches either branded branch.

## Boundary Map

- `S01` produces `PluginAgentRunner`, `PluginAgentRunRequest`, `PluginAgentRunResult`, a fresh worker-process protocol, `ManagedProcessTree`, deadline/termination policies, and enforced name-level tool filtering. `S04`, `S05`, `S06`, and `S08` consume them.
- `S02` produces immutable workflow definitions, discovery precedence, `TopologyProjection`, compatibility reports, and resolved package roots. Every later workflow slice consumes them.
- `S02` also produces package digests, profile-owned trust decisions, risk summaries, and isolated-execution requirements; no execution slice bypasses them.
- `S03` produces `RunAdmissionController`, idempotent start/overlap/capacity decisions, immutable input snapshots, `RunStore`, `RunScheduler`, `NodeClaim`, event records, artifact metadata, bash execution, catalog-independent run queries, stable status JSON, and bounded retention/cleanup. `S04`–`S14` extend rather than bypass these contracts.
- `S04` produces AI-node execution, command resolution, structured outputs, and node session lineage. `S05`, `S06`, `S08`, and `S11` consume them.
- `S05` makes scheduling and claims production-safe. Approval, loop, cron, and distribution work cannot ship before it.
- `S06` produces script/loop/cancel executor contracts. `S07` reuses the loop pause model for rejection rework.
- `S07` produces compare-and-set approval decisions and resumable gates. `S09` exposes them to users.
- `S08` produces scoped execution workers and field-level compatibility mapping. `S11` surfaces those mappings during authoring.
- `S09` produces scope-authorized natural-language/slash/CLI entry points and deterministic surface-selection instructions for text/Mermaid catalog inspection, run inspection, actions, and cron lifecycle behavior. `S10` consumes the same contracts rather than reading run files.
- `S10` produces the authenticated workflow snapshot/delta/action API, generic `kanban_db` mutation preconditions, hardened Kanban delta/CAS API, shared desktop activity-board presentation layer, and independent workflow/Kanban adapters. `S12` enables the workflow page for branded capability packages.
- `S11` produces package authoring and doctor output. `S12` uses both to convert Ericsson fixtures.
- `S13` consumes the user-facing runtime and ships a versioned, digest-verified showcase catalog, packages, guide skill, evidence reporter, and safe cleanup path without bypassing ordinary workflow contracts.
- `S14` consumes the assembled system and proves it against real process, desktop, authentication, installed-showcase, and branch-topology boundaries.

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
├── admission.py
├── locks.py
├── models.py
├── resources.py
├── scheduler.py
├── schema.py
├── showcase.py
├── store.py
├── topology.py
├── trust.py
├── showcases/
│   ├── catalog.yaml
│   ├── catalog.schema.json
│   ├── digests.json
│   └── packages/
│       ├── laptop-diagnostic/
│       ├── resilience/
│       ├── ai-extensions/
│       └── scheduling/
├── dashboard/
│   ├── manifest.json
│   ├── dist/index.js
│   └── plugin_api.py
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
skills/productivity/workflow-showcase/
├── SKILL.md
├── workflows/
└── references/
skills/software-development/workflow-builder/
├── SKILL.md
└── references/
apps/desktop/src/
├── app/
│   ├── workflows/
│   └── kanban/
├── components/activity-board/
├── lib/workflow-api.ts
└── lib/kanban-api.ts
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
- Produces: `check_upstream_customizations.py --manifest PATH --diff RANGE` for ledger coverage and `--upstream-diff RANGE --report PATH` for overlap classification/evidence against every ledger-owned upstream surface.
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

- [ ] **Step 8: Add the machine-readable upstream-customization record and validator**

```yaml
schema_version: 1
feature: workflow-orchestration
upstream_changes:
  - id: managed-process-tree
    change_class: agent-core-generic
    owner: workflow-orchestration
    files:
      - tools/managed_process.py
      - tools/process_registry.py
    owned_symbols:
      - ProcessIdentity
      - TerminationPolicy
      - ManagedProcessTree
    tests:
      - tests/tools/test_managed_process.py
      - tests/tools/test_process_registry.py
    expected_commit_subject: "refactor(tools): extract managed process tree"
    upstream_candidate: true
    merge_guidance: Reconcile process identity, process-tree termination, escalation, and wait/reap behavior before replaying the extraction.
    removal_condition: Remove when Hermes upstream exposes an equivalent generic managed-process-tree primitive used by ProcessRegistry.
  - id: plugin-agent-runner
    change_class: agent-core-generic
    owner: workflow-orchestration
    files:
      - agent/plugin_agent.py
      - agent/plugin_agent_worker.py
      - tools/registry.py
      - hermes_cli/plugins.py
    owned_symbols:
      - PluginAgentRunRequest
      - PluginAgentRunResult
      - PluginAgentRunner
      - ToolRegistry.scoped_names
      - PluginContext.agent
    tests:
      - tests/agent/test_plugin_agent.py
      - tests/tools/test_registry.py
    expected_commit_subject: "feat(plugins): expose scoped host agent runner"
    upstream_candidate: true
    merge_guidance: Reconcile ToolRegistry generation/dispatch behavior and PluginContext facade construction before replaying this commit.
    removal_condition: Remove when Hermes upstream exposes a trusted-plugin agent runner with isolated name-scoped registry execution.
```

Immediately after writing the manifest, run `python3 scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml --set-verified-upstream "$(git rev-parse origin/main)"`; this writes the exact 40-hex `last_verified_upstream` value to every newly introduced entry before the first feature commit. The checker rejects non-hex, missing, or ancestor-inconsistent baselines. It validates schema shape, unique IDs, repository-contained paths, existing files/tests, expected commit-subject boundaries, and coverage of every upstream-owned file in the supplied feature diff. For `--upstream-diff`, it emits `none`, `same_file`, `owned_symbol`, or `possible_upstream_equivalent`, shows rationale/merge guidance/removal condition/tests, and exits non-zero when an owned-symbol/equivalent decision has not been acknowledged in the generated evidence record.

Tests synthesize upstream additions, deletions, renames, same-file non-overlap, owned-symbol edits, and equivalent public contracts. They prove the checker never treats a clean textual merge as proof of preserved behavior, never advances `last_verified_upstream`, and never edits branches; it only validates and reports. The merge skill owns the controlled baseline update after tests pass.

- [ ] **Step 9: Run focused and neighboring tests**

Run:

```bash
python3 -m pytest tests/agent/test_plugin_agent.py tests/tools/test_managed_process.py tests/scripts/test_check_upstream_customizations.py -q
python3 -m pytest tests/tools/test_process_registry.py tests/tools/test_registry.py tests/test_model_tools.py tests/test_get_tool_definitions_cache_isolation.py tests/tools/test_tool_search.py tests/hermes_cli/test_plugins.py -q
git diff --check
```

Expected: all selected tests pass and the checker reports the planned upstream-owned changes as covered.

- [ ] **Step 10: Commit the isolated generic plugin-agent seam**

```bash
git add agent/plugin_agent.py agent/plugin_agent_worker.py tools/registry.py hermes_cli/plugins.py tests/agent/test_plugin_agent.py docs/upstream-customizations scripts/check_upstream_customizations.py tests/scripts/test_check_upstream_customizations.py
git commit -m "feat(plugins): expose scoped host agent runner"
```

## Task 2: Portable Package Discovery, Models, Validation, and CLI

**Files:**
- Create: `plugins/workflow/__init__.py`
- Create: `plugins/workflow/plugin.yaml`
- Create: `plugins/workflow/models.py`
- Create: `plugins/workflow/schema.py`
- Create: `plugins/workflow/discovery.py`
- Create: `plugins/workflow/compat.py`
- Create: `plugins/workflow/topology.py`
- Create: `plugins/workflow/trust.py`
- Create: `plugins/workflow/cli.py`
- Create: `tests/plugins/workflow/conftest.py`
- Create: `tests/plugins/workflow/fixtures/portable/workflows/minimal.yaml`
- Create: `tests/plugins/workflow/test_schema.py`
- Create: `tests/plugins/workflow/test_discovery.py`
- Create: `tests/plugins/workflow/test_compat_matrix.py`
- Create: `tests/plugins/workflow/test_topology.py`
- Create: `tests/plugins/workflow/test_trust_policy.py`
- Create: `tests/plugins/workflow/test_catalog_cli.py`
- Create: `tests/plugins/workflow/test_cli.py`

**Interfaces:**
- Produces: `WorkflowDefinition`, `WorkflowNode`, `WorkflowPackage`, `ValidationIssue`, `CompatibilityReport`.
- Produces: `WorkflowPackageDigest`, `WorkflowRiskSummary`, `WorkflowTrustStore`, and `ExecutionEnvironmentRequirement`, with profile-owned digest-bound trust independent of package YAML.
- Produces: `TopologyProjection` and `project_topology(definition)`, with deterministic bounded text/Mermaid fields generated from the normalized DAG.
- Produces: `load_workflow(path)`, `discover_workflows(workdir, hermes_home, user_home)`, and `validate_package(package)`.
- Produces: side-effect-free plugin CLI commands `hermes workflow list`, `show`, `validate`, and `doctor --compat-report` with stable human and JSON catalog contracts.
- Consumes: `PluginContext.register_cli_command` only; no agent or network calls.

- [ ] **Step 1: Add failing fixtures for exact one-of node validation and precedence**

Cover all seven node types, mutual exclusivity, removed `steps:`, duplicate IDs, cycles, missing dependencies, invalid trigger rules, invalid retry bounds, path traversal, project-over-profile precedence, same-level duplicates, `persist_sessions`/`persist_session`, every published workflow/node option, every hook event/response field, Archon tool aliases, and provider-specific compatibility classification. Trust tests prove the digest covers YAML, sidecar, commands, scripts, MCP definitions, and executable resources; changing any covered byte invalidates trust; moving an identical package does not create trust; a package cannot trust itself; and profile A's trust never applies to profile B.

Catalog tests prove `list` returns name, description, source/precedence, compatibility, and runnable state, while `show` adds argument hints, `topology_text`, `topology_mermaid`, `topology_warnings`, node-type counts, approvals/outward-action points, required tools/skills/MCP/providers/runtimes, related Hermes cron schedules, and blocking findings. Full command/prompt bodies and resolved secrets must be absent.

Topology tests use sequential, fan-out/fan-in, disconnected-root, and 100-node boundary fixtures. Assert stable topological ordering with `(source_index, id)` tie-breaking; matching nodes/edges across text and Mermaid; generated `n0` aliases; bounded sanitized ID/type labels; no code fence inside JSON; and identical output over repeated loads. Prove schema validation rejects control/ANSI characters in identifiers. Put quotes, brackets, backticks, newlines, `%%{init:...}%%`, `click`, `classDef`, HTML, URLs, and secret canaries in descriptions/prompts and prove they never enter either projection; separately unit-test the label sanitizer with the same adversarial strings and prove none can escape a generated label or become a Mermaid directive.

```python
def test_node_requires_exactly_one_portable_type(tmp_path):
    path = write_workflow(tmp_path, node={"id": "bad", "prompt": "x", "bash": "echo x"})
    with pytest.raises(WorkflowValidationError, match="exactly one node type"):
        load_workflow(path)
```

- [ ] **Step 2: Run tests and confirm the plugin contracts are absent**

Run: `python3 -m pytest tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_discovery.py tests/plugins/workflow/test_topology.py -q`

Expected: FAIL on missing `plugins/workflow` and `plugins.workflow.topology` modules.

- [ ] **Step 3: Implement immutable parsed models and deterministic validation**

Use frozen dataclasses and preserve source locations for diagnostics. Unknown top-level or node fields become compatibility issues; fields that could alter execution fail strict validation rather than being ignored.

- [ ] **Step 4: Implement recursive discovery with explicit precedence**

Resolve explicit path, project `.hermes/workflows`, and `$HERMES_HOME/workflows` in that order. Never create or discover an Archon-branded directory. Sort normalized paths before loading. Cache only successful parses by path, size, mtime-ns, and SHA-256; provide deterministic invalidation. An explicitly supplied external package retains its portable YAML shape and is normalized into the same immutable package model.

- [ ] **Step 5: Implement deterministic dual topology projection**

```python
@dataclass(frozen=True)
class TopologyProjection:
    text: str
    mermaid: str | None
    warnings: tuple[str, ...]
    node_count: int
    edge_count: int


def project_topology(definition: WorkflowDefinition) -> TopologyProjection:
    """Build bounded text and strict-subset Mermaid from one normalized DAG."""
```

Walk nodes once in stable topological order and edges once in sorted source/target order. Text uses compact layer notation such as `collect -> [security, commercial] -> approval -> send`, bounded to 12 KiB with a deterministic omitted-node/edge suffix. Both projections build display labels only from node ID/type, reject control/ANSI input at validation, and replace characters outside Unicode letters/numbers and ` -_.:/()` with a safe replacement. Mermaid emits raw source beginning with `flowchart LR`; generated aliases (`n0`, `n1`, …); double-quoted sanitized labels; and `nX --> nY` edges only. It never emits initialization directives, raw HTML, URLs, click handlers, styles, classes, subgraphs, or workflow-provided Mermaid.

Truncate node display labels deterministically to 80 Unicode code points including an ellipsis and report `topology_label_truncated`. Bound text/source on valid UTF-8 boundaries. Text truncation reports `topology_text_truncated`. Set Mermaid to `None` when the graph exceeds 100 nodes, 200 edges, or 64 × 1,024 bytes after label truncation, reporting `topology_mermaid_too_many_nodes`, `topology_mermaid_too_many_edges`, or `topology_mermaid_too_large` in that deterministic order. These graph/source limits disable Mermaid while preserving text. Serialize to catalog JSON as `topology_text`, `topology_mermaid`, and `topology_warnings`.

- [ ] **Step 6: Implement field-level compatibility reporting**

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

- [ ] **Step 7: Implement digest-bound trust and isolated-execution preflight**

```python
@dataclass(frozen=True)
class WorkflowPackageDigest:
    sha256: str
    covered_relative_paths: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionEnvironmentRequirement:
    mode: Literal["trusted_local", "isolated_backend_required"]
    required_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowRiskSummary:
    package_digest: str
    risk_digest: str
    shell_or_script_nodes: tuple[str, ...]
    requested_tools: tuple[str, ...]
    requested_skills: tuple[str, ...]
    local_mcp_servers: tuple[str, ...]
    providers: tuple[str, ...]
    outward_action_nodes: tuple[str, ...]
    required_secret_names: tuple[str, ...]
    execution_environment: Literal["trusted_local", "isolated_backend_required"]


class WorkflowTrustStore:
    def trust(self, package_digest: str, *, actor: str, risk_digest: str) -> None: ...
    def revoke(self, package_digest: str) -> bool: ...
    def check(self, package_digest: str) -> Literal["trusted", "untrusted"]: ...
```

Store trust under the active profile's neutral workflow state, outside package roots, using a bounded cross-process lock, atomic replacement, restrictive permissions, and no secret values. `doctor` produces the risk summary without starting an agent/provider/network/MCP process. `trust NAME --digest SHA256` requires the current package and risk digests to match; `untrust NAME` revokes. Local execution fails closed when untrusted. An isolated execution request succeeds only when the selected existing Hermes terminal backend advertises the required isolation and package/workdir containment; this task does not add a new container implementation.

Test a malicious package attempting to set trust in YAML/sidecar, symlink/resource escape, unreadable resources, trust-store corruption, concurrent trust/revoke, package mutation between `doctor` and `trust`, and no configured isolated backend. Human/JSON output names risks and remediation without returning command/prompt bodies or resolved secrets.

- [ ] **Step 8: Register the plugin CLI and keep inspection side-effect free**

`list`, `show`, `validate`, `doctor`, `trust`, and `untrust` must not initialize MCP, providers, Mermaid, or `AIAgent`. JSON output is available with `--json`; human output is stable and redacts environment values. Define the parser's `--topology text|mermaid|both` default as `None`; human output resolves `None` to `text`, while `show NAME --json` always returns both topology fields and rejects only an explicitly supplied selector. Mermaid human output is a fenced source block, not terminal rendering. Cron linkage is a read-only join against existing profile-local Hermes cron definitions and does not mutate schedules.

- [ ] **Step 9: Run focused plugin, trust, and plugin-discovery tests**

```bash
python3 -m pytest tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_discovery.py tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_topology.py tests/plugins/workflow/test_trust_policy.py tests/plugins/workflow/test_catalog_cli.py tests/plugins/workflow/test_cli.py -q
python3 -m pytest tests/hermes_cli/test_plugins.py -q
git diff --check
```

- [ ] **Step 10: Commit the validation and trust tracer**

```bash
git add plugins/workflow tests/plugins/workflow
git commit -m "feat(workflow): discover and validate Archon packages"
```

## Task 3: Durable Bash DAG Execution Tracer

**Files:**
- Create: `plugins/workflow/locks.py`
- Create: `plugins/workflow/admission.py`
- Create: `plugins/workflow/store.py`
- Create: `plugins/workflow/scheduler.py`
- Create: `plugins/workflow/executors/__init__.py`
- Create: `plugins/workflow/executors/base.py`
- Create: `plugins/workflow/executors/bash.py`
- Create: `tests/plugins/workflow/test_store.py`
- Create: `tests/plugins/workflow/test_admission.py`
- Create: `tests/plugins/workflow/test_scheduler.py`
- Create: `tests/plugins/workflow/test_bash_e2e.py`
- Create: `tests/plugins/workflow/test_run_queries.py`
- Create: `tests/plugins/workflow/test_retention.py`
- Modify: `plugins/workflow/cli.py`

**Interfaces:**
- Produces: `RunAdmissionRequest`, `RunAdmissionResult`, `RunAdmissionController.start`, and the sole `RunStore.start_run` path with atomic idempotency, overlap, queue, rate, executing/queued/paused/nonterminal, and profile-worker admission.
- Produces: `RunStore.load_run`, `append_event`, `claim_node`, `complete_node`, and `release_or_expire_claim`; no caller may create a run directory or schedule work outside `start_run`.
- Produces: `RunStore.list_runs`, `get_run_status`, `tail_events(after_sequence, limit)`, compare-and-set action methods, and `cleanup_runs`, with profile/conversation authorization filters, monotonic state/event versions, and bounded pagination/retention.
- Produces: `NodeExecutor.execute(context) -> NodeExecutionResult`.
- Produces: `RunScheduler.advance(run_id)` and CLI `run`, `runs`, `status`, `events`, `resume`, `cancel`, `abandon`, and `cleanup` foundations, including a durable pause envelope for worker interactions.
- Consumes: validated `WorkflowPackage` from Task 2.

- [ ] **Step 1: Write failing run-store, locking, and bash end-to-end tests**

The E2E fixture has two dependent bash nodes. Assert package/policy/input snapshot, sequential execution, stdout artifact, journal sequence, atomic projection, status output, and resume without re-running the completed first node. Change and delete the original input after admission and prove downstream nodes continue from the immutable snapshot; unreadable, oversized, symlink-escaping, or changed-during-copy inputs fail before a run becomes runnable.

Add query tests for active/recent filtering, workflow/status/limit filters, deterministic newest-first ordering, unknown/unauthorized run IDs, sanitized event tails, and stable JSON. The run summary contains `action`, `run_id`, `workflow`, `workflow_version`, `definition_digest`, `policy_digest`, `input_manifest_digest`, `trigger`, `idempotency_key_digest`, `concurrency_key`, `admission_disposition`, `queue_position`, `blocked_by_run_id`, `state_version`, `event_sequence`, `status`, `health`, `started_at`, `updated_at`, `elapsed_ms`, `current_nodes`, graph `progress`, `last_semantic_progress_at`, `attempts`, `next_retry_at`, `pending_interaction`, `last_error`, `artifacts`, `warnings`, and `next_actions`, but no full prompts, reasoning, secrets, unrestricted tool arguments, raw idempotency key, or raw input values. `health` is a derived diagnostic (`healthy`, `semantic_idle`, `stale_owner`, `retry_wait`, `user_wait`, `reconciliation_required`, or `terminal`) and never replaces the lifecycle state.

- [ ] **Step 2: Write failing duplicate-start, overlap-policy, and admission-pressure tests**

```python
@dataclass(frozen=True)
class RunAdmissionRequest:
    workflow_name: str
    definition_digest: str
    policy_digest: str
    input_manifest_digest: str
    trigger_source: Literal["chat", "desktop", "cli", "api", "cron"]
    idempotency_key: str
    concurrency_key: str
    concurrency_policy: Literal["queue", "allow", "forbid"] = "queue"


@dataclass(frozen=True)
class RunAdmissionResult:
    run_id: str | None
    disposition: Literal["created", "existing", "queued", "rejected"]
    reason_code: str | None


@dataclass(frozen=True)
class PreparedRunSnapshot:
    staging_directory: Path
    definition_digest: str
    policy_digest: str
    input_manifest_digest: str
    reserved_bytes: int


class RunAdmissionController:
    def start(
        self,
        request: RunAdmissionRequest,
        *,
        immutable_snapshot: PreparedRunSnapshot,
    ) -> RunAdmissionResult: ...
```

Race 100 processes/threads with the same key and digest; assert one run ID and 99 `existing` results. Reuse the key with one changed digest and require `idempotency_conflict`. With distinct keys, prove default `queue` admits one active concurrency-key owner and durable queued followers, `forbid` rejects, and `allow` overlaps only within executing/worker capacity. Use cron schedule ID plus UTC fire instant to deduplicate duplicate delivery across DST/restart. Enforce initial profile defaults of four executing, 100 queued, 100 paused, 200 total nonterminal, 60 start requests per minute, and four total workflow workers. Paused/retry waits do not consume executing/worker capacity. A rejected start allocates nothing; a queued start persists and reserves only its exact immutable definition/input snapshot, with no worker, MCP process, future output-artifact reservation, or retry timer until promotion.

Simulate crash after admission reservation, after snapshot creation, and before queue publication. Restart reconciliation must either finish one discoverable run or release the reservation with a typed event; the idempotency key must never create two runs. A stale queued run whose trusted package digest changed remains bound to its original immutable snapshot.

- [ ] **Step 3: Add two-process claim and start-versus-shutdown races before implementation**

Spawn two processes that attempt to claim the same ready node. Exactly one receives a `NodeClaim`; the other observes the active lease. Race start admission against coordinator shutdown: either the run is durably queued/interrupted and discoverable or admission is rejected before allocation; no worker may start after admission closes. Include simulated Windows lock operations in a unit test.

- [ ] **Step 4: Implement bounded cross-process locking and the admission ledger**

Follow cron's reentrant in-process plus `fcntl`/`msvcrt` pattern. Use non-blocking acquisition with monotonic timeout. The lock file contains one byte on Windows and is never deleted during an active run. Store start reservations/queue order in a profile-local SQLite admission ledger owned by `RunStore`, using WAL, a bounded busy timeout, a unique source/workflow/idempotency constraint, and explicit reservation states. Per-run snapshots/journals remain workflow lifecycle truth; admission reconciliation joins ledger rows to run projections and never infers success.

- [ ] **Step 5: Implement snapshot, journal, projection, immutable inputs, and artifacts**

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

Use unique temporary projection/input files, flush, `fsync`, and atomic replace. Copy inputs without following escaping symlinks, verify size/digest after copy, and publish the run only after every required snapshot is readable. Append journal records under lock and verify sequence continuity on load. Define run states `queued`, `running`, `waiting_retry`, `paused`, `interrupted`, `succeeded`, `failed`, `cancelled`, and `abandoned`; define node states `pending`, `ready`, `claimed`, `running`, `waiting_retry`, `paused`, `succeeded`, `failed`, `skipped`, `cancelled`, and `interrupted`. Reject transitions outside the explicit state machine.

- [ ] **Step 6: Implement scheduler readiness and the bash executor**

The scheduler claims under lock, releases the lock, executes, then compare-and-set completes the same attempt. The bash executor uses Hermes terminal environment conventions, sanitized environment, timeout, bounded stdout/stderr files, and process-group termination.

- [ ] **Step 7: Add CLI execution, run inspection, admission, and retention output**

`hermes workflow run PATH --arguments TEXT --idempotency-key KEY` calls `RunStore.start_run`; interactive CLI generates a cryptographically random key when omitted. Raw keys are neither journaled nor returned by status APIs; only a digest is retained for audit. It reports `created`, `existing`, `queued`, or a stable non-billable rejection before work starts, then exits with distinct codes for completed, paused, cancelled, and failed when the caller waits. `runs [--workflow NAME] [--status STATE] [--limit N] --json` lists authorized active/recent summaries; `status RUN_ID --json` exposes detailed node/attempt state; `events RUN_ID [--tail N] --json` exposes a sanitized bounded diagnostic tail. `status` with no run ID returns the same active/recent summary as `runs` for conversational convenience.

`abandon RUN_ID` makes an interrupted/failed/paused run terminal without deleting audit evidence. `cleanup [--older-than 7d] [--dry-run] [--json]` defaults to Archon's seven-day cleanup window, never removes active/queued runs or live admission keys, uses per-run locks plus rename-to-quarantine before bounded deletion, tolerates concurrent readers/restarts, and reports reclaimed files/bytes. Initial configurable defaults are 512 MiB per run and 2 GiB for all workflow runs in one profile; writes reserve space before commit and fail with a typed quota error instead of partially exceeding either cap. Before snapshot/process allocation, require free space equal to `max(1 GiB, min(5 GiB, 5% of target-volume capacity))`.

- [ ] **Step 8: Run durability, admission, and race tests repeatedly**

```bash
python3 -m pytest tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_run_queries.py tests/plugins/workflow/test_retention.py -q
git diff --check
```

The admission test runs the 100-way duplicate-start scenario once per supported lock implementation; the scheduler race test runs claim/complete contention 20 times without an optional pytest plugin.

Expected: duplicate delivery produces one run, every internal scheduling repetition produces exactly one claim/completion event per attempt, and teardown leaves no admission reservation, worker, process, timer, or artifact allocation for rejected starts.

- [ ] **Step 9: Commit admission separately, then the executable vertical slice**

```bash
git add plugins/workflow/admission.py plugins/workflow/store.py plugins/workflow/locks.py plugins/workflow/cli.py tests/plugins/workflow/test_admission.py
git commit -m "feat(workflow): add idempotent bounded run admission"

git add plugins/workflow tests/plugins/workflow
git commit -m "feat(workflow): execute durable bash DAG runs"
```

## Task 4: Command and Prompt AI Nodes

**Files:**
- Create: `plugins/workflow/resources.py`
- Create: `plugins/workflow/sessions.py`
- Create: `plugins/workflow/executors/ai.py`
- Create: `tests/plugins/workflow/fixtures/portable/commands/investigate.md`
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
- Modify: `plugins/workflow/admission.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/locks.py`

**Interfaces:**
- Produces: bounded ready-layer execution across all active runs, all Archon trigger rules, deadline inheritance, persisted combined retry timing, lease renewal/expiry, coordinated shutdown/reaping, suspend/restart/admission reconciliation, host-pressure refusal, and journal-based projection repair.
- Consumes: executor and claim contracts from Tasks 3 and 4.

- [ ] **Step 1: Add table-driven trigger-rule and skip-propagation tests**

Cover `all_success`, `one_success`, `none_failed_min_one_success`, and `all_done` for succeeded, failed, skipped, cancelled, and running dependencies. Cover Archon's string/numeric operators, JSON dot access, `&&`/`||` precedence, static parse errors, and runtime non-finite/type failures that skip with a journaled warning.

- [ ] **Step 2: Add deterministic retry/backoff tests**

Inject a seeded jitter source. Assert maximum attempts, transient classification, capped delay, persisted `next_attempt_at`, no busy-loop before due time, and no retry for authentication, authorization, credit exhaustion, validation, cancellation, or unknown-side-effect outcomes. Count internal provider retries and workflow attempts against one configured maximum of five by default so the two layers cannot multiply. Prove backoff releases both worker and scheduler capacity and is interruptible by cancel/shutdown.

- [ ] **Step 3: Add parallel and stale-worker fault tests**

Use barriers to prove independent nodes overlap without exceeding `max_parallel_nodes`. Complete a stale claim after a replacement attempt begins and assert compare-and-set rejection. Kill a worker after claim and assert lease expiry produces `interrupted`. Race two persisted-session runs for the same workflow/node/scope and prove generation compare-and-set prevents an older completion from replacing the newer session record.

Add deadline tests that independently stall a provider response, model stream, tool, hook, MCP startup, deterministic subprocess, and child agent. Prove semantic progress resets only the idle timer, heartbeat resets only the lease, and the hard wall deadline still wins. A child receives the minimum of its request, remaining parent time, and workflow cap. Inline `maxTurns` cannot make that child unbounded.

Add shutdown/recovery tests at spawn-before-record, identity-recorded, provider-call, tool-call, child-agent wait, retry backoff, completion-before-persist, and persist-before-reap boundaries. Normal shutdown stops admission, writes a shutdown event, cancels every active tree concurrently, escalates/reaps, marks attempts `interrupted`, releases leases, and returns within the configured total deadline. Coordinator IPC EOF makes workers self-terminate. Simulated suspend/wake and wall-clock jumps reconcile from monotonic/UTC gaps. A stale PID with a different start identity is never signalled.

Race completion versus cancel, retry wake-up versus cancel, approval resume versus cancel, queued-run promotion versus shutdown, and low-disk/capacity release versus new admission. The first committed state transition wins; every loser receives the current sanitized state and cannot create a worker or overwrite a terminal outcome. Simulate a process that refuses cooperative/TERM handling and a platform-reported uninterruptible process: the runtime must attempt bounded KILL/reap, record `cleanup_failed` when exit cannot be observed, block related new work, and never claim the process was reaped.

- [ ] **Step 4: Implement topological ready layers with a bounded executor**

Do not create one thread/process per node or per queued run. Submit at most the lesser of per-run `max_parallel_nodes` and the profile-wide `max_total_workers`, replenish fairly across admitted runs, and stop scheduling new work after cancellation, shutdown, or terminal failure rules require it. Track every submitted worker and descendant in one coordinator registry; no callback may discard the final process handle before `wait()`/reap and durable outcome reconciliation.

- [ ] **Step 5: Implement deadline, lease, retry, shutdown, and resume semantics**

Resolve all lifecycle configuration before spawn. Initial `config.yaml` defaults are `max_parallel_nodes: 4`, profile-wide `max_total_workers: 4`, `max_executing_runs: 4`, `max_queued_runs: 100`, `max_paused_runs: 100`, `max_nonterminal_runs: 200`, `max_start_requests_per_minute: 60`, AI semantic idle `300s`, AI hard wall `1800s`, provider request `300s`, deterministic subprocess `120s`, heartbeat `5s`, lease `30s`, cooperative shutdown `5s`, TERM grace `5s`, KILL/reap grace `2s`, combined retries `5`, process-tree RSS `2048 MiB`, process-tree CPU `900s`, descendants per node `32`, and free-disk watermark `max(1 GiB, min(5 GiB, 5% of target-volume capacity))`. Values are schema-bounded and may be tightened by profile/sidecar; no timeout accepts infinity or a non-positive value. The parent/child effective deadline is calculated from monotonic absolute deadlines, not by restarting relative timers. Resource polling is bounded and stops with the worker; unsupported platform metrics fail closed for enforcement-required configurations and are called out by `doctor`.

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

Use a table-driven race matrix for cancel against queued admission, node claim, script process-tree start, AI loop iteration, child-agent spawn, retry wake-up, approval/input resume, successful completion, failed completion, outward-action request send, outward-action response loss, and coordinator shutdown. Assert:

- cancel is idempotent and stops new admission/scheduling first;
- completion committed first remains terminal and cancel reports `already_terminal`;
- cancel committed first terminates/reaps the complete registered tree and rejects every late success/failure event;
- queued, paused, and backoff work cancels without allocating a process;
- an outward action with a lost response becomes `reconciliation_required` rather than cancelled/retried;
- cancellation resumed after coordinator restart completes cleanup from recorded process identities; and
- a simulated uninterruptible child produces `cleanup_failed`, blocks related new work, and never produces a false `reaped` event.

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

## Task 9: Chat, Gateway, Desktop Chat, and Cron Activation

**Files:**
- Create: `skills/productivity/workflow/SKILL.md`
- Create: `tests/agent/test_workflow_skill_command.py`
- Create: `tests/cron/test_workflow_cron.py`
- Create: `tests/gateway/test_workflow_skill_dispatch.py`
- Create: `tests/tui_gateway/test_workflow_skill_dispatch.py`
- Create: `tests/plugins/workflow/test_operator_scope.py`
- Create: `ui-tui/src/__tests__/workflowTopology.test.ts`
- Create: `apps/desktop/src/components/assistant-ui/embeds/workflow-topology.test.tsx`
- Create: `apps/desktop/src/lib/workflow-skill-command.test.ts`
- Modify: `plugins/workflow/cli.py`

**Interfaces:**
- Produces: the `/workflow` skill command; deterministic surface-selection instructions for `topology_text`/`topology_mermaid`; and conversational list/show/runs/status/events/run/approve/reject/provide-input/resume/retry/reconcile/cancel/abandon/cleanup/reset-sessions instructions.
- Consumes: normal Hermes skill command injection, workflow CLI, durable approvals, and cron skill/provider/model/toolset/workdir/delivery fields.

- [ ] **Step 1: Write explicit skill-command dispatch tests on every chat surface**

Assert `/workflow run demo`, `/workflow list`, `/workflow show demo`, `/workflow runs`, and `/workflow status RUN_ID` load the skill as a user message in CLI/gateway/TUI/desktop catalog paths, do not register a new model tool, and do not mutate the system prompt or global tool list. Skill/quick-command discovery remains visible in the desktop slash palette rather than being removed by built-in curation.

Add renderer-contract tests using the existing chat-message components. Ink `Md` receives a fenced Mermaid sample and must display the language/source as a code block rather than pretending to render a graph. Desktop `RichCodeBlock` must route `language="mermaid"` to the lazy Mermaid renderer, retain source during streaming/parse failure, use `securityLevel: "strict"`, and render the completed SVG inside the existing rich boundary. These are regression tests around existing chat renderer plumbing; Task 9 adds no new chat-message-specific renderer or dependency. Task 10 separately adds the native operations pages.

Also assert that a disabled workflow plugin produces an actionable `hermes plugins enable workflow` response, while an Ericsson-staged profile has the plugin enabled through the existing `plugins.enabled` config path.

- [ ] **Step 2: Write natural-language activation description tests**

Verify the skill description contains run, schedule, list, describe/show, topology/diagram, active/recent runs, status/health/progress, wait reason, failure/retry diagnostics, approval, input, reject, resume, reconcile, cancel, cleanup, reset sessions, automation, and workflow intent terms while remaining concise enough for the stable skill index.

Exercise natural-language requests: “What workflows can I run?”, “What does supplier review do?”, “Which workflows are running?”, “Show workflows waiting for approval”, “How far is run X?”, “Why did it fail?”, “What is it waiting on?”, “Is that running node still making progress?”, “What happens next?”, and “Cancel the inbox workflow.” Assert each maps to the corresponding read-only/action CLI JSON contract and that ambiguous destructive actions ask for selection/confirmation rather than guessing a run.

- [ ] **Step 3: Write cron completion and paused-approval tests**

Create a real temp-home cron job with `skills=["workflow"]`. Snapshot every required file/document input before saving the schedule and bind the schedule to that immutable input manifest or an explicitly configured refresh-at-fire policy that revalidates before admission. Assert the cron idempotency key is `schedule_id + scheduled_utc_fire_instant`, duplicate delivery across restart/DST returns the existing run, and overlapping fires follow the sidecar concurrency policy. Assert it runs the same workflow store, delivers final output, and on either a workflow gate or an inner Hermes tool-approval pause delivers run ID/instructions then exits without holding a worker. A later approval resumes the run exactly once. Existing `approvals.cron_mode` remains authoritative.

- [ ] **Step 4: Author the operational workflow skill**

The skill treats `hermes workflow` as the control plane, never edits run/session state directly, never changes graph order, and never auto-approves outward action. Before `run` or schedule creation it obtains required inputs, resolves user-supplied paths, proves readability/type/size, shows the selected artifacts and risk/trust result, and asks for confirmation when selection is ambiguous or outward impact requires it. It never starts work and then asks for a mandatory input. It distinguishes interactive continuation from background notification and always scopes chat `runs`, `status`, `events`, actions, and `reset-sessions` to the authenticated profile plus current conversation/user identity. An explicit run ID still passes authorization; it is not an enumeration bypass. The local CLI remains profile-scoped.

Chat derives one idempotency key from authenticated conversation identity plus originating message/action identity; Desktop supplies one UUID per Run-button action; cron and API use their defined source keys. Re-delivery of the same chat/Desktop action opens or reports the existing run rather than starting another. If the same workflow/concurrency key is active, the skill explains whether the new intentional request will queue, overlap, or be refused and never silently changes policy.

For catalog questions, the skill uses `list --json` or `show NAME --json` and explains description, runnable/compatibility state, arguments, topology, approvals/outward actions, requirements, and schedules. On classic CLI, Ink TUI, dashboard-embedded TUI, unknown, and messaging surfaces it emits `topology_text` only. On desktop it emits `topology_text` first as the accessible/copyable summary, then—when non-null—wraps the raw `topology_mermaid` value in exactly one fenced `mermaid` block. It never puts the fence inside JSON, claims terminal Mermaid source was rendered, or omits the text fallback. For execution questions, it uses `runs`, `status`, or `events --tail` and explains progress, current nodes, elapsed time, retry/approval state, sanitized error, artifacts, and `next_actions`. It never reads raw run files, full prompts, hidden reasoning, secret material, or unrestricted tool arguments.

- [ ] **Step 5: Add machine-readable CLI output required by the skill**

Every invoked CLI command supports `--json`. Catalog records have stable `action`, `workflow`, `description`, `source`, `precedence`, `compatibility`, `runnable`, `topology_text`, `topology_mermaid`, `topology_warnings`, `requirements`, `approvals`, `schedules`, `warnings`, and `next_actions` fields as applicable. `topology_mermaid` is raw source or `null`, never a Markdown fence. Run records use the Task 3 summary contract and detailed status adds node/attempt state. Secret values, credentials, full prompt/command bodies, reasoning, and unrestricted tool arguments are excluded.

- [ ] **Step 6: Run cross-surface and cron tests**

```bash
python3 -m pytest tests/agent/test_workflow_skill_command.py tests/gateway/test_workflow_skill_dispatch.py tests/tui_gateway/test_workflow_skill_dispatch.py tests/cron/test_workflow_cron.py tests/plugins/workflow/test_operator_scope.py -q
cd ui-tui && npx vitest run src/__tests__/workflowTopology.test.ts src/__tests__/markdown.test.ts
cd ../apps/desktop && npx vitest run src/components/assistant-ui/embeds/workflow-topology.test.tsx src/lib/workflow-skill-command.test.ts src/lib/desktop-slash-commands.test.ts
cd ../..
git diff --check
```

- [ ] **Step 7: Commit user activation**

```bash
git add skills/productivity/workflow plugins/workflow/cli.py tests/agent/test_workflow_skill_command.py tests/gateway/test_workflow_skill_dispatch.py tests/tui_gateway/test_workflow_skill_dispatch.py tests/cron/test_workflow_cron.py tests/plugins/workflow/test_operator_scope.py ui-tui/src/__tests__/workflowTopology.test.ts apps/desktop/src/components/assistant-ui/embeds/workflow-topology.test.tsx apps/desktop/src/lib/workflow-skill-command.test.ts
git commit -m "feat(workflow): activate runs from chat and cron"
```

## Task 10: Native Desktop Workflow and Kanban Operations

**Files:**
- Create: `plugins/workflow/dashboard/manifest.json`
- Create: `plugins/workflow/dashboard/dist/index.js`
- Create: `plugins/workflow/dashboard/plugin_api.py`
- Create: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `hermes_cli/kanban_db.py`
- Create: `tests/hermes_cli/test_kanban_mutation_preconditions.py`
- Modify: `plugins/kanban/dashboard/plugin_api.py`
- Modify: `tests/plugins/test_kanban_dashboard_plugin.py`
- Create: `apps/desktop/src/components/activity-board/types.ts`
- Create: `apps/desktop/src/components/activity-board/activity-board.tsx`
- Create: `apps/desktop/src/components/activity-board/virtual-card-column.tsx`
- Create: `apps/desktop/src/components/activity-board/activity-board.test.tsx`
- Create: `apps/desktop/src/app/workflows/index.tsx`
- Create: `apps/desktop/src/app/workflows/adapter.ts`
- Create: `apps/desktop/src/app/workflows/adapter.test.ts`
- Create: `apps/desktop/src/app/workflows/run-inspector.tsx`
- Create: `apps/desktop/src/app/workflows/attention-inbox.tsx`
- Create: `apps/desktop/src/app/workflows/store.ts`
- Create: `apps/desktop/src/app/workflows/index.test.tsx`
- Create: `apps/desktop/src/app/kanban/index.tsx`
- Create: `apps/desktop/src/app/kanban/adapter.ts`
- Create: `apps/desktop/src/app/kanban/adapter.test.ts`
- Create: `apps/desktop/src/app/kanban/task-inspector.tsx`
- Create: `apps/desktop/src/app/kanban/store.ts`
- Create: `apps/desktop/src/app/kanban/index.test.tsx`
- Create: `apps/desktop/src/app/routes.test.ts`
- Modify: `apps/desktop/src/app/routes.ts`
- Modify: `apps/desktop/src/app/desktop-controller.tsx`
- Modify: `apps/desktop/src/app/chat/sidebar/index.tsx`
- Modify: `apps/desktop/src/hermes.ts`
- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/i18n/types.ts`
- Modify: `apps/desktop/src/i18n/en.ts`
- Modify: `apps/desktop/src/i18n/ja.ts`
- Modify: `apps/desktop/src/i18n/zh.ts`
- Modify: `apps/desktop/src/i18n/zh-hant.ts`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Produces: `WorkflowDesktopApi`, `WorkflowRunSnapshot`, `WorkflowAttentionPage`, `WorkflowEventPage`, `TaskMutationPrecondition`, `TaskMutationConflict`, `kanban_db.set_task_status`, Kanban board/task pagination and event cursors, same-transaction compare-and-set lifecycle actions, `ActivityBoardModel`, `workflowBoardModel`, `kanbanBoardModel`, `/workflows`, and `/kanban` desktop pages.
- Consumes: Task 3 `RunStore` summary/detail/event projections, Task 7 compare-and-set interaction decisions, Task 9 authorization/sanitization contracts, the existing authenticated Desktop REST bridge, `kanban_db`, TanStack Query, nanostores, React Virtual, existing Desktop design primitives, and existing native notification infrastructure.

- [ ] **Step 1: Write failing workflow API authorization, pagination, cursor, and action tests**

Create a hidden dashboard manifest whose only purpose is mounting the bundled workflow plugin API in both `hermes dashboard` and headless `hermes serve`; its `dist/index.js` is an inert no-op and registers no dashboard tab or component. API tests use a temporary `HERMES_HOME` and real `RunStore` records and assert:

- `GET /api/plugins/workflow/runs` returns at most 100 summaries per page with an opaque continuation cursor, exact state, workflow version/digest, trigger, admission disposition, queue position/blocking run, graph-progress numerator/denominator, health, last semantic progress, pending interaction summary, artifact count, and `next_actions`;
- `GET /api/plugins/workflow/runs/{run_id}` returns node/attempt/dependency/retry/attention/artifact/verification detail without full prompts, hidden reasoning, secret values, unrestricted tool arguments, or raw environment values;
- `GET /api/plugins/workflow/attention` returns only actionable approval, input, capability, and reconciliation items, ordered oldest first and deduplicated by run plus interaction generation;
- `GET /api/plugins/workflow/runs/{run_id}/events?after=<sequence>&limit=200&wait_seconds=20` bounds `limit` to 200 and `wait_seconds` to 20, returns a monotonic cursor and `cursor_reset=true` when the requested sequence cannot be continued;
- local Desktop management is limited to the selected profile, and any authenticated user/conversation restriction recorded on a run is enforced rather than bypassed by an explicit run ID;
- action requests carry `expected_version` plus the pending interaction ID/digest where applicable; one concurrent approve/reject/resume/retry/reconcile/cancel/abandon wins and stale requests return `409` with the current sanitized snapshot; and
- a disabled workflow plugin remains absent and Desktop can distinguish `plugin_disabled` from a transport failure.

- [ ] **Step 2: Implement the thin workflow plugin REST adapter**

Implement endpoint handlers as authorization, validation, pagination, and serialization adapters over public `RunStore` methods. They never read `run.json`/`events.jsonl` directly, run a scheduler, initialize a provider/MCP client, or mutate a run outside the Task 3/7 compare-and-set methods. Use opaque base64url cursors containing schema version, sort key, and run/sequence position; sign or validate their structural scope so a cursor from another profile/run cannot enumerate data. Return `409` for stale state, `410` plus `cursor_reset=true` for an expired cursor, `404` for unauthorized/not-found without revealing which, and bounded stable error codes for the Desktop client.

Use an API-only hidden manifest and inert asset so the browser dashboard does not gain a duplicate Workflow tab:

```json
{
  "name": "workflow",
  "label": "Workflow",
  "version": "1.0.0",
  "tab": { "hidden": true },
  "entry": "dist/index.js",
  "api": "plugin_api.py"
}
```

`dist/index.js` contains only `void 0;`; it does not access `window.__HERMES_PLUGIN_SDK__`, register a component, or add a browser route.

Long polling performs no busy loop: wait on the store's bounded event notification with a monotonic deadline and return an empty page at 20 seconds. If the store cannot provide a notification primitive without coupling persistence to FastAPI, use a condition owned by the plugin API adapter and a maximum 500 ms reconciliation poll while at least one request is present; release all waiters on backend shutdown. Record the API-only dashboard manifest and every existing file touch in the customization ledger.

- [ ] **Step 3: Write failing Kanban REST delta and compare-and-set tests**

Extend the existing Kanban dashboard-plugin tests to require:

- `GET /board/summary?board=<slug>` returns column counts, assignees/lanes, latest/oldest event cursors, diagnostics counts, and board metadata without returning every card;
- `GET /tasks?board=<slug>&status=<state>&limit=100&cursor=<opaque>` returns at most 100 cards in deterministic priority/update/id order, rejects cross-board cursors, and supports tenant, assignee, search, and archived filters;
- `GET /events?board=<slug>&after=<id>&limit=200&wait_seconds=20` uses the same bounded long-poll/cursor-reset contract as workflow events and authorizes local, token, and OAuth REST through the existing server gate;
- status/assignee/reclaim/comment mutations accept optional `expected_status`, `expected_current_run_id`, and `expected_event_id`, reject stale requests with `409`, and preserve current dashboard behavior when the fields are omitted; and
- setting `running` directly remains forbidden, a stale drag/action never reclaims a newer worker, dependency promotion remains atomic, and board switching never changes the CLI's persisted current-board pointer.

- [ ] **Step 4: Write failing generic Kanban persistence-precondition tests**

```python
class UnsetType(Enum):
    TOKEN = "unset"


UNSET = UnsetType.TOKEN


@dataclass(frozen=True)
class TaskMutationPrecondition:
    expected_status: str | UnsetType = UNSET
    expected_current_run_id: int | None | UnsetType = UNSET
    expected_event_id: int | UnsetType = UNSET


@dataclass(frozen=True)
class TaskMutationSnapshot:
    task_id: str
    status: str
    current_run_id: int | None
    latest_event_id: int


class TaskMutationConflict(RuntimeError):
    current: TaskMutationSnapshot
```

Test `complete_task`, `block_task`, `schedule_task`, `unblock_task`, `archive_task`, `reclaim_task`, `assign_task`, append-only comment creation, and the new generic `set_task_status` with omitted, matching, and stale preconditions. `UNSET` means no check; explicit `None` means the task must have no current run. Expected event ID is the task's latest event ID read inside the mutation transaction. Race a stale Desktop reclaim/status request against a newly claimed worker and prove the stale transaction raises `TaskMutationConflict` without signalling/reclaiming the newer worker, ending its run, changing dependencies, or appending a success event.

Preserve backward compatibility: omitted preconditions retain current CLI/dashboard behavior, existing `expected_run_id` arguments remain accepted, and supplying both forms with different values is a validation error. Move dashboard-only `_set_status_direct` behavior into `kanban_db.set_task_status` while preserving dependency gating, current-run closure, child demotion/promotion, and event semantics; `running` remains claim-only.

- [ ] **Step 5: Implement generic Kanban CAS, then the thin API hardening**

Add a private transaction-level precondition helper in `hermes_cli/kanban_db.py`; every lifecycle method invokes it after acquiring `write_txn` and before closing a run, changing task/dependency state, or appending a success event. `TaskMutationConflict` carries a sanitized current snapshot but no prompt/body/secret content. Reclaim uses two phases: its first transaction validates preconditions, atomically claims the reclaim transition, clears/closes the exact run, and captures that claim's process identity; after commit it signals only the captured identity with no SQLite transaction held, then records termination metadata in a second bounded transaction. A failed precondition sends no signal, and a late worker completion loses its run/claim CAS. This is a generic Kanban safety feature with no workflow imports or terminology.

Factor existing board aggregation into reusable summary/page helpers, but leave `kanban_db` as the sole mutation and dispatch authority. The REST adapter translates optional Pydantic fields—using `model_fields_set` so omitted differs from explicit null—into `TaskMutationPrecondition` and maps `TaskMutationConflict` to `409` with the current sanitized task. Add bounded REST event long polling rather than a Desktop-specific WebSocket or Electron bridge. Existing dashboard routes, WebSocket, clients that omit preconditions, dispatcher, worker lanes, and CLI remain compatible. Do not add workflow transitions, workflow cards, or per-run physical boards to Kanban.

Add a `kanban-mutation-preconditions` ledger entry with `change_class: kanban-persistence-generic`, files `hermes_cli/kanban_db.py` and `tests/hermes_cli/test_kanban_mutation_preconditions.py`, owned symbols `TaskMutationPrecondition`, `TaskMutationConflict`, and `set_task_status`, expected commit subject `feat(kanban): add mutation preconditions`, invariant tests including existing Kanban DB/reclaim/dispatcher suites, exact `origin/main` baseline captured by the checker, upstream-candidate status, merge guidance, and removal condition. Add a separate `kanban-desktop-rest` entry for `plugins/kanban/dashboard/plugin_api.py`; never combine these entries or commits.

- [ ] **Step 6: Write and implement the presentation-only Activity Board**

Define a renderer-neutral model with no lifecycle mutation method:

```ts
export interface ActivityBoardCard {
  id: string
  title: string
  exactState: string
  health: 'healthy' | 'idle' | 'waiting' | 'attention' | 'failed' | 'terminal' | 'stale'
  updatedAt: number
  badges: readonly ActivityBadge[]
  ariaDescription: string
}

export interface ActivityBoardColumn {
  id: string
  label: string
  count: number
  cards: readonly ActivityBoardCard[]
  nextCursor: string | null
}

export interface ActivityBoardModel {
  source: 'workflow' | 'kanban'
  scopeLabel: string
  revision: string
  stale: boolean
  columns: readonly ActivityBoardColumn[]
}
```

`ActivityBoard` receives `model`, `onOpenCard`, and `onLoadMore`; source pages render their own inspector and actions. It has no generic `onMove` prop. Use existing buttons, badges, scroll areas, error/empty states, focus styles, and `@tanstack/react-virtual`. Tests cover keyboard traversal/open, focus preservation when deltas reorder other cards, accessible column/card counts, 320/768/1440 px layouts without page-level horizontal overflow, per-column virtualization at 1,000 cards, bounded load-more calls, stale/disconnected affordances, and reduced-motion behavior.

- [ ] **Step 7: Write and implement the Workflows page and adapter**

`workflowBoardModel` maps run summaries to `Queued`, `Active`, `Needs attention`, `Completed`, and `Failed / stopped`; the selected run maps exact node states to `Waiting`, `Ready / starting`, `Active / retrying`, `Needs attention`, `Done`, and `Failed / stopped`. Preserve exact state badges and label `completed_nodes / total_nodes` as graph progress. A running node with no duration estimate uses indeterminate activity, last semantic progress, semantic-idle age, and wall deadline rather than a percentage.

The `/workflows` page provides portfolio/run toggles, active/attention-first filters, an attention inbox, bounded text/Mermaid topology, and a run inspector containing wait reason, dependency blockers, attempt/event timeline, retry budget/time, model/tool/iteration/resource limits and remaining capacity, child-agent/loop progress, artifacts/output digests, verification evidence, immutable definition/input/trigger identity, reconciliation warning, and exact `next_actions`. It shows elapsed time, deadlines, and next scheduled/retry time but emits `estimate_unavailable` instead of inventing a whole-run completion ETA from graph progress. Buttons call source-specific typed mutations with `expected_version`; they disable on stale/disconnected data and roll back/refetch on `409`.

A Run-button action creates one client UUID before submission, disables synchronously, and reuses that UUID across transport retry/reconnect. Duplicate delivery opens the returned existing run. After a distinct run request receives a response, if the same concurrency key is already active the UI offers `Open active run`, `Queue another run`, or `Cancel`; it does not silently create a second action from a double-click. Queued cards show queue position, blocking run, and the policy/capacity reason while holding no live worker.

Use TanStack Query for server snapshots/pages. A feature-owned nanostore persists only view/filter/selected-run state. One 20-second long poll exists per visible workflow page; profile switch and route unmount increment a generation token so late responses cannot overwrite the new scope. Failures back off at 1, 2, 4, 8, then 15 seconds with bounded jitter; success resets the backoff. Terminal/failure/attention deltas invalidate immediately; cosmetic progress invalidation coalesces for at most 250 ms. Native notifications fire once per run plus interaction/error generation only for new user-actionable attention or terminal failure.

- [ ] **Step 8: Write and implement the native Kanban page and adapter**

The `/kanban` page uses physical board, tenant, assignee, search, lane, and archived filters; persists its selected board in the feature nanostore without calling the CLI board-switch endpoint; and presents task detail, dependency counts, diagnostics, comments, attempts, attachments/artifact links, and safe lifecycle buttons. The first slice does not implement card drag/drop or every dashboard configuration/decomposition control. It does allow explicit status/assignee/reclaim/comment operations only when the API advertises them and always supplies expected CAS fields.

Use one visible-page event long poll and invalidate only affected board/task queries. Board/profile/filter switches use the same generation guard and bounded retry policy as Workflows. A backend cursor reset reloads summary plus visible column pages. The UI identifies Kanban as a machine-shared physical board and never labels it as workflow state.

- [ ] **Step 9: Integrate routes, navigation, locale copy, and plugin-disabled states**

Add `/workflows` and `/kanban` as durable non-overlay `AppView` routes, lazy-load both pages in `desktop-controller`, and add distinct sidebar entries. Keep route roots thin. Add complete English, Japanese, Simplified Chinese, and Traditional Chinese copy/types for column names, health, stale/reconnect, attention, errors, actions, scope labels, empty states, and accessibility descriptions. If either plugin is disabled/unavailable, render an actionable enable/setup state while chat and the other page continue working. Background status changes never navigate, select a card, open a drawer, or focus the page.

- [ ] **Step 10: Run focused API, persistence, desktop, accessibility, and existing-dashboard regressions**

```bash
python3 -m pytest tests/hermes_cli/test_kanban_mutation_preconditions.py tests/hermes_cli/test_kanban_db.py tests/hermes_cli/test_kanban_reclaim_claim_lock_guard.py tests/hermes_cli/test_kanban_dispatch_lock.py tests/plugins/workflow/test_desktop_api.py tests/plugins/test_kanban_dashboard_plugin.py tests/plugins/workflow/test_operator_scope.py -q
cd apps/desktop && npx vitest run src/components/activity-board/activity-board.test.tsx src/app/workflows/adapter.test.ts src/app/workflows/index.test.tsx src/app/kanban/adapter.test.ts src/app/kanban/index.test.tsx src/app/routes.test.ts src/i18n/languages.test.ts
npm run typecheck
npm run lint
cd ../..
python3 scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml --diff HEAD~1..HEAD
git diff --check
```

Expected: all focused tests, typecheck, lint, and ledger checks pass; existing web-dashboard Kanban tests remain green; test teardown leaves no long-poll waiter/task/timer; no request or late response crosses a profile/board generation; and no file outside the amended touch budget is required.

- [ ] **Step 11: Commit Kanban persistence, REST, and desktop operations in independently replaceable layers**

Commit in this order so upstream replacements can remove one concern without retaining parallel implementations:

```bash
git add plugins/workflow/dashboard tests/plugins/workflow/test_desktop_api.py
git commit -m "feat(workflow): expose bounded desktop operations api"

git add hermes_cli/kanban_db.py tests/hermes_cli/test_kanban_mutation_preconditions.py docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(kanban): add mutation preconditions"

git add plugins/kanban/dashboard/plugin_api.py tests/plugins/test_kanban_dashboard_plugin.py docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(kanban): harden desktop board api"

git add apps/desktop/src/components/activity-board
git commit -m "feat(desktop): add reusable activity board presentation"

git add apps/desktop/src/app/workflows apps/desktop/src/app/kanban apps/desktop/src/hermes.ts apps/desktop/src/types/hermes.ts
git commit -m "feat(desktop): add workflow and kanban operations pages"

git add apps/desktop/src/app/routes.ts apps/desktop/src/app/routes.test.ts apps/desktop/src/app/desktop-controller.tsx apps/desktop/src/app/chat/sidebar/index.tsx apps/desktop/src/i18n docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(desktop): integrate workflow operations navigation"
```

## Task 11: Workflow Builder Skill and Compatibility Doctor

**Files:**
- Create: `skills/software-development/workflow-builder/SKILL.md`
- Create: `skills/software-development/workflow-builder/references/portable-schema.md`
- Create: `skills/software-development/workflow-builder/references/authoring-checklist.md`
- Create: `tests/agent/test_workflow_builder_skill.py`
- Create: `tests/plugins/workflow/test_doctor.py`
- Modify: `plugins/workflow/compat.py`
- Modify: `plugins/workflow/cli.py`

**Interfaces:**
- Produces: guided package authoring and `hermes workflow doctor PACKAGE --json`, including risk/trust, immutable-input, overlap-policy, and admission-capacity findings.
- Consumes: exact schema, discovery, resource resolution, provider compatibility, installed skills/tools/MCP, and cron.

- [ ] **Step 1: Write builder-contract and doctor snapshot-invariant tests**

The builder must create `workflows`, `commands`, `scripts`, and `mcp` resources when referenced; validate before offering execution; identify required invocation inputs and snapshot policy; choose/explain `queue`, `allow`, or `forbid`; present the digest-bound risk summary and trust confirmation; avoid unsupported fields; and keep Ericsson policy outside portable YAML. Doctor tests assert relationships and issue codes rather than freezing complete prose.

- [ ] **Step 2: Implement a structured doctor report**

```python
@dataclass(frozen=True)
class InputRequirement:
    name: str
    kind: Literal["text", "file", "directory", "json"]
    required: bool
    max_bytes: int | None


@dataclass(frozen=True)
class DoctorReport:
    package: str
    workflow: str
    runnable: bool
    package_digest: str
    trust_state: Literal["trusted", "untrusted"]
    risk_summary: WorkflowRiskSummary
    input_requirements: tuple[InputRequirement, ...]
    concurrency_policy: Literal["queue", "allow", "forbid"]
    findings: tuple[CompatibilityFinding, ...]
    resolved_commands: tuple[str, ...]
    resolved_scripts: tuple[str, ...]
    resolved_mcp_servers: tuple[str, ...]
    resolved_skills: tuple[str, ...]
```

Include missing runtimes, mapped/unknown tool aliases, skills, MCP variables, credentials, output-schema enforcement, persistent-session capability/fingerprint constraints, every configured hook event, inline-agent bounds, worktree/service requirements, provider-field support, local-versus-isolated execution requirement, executable resources included in the digest, immutable input requirements, overlap behavior, and effective admission/resource ceilings. Never connect to remote MCP or call a model in doctor mode.

- [ ] **Step 3: Author the builder skill around whole-package creation**

The skill asks one decision at a time, plays the workflow back in plain language, writes command templates for long prompts, uses Archon field names/tool aliases, inserts approval gates around outward actions unless the user opts out, chooses fresh context when cache fingerprints would differ, defaults overlapping invocations to `queue`, and offers run or cron only after a runnable doctor result. Before trusting a newly authored digest it shows requested scripts/tools/MCP/providers/network/outward actions, required secrets, execution environment, and resource ceilings and obtains explicit confirmation. It never writes trust into the package or silently trusts manually supplied code.

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

## Task 12: Ericsson Conversion, Capability Staging, and Brand Delivery

**Files:**
- Create: `capabilities/workflow-packages/ericsson/workflows/inbox-digest.yaml`
- Create: `capabilities/workflow-packages/ericsson/digests.json`
- Create: `capabilities/workflow-packages/ericsson/workflows/my-tickets-summary.yaml`
- Create: `capabilities/workflow-packages/ericsson/commands/collect-inbox.md`
- Create: `capabilities/workflow-packages/ericsson/commands/summarize-inbox.md`
- Create: `capabilities/workflow-packages/ericsson/commands/fetch-tickets.md`
- Create: `capabilities/workflow-packages/ericsson/commands/summarize-tickets.md`
- Create: `capabilities/workflow-packages/ericsson/workflows/my-tickets-summary.hermes.yaml`
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

Assert a versioned capability update stages workflows, commands, scripts, MCP files, sidecars, and a distribution-owned digest manifest into one temporary directory and atomically swaps it into the profile. Only a package copied from the installed, repository-owned capability root whose computed digest matches that manifest may receive profile trust provenance `trusted_distribution`; package YAML/sidecar cannot request it. Simulate interruption before swap and prove the prior package and trust decision remain usable. Reject traversal, symlink escape, digest mismatch, and a user package masquerading as distribution content.

- [ ] **Step 2: Extend the capability manifest without adding workflow runtime logic**

```json
{
  "plugins": ["plugins/workflow", "plugins/ericsson-jira", "plugins/ericsson-teams"],
  "workflowPackages": [
    {
      "path": "capabilities/workflow-packages/ericsson",
      "digestManifest": "capabilities/workflow-packages/ericsson/digests.json"
    }
  ]
}
```

Baked and remotely staged capabilities enable the declared workflow plugin through the existing `plugins.enabled` configuration merge. Remote content is trusted only after its capability/update authenticity checks succeed and its package digest matches the distribution manifest; otherwise staging fails closed and preserves the prior package/trust state.

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
git commit -m "feat(ericsson): ship portable workflow packages"
```

## Task 13: Offline Production Showcase Harness

**Files:**
- Create: `plugins/workflow/showcase.py`
- Create: `plugins/workflow/showcases/catalog.yaml`
- Create: `plugins/workflow/showcases/catalog.schema.json`
- Create: `plugins/workflow/showcases/digests.json`
- Create: `plugins/workflow/showcases/packages/laptop-diagnostic/workflows/laptop-diagnostic.yaml`
- Create: `plugins/workflow/showcases/packages/laptop-diagnostic/fixtures/laptop-snapshot.json`
- Create: `plugins/workflow/showcases/packages/laptop-diagnostic/scripts/analyze-snapshot.py`
- Create: `plugins/workflow/showcases/packages/laptop-diagnostic/scripts/render-report.py`
- Create: `plugins/workflow/showcases/packages/laptop-diagnostic/commands/interpret-report.md`
- Create: `plugins/workflow/showcases/packages/laptop-diagnostic/laptop-diagnostic.hermes.yaml`
- Create: `plugins/workflow/showcases/packages/resilience/workflows/resilience.yaml`
- Create: `plugins/workflow/showcases/packages/resilience/scripts/fail-once.py`
- Create: `plugins/workflow/showcases/packages/resilience/scripts/bounded-wait.py`
- Create: `plugins/workflow/showcases/packages/resilience/resilience.hermes.yaml`
- Create: `plugins/workflow/showcases/packages/ai-extensions/workflows/ai-extensions.yaml`
- Create: `plugins/workflow/showcases/packages/ai-extensions/commands/inspect-evidence.md`
- Create: `plugins/workflow/showcases/packages/ai-extensions/mcp/echo-server.py`
- Create: `plugins/workflow/showcases/packages/ai-extensions/ai-extensions.hermes.yaml`
- Create: `plugins/workflow/showcases/packages/scheduling/workflows/scheduled-check.yaml`
- Create: `plugins/workflow/showcases/packages/scheduling/scripts/write-checkpoint.py`
- Create: `plugins/workflow/showcases/packages/scheduling/scheduled-check.hermes.yaml`
- Create: `skills/productivity/workflow-showcase/SKILL.md`
- Create: `skills/productivity/workflow-showcase/workflows/explain-showcase.md`
- Create: `skills/productivity/workflow-showcase/workflows/run-showcase.md`
- Create: `skills/productivity/workflow-showcase/workflows/resume-and-report.md`
- Create: `skills/productivity/workflow-showcase/workflows/reset-and-cleanup.md`
- Create: `skills/productivity/workflow-showcase/references/showcase-contract.md`
- Create: `skills/productivity/workflow-showcase/references/safety-and-interpretation.md`
- Create: `tests/plugins/workflow/test_showcase_catalog.py`
- Create: `tests/plugins/workflow/test_showcase_evidence.py`
- Create: `tests/plugins/workflow/test_showcase_offline_e2e.py`
- Create: `tests/plugins/workflow/test_showcase_resilience_e2e.py`
- Create: `tests/plugins/workflow/test_showcase_ai_e2e.py`
- Create: `tests/plugins/workflow/test_showcase_schedule_e2e.py`
- Create: `tests/plugins/workflow/test_showcase_distribution_e2e.py`
- Create: `tests/agent/test_workflow_showcase_skill.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `pyproject.toml`
- Modify: `MANIFEST.in`
- Modify: `tests/test_packaging_metadata.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Create: `docs/workflow-orchestration.md`

**Interfaces:**
- Produces: `hermes workflow showcase list|describe|preflight|run|status|report|reset|cleanup [--json]`, a bundled `workflow-showcase` conversational skill, four digest-verified showcase packages, and an evidence-backed `ShowcaseReport`.
- Consumes: ordinary package validation/trust, immutable input snapshotting, idempotent admission, RunStore/events, process supervision, approvals/rework, artifacts, cron, scoped AI/MCP/skills/hooks, workflow status APIs, and Desktop projections. It has no execution path that bypasses those contracts.

- [ ] **Step 1: Write the showcase catalog, command, and safety-contract tests**

Define a versioned schema whose scenario records include stable ID, display name, purpose, bundle/package version, workflow path, interaction mode, `offline`, `requires_ai`, `requires_network`, safety class, supported platforms, expected checkpoints, expected terminal outcomes, expected artifacts, capability claims, scenario-specific limits, and cleanup ownership. Validate paths against the read-only bundled root, reject duplicate IDs, traversal, symlinks, unknown claims, destructive safety classes, live-machine collectors, unbounded timeouts, external MCP/network endpoints, outward actions, and any package missing from the distribution digest manifest.

Command tests require stable JSON and exit categories for list, describe, preflight, run, status, report, reset, and cleanup. Inspection/preflight may not initialize a model/provider, MCP server, worker, cron scheduler, browser, or network connection. Showcases are a separate catalog; they do not alter explicit/project/profile workflow precedence and cannot shadow or overwrite a user workflow with the same display name.

- [ ] **Step 2: Implement read-only bundle loading, digest trust, and tighter admission policy**

`showcase.py` resolves the read-only bundle with `importlib.resources`, selects an exact package path from the validated catalog, and then calls the ordinary loader, doctor, trust, snapshot, and `RunStore.start_run` contracts. Add the narrow wheel package-data pattern `workflow/showcases/**/*` under the existing `plugins` package and `recursive-include plugins/workflow/showcases *` for the sdist; do not rely on the source checkout or broaden package data to every arbitrary plugin asset. The authenticated installed bundle receives `trusted_distribution` only when every digest-covered byte matches `digests.json`; mismatch or partial installation fails closed. The package cannot self-declare trust. Each user action gets a unique random idempotency key while duplicate delivery of that action returns the existing run.

Tag runs with immutable `showcase_id`, `showcase_version`, and bundle digest. The effective sidecar limits are no looser than four parallel workers, eight descendants, 1 MiB combined captured process output, 16 MiB artifacts, 32 MiB run storage, five minutes for offline/resilience modes, and ten minutes for an explicitly confirmed AI mode; ordinary stricter profile/global limits still win. No showcase may request credentials, live inventory, elevation, external writes, or network by default.

When the Laptop Diagnostic default needs a file input, materialize the bundled sanitized fixture into an ownership-tagged temporary staging directory, submit it through ordinary immutable input capture, and delete the staging copy only after snapshot success or failed admission cleanup. Downstream nodes never read the bundle or staging source directly.

- [ ] **Step 3: Build the cross-platform Laptop Diagnostic Tour**

Use only fictional identifiers and standard-library deterministic scripts. Required input is a short symptom/focus; no host inventory is collected. The package analyzes CPU/memory, storage, network, and startup/process evidence branches in parallel, joins typed findings, loops over the bounded finding list, branches by severity, and emits verified `diagnostic-report.json`, `diagnostic-report.md`, and a proposed remediation-plan artifact. The report labels the evidence simulated and must never imply it describes the user's actual laptop.

Pause before finalizing the remediation plan. Approve finalizes the artifact only. Reject captures bounded feedback as data, runs deterministic `on_reject` rework once, and pauses again; it never executes a remediation. An opt-in AI interpretation branch uses `interpret-report.md`, fresh context, typed output, and the same snapshotted evidence. Without explicit AI consent or a compatible provider, deterministic rendering is used and the AI claim is recorded as skipped.

Tests start with missing input and prove the stable requirement response; submit an input and prove immutable capture; observe real parallel claims and fan-in; reject/rework; restart the coordinator while paused; approve/resume; verify artifacts and topology through CLI and Desktop read projections; and prove no model, network, PowerShell, shell inventory command, credential, or real host identifier was accessed in the default path.

- [ ] **Step 4: Implement evidence-backed reports instead of declared passes**

```python
@dataclass(frozen=True)
class ShowcaseClaimResult:
    capability: str
    outcome: Literal["passed", "failed", "skipped"]
    reason_code: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ShowcaseReport:
    schema_version: int
    showcase_id: str
    showcase_version: str
    bundle_digest: str
    run_id: str
    definition_digest: str
    terminal_outcome: str
    claims: tuple[ShowcaseClaimResult, ...]
    interactions: tuple[dict, ...]
    artifacts: tuple[dict, ...]
    cleanup: dict
    suggested_next: tuple[str, ...]
```

Derive claim results from the normalized snapshotted definition plus checksum-verified RunStore events, attempt lineage, approval/input records, semantic progress, process termination/reaping evidence, cron ownership records, and artifact metadata/content verification. A catalog claim with no required evidence fails. Evidence refs are bounded opaque event/artifact identifiers, not prompt, reasoning, secret, raw input, or unrestricted tool-argument bodies. Expected timeout/cancel/rejection outcomes may pass the relevant demonstration claim while retaining the workflow's truthful failed/cancelled/paused lifecycle state.

Reports remain available under ordinary run retention. `showcase cleanup` delegates to normal race-safe cleanup, defaults to `--dry-run`, and filters by immutable showcase tags rather than path/name guesses. `reset` never deletes audit evidence by default; it removes owned temporary staging, reports any still-present exact one-shot schedule, and requires ordinary explicit cron-removal confirmation rather than deleting a changed or unproven record.

- [ ] **Step 5: Build the controlled Resilience Lab**

One Archon-shaped package exposes `retry`, `timeout`, and `cancel` modes through validated input. `retry` uses an atomic run-local marker to fail exactly once, releases its worker during persisted backoff, then succeeds within two attempts. `timeout` runs a harmless bounded wait beyond a three-second deterministic-process deadline and must end with typed timeout plus observed process-tree reaping. `cancel` starts a harmless bounded parent/child wait for at most thirty seconds so the user can cancel through the normal compare-and-set action; lack of cancellation still ends naturally within the showcase wall limit.

The skill explains the expected outcome before each mode. Tests cover retry-wakeup/cancel and completion/cancel races, coordinator restart during backoff, cancel while the child is alive, repeated invocation, and cleanup. After every mode require zero live showcase-owned process identities, zero unreaped children, no held worker/backoff timer, valid journal/projection, and an evidence report that distinguishes expected fault behavior from workflow success. No installed command exposes corruption, kill-Hermes, memory/disk exhaustion, process floods, or soak modes.

- [ ] **Step 6: Build the optional AI and Extensions Tour**

Preflight resolves compatibility without a model or MCP connection. Running requires an explicit AI confirmation token bound to the preflight digest and shows provider, model, declared tools/skills, local MCP, child-agent limit, hard deadline, and possible model cost without exposing credentials. If no compatible provider is configured, return a typed skipped scenario with remediation; the offline suite remains successful.

The package exercises a command template, fresh and cache-fingerprint-compatible shared context, structured output, one explicitly resolved harmless bundled skill (default `ascii-art`), mapped hooks, a package-local stdio echo MCP server, one bounded inline agent, and persistent session lineage. The selected skill is optional-profile aware: preflight lists it, and a curated/disabled installation skips that claim instead of silently loading it. The MCP process is local, receives no secrets, and is torn down on success/failure/cancel. A repeat run in the same authorized scope proves persistent-session reuse; changing a cache-affecting field proves fresh-context fallback/diagnostic. CI uses the deterministic fake provider and local MCP; release documentation labels a real-provider run optional and potentially billable.

- [ ] **Step 7: Build the opt-in Scheduling Tour with exact ownership cleanup**

The scheduling package writes a deterministic timestamp/checkpoint artifact from snapshotted inputs. `showcase run scheduling` creates a uniquely tagged Hermes one-shot with `repeat=1` only after explicit confirmation. It reuses the existing cron parser, atomic finite-one-shot dispatch claim, heartbeat/stale-claim recovery, repeat limit, and automatic post-run deletion. Persist the returned schedule ID, nonce, definition digest, expected trigger, and profile as showcase ownership evidence before returning success; no second scheduling state machine is introduced. A restart between creation, fire, admission, and completion is handled by existing cron recovery plus workflow idempotency.

`showcase reset` enumerates ownership evidence, not cron names. If an owned one-shot still exists, reset reports the exact job ID/current metadata and routes removal through the ordinary explicitly confirmed cron command; it does not implement an API-side check-then-delete or automatically remove a changed record. Tests create colliding names, changed schedule metadata, duplicate delivery, restart/stale-claim recovery, and an unrelated user cron job; normal completion auto-deletes only the finite one-shot, while reset never deletes anything it cannot prove and confirm.

- [ ] **Step 8: Author the conversational showcase skill**

Keep `SKILL.md` a compact router whose stable description covers workflow demo/showcase/tour, Laptop Diagnostic, resilience/retry/timeout/cancel, optional AI/extensions, scheduling, status, report, resume, and cleanup intents without bloating the global skill index. Branch-specific procedures live one level down. The skill always calls `--json`, explains a scenario's synthetic/offline status and expected interactions before starting, asks one missing input at a time, obtains explicit AI/schedule consent, never supplies approval on the user's behalf, and interprets actual report outcomes rather than promising success.

It supports: “show me what workflows can do,” “run the laptop diagnostic demonstration,” “explain the resilience lab,” “what is my showcase waiting on,” “continue my showcase,” and “clean up showcase runs.” Through chat it uses existing skill command plumbing; through provider-free installations the documented CLI remains complete. No new slash registry entry, model tool, system-prompt mutation, or chat implementation is added.

- [ ] **Step 9: Add installed-distribution and cross-platform E2E coverage**

Build both wheel and sdist, install each into an installation-shaped temporary tree containing only packaged runtime assets, enable the workflow plugin through ordinary config, and assert every catalog/digest/package/fixture/script/command/MCP/sidecar file retained its relative path. Then run list/describe/preflight plus the complete offline Laptop Diagnostic and resilience retry/timeout paths with credentials and network disabled. Exercise input, reject/rework, approval, restart, status, artifact links, evidence report, and cleanup. Assert the same run appears on the existing Desktop Workflows projection without a showcase-specific UI adapter.

Run on Linux, macOS, and native Windows-supported CI before claiming all-platform showcase support. Use standard-library scripts and platform-neutral paths; an unavailable optional node capability is `skipped` with a reason and cannot make a claimed offline-required capability pass. Scan bundled files and command traces to prove the legacy PowerShell collector, live inventory commands, external URLs/MCP, customer data, destructive modes, and secrets are absent. Validate that upgrades replace only the authenticated read-only bundle and never profile/project workflows or retained run evidence.

- [ ] **Step 10: Run focused gates and commit the showcase separately**

```bash
python3 -m pytest tests/plugins/workflow/test_showcase_catalog.py tests/plugins/workflow/test_showcase_evidence.py tests/plugins/workflow/test_showcase_offline_e2e.py tests/plugins/workflow/test_showcase_resilience_e2e.py -q
python3 -m pytest tests/plugins/workflow/test_showcase_ai_e2e.py tests/plugins/workflow/test_showcase_schedule_e2e.py tests/plugins/workflow/test_showcase_distribution_e2e.py tests/agent/test_workflow_showcase_skill.py -q
python3 -m pytest tests/agent/test_skill_commands.py tests/cron/test_workflow_cron.py -q
python3 -m pytest tests/test_packaging_metadata.py -q
git diff --check
```

```bash
git add plugins/workflow/showcase.py plugins/workflow/showcases skills/productivity/workflow-showcase tests/plugins/workflow/test_showcase_*.py tests/agent/test_workflow_showcase_skill.py plugins/workflow/cli.py pyproject.toml MANIFEST.in tests/test_packaging_metadata.py docs/workflow-orchestration.md docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): add offline guided showcase suite"
```

## Task 14: Production Quality and Upstream-Merge Release Gate

**Files:**
- Create: `tests/plugins/workflow/test_portable_compatibility_e2e.py`
- Create: `tests/plugins/workflow/test_fault_injection.py`
- Create: `tests/plugins/workflow/test_performance_bounds.py`
- Create: `tests/plugins/workflow/test_process_lifecycle_soak.py`
- Create: `tests/plugins/workflow/test_operator_e2e.py`
- Create: `tests/plugins/workflow/test_security_boundaries.py`
- Create: `apps/desktop/src/app/workflows/workflow-operations.e2e.test.tsx`
- Create: `apps/desktop/src/app/kanban/kanban-operations.e2e.test.tsx`
- Create: `apps/desktop/src/components/activity-board/activity-board.performance.test.tsx`
- Create: `scripts/test_workflow_merge_gate.sh`
- Create: `scripts/test_workflow_upstream_merge.sh`
- Create: `tests/scripts/test_workflow_merge_gate.py`
- Create: `tests/scripts/test_workflow_upstream_merge.py`
- Create: `docs/upstream-customizations/merge-evidence.schema.json`
- Modify: `docs/workflow-orchestration.md`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Modify: `.github/workflows/ci.yml` only if repository CI lacks a suitable existing job

**Interfaces:**
- Produces: the lightweight offline live-merge gate, overlap-aware customization checker, versioned merge-evidence bundle, isolated merge rehearsal, installed-showcase verification, operator documentation, and CI gate that blocks brand propagation until the tested base customization contracts survive.
- Consumes: all previous slices and the `main → base → otto/loop24` branch topology.

- [ ] **Step 1: Add an unmodified portable Archon end-to-end fixture**

The fixture includes command/frontmatter variables, prompt, bash, uv script, parallel join, typed/compound condition, structured/typed output, tool aliases/restrictions, skills, mapped hooks, local MCP, inline agents, retry, persistent sessions, loop, approval/rejection, and cancel paths. Run with a deterministic fake provider plus local MCP. Interrupt after multiple nodes, restart the process, approve, and finish without repeating completed work; invoke it again under the same conversation scope to prove persistent-node-session reuse.

In the same installation-shaped environment, run the shipped Laptop Diagnostic and resilience showcases through their public CLI contracts with credentials and network disabled. Validate their bundle digests, pause/rework/resume lifecycle, reports, and cleanup from installed assets rather than source-tree shortcuts. Run the optional AI scenario with the fake provider, and prove the provider-free form reports a skip without weakening required offline claims. Assert no destructive CI scenario is addressable through the installed showcase catalog or skill.

- [ ] **Step 2: Add concurrency and fault-injection stress tests**

Exercise 100 duplicate start deliveries with one source key, 100 duplicate scheduler advances, intentional simultaneous starts under `queue`/`allow`/`forbid`, admission-rate/executing/queued/paused/nonterminal/worker-cap exhaustion, 20 simultaneous approval decisions, completion-versus-cancel, retry-wakeup-versus-cancel, approval-versus-cancel, admission-versus-shutdown, worker termination at every persistence boundary, coordinator death/IPC EOF, shutdown during every lifecycle phase, laptop suspend/wake gap, wall-clock jump, PID reuse, projection/admission-ledger/journal corruption, lock timeout, provider DNS/TLS/disconnect/stalled-stream/model errors, MCP startup failure, hook timeout, process kill refusal, and cancellation while descendants are active. Assertions focus on invariants: one run per idempotency key/start digest, one winning claim/terminal transition, monotonic events, bounded completion, no leaked/zombie process, mandatory observed reaping, no worker held during queue/backoff/user wait, no outward-action blind retry, and no silent success.

Run 100 fast spawn/success/cancel/idle-timeout/wall-timeout/provider-failure cycles in normal CI and 500 cycles in the release gate. Sample live process/child counts, threads, descriptors on POSIX or handles on Windows, coordinator/worker RSS, run-directory bytes, and quarantine contents before, during, and after. After bounded cleanup and garbage-collection settling, require zero live workflow-owned process identities, zero unreaped owned children, no retained scheduler/reader threads, no retained descriptors/handles, no quarantine leak, and no statistically upward resource trend across equal-size batches.

- [ ] **Step 3: Add security boundary tests**

Cover YAML aliases/depth limits, oversized documents, traversal, symlink escape, command injection, unsafe uv dependency tokens, secret redaction, MCP environment expansion, unauthorized provider override, hook input mutation, artifact/output/storage quotas, approval-digest tampering/replay, idempotency-key cross-scope replay, and proof that no secret or sudo value can enter durable state or plugin-visible IPC. Prove imported/external packages start untrusted, package/sidecar self-trust is ignored, trust is profile- and digest-bound, every executable-resource change revokes trust, local execution refuses untrusted packages, an advertised isolated backend is required for untrusted execution, and risk output contains no prompt/secret bodies. Tamper with each showcase YAML/script/command/MCP/fixture/sidecar/catalog/digest input and prove distribution trust fails closed; prove a package cannot masquerade as a bundled showcase, reports cannot be forged from catalog claims, and reset cannot delete an unowned cron job or profile workflow. Scan the installed catalog for external endpoints, live inventory/elevation, outward actions, and destructive modes. Add topology-injection canaries for Mermaid initialization directives, raw HTML, links, click handlers, styles/classes, quotes, newlines, and fence termination; generated source must remain within the strict graph/node/edge grammar and desktop rendering must retain Mermaid `securityLevel: "strict"`.

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
    runtime.config.max_total_workers = 4
    runtime.run("twenty-independent-nodes")
    assert runtime.observed_peak_workers <= 4


def test_agent_workers_leave_parent_process_state_unchanged(runtime):
    before = runtime.capture_parent_process_state()
    runtime.run("four-disjoint-tool-scopes")
    assert runtime.capture_parent_process_state() == before
```

Measure cold worker startup latency and peak resident memory at concurrency 1 and 4 and across four simultaneous admitted runs sharing the global four-worker cap. Store baseline timing in test output, not a brittle committed machine-specific number. Enforce algorithmic/resource invariants plus a generous CI ceiling derived from three CI runs, and fail on worker/process/thread/descriptor growth after completion. Exceed process-tree RSS/CPU, descendant, output, artifact, event, per-run storage, profile-storage, executing-run, queued-run, paused-run, nonterminal-run, start-rate, and global-worker limits one at a time; each must refuse/terminate/pause with a typed diagnostic, reap descendants, preserve a valid projection/journal/admission ledger, and allow later cleanup. Simulate available disk just above and below `max(1 GiB, min(5 GiB, 5% of target-volume capacity))`; refusal occurs before snapshot or process allocation. Generate text/Mermaid projections for 1, 100, and 1,000-node DAGs; prove linear node/edge visits, 12 KiB text truncation, Mermaid availability at the 100-node/200-edge boundary, `null` plus warnings above it, and no Mermaid parser/browser/model/network initialization in the Python control plane.

Add an operator E2E that installs two workflows and creates healthy-running, semantically-idle, waiting-retry, paused-for-input, paused-for-approval, reconciliation-required, failed, succeeded, cancelled, interrupted, and abandoned runs. Prove `list`, `show`, `runs`, `status`, and `events` agree across CLI JSON, `/workflow`, natural-language skill paths, Desktop portfolio columns, selected-run node columns, attention inbox, topology, and inspector timeline. The test must answer: whether there is a problem, where the run is, whether it is done, whether the user is needed, exactly what it waits on, why it failed, whether/when it retries, whether a running node is making semantic progress, what happens next, which immutable definition/input/trigger is running, what bounded budget/capacity remains, and what artifacts/verification evidence exist. It must also prove that completion ETA remains unavailable rather than being inferred from node counts.

Exercise local token, remote token, and remote OAuth Desktop REST routing; profile/board switches with late responses; event cursor gaps; backend restart; duplicated/out-of-order cosmetic events; stale action conflicts; hidden-window refresh suspension; notification deduplication; and plugin-disabled states. Assert board data never becomes execution authority, Workflow actions never appear as generic card moves, Kanban tasks never appear as ordinary workflow nodes, and prompt/reasoning/secret/tool-argument canaries never enter API or renderer state.

- [ ] **Step 5: Add an isolated upstream-merge rehearsal script**

First add `scripts/test_workflow_merge_gate.sh` with `--phase base` and `--phase brand --brand SLUG` modes. The base mode runs the customization checker plus entry-specific invariant suites for managed process trees, scoped plugin agents, generic Kanban mutation preconditions/reclaim safety, Kanban REST compatibility, workflow admission/trust, and Desktop contract/type checks. The brand mode proves the brand contains the exact tested base commit, performs workflow discovery/trust/validation and generic-surface checks without a provider/model/credentials/network call, and proves generic runtime files are not brand-divergent. Test missing counterparts, invalid phases/brands, base failures, per-entry failures, per-brand failures, wrong base ancestry, modified generic files on a brand, and successful no-network execution in `tests/scripts/test_workflow_merge_gate.py`.

The rehearsal script creates temporary worktrees, fetches no network by default, and captures immutable pre-merge refs plus a patch/evidence digest for every ledger entry. It merges the supplied local upstream ref into a temporary base branch, runs the customization checker and focused Python/Node tests, then merges that exact temporary base commit into temporary OTTO and LOOP24 branches and runs brand equivalence checks. It never mutates real `base`, `otto`, or `loop24` refs.

`tests/scripts/test_workflow_upstream_merge.py` builds synthetic repositories for four mandatory cases: no ledger overlap, same-file/non-owned-symbol overlap that auto-merges, owned-symbol textual conflict, and upstream adding an equivalent public contract/removing a locally owned seam. The first two may continue only after invariant tests; the last two must stop for an explicit `preserve`, `adapt`, or `remove-as-upstream-equivalent` decision and must reject blanket whole-file `ours`/`theirs`. A simulated failed invariant test proves no verified-upstream baseline, base release ref, or brand ref advances.

Write a versioned JSON evidence bundle validated by `merge-evidence.schema.json` containing prior/candidate upstream commits, pre/post base commits, ledger baseline, per-entry overlap class and decision, conflict files, retained/removed commit subjects, commands/results/durations/platform, tested base tree, each generated brand commit/tree/descriptor, and final ancestry checks. Evidence contains no credentials, environment values, prompts, or workflow inputs. CI retains it as an artifact; normal real merges may store it outside the repository release workspace.

```bash
scripts/test_workflow_upstream_merge.sh \
  --upstream-ref main \
  --base-ref base \
  --brand-ref otto \
  --brand-ref loop24
```

- [ ] **Step 6: Integrate the existing merge skill at its owning location**

Update `/Users/coreyellis/code/github.com/cmetech/otto_hermes/.claude/skills/otto-upstream-merge/SKILL.md` without changing its upstream/main → local main → base → discovered-brand flow. Stage 0 validates the manifest/baseline, captures pre-merge refs/customization evidence, compares `last_verified_upstream..origin/main`, and prints every ledger entry's overlap class, owned contracts, merge guidance, invariant tests, and removal condition. The skill must classify `hermes_cli/kanban_db.py` as a generic Kanban-persistence UNION file and `plugins/kanban/dashboard/plugin_api.py` as a separately replaceable REST adapter, not agent core.

During Stage 1, a conflict in any ledger-owned file disables blanket whole-file `git checkout --ours/--theirs`; the merge pauses for an explicit per-entry `preserve`, `adapt`, or `remove-as-upstream-equivalent` decision. After resolution, run the entry-specific tests and `test_workflow_merge_gate.sh --phase base` before committing the upstream merge. Only then write the candidate upstream commit to `last_verified_upstream` in a dedicated merge-evidence commit, rerun the checker/base gate on that final tree, capture its `TESTED_BASE_SHA`, and propagate exactly that commit. After each brand restamp, run the no-network brand gate and verify ancestry plus no divergence in ledger-owned generic runtime/Kanban files. Any failure stops before push and leaves refs/evidence for recovery.

If the workflow manifest/checker/gate is wholly absent before the feature lands, report that the optional gate is not installed. Once any marker exists, every counterpart and valid ledger baseline is mandatory and a partial installation fails closed.

Do not call `test_workflow_upstream_merge.sh` from inside the real merge skill. That script repeats the entire branch graph in temporary worktrees and belongs in CI, release verification, or an explicitly requested preflight. Test the skill change against temporary clones covering all four overlap classes and record its version/commit in release evidence. The skill remains external and is not vendored into Hermes. Updating the skill is an implementation task after the repository checker/gates exist; this planning amendment does not edit it prematurely.

- [ ] **Step 7: Document operations and compatibility**

Document neutral package layout/discovery precedence, digest trust/risk review and isolated-execution requirements, immutable input capture, idempotency/queue/allow/forbid behavior, admission/resource limits, explicit external-package import, `list/show/runs/status/events` examples, natural-language equivalents, text/Mermaid topology fields and limits, `show --topology text|mermaid|both`, the exact CLI/TUI/dashboard/Desktop-chat/Workflows-page fallback matrix, status/health meanings, honest graph progress, board-versus-inspector responsibilities, attention inbox, wait/failure/retry/reconciliation questions, cancellation race outcomes, OS kill limitations, workflow/Kanban source-of-truth boundary, physical board strategy, plugin-disabled behavior, cron, approvals/input, resume/retry/reconcile/cancel/abandon/cleanup, artifacts, config limits, renderer-versus-owner shutdown behavior, provider/network failures, orphan/restart recovery, storage retention, compatibility levels, security/authorization model, Kanban CAS behavior, the offline Laptop Diagnostic and controlled resilience showcase paths, optional AI/scheduling consent and cost/lifecycle behavior, evidence-report interpretation, showcase reset/cleanup safety, the explicit separation from live Windows collection and destructive CI tests, how the merge skill invokes overlap classification/invariant gates, and how CI or an explicit preflight invokes the full rehearsal/evidence script.

- [ ] **Step 8: Run the full release gate**

```bash
python3 -m pytest tests/agent/test_plugin_agent.py tests/tools/test_managed_process.py tests/tools/test_process_registry.py tests/plugins/workflow tests/cron/test_workflow_cron.py tests/gateway/test_workflow_skill_dispatch.py tests/tui_gateway/test_workflow_skill_dispatch.py -q
python3 -m pytest tests/plugins/workflow/test_showcase_distribution_e2e.py tests/plugins/workflow/test_showcase_resilience_e2e.py tests/plugins/workflow/test_showcase_ai_e2e.py tests/plugins/workflow/test_showcase_schedule_e2e.py tests/agent/test_workflow_showcase_skill.py -q
python3 -m pytest tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_performance_bounds.py tests/plugins/workflow/test_process_lifecycle_soak.py tests/plugins/workflow/test_operator_e2e.py -q
python3 -m pytest tests/hermes_cli/test_kanban_mutation_preconditions.py tests/hermes_cli/test_kanban_db.py tests/hermes_cli/test_kanban_reclaim_claim_lock_guard.py tests/plugins/test_kanban_dashboard_plugin.py -q
python3 -m pytest tests/scripts/test_workflow_merge_gate.py tests/scripts/test_workflow_upstream_merge.py -q
cd ui-tui && npx vitest run src/__tests__/workflowTopology.test.ts src/__tests__/markdown.test.ts
cd ../apps/desktop && npx vitest run src/components/assistant-ui/embeds/workflow-topology.test.tsx src/components/activity-board/activity-board.test.tsx src/components/activity-board/activity-board.performance.test.tsx src/app/workflows/adapter.test.ts src/app/workflows/index.test.tsx src/app/workflows/workflow-operations.e2e.test.tsx src/app/kanban/adapter.test.ts src/app/kanban/index.test.tsx src/app/kanban/kanban-operations.e2e.test.tsx src/lib/workflow-skill-command.test.ts src/lib/desktop-slash-commands.test.ts
npm run typecheck
npm run lint
cd ../.. && node --test scripts/__tests__/vendor-ericsson.test.mjs scripts/brand/__tests__/*.test.mjs
python3 scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml --diff main..HEAD --upstream-diff "$(python3 scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml --print-verified-upstream)..main" --report /tmp/workflow-customization-overlap.json
scripts/test_workflow_upstream_merge.sh --upstream-ref main --base-ref base --brand-ref otto --brand-ref loop24 --report-dir /tmp/workflow-merge-evidence
git diff --check
```

Expected: all tests pass; installed offline showcases run without provider credentials, network, external integrations, live machine inventory, or source-tree shortcuts; every showcase claim has durable evidence; optional AI skips cleanly when unavailable; temporary schedule cleanup leaves user jobs untouched; no destructive scenario is installed; no leaked/zombie processes, scheduler threads, descriptors/handles, reservations, or quarantine entries remain; resource slopes stay bounded; the customization ledger covers every upstream-owned agent-core, Kanban-persistence/API, and Desktop composition change; overlap decisions and invariant results validate against the evidence schema; and temporary branch rehearsals preserve the tested base plus both branded overlays.

- [ ] **Step 9: Request security and code review before branded propagation**

Run the repository security-review and code-review skills against the full feature diff. Resolve every high-severity finding and re-run the release gate. Record commands, results, timing, and platform coverage in the PR description.

- [ ] **Step 10: Commit the production gate and documentation**

```bash
git add tests/plugins/workflow tests/scripts/test_workflow_merge_gate.py tests/scripts/test_workflow_upstream_merge.py scripts/test_workflow_merge_gate.sh scripts/test_workflow_upstream_merge.sh docs/workflow-orchestration.md docs/upstream-customizations/workflow-orchestration.yaml docs/upstream-customizations/merge-evidence.schema.json .github/workflows/ci.yml
git commit -m "test(workflow): enforce production and merge gates"
```

## Requirement Coverage

| Requirement | Slices |
|---|---|
| Exact Archon-shaped YAML and neutral package/command storage | S02, S04, S11, S12, S13 |
| Digest-bound trust, risk preflight, isolated untrusted execution, and immutable inputs | S02, S03, S09, S11, S13, S14 |
| Trigger idempotency, overlap policy, admission backpressure, and global worker capacity | S03, S05, S09, S13, S14 |
| Fresh/shared context | S01, S04, S13 |
| Durable state, locking, race safety | S03, S05, S07, S10, S13, S14 |
| Parallel DAG, triggers, retries, resume | S05, S13, S14 |
| Bash, script, loop, approval, cancel | S03, S06, S07, S13, S14 |
| Structured output and artifacts | S03, S04, S13, S14 |
| Tools, skills, hooks, MCP, provider mapping | S01, S08, S13, S14 |
| Workflow catalog, description, topology, requirements, and schedules | S02, S09, S10, S13, S14 |
| Portable text plus bounded desktop Mermaid topology | S02, S09, S10, S13, S14 |
| Active/recent runs, detailed status, sanitized diagnostics, attention, and cleanup | S03, S09, S10, S13, S14 |
| Native workflow portfolio/run board, inspector, and attention inbox | S10, S13, S14 |
| Native Kanban page with independent lifecycle authority | S10, S14 |
| Generic same-transaction Kanban mutation preconditions | S10, S14 |
| Cursor recovery, stale-write rejection, bounded refresh, and virtualization | S10, S14 |
| Natural chat and `/workflow` | S09, S13, S14 |
| Cron scheduling and delivery | S09, S13, S14 |
| Workflow developer authoring assistance | S11 |
| Ericsson conversion | S12 |
| Minimal upstream agent-core surface and separately replaceable Kanban CAS | S01, S08, S10 |
| Offline production showcase, guided skill, evidence reports, and safe reset | S13, S14 |
| Repeatable upstream merges and branded propagation | S01, S10, S12, S14 |
| Deadlines, shutdown, orphan prevention, restart/suspend recovery | S01, S03, S05, S06, S08, S10, S13, S14 |
| Retry/provider/network failure and resource/storage bounds | S03, S05, S06, S08, S10, S13, S14 |
| Security, accessibility, performance, and production quality | S01, S03, S05, S06, S07, S08, S10, S13, S14 |

## Definition of Done

- All fourteen slices are complete in dependency order.
- The design acceptance criteria are mapped to passing tests.
- No old `kind:` workflow schema or Ericsson-only orchestrator runtime remains.
- The portable E2E fixture runs without YAML edits.
- The installed Laptop Diagnostic and resilience showcases run offline from packaged assets through public CLI/skill contracts, produce evidence-backed reports, and leave no owned process, staging file, or temporary schedule after cleanup.
- The default showcase uses only sanitized fictional laptop evidence and never invokes the legacy Windows PowerShell collector, live host inventory, an external integration, or a model. The live collector remains a separately reviewed capability.
- AI/extension and scheduling tours require explicit confirmation; missing AI is reported as skipped without failing offline-required claims, and schedule cleanup proves exact ownership before deleting anything.
- The installed showcase exposes no corruption, forced-shutdown, resource-exhaustion, flood, or soak mode; those scenarios remain CI/release-only.
- Wheel and sdist installation tests prove every showcase resource is packaged at its expected relative path and executable without falling back to the source checkout.
- Linux/macOS tests and Windows-specific lock/process simulations pass. Native Windows CI must pass before claiming Windows workflow support; otherwise the release is blocked or Windows workflow support is explicitly disabled and documented.
- No unbounded worker, retry, loop, output, artifact, lock wait, or subprocess path remains.
- Duplicate chat/Desktop/API/cron delivery returns one run; intentional overlap follows `queue`, `allow`, or `forbid`; and executing/queued/paused/nonterminal/start-rate/global-worker pressure fails or queues before process/artifact allocation.
- Imported/external packages cannot execute locally without digest-bound trust, executable-resource changes invalidate trust, untrusted execution requires an advertised isolated Hermes backend, and immutable input snapshots survive source mutation/deletion.
- Natural language, `/workflow`, and `hermes workflow` can list/show workflows, list active/recent/waiting runs, inspect detailed status and sanitized failures, and report actionable next steps from the same catalog/store contracts.
- Every workflow `show --json` has a bounded `topology_text` and, within the exact limits, strict-subset raw `topology_mermaid`; both represent the same normalized graph and neither contains prompt/secret content or workflow-authored directives.
- CLI/TUI/dashboard/unknown surfaces present text topology; Desktop chat presents the text fallback plus a rendered fenced Mermaid diagram; the Workflows page presents the same bounded topology beside its operational board and inspector; oversize or failed Mermaid always degrades to text with a warning.
- Desktop Workflows answers problem/position/completion/attention questions at a glance and exposes wait reason, failure/retry causality, semantic activity, next actions, immutable trigger/input identity, artifacts, verification, and reconciliation evidence in the inspector.
- Desktop Kanban uses physical boards by project/repository/domain; no physical board is created per workflow/run, no ordinary workflow node is mirrored into Kanban, and the shared Activity Board exposes no generic workflow move operation.
- Workflow and Kanban pages recover from cursor gaps, backend restart, profile/board switches, late responses, stale mutations, and disabled plugins without displaying cross-scope data or stealing focus/navigation.
- Board/card/event work remains bounded and paginated, visible-page refresh is the only refresh, large columns virtualize, hidden windows stop polling, attention/failure notifications deduplicate, and performance tests show no unbounded renderer/request/timer growth.
- Workflow/Kanban pages pass keyboard, focus, reduced-motion, screen-reader labeling, contrast-token, and laptop-width tests in all four supported locales.
- Catalog/status authorization prevents cross-profile and cross-conversation/user disclosure, including when an explicit run ID is supplied.
- Shutting down an owning Hermes process stops admission, terminates and reaps every workflow process tree within the configured deadline, and leaves active attempts durably `interrupted`; renderer-only closure behavior is documented and tested separately.
- Parent loss, provider/network stalls, PID reuse, laptop suspend/wake, and forced restart have deterministic bounded outcomes and never infer success.
- Completion/cancel, retry/cancel, approval/cancel, admission/shutdown, and cleanup/reader races have one durable winner; stale writers lose; uncertain outward actions require reconciliation; and an OS process whose exit cannot be observed is reported as `cleanup_failed`, never falsely reaped.
- Retry backoff holds no worker, nested retry layers share one budget, and fatal/unknown-side-effect errors do not retry.
- The 100-cycle CI soak and 500-cycle release soak leave zero workflow-owned processes/zombies, retained scheduler/reader threads, descriptors/handles, or quarantine entries and no upward memory/disk resource trend.
- Run/profile disk quotas and seven-day cleanup/retention are race-safe, restart-safe, and covered by dry-run and concurrent-reader tests.
- Prompt-cache and message-alternation invariants pass existing regressions.
- The customization ledger and checker cover the final agent-core, generic Kanban persistence, Kanban API, and Desktop composition diff with owned contracts, invariant tests, exact last-verified upstream commit, merge guidance, and removal conditions.
- The merge skill and release rehearsal reject blanket whole-file resolution of ledger-owned files, exercise no-overlap/same-file/owned-symbol/upstream-equivalent cases, update the verified baseline only after tests pass, and never propagate an unverified base commit.
- The upstream merge rehearsal passes through temporary base, OTTO, and LOOP24 branches; evidence proves each brand contains the exact tested base commit plus generated overlay and no divergent generic workflow/Kanban implementation.
- Documentation and PR evidence identify every unsupported provider-specific Archon field.
- Security and code review have no unresolved high-severity findings.
