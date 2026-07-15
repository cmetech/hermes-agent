# Archon-Compatible Workflow Orchestration Design

**Status:** Design of record; approved for implementation planning

**Date:** 2026-07-15

**Scope:** Hermes/Co-worker only; no Pi framework or legacy OTTO workflow runtime

## Summary

Co-worker will gain a production-grade workflow capability that accepts Archon-shaped workflow packages with minimal or no edits, executes them through Hermes agents and tools, and is available from natural chat, `/workflow`, `hermes workflow`, and Hermes cron.

The workflow engine will live at the edge as an additive plugin plus skills. Hermes core will receive only a small, generic plugin-agent execution contract that exposes existing `AIAgent` behavior safely. Every core modification will be isolated, tested, recorded in a machine-readable upstream-customization ledger, and designed as an upstreamable change rather than a workflow-specific fork.

## Goals

1. Load valid Archon DAG workflow YAML without rewriting its structure.
2. Support the portable semantics of Archon command, prompt, bash, script, loop, approval, and cancel nodes.
3. Reuse Hermes skills, tools, plugins, hooks, MCP, sessions, approvals, cron, profiles, and provider routing.
4. Preserve Hermes prompt caching. `context: fresh` creates an isolated child session; it never clears or rewrites the parent conversation.
5. Make runs durable, resumable, concurrency-safe, observable, and bounded in resource use.
6. Provide a workflow-authoring skill that produces an entire valid package, not merely a YAML file.
7. Keep Ericsson-specific policy and distribution separate from the portable workflow definition.
8. Keep future merges from upstream Hermes routine by minimizing and documenting changes to upstream-owned files.

## Non-Goals

- Embedding the Archon application or runtime in Hermes.
- Reusing any Pi-based OTTO extension or state format.
- Adding a workflow model tool to the permanent Hermes tool schema.
- Mutating a live conversation's system prompt or toolset.
- Guaranteeing byte-for-byte behavior for provider-specific Archon SDK fields that Hermes cannot implement safely.
- Building a workflow dashboard in the first milestone. CLI, chat, gateway, cron, state, and artifacts are the initial surfaces.
- Migrating deployed Ericsson workflow runs. There are no deployed installations, so the current schema is replaced rather than versioned alongside the new one.

## Approaches Considered

### A. Edge runtime plus one generic Hermes agent-runner seam — selected

An additive `workflow` plugin owns schema, discovery, graph scheduling, durable state, workers, resources, and CLI commands. Two skills own conversational execution and authoring. A small public Hermes facade lets trusted plugins run isolated, tool-using agents with explicit provider, model, tool, skill, session, and callback policy.

This maximizes reuse, preserves cache invariants, and confines upstream merge risk to a small generic contract.

### B. Skill-only orchestration using the parent agent — rejected as the destination

The current Ericsson controller can be expanded and the parent model can continue executing nodes. This avoids core changes, but per-node tools, skills, MCP, provider/model selection, hooks, structured output, and context isolation would be advisory rather than enforced. It is acceptable only as a temporary compatibility path during development.

### C. One Hermes CLI subprocess per node — rejected as the default

This provides process isolation but the existing one-shot path bypasses dangerous-command approvals and does not expose all required skill/session semantics. It also duplicates startup work and makes progress, cancellation, and shared sessions harder to coordinate. A dedicated node worker may still use process isolation behind the new runner contract when ephemeral MCP or strict cancellation requires it.

## Compatibility Contract

### Canonical workflow shape

The accepted portable document follows Archon's published schema:

```yaml
name: workflow-name
description: What the workflow accomplishes
nodes:
  - id: first-node
    command: investigate
  - id: second-node
    prompt: Summarize $first-node.output
    depends_on: [first-node]
    context: fresh
```

Each node has exactly one node-type field:

- `command`
- `prompt`
- `bash`
- `script`
- `loop`
- `approval`
- `cancel`

Portable common fields include `depends_on`, `when`, `trigger_rule`, `context`, `idle_timeout`, `retry`, `always_run`, and `output_type`. AI-node fields include `persist_session`, provider/model configuration, `output_format`, tool restrictions, hooks, MCP, skills, and agents. Workflow-level `persist_sessions` supplies the default for eligible AI nodes.

References:

- [Authoring workflows](https://archon.diy/guides/authoring-workflows/)
- [Authoring commands](https://archon.diy/guides/authoring-commands/)
- [Loop nodes](https://archon.diy/guides/loop-nodes/)
- [Approval nodes](https://archon.diy/guides/approval-nodes/)
- [Script nodes](https://archon.diy/guides/script-nodes/)
- [Hooks](https://archon.diy/guides/hooks/)
- [MCP servers](https://archon.diy/guides/mcp-servers/)
- [Skills](https://archon.diy/guides/skills/)

### Compatibility levels

The validator reports one of three outcomes per field:

1. **Portable:** syntax and runtime semantics are supported.
2. **Mapped:** Hermes provides equivalent behavior and reports the mapping.
3. **Provider-specific or unsupported:** validation emits a precise warning or error. Unsupported behavior is never silently ignored.

Strict validation is the default for execution. A `--compat-report` mode may inspect a package without running it.

### Archon-to-Hermes feature mapping

This table is the implementation contract, not a claim that similarly named concepts are automatically equivalent.

| Archon surface | Hermes/Co-worker mapping | Contract |
|---|---|---|
| `command`, `prompt` | Snapshotted command Markdown or inline text becomes one user message to a scoped Hermes agent worker | Portable |
| `bash` | Hermes terminal environment, approval policy, timeout, process-group cancellation, bounded stdout/stderr | Portable |
| `script` + `runtime`/`deps` | Argument-vector `uv` or Bun worker with contained named resources | Portable when the declared runtime exists |
| `loop` | Sequential persisted iterations, completion signal/`until_bash`, hard maximum, interactive pause | Portable |
| `approval` + `on_reject` | Durable compare-and-set gate, captured response, bounded rework | Portable |
| `cancel` | Durable cancellation plus in-flight worker/process-tree termination | Portable |
| DAG, `depends_on`, `trigger_rule`, `when` | Deterministic topological scheduler and Archon's documented condition grammar/precedence | Portable |
| `retry`, `always_run`, resume | Persisted classified retry/backoff and explicit resume using cached successful nodes | Portable |
| `context: fresh` | New worker and Hermes session; parent conversation untouched | Portable |
| `context: shared` | Resume predecessor session only when its cache fingerprint is identical | Mapped; incompatible changes block with `use fresh` guidance |
| `persist_sessions` / `persist_session` | Profile/conversation-scoped Hermes node-session registry keyed by workflow, node, scope, and provider, with reset commands and cache-fingerprint checks | Mapped for `command`/`prompt`; fresh context wins |
| `output_format`, `output_type`, artifacts | Strict JSON Schema validation and typed artifact sidecars in the run artifact store | Portable; required validator must be installed |
| `allowed_tools`, `denied_tools` | Archon tool aliases resolve to Hermes names, then filter schemas, Tool Search, unwrap, and dispatch; deny applies last | Mapped and enforced for every Hermes provider |
| `skills` | Resolved skill contents are snapshotted and added to the node's user message, never the system prompt | Mapped |
| `mcp` | Existing Hermes MCP client starts the declared servers only inside the node worker and tears them down | Mapped when the existing `mcp` extra and config are available |
| inline `agents` | A worker-local `workflow_agent` tool runs bounded Hermes child-agent workers from the declared description, prompt, model, tools, skills, and max-turn policy | Mapped; no permanent core model tool |
| `provider`, `model`, `fallbackModel` | Existing Hermes provider profiles, model resolution, credentials, and fallback chain behind plugin trust policy | Mapped when the selected provider advertises the capability |
| `effort`, `thinking`, `maxBudgetUsd`, `modelReasoningEffort` | Hermes reasoning and iteration/cost budgets where the selected provider exposes equivalent controls | Conditional; otherwise a blocking compatibility finding |
| `systemPrompt` | Initial snapshotted worker system prompt only; never changed in a shared session | Conditional on fresh/fingerprint-safe context |
| `betas`, `sandbox`, `webSearchMode` | Field-by-field provider/terminal capability table | No guessed equivalence; unsupported behavior blocks strict execution |
| `requires` | Preflight against configured Hermes services/identities before run creation | Mapped only for declared entries in the compatibility table |
| `worktree` | Use an explicitly supplied isolated workdir; this milestone does not create or remove git worktrees | Required `true` blocks if the caller did not supply equivalent isolation |
| `interactive`, `tags` | Invocation/display metadata for chat/desktop/gateway surfaces | Mapped where that surface exists; otherwise observational warning |
| command frontmatter and variables | Preserve `description`, `argument-hint`, positional arguments, standard variables, node references, and substitution order | Portable; shell-node substitutions receive Archon-compatible safe quoting/spill behavior |

Tool aliases are data-driven and reported by doctor output—for example Archon `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, and `Agent` map only when the corresponding Hermes terminal/file/search/worker capability is enabled. Unknown aliases fail before a model call.

### Archon hook mapping

All published Archon hook event names are recognized by the schema. Strict execution permits only the mappings below; an unsupported behavior-changing hook is an error, never a warning-and-ignore path.

| Archon event | Hermes worker mapping | Initial support |
|---|---|---|
| `PreToolUse` | Pre-tool policy/middleware: allow, deny, ask, input update, added context, stop | Mapped |
| `PostToolUse` | Post-tool/transform path: added context, output replacement, steering, stop | Mapped |
| `PostToolUseFailure` | Status-aware post-tool path for failed dispatch | Mapped |
| `SubagentStart`, `SubagentStop` | Hermes child-agent lifecycle hooks from `workflow_agent` | Mapped |
| `SessionStart`, `SessionEnd` | Isolated worker session lifecycle | Mapped |
| `UserPromptSubmit` | Fires after snapshot/substitution and before the model request | Mapped |
| `PermissionRequest` | Typed durable approval broker; existing deny/hardline policy remains authoritative | Mapped |
| `Setup` | Worker initialization/maintenance lifecycle | Mapped |
| `Elicitation`, `ElicitationResult` | Existing MCP elicitation bridge inside the worker; secret redaction applies | Mapped when MCP is enabled |
| `InstructionsLoaded` | Fires for snapshotted context/skill/command instructions before submission | Mapped |
| `TaskCompleted` | Completion of a worker-local inline agent task | Mapped only for `workflow_agent` tasks |
| `Notification` | No exact node-local Hermes notification event/response contract | Unsupported initially |
| `Stop` | Hermes has no safe post-final-response continuation hook with identical semantics | Unsupported initially |
| `PreCompact` | No generic per-node pre-compression response hook | Unsupported initially |
| `TeammateIdle` | No equivalent teammate runtime | Unsupported initially |
| `ConfigChange` | Run config/resources are immutable snapshots by design | Unsupported for active runs |
| `WorktreeCreate`, `WorktreeRemove` | No lifecycle fires when the caller supplies an already-created workdir | Unsupported in this milestone |

For mapped events, `hookSpecificOutput`, `systemMessage`, `continue`, `decision`, `stopReason`, and `suppressOutput` are translated field-by-field. Model-visible guidance is appended through tool/user result content rather than mutating the system prompt. Unknown response fields or a mismatched `hookEventName` block validation.

### Package locations and precedence

Discovery order is:

1. An explicitly supplied workflow file or package root.
2. Project-local `.archon/` under the selected working directory.
3. Profile-local `$HERMES_HOME/workflows/` and its sibling `commands/`, `scripts/`, and `mcp/` directories.
4. User-global `~/.archon/` for direct Archon package compatibility.

A project-local definition overrides a same-named global definition. Duplicate names at the same precedence are validation errors. Discovery is recursive and deterministic.

### Co-worker policy sidecar

Portable YAML will not gain Ericsson-only fields. Optional policy lives beside a workflow:

```text
my-workflow.yaml
my-workflow.hermes.yaml
```

The sidecar may define delivery defaults, required configured services, retention, branded tags, and outward-action policy. It cannot change graph topology or weaken the portable workflow's security policy.

## Architecture

### 1. Plugin agent runner — narrow Hermes-core seam

`agent.plugin_agent` will expose immutable request/result contracts and a host-owned runner. `PluginContext.agent` will provide the facade to trusted plugins. Each run executes in a fresh host-owned worker process started with spawn/subprocess semantics, never by forking a live multithreaded Hermes process. The child resolves credentials and constructs `AIAgent`; the plugin receives only sanitized progress and results over bounded IPC.

The worker imports the installed Hermes package and constructs the normal `AIAgent`; it is not a partial copy of Hermes and it is not another gateway, TUI, desktop backend, or daemon. An ordinary workflow AI node enables no delegation tool and runs only that node. Current Hermes `delegate_task` is deliberately not exposed raw inside a workflow worker: today it creates child `AIAgent` objects in the same process, uses threads for parallel children, and returns top-level delegation results asynchronously. Those semantics can outlive an ephemeral node worker and would reintroduce process-global tool/MCP state sharing inside the isolation boundary.

When an Archon node explicitly declares `agents`, the worker receives an ephemeral `workflow_agent` tool backed by the same generic plugin-agent runner. A call sends a synchronous child-run request to the workflow coordinator; the coordinator reserves the node's predeclared descendant capacity and starts each child as a separately spawned, scoped worker process. The parent node waits for bounded child results before it can complete. Child progress is relayed as sanitized nested events, large results become artifacts, and cancellation terminates the parent plus every coordinator-tracked child worker and process group. The raw `delegate_task` background-result path is never used.

Conceptual interface:

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
    timeout_seconds: float | None = None

@dataclass(frozen=True)
class PluginAgentRunResult:
    final_response: str
    session_id: str
    provider: str
    model: str
    status: Literal["completed", "paused", "cancelled", "failed"]
    pending_interaction: Mapping[str, str] | None
    usage: Mapping[str, int | float | None]
```

The facade resolves credentials through Hermes, enforces tool filters before the first model call and through Tool Search/dispatch, injects only selected skill content, preserves message-role alternation, and propagates cancellation, progress, and sanitized interaction events. It does not expose provider credentials to plugins.

The process boundary is required because current Hermes retains backward-compatible process-global tool-resolution state. A workflow worker must not overwrite the parent conversation's `_last_resolved_tool_names`, tool cache, terminal working directory, MCP registry, hooks, or plugin registry. Inside the child, a generic `ToolRegistry.scoped_names()` context filters registry snapshots, lookup, Tool Search, and dispatch and bumps the existing registry generation on enter/exit so cached schemas cannot cross scopes. After `AIAgent` construction, the runner also verifies the final agent-owned schema/name sets for non-registry tools before the first model call. A denied deferred tool cannot reappear through `tool_search`, `tool_describe`, or `tool_call`.

Shared workflow context persists by Hermes session ID, not by reusing a mutable worker. MCP and declarative hook scoping are worker concerns and are torn down with the worker. They must not mutate the parent conversation's registered tools.

Each node session records a cache fingerprint covering provider, model/API mode, byte-identical system prompt, exposed tool schemas and order, name/toolset policy, MCP schemas, profile, workdir-derived prompt state, reasoning configuration, and other cache-affecting runner inputs. A `context: shared` edge is valid only when its fingerprint matches the predecessor exactly. Resume reconstructs from the snapshotted system prompt and schemas rather than rebuilding dynamic prompt content. If a workflow wants different tools, MCP, model, profile, or prompt-affecting settings, it must use `context: fresh`. Selected skill instructions and command content are snapshotted and combined into the new user message, preserving role alternation without modifying the system prompt.

Every worker installs explicit host-owned approval, clarification, sudo, and secret-capture callbacks. It never falls into Hermes' legacy bare non-interactive auto-approval path. A dangerous action without a live answer returns a sanitized, digest-bound pending interaction and terminates the worker so the workflow can pause durably without holding resources. Resume may grant that exact action once; a changed action pauses again. Clarification answers may be durable. Sudo passwords and secret values are never persisted, logged, returned to the plugin, or carried in a resume token: already-configured profile secrets are resolved inside the host worker, while missing secrets or sudo requirements fail closed with standard setup guidance. Cron honors existing `approvals.cron_mode` and never waits for a person.

### 2. Workflow plugin

The additive `plugins/workflow/` package is divided into deep modules:

- `schema.py`: immutable workflow models and strict one-of validation.
- `discovery.py`: deterministic project/profile/global resource lookup.
- `resources.py`: command, script, MCP, variable, and artifact resolution.
- `store.py`: snapshots, event journal, atomic state projections, and retention.
- `locks.py`: bounded cross-process locks and node execution leases.
- `scheduler.py`: DAG readiness, trigger rules, skip propagation, parallel bounds, and resume.
- `executors/`: separate AI, bash, script, loop, approval, and cancel executors.
- `compat.py`: field-by-field Archon compatibility report.
- `cli.py`: `hermes workflow` operator commands.

The scheduler depends on executor interfaces, not on Hermes CLI or gateway code. Executors return typed results; they never write run state directly.

### 3. Conversational skills

The generic `workflow` skill is the deterministic conversational entry point:

- `/workflow run <name> [arguments]`
- `/workflow list`
- `/workflow status <run-id>`
- `/workflow approve <run-id> [comment]`
- `/workflow reject <run-id> [reason]`
- `/workflow resume <run-id>`
- `/workflow cancel <run-id>`

Natural user requests rely on the normal Hermes skill index. Explicit `/workflow` loads the full skill as a user message, preserving prompt caching and working across CLI, gateway, TUI, and desktop skill-command plumbing.

The `workflow-builder` skill interviews the user, inspects installed tools/MCP/skills, writes the YAML plus referenced commands/scripts/MCP files, runs package validation, and shows compatibility findings before offering execution or scheduling.

The bundled workflow plugin remains opt-in under Hermes' existing `plugins.enabled` mechanism. Ericsson capability staging enables it for branded profiles. On a general Hermes profile, the workflow skill detects the disabled state and gives the existing `hermes plugins enable workflow` activation command before attempting a run; no workflow-specific plugin-loader exception is added to core.

### User-to-runtime flow

```text
Natural request or /workflow
  → stable Hermes skill index selects/loads the workflow skill as one user message
  → skill invokes hermes workflow ... through the existing terminal tool
  → enabled workflow plugin discovers + validates the snapshotted package
  → durable scheduler claims ready nodes
  → deterministic subprocesses or isolated Hermes agent workers execute
  → JSON status/artifact/next-action result returns to the skill
  → skill explains completion, pause, approval, failure, or resume to the user
```

Natural-language routing depends on the concise skill description already present in Hermes' stable skill index. Explicit `/workflow ...` is the deterministic escape hatch on CLI, gateway, TUI, and desktop: it loads the full skill as a user message, not a system-prompt mutation. The skill uses machine-readable CLI responses and never edits run files directly.

An approval or clarification ends the worker and durably pauses the run. The user can answer in the same conversation or later with `/workflow approve`, `/workflow reject`, or `/workflow resume`; the next CLI invocation continues the same run without holding the original chat turn or worker. Cron enters at the same skill/CLI boundary, delivers a paused run ID instead of waiting, and resumes only after an explicit later action.

For authoring, a natural request such as “build a workflow that reviews a ticket and asks before posting” selects `workflow-builder`. That skill inventories the current profile's tools, MCP servers, skills, providers, and runtimes; writes an Archon-shaped package; runs `doctor`; and offers on-demand execution or an existing Hermes cron schedule only after the package is runnable.

### 4. Scheduling

Scheduled execution uses Hermes cron rather than a second scheduler. A cron job attaches the `workflow` skill and a self-contained run instruction. Existing cron provider/model/toolset/workdir/delivery fields remain authoritative for the outer job; workflow-level node overrides apply inside the run.

An approval gate ends the current cron firing cleanly after delivering the run ID and review instructions. A later user approval resumes the durable workflow; cron does not wait while holding a worker.

## Run State and Concurrency

### Layout

```text
$HERMES_HOME/workflows/runs/<workflow>/<run-id>/
├── definition.yaml
├── policy.yaml                 # present only when a sidecar was used
├── run.json                    # atomic materialized projection
├── events.jsonl                # append-only audit journal
├── .lock
├── artifacts/
└── nodes/<node-id>/<attempt-id>/
```

The definition and resolved command resources are snapshotted at start. Resume never depends on a mutable source file.

### Locking and claims

- State transitions use an in-process reentrant lock plus a bounded cross-process advisory lock, following Hermes cron's proven POSIX/Windows pattern.
- A lock is never held while an AI, shell, script, hook, or MCP operation runs.
- Ready nodes are claimed under lock with an owner ID, attempt ID, start time, and renewable lease.
- Completion records must match the active claim. A stale worker cannot overwrite a newer attempt.
- Expired claims become `interrupted`, not automatically successful or failed.
- Unknown-outcome attempts that may have produced external side effects require operator reconciliation before retry.
- Parallelism is bounded by `workflow.max_parallel_nodes`; the default is 4. Each AI node consumes one worker-process slot. Loop iterations remain sequential.
- A node that declares child agents reserves a weighted execution bundle before its parent worker starts: one slot for the node plus its declared maximum simultaneous children. A worker can spawn only within that reservation. This prevents four parent workers from consuming every slot and deadlocking while each waits for an unaccounted child.

### Persistence

- Journal append and projection replacement occur under the run lock.
- Projection writes use a unique temporary file, flush, `fsync`, and atomic replace where the platform supports it.
- Event records carry schema version, sequence, timestamp, run ID, node ID, attempt ID, event type, and sanitized payload.
- Large output is stored as artifacts. Run state stores digests, media type, size, and relative path rather than embedding content.
- Artifact path traversal and symlink escape are rejected.

## Execution Semantics

### DAG, conditions, and triggers

Independent ready nodes run concurrently. `trigger_rule` follows Archon's documented values. Context defaults follow Archon: fresh for parallel layers and inherited for an unambiguous sequential predecessor, subject to the Hermes cache-fingerprint gate. Conditions use typed parsed output when available and raw text otherwise. Invalid references and statically malformed expressions fail package validation. Runtime type/number evaluation that cannot produce a valid comparison follows Archon's fail-closed behavior: the condition is false, the node is skipped, and a warning is journaled.

### AI nodes

- `command` loads a markdown template and substitutes approved variables.
- `prompt` uses the inline prompt.
- `context: fresh` starts a new isolated session.
- `context: shared` resumes the selected predecessor session only with an identical cache fingerprint. An ambiguous predecessor or a cache-affecting mismatch is a validation error with guidance to use fresh context.
- `output_format` is validated after provider output; schema-invalid output fails the attempt.
- Allowed and denied tools are enforced in the actual tool schema and dispatch allowlist.
- Skills are loaded only for that node.
- Per-node MCP is started in the isolated execution worker and stopped in `finally` cleanup.
- Provider-specific options are mapped only when the selected Hermes provider supports them.

Declared inline agents use the worker-local `workflow_agent` tool, never ambient `delegate_task`. Each declared agent gets its own worker process, session, tool/skill/MCP scope, provider policy, iteration budget, and result contract. The parent node receives only the bounded child result and artifact references. Child-agent count, simultaneous children, total descendants, spawn depth, tokens, cost, iterations, and wall time are validated against hard workflow limits; Hermes' more permissive global delegation settings cannot raise those workflow limits.

Strict JSON Schema validation reuses `jsonschema` from Hermes' existing `mcp`/`all` installation path. A lean installation without that dependency may run workflows that do not declare `output_format` or per-node MCP; validation and doctor commands fail closed with the exact existing-extra installation guidance when either feature requires it. The workflow does not silently skip schema validation and does not add an unconditional core dependency.

### Deterministic nodes

- `bash` executes through the existing Hermes terminal environment with an explicit timeout and sanitized environment.
- `script` supports named or inline Bun/JavaScript and uv/Python execution with validated runtime/dependency declarations.
- stdout is bounded and captured as node output; stderr is retained as a diagnostic artifact.
- Process groups are terminated on timeout or cancellation so descendants do not leak.

### Loops

Loop state records each iteration independently. `max_iterations` is mandatory and enforced. Completion may come from the declared signal or a successful `until_bash` check. Interactive loops persist the gate and return control to the user rather than occupying a worker.

### Approvals

Approval state includes the originating profile, conversation identity when available, capture policy, and rejection-attempt count. Decisions are compare-and-set operations: only the first valid approve/reject transition wins. `on_reject` rework is bounded and returns to the same gate.

Workflow approval nodes and Hermes tool approvals are distinct layers. A workflow gate authorizes graph progression; it does not disable terminal hardline blocks, user deny rules, or tool-level approval policy. Tool approvals are bound to the exact sanitized action digest and are single-use unless Hermes' existing policy records a broader user-approved scope.

### Retry policy

Retries are driven by classified errors and persisted next-attempt time. Exponential backoff includes jitter and is capped. Configuration is bounded at validation time. Validation errors, permission denials, and unknown side-effect outcomes are not transient.

## Security and Resource Controls

- Workflow and resource paths are canonicalized and constrained to approved package roots.
- Command names and script references cannot contain traversal segments.
- MCP environment references are expanded at execution without writing secret values to state, logs, compatibility reports, or prompts.
- Hook matchers and responses are schema-validated before execution.
- Shell/script execution retains Hermes approval and environment-sanitization policy.
- Workflow concurrency, node and child-agent iterations, output bytes, artifact bytes, timeout, retry attempts, descendant count, child concurrency, and spawn depth have config-backed hard upper bounds.
- `config.yaml` owns behavioral settings; no new non-secret `HERMES_*` user configuration is introduced.
- Cancellation terminates worker process groups and always releases leases and MCP sessions.

## Performance Requirements

- Listing and validating workflows performs no network or model calls.
- Discovery caches by directory metadata and file digest and invalidates deterministically.
- Scheduler lock critical sections target less than 50 ms under normal local-filesystem load.
- A run with 1,000 completed nodes can load its projection without replaying the full journal.
- Ready-node scheduling is linear in nodes plus edges for a scheduling pass.
- Worker concurrency is bounded; no unbounded thread, process, or task creation is allowed.
- Worker startup latency and peak resident memory at concurrency 1 and 4 are measured in the release gate; the implementation must not pre-spawn an unbounded or idle permanent pool.
- Node outputs are streamed to bounded files rather than accumulated without limit in memory.
- The parent chat prompt and tool schema remain byte-stable throughout a conversation.

## Failure and Recovery

- Invalid packages fail before creating billable AI work.
- A partially initialized run is either absent or recoverable from its snapshot and journal.
- Corrupt projections are quarantined and rebuilt from a verified journal when possible.
- Corrupt journals stop the run with an actionable diagnostic; they are never silently truncated.
- Worker crash, host restart, and duplicate scheduler invocation converge through leases and compare-and-set completion.
- Approval and cancellation are idempotent.
- Resume reuses completed nodes unless `always_run` is set.
- Workflow source changes affect new runs only.

## Packaging and Ericsson Conversion

Generic runtime code and skills are shared by OTTO and LOOP24. Ericsson packages contain only Ericsson workflows, commands, scripts, MCP references, sidecars, and required capability metadata.

The existing `my-tickets-summary` and `inbox-digest` files will be replaced with Archon-shaped workflows. Long prompts move to `.archon/commands`. Current custom fields such as required environment values, reports, and side-effect flags move to capability configuration or Hermes sidecars.

Capability staging will copy complete workflow packages atomically instead of copying isolated YAML files. Brand branches select the shared capability through descriptors; workflow runtime code is not duplicated into branded branches.

## Upstream Merge Discipline

The branch flow remains:

```text
Hermes upstream/main → local main mirror → base → otto / loop24
```

Rules for this milestone:

1. Prefer additive files under `plugins/workflow`, skills, capabilities, tests, and docs.
2. Core changes must be generic, contain no Ericsson or brand naming, and be viable as an upstream Hermes contribution.
3. Every modified upstream-owned file is recorded in `docs/upstream-customizations/workflow-orchestration.yaml` with rationale, owner, tests, upstreamability, merge guidance, and removal condition.
4. A validation script checks that recorded files and tests exist and that entries remain internally consistent.
5. Core-seam commits remain separate from workflow-plugin commits so they can be rebased, submitted upstream, replaced, or dropped independently.
6. The external merge skill runs the lightweight ledger checker and focused offline workflow tests during a real `main` to `base` merge. The full temporary-worktree rehearsal is a CI/release or explicit preflight gate; it is not invoked recursively from inside the real merge.
7. If upstream later provides an equivalent API, the customization entry requires an explicit replace-or-remove decision; parallel implementations are not retained.
8. The repository exposes a stable checker, focused smoke command, and merge-rehearsal command. The owning skill is `/Users/coreyellis/code/github.com/cmetech/otto_hermes/.claude/skills/otto-upstream-merge/SKILL.md`; it remains outside this repository and is not copied into Hermes.

### Planned upstream-core touch budget

The implementation may modify only two existing upstream-owned files without a design amendment:

- `tools/registry.py`: generic, reversible scoped-name discovery/dispatch view used only inside an isolated worker.
- `hermes_cli/plugins.py`: lazy `PluginContext.agent` facade, parallel to the existing `PluginContext.llm` facade.

`agent/plugin_agent.py` and `agent/plugin_agent_worker.py` are new generic modules intended for upstream contribution. The workflow runtime itself does not modify `run_agent.py`, `model_tools.py`, `agent/agent_init.py`, `agent/tool_executor.py`, or `agent/agent_runtime_helpers.py`. If implementation evidence proves another core touch unavoidable, work pauses for an explicit design/ledger update rather than expanding the fork silently.

## Testing Strategy

### Unit

- Schema one-of rules and every supported field.
- Variable substitution and path containment.
- Trigger rules, conditions, retry classification, backoff bounds, and loop termination.
- State projection, journal validation, compare-and-set transitions, and lease expiry.
- Tool filtering, skill scoping, structured output validation, and compatibility diagnostics.

### Concurrency and fault injection

- Multiple processes racing to start, claim, complete, approve, reject, resume, and cancel the same run.
- Stale attempt completion after a newer lease is active.
- Crash between journal append and projection replacement.
- Lock contention and timeout on POSIX and simulated Windows locking.
- Cancellation during AI, shell, script, hook, and MCP execution.

### Integration

- Real plugin discovery and `hermes workflow` command registration.
- Real `AIAgent` construction against a deterministic fake provider with actual tool dispatch.
- Project/profile/global discovery precedence.
- Per-node MCP startup and shutdown with a local test server.
- Cron firing with attached workflow skill, delivery, and approval pause.
- Skill command dispatch through CLI, gateway, TUI gateway, and desktop command catalog paths.

### End-to-end

- Run an unmodified portable Archon fixture containing command, prompt, bash, script, parallel join, structured output, loop, approval, and cancel paths.
- Interrupt and resume the fixture without repeating completed work.
- Execute converted Ericsson fixtures with fake Jira and Outlook services.
- Merge-rehearsal test from the latest upstream mirror through `base`, then validate OTTO and LOOP24 brand generation and focused test suites.

## Acceptance Criteria

1. An Archon workflow package can be copied into a project and validated without YAML rewriting.
2. `/workflow run`, `hermes workflow run`, and cron all execute through the same durable runtime.
3. Fresh contexts do not mutate the parent conversation; shared contexts resume only the intended node session.
4. Parallel execution is bounded and duplicate schedulers cannot execute the same claim concurrently.
5. Approvals survive process restart and accept only one winning decision.
6. Structured outputs fail closed when invalid.
7. Tool, skill, hook, and MCP scopes are enforced at runtime rather than documented only.
8. Interrupted runs resume without repeating completed nodes or silently repeating unknown side effects.
9. Existing Ericsson workflows are Archon-shaped and pass portable package validation.
10. Existing upstream-file modifications remain limited to `tools/registry.py` and `hermes_cli/plugins.py`; the two new generic runner modules and all touches are recorded in the customization ledger and covered by merge-rehearsal tests.
11. Focused unit, concurrency, integration, security, performance, and end-to-end gates pass on Linux, macOS, and Windows-supported paths.

## Open Questions

No architectural question blocks implementation planning. Exact mappings for provider-specific Archon fields will be finalized field-by-field in the compatibility table; unsupported fields must remain explicit diagnostics rather than guessed behavior.
