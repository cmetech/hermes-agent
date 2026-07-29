# Portable Workflow Orchestration Design

**Status:** Design of record amended through adversarial re-review; ready for explicit implementation approval

**Date:** 2026-07-16

**Scope:** Hermes/Co-worker only; no Pi framework or legacy OTTO workflow runtime

**2026-07-18 production amendment:** The original foreground-owned scheduling
model is superseded by
`docs/superpowers/specs/2026-07-18-plugin-background-services-workflow-coordination-design.md`.
The workflow plugin now owns a durable coordinator hosted through a minimal
generic plugin background-service lifecycle in web/Desktop and Gateway. The
operator contract and risk-ordered delivery sequence are defined in the
corresponding 2026-07-18 operator-experience design and plan. Earlier language
that assigns continuation to a request, cron firing, renderer, or incidental
foreground process is not authoritative.

## Summary

Co-worker will gain a production-grade workflow capability that accepts Archon-shaped workflow packages with minimal or no edits, executes them through Hermes agents and tools, and is available from natural chat, `/workflow`, `hermes workflow`, and Hermes cron. A bundled offline-first showcase suite will exercise the real runtime on a production installation without external integrations or mandatory workflow-node model calls.

The workflow engine will live at the edge as an additive plugin plus skills. Hermes agent core will receive only a small, generic plugin-agent execution contract that exposes existing `AIAgent` behavior safely. A separate generic compare-and-set extension in the existing Kanban persistence subsystem will protect native Desktop mutations. Every upstream-owned modification will be isolated, tested, recorded in a machine-readable upstream-customization ledger, and designed as an upstreamable change rather than a workflow-specific fork.

## Goals

1. Load valid Archon DAG workflow YAML without rewriting its structure.
2. Support the portable semantics of Archon command, prompt, bash, script, loop, approval, and cancel nodes.
3. Reuse Hermes skills, tools, plugins, hooks, MCP, sessions, approvals, cron, profiles, and provider routing.
4. Preserve Hermes prompt caching. `context: fresh` creates an isolated child session; it never clears or rewrites the parent conversation.
5. Make runs durable, resumable, concurrency-safe, observable, and bounded in resource use.
6. Provide a workflow-authoring skill that produces an entire valid package, not merely a YAML file.
7. Keep Ericsson-specific policy and distribution separate from the portable workflow definition.
8. Keep future merges from upstream Hermes routine by minimizing and documenting changes to upstream-owned files.
9. Make workflow discovery, description, active/recent run status, pending actions, and sanitized diagnostics available through chat, CLI, and a native desktop operational board backed by the same run-store projection.
10. Guarantee bounded worker lifecycles, explicit shutdown behavior, restart reconciliation, and resource cleanup on a laptop-class host.
11. Reuse a desktop-native activity-board presentation layer for workflow projections and Hermes Kanban without merging their execution state machines.
12. Make every trigger idempotent, admission-bounded, and explicit about overlapping-run policy so duplicate chat, Desktop, API, or cron delivery cannot silently create duplicate work.
13. Treat imported executable workflow packages as untrusted until the user approves a digest-bound risk summary; resource limits are never represented as a security sandbox.
14. Ship a production-safe, conversational showcase harness that explains, runs, verifies, and cleans up bundled workflows through the same runtime and operator surfaces users will rely on for their own workflows.

## Non-Goals

- Embedding the Archon application or runtime in Hermes.
- Reusing any Pi-based OTTO extension or state format.
- Adding a workflow model tool to the permanent Hermes tool schema.
- Mutating a live conversation's system prompt or toolset.
- Guaranteeing byte-for-byte behavior for provider-specific Archon SDK fields that Hermes cannot implement safely.
- Building a visual workflow authoring editor or allowing the operational board to edit graph topology. The native desktop board observes and operates valid runtime actions; workflow authoring remains YAML plus the workflow-builder skill.
- Mirroring ordinary workflow nodes into Hermes Kanban tasks or making Kanban the workflow scheduler.
- Reproducing every web-dashboard Kanban management feature in the first native desktop slice. The first slice covers reliable status, attention, inspection, comments, and safe lifecycle actions; advanced parity follows evidence of need.
- Building a new general-purpose OS sandbox. Untrusted packages must use an already configured Hermes execution environment that advertises the required isolation; local user-privileged execution is available only to a digest-trusted package.
- Porting or running the legacy Windows laptop PowerShell collector as part of the workflow milestone. The showcase uses sanitized fictional evidence; a live read-only collector is a separate security-reviewed capability.
- Running destructive reliability experiments on a production installation. Journal corruption, forced Hermes termination, resource exhaustion, high-concurrency floods, and long soak tests remain CI/release-only.
- Migrating deployed Ericsson workflow runs. There are no deployed installations, so the current schema is replaced rather than versioned alongside the new one.

## Approaches Considered

### A. Edge runtime plus one generic Hermes agent-runner seam — selected

An additive `workflow` plugin owns schema, discovery, graph scheduling, durable state, workers, resources, CLI commands, and read-only bundled showcase packages. Three skills own conversational execution, authoring, and guided showcase operation. A small public Hermes facade lets trusted plugins run isolated, tool-using agents with explicit provider, model, tool, skill, session, and callback policy.

This maximizes reuse, preserves cache invariants, and confines upstream merge risk to a small generic contract.

### B. Skill-only orchestration using the parent agent — rejected as the destination

The current Ericsson controller can be expanded and the parent model can continue executing nodes. This avoids core changes, but per-node tools, skills, MCP, provider/model selection, hooks, structured output, and context isolation would be advisory rather than enforced. It is acceptable only as a temporary compatibility path during development.

### C. One Hermes CLI subprocess per node — rejected as the default

This provides process isolation but the existing one-shot path bypasses dangerous-command approvals and does not expose all required skill/session semantics. It also duplicates startup work and makes progress, cancellation, and shared sessions harder to coordinate. A dedicated node worker may still use process isolation behind the new runner contract when ephemeral MCP or strict cancellation requires it.

### Workflow status visualization alternatives

Three visualization integrations were evaluated separately from the runtime choice above:

1. **Mirror every workflow node into a physical Kanban task — rejected.** Kanban would be free to claim, dispatch, reclaim, retry, block, and complete the mirrored task while the workflow scheduler performed the same operations on its node. Dragging a card could terminate a Kanban run without changing the workflow claim, and loops, skips, approvals, cancellation, and unknown external-side-effect states do not have lossless Kanban equivalents.
2. **Compile the workflow into Kanban and make Kanban the executor — rejected.** This is internally coherent but replaces the workflow runner rather than visualizing it. It would discard or duplicate per-node context, hook, artifact, approval-snapshot, bounded process-tree, and compatibility semantics already required by this design.
3. **Render separate workflow and Kanban read models through one desktop-native activity-board presentation layer — selected.** Workflow `RunStore` remains authoritative for workflow runs and nodes; `kanban_db` remains authoritative for Kanban boards and tasks. Source-specific adapters translate snapshots, deltas, and allowed actions into shared visual columns/cards without translating execution ownership.

The shared boundary is deliberately visual. It does not expose a generic `move(any_card, any_column)` operation. Kanban mutations remain Kanban operations; workflow mutations remain typed approve, reject, provide-input, resume, retry-when-policy-allows, reconcile, cancel, and abandon operations. An optional future workflow node may explicitly delegate durable work to Kanban, but ordinary nodes are never mirrored automatically.

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
2. Project-local `.hermes/workflows/` under the selected working directory.
3. Profile-local `$HERMES_HOME/workflows/` with package-local `commands/`, `scripts/`, and `mcp/` resources.

A project-local definition overrides a same-named profile definition. Duplicate names at the same precedence are validation errors. Discovery is recursive and deterministic.

Co-worker never creates or depends on an Archon-branded directory. An existing external Archon package may be supplied explicitly or copied/imported into one of the neutral workflow locations without rewriting its portable YAML shape. Profile-local storage remains isolated so upgrades and other profiles cannot overwrite user-authored packages.

### Co-worker policy sidecar

Portable YAML will not gain Ericsson-only fields. Optional policy lives beside a workflow:

```text
my-workflow.yaml
my-workflow.hermes.yaml
```

The sidecar may define delivery defaults, required configured services, retention, branded tags, outward-action policy, execution-environment requirements, and overlapping-run policy. It cannot change graph topology, mark its own package trusted, or weaken the portable workflow's security policy.

### Package trust, immutable inputs, and run admission

The runtime computes one package digest over the portable YAML, policy sidecar, and every referenced command, script, MCP, and other executable resource. Trust is profile-owned state outside the package and binds the user's decision to that digest. Moving or renaming an unchanged package does not grant new authority; changing any digest-covered byte invalidates the prior trust decision. Explicitly supplied or imported packages start disabled for execution until `doctor` produces a bounded risk summary and the user trusts the digest. Packages produced by the workflow-builder follow the same final confirmation path. First-party capability staging may seed `trusted_distribution` only when content comes from the authenticated installed capability source and its computed digest matches a distribution-owned manifest; a mismatch preserves the prior package/trust state and fails closed. A package cannot self-declare trust in YAML or its sidecar.

The risk summary names shell/script execution, requested tools and skills, local MCP processes, provider/network access, outward actions, required secrets, execution environment, and resource ceilings without exposing prompt bodies or secret values. A trusted package may run in the local Hermes terminal environment under normal approval and hardline policy. An untrusted package may run only through a configured Hermes terminal backend that advertises the required isolation; if none exists, execution fails closed with setup guidance. Process, memory, CPU, descendant, output, disk, timeout, and approval limits mitigate accidents but are not described as containment against malicious local code.

File and document inputs are validated, size-bounded, read-tested, and copied into the immutable run snapshot before admission completes. The run records source path, size, media type, and digest, but downstream nodes consume the snapshot rather than reopening a mutable user path. A changed, missing, unreadable, oversized, or symlink-escaping input fails before billable work.

Every start request carries a source-scoped idempotency key and a digest of workflow version, policy, sanitized inputs, profile, and trigger. Desktop/chat generate one key per user action or originating message; cron uses schedule ID plus scheduled UTC fire instant; API callers provide a key. An atomic profile-level admission record enforces these outcomes:

- the same key and same start digest returns the existing run ID and never creates another run;
- the same key with a different start digest fails with a conflict;
- an intentional second invocation uses a new key and is evaluated by the workflow's concurrency policy;
- `queue` is the default and permits one active run for the workflow/concurrency key while later runs remain durable without holding a worker;
- `allow` permits bounded overlap and `forbid` rejects while a matching run is active; automatic `replace` is unsupported in the first milestone; and
- profile-wide executing-run, queued-run, paused-run, total-nonterminal-run, start-rate, and total-worker limits apply before run-directory or artifact allocation. Paused/user-wait and persisted-retry runs consume nonterminal capacity but not executing/worker capacity.

The concurrency policy lives in the neutral sidecar/profile configuration, not the portable YAML. A scheduled overlap follows the same policy as an on-demand trigger. Admission reservations are restart-reconciled; a crash cannot leave a request permanently consuming capacity without a discoverable run or typed recovery state.

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
    idle_timeout_seconds: float = 300.0
    wall_timeout_seconds: float = 1800.0
    provider_request_timeout_seconds: float = 300.0

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

The runner uses a generic managed-process primitive extracted from Hermes' existing process-registry behavior. It records PID, process start identity, and process-group/job identity before treating a worker as running; guards against PID reuse; performs cooperative cancel, bounded TERM, bounded KILL, and an unconditional wait/reap; and reconciles the final outcome into durable workflow state. No worker or descendant is fire-and-forget.

### 2. Workflow plugin

The additive `plugins/workflow/` package is divided into deep modules:

- `schema.py`: immutable workflow models and strict one-of validation.
- `discovery.py`: deterministic explicit/project/profile resource lookup.
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
- `/workflow show <name>`
- `/workflow runs [--status <state>]`
- `/workflow status <run-id>`
- `/workflow events <run-id> [--tail <count>]`
- `/workflow approve <run-id> [comment]`
- `/workflow reject <run-id> [reason]`
- `/workflow provide-input <run-id> <interaction-id> <artifact-or-value>`
- `/workflow resume <run-id>`
- `/workflow retry <run-id> [node-id]`
- `/workflow reconcile <run-id> <confirmed-succeeded|confirmed-failed|safe-to-retry>`
- `/workflow cancel <run-id>`

Natural user requests rely on the normal Hermes skill index. Explicit `/workflow` loads the full skill as a user message, preserving prompt caching and working across CLI, gateway, TUI, and desktop skill-command plumbing.

The `workflow-builder` skill interviews the user, inspects installed tools/MCP/skills, writes the YAML plus referenced commands/scripts/MCP files, runs package validation, and shows compatibility findings before offering execution or scheduling.

The `workflow-showcase` skill is a compact conversational router over deterministic `hermes workflow showcase ... --json` commands. It lists and explains installed scenarios, performs a no-network preflight, gathers only scenario inputs, starts or resumes the real workflow run, translates pauses and failures into user actions, and interprets the final machine-readable evidence report. It never implements a second scheduler, edits run files, or treats declared showcase metadata as proof that behavior occurred.

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

An approval or clarification ends the worker and durably pauses the run. The
user can answer in the same conversation or later with `/workflow approve`,
`/workflow reject`, or `/workflow resume`; that action commits a durable wake
and the elected coordinator continues outside the original chat/HTTP/CLI turn.
Explicit foreground continuation is available only under the coordinator-
unavailable ownership rules. Cron enters at the same skill/CLI boundary,
delivers a paused run ID instead of waiting, and resumes only after an explicit
later action.

For authoring, a natural request such as “build a workflow that reviews a ticket and asks before posting” selects `workflow-builder`. That skill inventories the current profile's tools, MCP servers, skills, providers, and runtimes; writes an Archon-shaped package; runs `doctor`; and offers on-demand execution or an existing Hermes cron schedule only after the package is runnable.

### Production showcase harness

The installed showcase is both a product demonstration and a bounded production diagnostic of the workflow feature. It is not a substitute for the destructive CI/release suite. The selected structure is one coherent flagship plus focused labs:

1. **Laptop Diagnostic Tour:** the offline scenario uses a bundled, sanitized, fictional laptop snapshot. It requests a symptom/focus, snapshots the input, analyzes independent evidence branches in parallel, joins and loops over findings, branches by severity, emits typed JSON and Markdown artifacts, and pauses for approval/rejection/rework of a proposed remediation plan. Approval finalizes the plan only; it never changes the host. The current scenario is deterministic and has no AI interpretation branch. Its authenticated `commands/interpret-report.md` resource remains deliberately dormant to preserve the verified v3.0.2 package tree and start identities; an MCP-capable CLI AI tour is deferred and must not activate that resource implicitly.
2. **Resilience Lab:** controlled modes demonstrate fail-once retry, a short bounded timeout, user cancellation of a harmless long-running process tree, cleanup evidence, and safe resume/status behavior. It never kills Hermes, corrupts state, exhausts resources, or generates unbounded load.
3. **AI and Extensions Tour:** an explicit opt-in exercises command templates, fresh/shared context, typed AI output, selected skills, mapped hooks, a bundled local stdio MCP echo server, persistent sessions, and bounded child agents. Preflight displays the configured provider/model and possible cost. Missing AI capability produces a typed `skipped` result rather than failing the offline suite.
4. **Scheduling Tour:** an opt-in temporary schedule runs a small deterministic package from snapshotted inputs and exposes scheduled/queued/running/completed state. It uses Hermes' existing one-shot `repeat=1` cron semantics, atomic dispatch claim, and automatic post-run deletion rather than implementing a workflow-specific scheduler or removal path. `showcase reset` reports an owned one-shot that still exists and requires ordinary explicit cron-removal confirmation; it never deletes a cron record by fuzzy name matching.

`hermes workflow showcase list|describe|preflight|run|status|report|reset|cleanup [--json]` is implemented inside the workflow plugin. Bundled packages are read-only plugin resources selected by explicit path and then passed through ordinary validation, immutable snapshotting, admission, scheduling, execution, artifacts, status, and cleanup. They are listed in a separate showcase catalog rather than injected into project/profile discovery precedence. No profile workflow is copied, overwritten, or shadowed.

The bundle has a distribution-owned digest manifest. The installed authenticated package must match that manifest before receiving `trusted_distribution`; a mismatch fails closed. Each run is profile-scoped, tagged `showcase`, uses a unique idempotency key per user action, and receives limits tighter than ordinary defaults for wall time, descendants, output, artifacts, and run storage. AI, network, live machine inventory, credentials, and external writes are absent by default. The live Windows diagnostic collector remains a separate future capability.

A versioned machine-readable showcase catalog declares scenario requirements, safety class, expected checkpoints, artifacts, and capability claims. The post-run reporter proves claims from normalized definitions plus durable RunStore events, attempts, interactions, process cleanup evidence, and artifact verification; catalog declarations alone cannot make a scenario pass. Its report includes scenario and bundle versions, run/definition digests, capabilities exercised, evidence references, passed/failed/skipped claims with reasons, user interactions, artifacts, cleanup state, and suggested next scenario.

The same report drives chat, CLI, and Desktop guidance. A user can ask “show me what workflows can do,” run the Laptop Diagnostic Tour, inspect it on the normal Workflows page, approve or reject its checkpoint, and receive an explanation of what the run demonstrated. With no model configured, the CLI remains fully usable; conversational routing naturally requires an otherwise functioning Hermes chat session, but no AI workflow node is required for the default tour.

### Operator experience across chat, CLI, and native desktop

Every operator surface queries the same discovery and run-store projection, so a board card, CLI status, and conversational answer cannot legitimately disagree:

1. **Catalog:** `hermes workflow list [--json]` and `show NAME [--json]` answer what is installed and what it can do. `list` returns name, description, source/precedence, compatibility, and runnable state. `show` adds argument hints, portable text and Mermaid topology projections, node types, approval/outward-action points, required tools/skills/MCP/providers/runtimes, relevant Hermes cron schedules, and blocking compatibility findings. Inspection makes no model or network call and never reveals full prompts, secrets, or resolved secret values.
2. **Run portfolio:** `hermes workflow runs [--workflow NAME] [--status STATE] [--limit N] [--json]`, natural-language status questions, and the desktop Workflows portfolio all list active and recent runs from one bounded summary contract. The desktop groups cards into `Queued`, `Active`, `Needs attention`, `Completed`, and `Failed / stopped`, while preserving the exact runtime state as a badge.
3. **Run detail:** `status RUN_ID [--json]`, `events RUN_ID [--tail N] [--json]`, and the desktop run inspector expose node state, attempts, dependencies, retry timing, semantic progress, pending interactions, sanitized errors, verification evidence, artifacts, and next actions. The desktop run board groups nodes into `Waiting`, `Ready / starting`, `Active / retrying`, `Needs attention`, `Done`, and `Failed / stopped`; exact node states remain visible.
4. **Actions:** `run`, `approve`, `reject`, `provide-input`, `resume`, `retry`, `reconcile`, `cancel`, `abandon`, `cleanup`, and `reset-sessions` operate through typed compare-and-set runtime APIs. `cleanup` has `--dry-run`; destructive or outward actions retain existing confirmation and approval policy. The workflow board never offers arbitrary drag-and-drop status mutation.

Natural-language examples such as “What workflows can I run?”, “What does supplier review do?”, “Which workflows are running?”, “Show workflows waiting for approval”, “How far is run X?”, “Why did it fail?”, “What is it waiting on?”, and “What happens next?” invoke the corresponding read-only JSON contract and summarize it. Explicit `/workflow ...` remains the deterministic escape hatch.

Catalog and run output is profile-scoped. A local CLI may inspect that profile's runs. Chat and gateway requests additionally default to the current authenticated conversation/user scope; supplying a run ID does not bypass authorization. Cross-profile or cross-user run enumeration is never exposed by the skill or desktop API. The desktop always labels its active profile and filters workflow runs accordingly. Hermes Kanban remains machine-shared and labels its selected physical board separately so the two scopes are not confused.

The stable run summary contract contains `action`, `run_id`, `workflow`, `workflow_version`, `trigger`, `admission_disposition`, `concurrency_key`, `queue_position`, `blocked_by_run_id`, `status`, `started_at`, `updated_at`, `elapsed_ms`, `current_nodes`, `progress`, `attempts`, `next_retry_at`, `pending_interaction`, `health`, `last_error`, `artifacts`, `warnings`, and `next_actions`. `health` distinguishes healthy activity, semantic idle, stale ownership, retry wait, user wait, and unknown-side-effect reconciliation without inventing a new lifecycle state. Run states are `queued`, `running`, `waiting_retry`, `paused`, `interrupted`, `succeeded`, `failed`, `cancelled`, or `abandoned`. Node states are `pending`, `ready`, `claimed`, `running`, `waiting_retry`, `paused`, `succeeded`, `failed`, `skipped`, `cancelled`, or `interrupted`.

#### Questions the board answers—and the inspector must answer

The board is an operational index, not the complete diagnostic surface:

| Operator question | Board answer | Required detail source |
|---|---|---|
| Is anything stuck, failed, or waiting for me? | Health badge and `Needs attention` / `Failed` placement | Attention reason, semantic-idle age, typed error, owner, and next action |
| Where are we and are we done? | Run column, node counts, active node cards | Topology plus completed/skipped/remaining nodes and exact terminal state |
| What is it waiting on? | Waiting/attention badge | Blocking dependency, scheduled retry time, required input, approval prompt, provider recovery, or resource limit |
| Why did it fail and will it retry? | Failed/retrying badge | Sanitized event timeline, attempt history, error classification, retry budget, and next retry time |
| Is a `running` node actually making progress? | Last-progress age and health indicator | Semantic progress events, process/lease health, current tool or child-agent phase, and idle/wall deadlines |
| What happened inside a long node or loop? | Iteration/child count summary | Bounded nested progress, loop iteration history, child-agent outcomes, and attempt lineage |
| What did it produce or change? | Artifact and output counts | Artifact links, output summaries/digests, verification results, and outward-action receipts |
| Is it safe to resume or retry? | Reconciliation-required warning | Unknown-side-effect evidence and an explicit operator reconcile decision |
| What happens next and when? | Next-action and next-scheduled-time summary | Next runnable nodes, unsatisfied dependencies, persisted backoff, schedule source, and policy blockers |
| When will the whole run finish? | No unsupported completion ETA | Elapsed time, hard deadlines, next retry/schedule time, and an explicit `estimate_unavailable` unless a future evidence-backed estimator exists |
| Are we approaching a limit? | Budget/resource warning | Attempts, iterations, model/tool budget, artifact/storage quota, wall deadline, and remaining bounded capacity without exposing billing secrets |
| Which definition and inputs are running? | Workflow/version/trigger labels | Immutable definition digest, sanitized input manifest, profile, conversation scope, and schedule/manual origin |

Node-count progress is explicitly labeled as graph progress, not elapsed-time or completion-time percentage. A workflow with one long AI node at `1/2` is not presented as “50% done.” The UI shows indeterminate activity plus last semantic progress when duration cannot be estimated honestly. Completion ETA is omitted unless a separately designed estimator can prove an evidence-backed confidence contract; this milestone does not extrapolate one from node counts.

#### Desktop board and inspector architecture

The native desktop adds two durable product pages rather than embedding the web dashboard:

- **Workflows:** portfolio board, selected-run node board, attention inbox, topology toggle, and run inspector/timeline. The workflow `RunStore` is the only source of workflow lifecycle truth.
- **Kanban:** native view of existing physical Kanban boards, tasks, profile lanes, comments, and safe lifecycle actions. `kanban_db` remains the only source of Kanban lifecycle truth. Physical boards remain project/repository/domain queues; no board is created per workflow definition or run.

Both pages use a desktop-owned `ActivityBoard` presentation component only after two source adapters exist. The shared component owns layout, keyboard navigation, column/card virtualization, loading/empty/error/stale states, and responsive behavior. Adapters own status grouping, card details, permissions, mutations, and source-specific terminology. No dashboard JavaScript/CSS bundle or dashboard plugin SDK is imported into Desktop.

The workflow adapter is read-model driven. It may expose only typed operations supported by the current state and `next_actions`; it cannot manufacture a transition. The Kanban adapter may expose drag/drop only after the backend accepts an expected revision/status/run identity and rejects stale mutations with `409 Conflict`. Optimistic UI always rolls back and reloads the affected snapshot after a conflict.

The desktop backend already hosts authenticated plugin REST APIs in headless mode. The workflow plugin adds bounded, profile-authorized endpoints for catalog summaries, run snapshots, sanitized event deltas, attention items, and lifecycle actions. Kanban retains its existing plugin API and adds a small cursor-based REST delta endpoint plus optional compare-and-set mutation fields so Desktop does not require a new generic plugin-WebSocket bridge. Each snapshot carries a monotonic revision/event cursor; delta gaps, schema-version mismatch, backend restart, profile/board switch, or reconnect trigger a complete bounded snapshot reload.

Only the visible page polls or long-polls. Requests and returned cards/events are bounded and paginated; large columns virtualize; background windows stop refresh; cosmetic progress events may coalesce, but terminal, failure, retry, and attention transitions flush immediately. The renderer closing does not stop a workflow. Backend shutdown follows the workflow coordinator's normal bounded interruption and process-tree cleanup contract.

The Workflows page does not become an authoring editor. Topology remains the deterministic text/Mermaid projection, and changing dependencies or node definitions remains a workflow-builder/YAML operation. A future explicitly declared Kanban-delegation node may create one durable Kanban task with an idempotency key and persist the task ID in workflow state, but it requires a separate design for cancellation and outcome reconciliation and is not part of the first milestone.

#### User-facing costs and mitigations

The native board adds navigation, status vocabulary, and another place to inspect work. To prevent confusion and notification fatigue:

- workflow and Kanban pages use distinct names, scope labels, and action vocabularies even though their cards share visual primitives;
- exact states and last-updated timestamps are never replaced by broad column names;
- stale/disconnected data is visibly marked and mutations are disabled until reconciliation;
- notifications fire on transitions into user-actionable attention or terminal failure, not on every node event, and are deduplicated by run plus interaction/error generation;
- filters default to active and attention-requiring work, with completed history available on demand;
- board, list, topology, and inspector views remain keyboard accessible and usable on a laptop-width viewport; and
- absence or disablement of either plugin degrades that page to an actionable enable/setup state without breaking chat.

#### Complexity and maintainability costs

This enhancement adds a second maintained visual surface for Kanban and a new visual surface for workflows. The main costs are deliberate and bounded:

- **Feature-parity drift:** the existing web-dashboard Kanban UI may gain controls before Desktop. The native first slice therefore promises operational visibility and safe actions, not automatic feature-for-feature parity. Shared backend contracts and contract tests prevent lifecycle behavior from drifting even when presentation differs.
- **Leaky shared abstraction:** workflow and Kanban cards look similar but have different semantics. `ActivityBoard` contains only layout/accessibility/virtualization concerns; adapters and inspectors remain source-owned. A second source is required before a behavior enters the shared component.
- **Event/API versioning:** long-lived or remote Desktop clients may reconnect after backend upgrades. Snapshots and cursors carry schema versions; unknown versions disable mutation and force a compatible reload/error instead of guessing.
- **More upstream overlap:** route/navigation/controller/locale files plus the generic Kanban persistence and REST modules become customization points. Kanban CAS, REST, shared presentation, adapters, and shell composition are isolated in separate commits and ledger entries, with no workflow code added to agent core or the permanent model-tool schema.
- **Resource use:** boards can create battery, network, and rendering pressure on a laptop. Visible-page-only bounded long polling, pagination, virtualization, generation guards, and teardown tests are release requirements rather than later optimizations.
- **Testing surface:** local/remote authentication, stale writes, event gaps, accessibility, four locales, and both source adapters increase test volume. Each layer has contract tests so failures identify API, adapter, shared presentation, or shell integration separately.

If implementation evidence shows the shared presentation layer requires source-specific condition ladders or that reliable remote updates require a broad new Electron/plugin transport, implementation pauses for a design review rather than accepting hidden coupling.

#### Cross-surface topology contract

`show --json` always returns both renderer-neutral fields for workflows within the visualization limits:

```json
{
  "topology_text": "collect -> [security, commercial] -> approval -> send",
  "topology_mermaid": "flowchart LR\n  n0[\"collect (command)\"]\n  n1[\"security (prompt)\"]\n  n2[\"commercial (prompt)\"]\n  n3[\"approval (approval)\"]\n  n4[\"send (command)\"]\n  n0 --> n1\n  n0 --> n2\n  n1 --> n3\n  n2 --> n3\n  n3 --> n4",
  "topology_warnings": []
}
```

Both projections are generated deterministically from the same validated, normalized DAG; neither is authored by the model or accepted as executable Mermaid from workflow YAML. Nodes use generated aliases (`n0`, `n1`, …), bounded sanitized labels containing only node ID/type display text, and sorted edges. Control/ANSI characters are rejected at schema validation; the projection replaces label characters outside Unicode letters/numbers and ` -_.:/()` with a safe replacement before emission. The generator emits only `flowchart LR`, node declarations, and directed edges: Mermaid initialization directives, raw HTML, links, click handlers, styles, classes, and arbitrary directives are forbidden.

Human CLI output accepts `show NAME --topology text|mermaid|both`; the default is `text`. `--json` always returns both fields and cannot be combined with an explicitly supplied human-output selector. `topology_text` is the portable and accessibility representation. Its UTF-8 encoding is bounded to 12 × 1,024 bytes and may end with a deterministic truncation summary. Mermaid generation is available for at most 100 nodes, 200 edges, and 64 × 1,024 UTF-8 bytes of source; node display labels are truncated deterministically to 80 Unicode code points including the ellipsis. Above a graph/source limit, `topology_mermaid` is `null`, `topology_text` remains available, and `topology_warnings` contains stable issue codes.

Surface selection is explicit:

- **Classic CLI:** use `topology_text`. Its optional Rich Markdown renderer does not execute Mermaid.
- **Ink TUI and dashboard-embedded TUI:** use `topology_text`. Fenced Mermaid is otherwise displayed only as source code.
- **Desktop chat:** include `topology_text` as the accessible/copyable summary and wrap `topology_mermaid` in a fenced `mermaid` block. The existing lazy Mermaid renderer shows source while streaming, renders the diagram after completion, and falls back to source on parse failure.
- **Desktop Workflows page:** render the same bounded topology source beside the operational board and inspector; retain `topology_text` as the accessible/copyable fallback and never accept workflow-authored Mermaid directives.
- **Unknown or messaging surfaces:** use `topology_text` unless that adapter has an explicitly tested Mermaid capability. Generic Markdown support alone does not imply Mermaid support.

This adds no Mermaid package to Python, core Hermes, CLI, or TUI. The workflow plugin only generates bounded Mermaid source; graphical rendering reuses the existing desktop capability.

### 4. Scheduling

Scheduled admission uses Hermes cron rather than a second cron subsystem. A
cron job attaches the `workflow` skill and a self-contained run instruction.
Existing cron provider/model/toolset/workdir/delivery fields remain
authoritative for the outer job; workflow-level node overrides apply inside the
run. Cron records truthful trigger provenance and a stable source-scoped
idempotency identity, then submits background work only when a fresh workflow
coordinator is available.

Execution scheduling, queued promotion, due retries, post-interaction
continuation, stranded recovery, and stall detection belong to the workflow
plugin's elected coordinator. Web/Desktop and Gateway host that coordinator
through the generic plugin lifecycle; neither base host imports workflow code.
If no healthy coordinator exists, background admission is rejected before a
run is created. An explicitly supported foreground command remains available
to a caller that chooses it.

An approval gate ends the current cron firing cleanly after delivering the run
ID and review instructions. A later user decision commits a durable wake;
the coordinator resumes the workflow outside the HTTP, chat, or cron request.
Cron does not wait while holding a worker.

## Worker Lifecycle, Deadlines, Shutdown, and Resource Exhaustion

Archon's AI `idle_timeout` and deterministic-node `timeout` remain compatible fields, but they are not the whole lifecycle contract. The runtime distinguishes:

- **Semantic idle timeout:** no model, tool, child-agent, or bounded progress frame. Archon's default AI value maps to 300 seconds. A lease heartbeat proves process ownership only and never resets semantic idle time.
- **Hard node wall deadline:** an absolute cap on the complete attempt, including provider calls, tools, MCP startup, hooks, and child agents. It defaults to 1,800 seconds for AI attempts when no stricter sidecar/config value exists.
- **Provider request timeout:** the cap for one model request and its response stream; it is always no greater than the remaining node wall time.
- **Deterministic process timeout:** Archon's bash/script default maps to 120 seconds unless the workflow specifies a bounded value.
- **Parent wait deadline:** a parent AI node may wait for declared child agents only within its remaining hard deadline. The effective child deadline is the minimum of its requested limit, the remaining parent deadline, and the workflow global cap. Inline `agents.maxTurns` is an iteration limit, not a timeout.
- **Shutdown grace:** a short configurable cooperative-cancel interval followed by TERM and KILL grace intervals. `None` never means unbounded; omitted values resolve to safe defaults before spawn.

Every worker follows one state machine:

```text
spawn requested
  -> process identity recorded (PID + start identity + group/job)
  -> running/monitored
  -> cooperative cancel requested
  -> grace expires: terminate process tree
  -> grace expires: kill process tree
  -> wait/reap
  -> durable succeeded | failed | cancelled | interrupted
```

The coordinator owns every worker and descendant group. The IPC channel includes a coordinator lifeline: EOF or parent loss causes the worker to cancel its local model/tool work, terminate its descendants, and exit. The coordinator still performs process-tree cleanup, so either side can finish cleanup after the other fails. PID reuse is rejected using the recorded process start identity.

On a workflow coordinator host or explicit foreground executor shutdown, the
runtime stops admitting new nodes, persists a shutdown event, cooperatively
cancels active attempts, escalates and reaps process trees it can identify,
marks incomplete attempts `interrupted` or `reconciliation_required`, releases
safe leases, and exits within a bounded shutdown deadline. A live process or
uncertain outward effect is never made replayable merely because its lease
expired. Closing only a renderer while its Hermes backend remains alive does
not stop the workflow. The first milestone does not create an independently
detached workflow daemon.

After forced termination, laptop power loss, or OS kill, restart reconciliation
detects stale leases/process identities. A proven stopped replay-safe attempt
becomes `interrupted`; an uncertain outward effect requires reconciliation. A
suspend/wake or wall-clock jump is detected from the monotonic/UTC heartbeat
gap. If the same newest process/attempt identity is still live and fencing is
intact, the current leader reclaims its monitoring lease instead of terminating
or replaying it. Unknown external-side-effect outcomes pause for operator
reconciliation before retry.

Retries have one combined attempt budget across provider-level and workflow-level retry layers so nested retries cannot multiply unexpectedly. Fatal authentication, authorization, credit, validation, cancellation, and unknown-side-effect errors do not retry. Transient backoff is persisted and interruptible; a sleeping retry holds neither a worker nor a scheduler slot.

Process-tree resident memory, CPU time where the platform can enforce or measure it, descendant count, open descriptors/handles, output bytes, artifact bytes, event-journal bytes, total run storage, and retention are bounded. A global workflow-worker limit applies across all simultaneous runs, not independently per run. Admission also checks a configured free-disk watermark before creating snapshots or workers. Exceeding a limit terminates the attempt with a typed resource error and sanitized diagnostic. Unsupported hard enforcement is reported by `doctor`; it is never silently presented as active protection. Release tests cover repeated spawn/cancel/timeout cycles and assert no upward process, thread, descriptor/handle, memory, or disk-growth trend after cleanup.

## Run State and Concurrency

### Admission and duplicate-trigger identity

`RunStore.start_run` is the only run-creation path. It atomically records the trigger source, idempotency key, start digest, concurrency key/policy, admission disposition, and run ID before scheduling any node. Chat, `/workflow`, CLI, Desktop, API, and cron all call this contract; none may create a run directory directly. A duplicate delivery returns `existing`, an intentional queued overlap returns `queued`, and a capacity/policy refusal returns a stable non-billable diagnostic. Queued runs are durable state only: they own no agent, process, thread, retry timer, MCP server, or scheduler slot.

The production amendment separates a workflow's lifecycle status from execution-
lane ownership. Persisted retry wait always releases its lane. Under `queue`, a
run paused for approval/input holds the lane by default to preserve strict
serialization; a digest-bound sidecar may explicitly opt into interleaving by
releasing at gates. A safely interrupted run may release, but an interrupted
run with an unresolved outward-classified attempt holds the lane until
reconciliation or abandon. When a released run becomes runnable again it enters
the durable fair queue; it does not bypass another lane owner. Paused/retry/
interrupted quotas still bound retained nonterminal state. Duplicate promotion
and wake processing are idempotent.

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

### Cancellation and terminal-state races

Cancellation is an idempotent compare-and-set transition, not a best-effort signal detached from state. The race contract is deterministic:

- if durable completion commits first, a later cancel reports the already-terminal result;
- if cancellation commits first, admission stops, queued/backoff/paused work becomes cancelled, active process trees are terminated/reaped, and every late success is rejected as stale;
- cancel during persisted retry removes the due wake-up without starting a worker;
- cancel during a parent/child operation covers the coordinator's complete registered descendant set;
- cancel during an outward action whose response was lost records `reconciliation_required` rather than claiming success, cancellation, or retry safety; and
- shutdown or coordinator failure during cancellation is resumed by restart reconciliation using the same process identities and claims.

A process in an uninterruptible operating-system state may not exit immediately even after the runtime requests a forced kill. The runtime records `cleanup_failed`, blocks related new work, surfaces the owned identity and recovery action, and continues reconciliation; it never reports that a process was reaped without observing its exit.

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
- Start idempotency, per-workflow overlap policy, profile-wide executing/queued/paused/nonterminal run limits, a global worker-process limit, start-rate admission, CPU accounting, and a free-disk watermark are enforced before new work is admitted.
- Trust records are profile-owned and digest-bound. Imported/external packages cannot execute locally until the user approves the risk summary; package YAML and sidecars cannot grant trust.
- Resource controls are documented as accident and availability protection, not as a security boundary for malicious user-privileged shell code. Untrusted execution requires an advertised isolated Hermes execution backend.
- `config.yaml` owns behavioral settings; no new non-secret `HERMES_*` user configuration is introduced.
- Cancellation terminates worker process groups and always releases leases and MCP sessions.

## Performance Requirements

- Listing, showing, and validating workflows performs no network or model calls.
- Text and Mermaid topology generation is deterministic and linear in nodes plus edges; it invokes no Markdown/Mermaid parser or renderer in the workflow plugin.
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
- Worker crash, coordinator loss, forced application shutdown, laptop suspend/wake, host restart, duplicate start delivery, and duplicate scheduler invocation converge through admission identity, process identity, leases, lifelines, and compare-and-set completion.
- Provider disconnects, stalled response streams, and model failures are bounded by the provider-request, semantic-idle, and wall deadlines; cleanup does not depend on the provider returning.
- An owning-process shutdown leaves no live workflow-owned worker or descendant. A hard crash is reconciled to `interrupted` on restart, never inferred as success.
- Backoff persists `next_retry_at`, consumes no worker while waiting, and obeys one combined retry budget across nested provider/workflow layers.
- Resource-limit failures are typed, journaled, sanitized, and recoverable; cleanup/retention bounds disk growth from artifacts, output, events, and abandoned runs.
- Approval and cancellation are idempotent.
- Completion-versus-cancel, retry-wakeup-versus-cancel, approval-versus-cancel, and cleanup-versus-reader races have an explicit first-committed-transition contract and reject stale writers.
- Resume reuses completed nodes unless `always_run` is set.
- Workflow source changes affect new runs only.

## Packaging and Ericsson Conversion

Generic runtime code and skills are shared by OTTO and LOOP24. Ericsson packages contain only Ericsson workflows, commands, scripts, MCP references, sidecars, and required capability metadata.

The existing `my-tickets-summary` and `inbox-digest` files will be replaced with Archon-shaped workflows. Long prompts move to neutral package-local `commands/` resources. Current custom fields such as required environment values, reports, and side-effect flags move to capability configuration or Hermes sidecars.

Capability staging will copy complete workflow packages atomically instead of copying isolated YAML files. Brand branches select the shared capability through descriptors; workflow runtime code is not duplicated into branded branches.

Generic showcase packages are plugin-owned read-only resources, not profile/capability copies. Wheel and sdist metadata include only `plugins/workflow/showcases/**`; runtime lookup uses `importlib.resources` so sealed installs do not depend on the source checkout. Packaging tests build both artifact forms and assert every catalog/digest/package/fixture/script/command/MCP/sidecar byte is present at its original relative path.

## Upstream Merge Discipline

The branch flow remains:

```text
Hermes upstream/main → local main mirror → base → otto / loop24
```

Rules for this milestone:

1. Prefer additive files under `plugins/workflow`, skills, capabilities, tests, and docs.
2. Core changes must be generic, contain no Ericsson or brand naming, and be viable as an upstream Hermes contribution.
3. Every modified upstream-owned file is recorded in `docs/upstream-customizations/workflow-orchestration.yaml` with rationale, owner, tests, upstreamability, merge guidance, and removal condition.
4. The ledger also records change class, owned symbols/contracts, the last verified upstream commit, invariant tests, expected commit boundary, and whether an upstream replacement is known. A validation script checks that recorded files/tests exist, entries remain internally consistent, and every upstream-owned file in the feature diff is covered.
5. Core-seam commits remain separate from workflow-plugin commits so they can be rebased, submitted upstream, replaced, or dropped independently.
6. The generic Kanban CAS change remains a separate commit from its REST adapter and Desktop consumer, so an upstream equivalent can replace the persistence seam without removing the UI.
7. Before a real `main` to `base` merge, the external merge skill compares the incoming upstream range with every ledger-owned file and symbol. A ledger-owned conflict or possible upstream-equivalent implementation is a mandatory human reconciliation point; blanket whole-file `ours`/`theirs` resolution is forbidden.
8. After reconciliation, the skill runs the ledger entry's invariant tests plus the focused offline workflow gate before committing `base` or propagating to a brand. Passing text merge alone is never accepted as proof that the customization survived.
9. The full temporary-worktree rehearsal is a CI/release or explicitly requested preflight gate; it is not invoked recursively from inside the real merge. It records upstream/base/brand commit identities, overlap classification, resolutions, and test results as release evidence.
10. If upstream later provides an equivalent API, the customization entry requires an explicit replace-or-remove decision and the local commit is removed or adapted before brand propagation; parallel implementations are not retained.
11. The repository exposes a stable checker, focused smoke command, and merge-rehearsal command. The owning skill is `/Users/coreyellis/code/github.com/cmetech/otto_hermes/.claude/skills/otto-upstream-merge/SKILL.md`; it remains outside this repository and is not copied into Hermes.

### Planned upstream and desktop touch budget

The 2026-07-18 production amendment additionally authorizes the following
generic lifecycle changes. This is an explicit expansion of the earlier
three-file agent-core budget, not an implicit workflow exception:

- new `hermes_cli/plugin_services.py`: generic blocking background-service
  protocol, host supervisor, health snapshots, bounded shutdown, and
  generation-safe reload;
- `hermes_cli/plugins.py`: attributed background-service registration and
  force-reload interlock;
- `hermes_cli/web_server.py`: generic `web` service hosting in FastAPI lifespan;
- `gateway/run.py`: generic `gateway` service hosting and shutdown ordering;
- focused generic lifecycle tests for each boundary.

The concrete workflow registration proves this surface is not speculative.
None of these files may import workflow modules, `RunScheduler`, or receive an
`AIAgent`, conversation history, prompts, tools, provider credentials, or model
context. Each change is separately ledgered with the tests and removal
condition specified in the focused lifecycle design. An upstream-equivalent
API removes the local customization after the workflow plugin migrates to it.

The implementation may modify only three existing upstream-owned **agent-core** files without a design amendment:

- `tools/registry.py`: generic, reversible scoped-name discovery/dispatch view used only inside an isolated worker.
- `hermes_cli/plugins.py`: lazy `PluginContext.agent` facade, parallel to the existing `PluginContext.llm` facade.
- `tools/process_registry.py`: consume a generic managed-process-tree primitive while preserving existing terminal background-process behavior.

`agent/plugin_agent.py`, `agent/plugin_agent_worker.py`, and `tools/managed_process.py` are new generic modules intended for upstream contribution. `tools/managed_process.py` extracts the reusable identity-guard, cross-platform process-tree termination, escalation, and reaping behavior already present in `tools/process_registry.py`; workflow code consumes that public primitive rather than importing private process-registry internals or implementing a second supervisor. The workflow runtime itself does not modify `run_agent.py`, `model_tools.py`, `agent/agent_init.py`, `agent/tool_executor.py`, or `agent/agent_runtime_helpers.py`. If implementation evidence proves another core touch unavoidable, work pauses for an explicit design/ledger update rather than expanding the fork silently.

This design amendment authorizes additive desktop feature directories plus narrow composition changes in the existing Desktop route registry, sidebar navigation, controller/lazy-page composition, shared API types/client, and locale catalogs. It also authorizes one generic optional-precondition/CAS change in `hermes_cli/kanban_db.py` and bounded cursor/revision fields in `plugins/kanban/dashboard/plugin_api.py`. The database change checks expected status, current run ID, and event revision in the same SQLite write transaction as the lifecycle mutation; the API adapter never implements a second mutation path. Reclaim first commits an exact claim/run transition and captures its process identity, then signals that identity after releasing SQLite and records termination evidence separately, so a stale request cannot signal a newer worker and no database transaction is held during OS process control. These are upstream-owned Kanban/product-surface changes, not additions to the model tool schema or agent core. Each existing upstream-owned file is listed separately in the customization ledger, and the Kanban persistence, Kanban REST, shared presentation, workflow adapter, Kanban adapter, and shell integration remain separate commits so an upstream equivalent can replace one layer without retaining both.

The showcase additionally authorizes narrow package-data entries in `pyproject.toml` and `MANIFEST.in` for `plugins/workflow/showcases/**`, plus the matching packaging regression test. These are separately ledgered packaging UNION changes, contain no workflow execution logic, and must not broaden to an unconstrained `plugins/**/*` asset glob without measuring artifact size and reviewing unintended files.

The ledger checker first intersects upstream-changed paths with the files explicitly owned by each ledger entry. It classifies the resulting bounded overlap as `none`, `same_file`, or `owned_symbol`; it does not scan unrelated repository paths or generate `possible_upstream_equivalent`. For every non-`none` entry it prints the rationale, owned contracts, merge guidance, removal condition, and exact invariant tests. `remove-as-upstream-equivalent` remains available only as an explicit human decision, not an inferred classification. The merge procedure preserves a pre-merge customization patch/evidence reference until post-merge tests pass, uses the declared invariant and base gates to catch compatibility failures outside owned files, refuses to advance `last_verified_upstream` while conflicts/tests remain unresolved, and verifies that branded branches contain the tested `base` commit plus generated overlays rather than divergent copies of generic runtime code.

No desktop plugin framework, dashboard-bundle compatibility shim, generic plugin WebSocket bridge, workflow-specific Electron preload method, or second chat surface is authorized. The first implementation uses the existing authenticated REST bridge and backend plugin route mounting. Any evidence that these are insufficient requires a design and ledger amendment before widening the core/desktop seam.

## Testing Strategy

### Unit

- Schema one-of rules and every supported field.
- Variable substitution and path containment.
- Trigger rules, conditions, retry classification, backoff bounds, and loop termination.
- State projection, journal validation, compare-and-set transitions, and lease expiry.
- Digest-bound package trust, immutable input snapshots, idempotent start admission, overlap policies, and profile/global capacity bounds.
- Tool filtering, skill scoping, structured output validation, and compatibility diagnostics.
- Deterministic text/Mermaid graph equivalence, escaping, directive rejection, exact size limits, and text fallback.
- Workflow/Kanban adapter state grouping, exact-state preservation, allowed-action derivation, cursor-gap recovery, stale-write rollback, attention deduplication, and honest graph-progress labeling.
- Activity-board keyboard navigation, focus retention, laptop-width behavior, bounded virtualization, and loading/empty/error/stale states.

### Concurrency and fault injection

- Multiple processes racing to deliver the same start key, intentionally start overlapping runs, claim, complete, approve, reject, resume, retry, and cancel the same run.
- Completion-versus-cancel, retry-wakeup-versus-cancel, approval-versus-cancel, admission-versus-shutdown, and cleanup-versus-reader race matrices.
- Stale attempt completion after a newer lease is active.
- Crash between journal append and projection replacement.
- Lock contention and timeout on POSIX and simulated Windows locking.
- Cancellation during AI, shell, script, hook, and MCP execution.
- Coordinator death/IPC EOF, shutdown during every worker lifecycle phase, parent/child deadline inheritance, TERM/KILL escalation, PID reuse, and unconditional wait/reap.
- Provider stalls/disconnects, suspend/wake gaps, retry-budget exhaustion, process kill refusal/uninterruptible-state simulation, global worker/admission exhaustion, low-disk refusal, resource-limit termination, and repeated cleanup/retention cycles.

### Integration

- Real plugin discovery and `hermes workflow` command registration.
- Real `AIAgent` construction against a deterministic fake provider with actual tool dispatch.
- Explicit external/project/profile discovery precedence.
- Per-node MCP startup and shutdown with a local test server.
- Cron firing with attached workflow skill, delivery, and approval pause.
- Skill command dispatch through CLI, gateway, TUI gateway, and desktop command catalog paths.
- Offline installed showcase discovery, preflight, execution, pause/resume, evidence reporting, and cleanup through the real plugin and RunStore; fake-provider execution of the optional AI scenario in CI.
- Temporary showcase scheduling with unique ownership tags, existing finite-one-shot claim/auto-delete behavior, restart recovery, and reset behavior that cannot remove user cron jobs.
- Surface rendering contract: CLI/TUI/dashboard/unknown use text; desktop keeps text and renders the existing fenced Mermaid path with strict security and source fallback.
- Headless Desktop backend plugin APIs through the existing authenticated REST bridge in local token, remote token, and remote OAuth modes.
- Workflow portfolio/run board, attention inbox, inspector/timeline, topology, artifacts, and typed lifecycle actions against the same `RunStore` projection as CLI and chat.
- Native Kanban board against `kanban_db`, including physical-board switching, event cursor recovery, comments, profile lanes, and same-transaction compare-and-set rejection of stale lifecycle mutations without changing dispatcher ownership.

### End-to-end

- Run an unmodified portable Archon fixture containing command, prompt, bash, script, parallel join, structured output, loop, approval, and cancel paths.
- Interrupt and resume the fixture without repeating completed work.
- From an installed distribution with no provider credentials or network, use both CLI and the guided skill contract to explain and run the Laptop Diagnostic Tour, supply its required input, observe parallel/artifact/condition/loop behavior, reject and rework once, approve, and verify its evidence report against durable events and artifacts.
- Run the safe resilience modes and assert bounded retry/timeout/cancel outcomes plus zero owned descendants, then prove that every destructive scenario remains absent from the production showcase catalog.
- Run the optional AI/extension scenario with a deterministic fake provider and local MCP in CI; on an otherwise identical provider-free installation, assert the scenario is reported as skipped while the offline suite remains successful.
- Schedule and clean up the temporary deterministic showcase without touching an unrelated user schedule, including restart between schedule creation and first admission.
- Execute converted Ericsson fixtures with fake Jira and Outlook services.
- Drive active, retrying, attention, failed, interrupted, cancelled, abandoned, and succeeded runs through CLI, chat, and Desktop; assert board, inspector, topology, and event answers agree after reconnect and backend restart.
- Load large workflow and Kanban boards under a bounded event stream and assert no unbounded rendering, polling, request, memory, focus, or notification growth.
- Merge-rehearsal test from the latest upstream mirror through `base`, including ledger-owned non-overlap, same-file overlap, owned-symbol conflict, and explicit human removal-decision propagation; then validate OTTO and LOOP24 ancestry, brand generation, and focused test suites.

## Acceptance Criteria

1. An Archon workflow package can be copied into a project and validated without YAML rewriting.
2. `/workflow run`, `hermes workflow run`, and cron all execute through the same durable runtime; `list`, `show`, `runs`, `status`, and sanitized `events` inspect the same catalog/store from natural language, slash commands, and CLI. `show` exposes matching bounded text/Mermaid projections, with text on every surface and graphical Mermaid only on explicitly supported surfaces.
3. Fresh contexts do not mutate the parent conversation; shared contexts resume only the intended node session.
4. Parallel execution is bounded; duplicate start delivery returns one run; intentional overlap follows `queue`, `allow`, or `forbid`; and duplicate schedulers cannot execute the same claim concurrently.
5. Approvals survive process restart and accept only one winning decision.
6. Structured outputs fail closed when invalid.
7. Tool, skill, hook, and MCP scopes are enforced at runtime rather than documented only.
8. Interrupted runs resume without repeating completed nodes or silently repeating unknown side effects.
9. Existing Ericsson workflows are Archon-shaped and pass portable package validation.
10. Workflow RunStore and Kanban DB remain independent lifecycle authorities; no ordinary workflow node is mirrored into Kanban and no workflow transition is produced by dragging a card.
11. Desktop provides a workflow portfolio, per-run node board, attention inbox, topology, and diagnostic inspector that answer health, position, completion, user-attention, wait reason, failure/retry, semantic progress, artifacts, and next-action questions from the same projection used by CLI and chat.
12. Desktop provides a native Kanban page backed by the existing Kanban plugin API and physical board model; workflow and Kanban share only presentation primitives and remain clearly scoped/labeled.
13. Existing upstream agent-core modifications remain limited to `tools/registry.py`, `hermes_cli/plugins.py`, and `tools/process_registry.py`; `hermes_cli/kanban_db.py`, the Kanban API, and Desktop composition files are separately classified and ledgered; and all generic runner/process, Kanban CAS, Desktop, and plugin-API touches are covered by overlap-aware merge-rehearsal tests.
14. An owning Hermes shutdown leaves no live workflow-owned worker/descendant, restart reconciliation never guesses success, and repeated timeout/cancel/provider-failure cycles show no process, thread, descriptor/handle, memory, or disk-growth leak.
15. Imported/external packages cannot execute locally without a digest-bound trust decision; changing any executable resource invalidates trust; and untrusted execution fails closed unless a configured isolated Hermes backend advertises the required capability.
16. The merge procedure never resolves a ledger-owned file with blanket whole-file `ours`/`theirs`, never advances the verified-upstream baseline before entry-specific invariant tests pass, and never propagates an unverified `base` commit into a brand.
17. Focused unit, concurrency, integration, accessibility, security, performance, and end-to-end gates pass on Linux, macOS, and Windows-supported paths.
18. An installed offline production showcase can explain, run, pause, resume, verify, report, and clean up the bundled Laptop Diagnostic and resilience scenarios through the normal runtime with no provider credentials, network, external integrations, live machine inventory, or destructive fault injection.
19. Optional AI/extension and scheduling tours are explicit opt-ins; unavailable AI is reported as skipped, temporary schedules cannot collide with or delete user schedules, and every claimed showcase capability is backed by durable runtime evidence rather than catalog metadata alone.
20. Built wheel and sdist artifacts preserve every showcase catalog, digest, YAML, sidecar, fixture, command, script, and local MCP resource at its expected relative path; the production harness never depends on a source checkout.

## Open Questions

No architectural question blocks implementation. Exact mappings for provider-specific Archon fields will be finalized field-by-field in the compatibility table; unsupported fields must remain explicit diagnostics rather than guessed behavior.
