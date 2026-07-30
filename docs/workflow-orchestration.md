# Portable Workflow Orchestration

Hermes loads portable Archon-shaped YAML through the optional `workflow` plugin. Archon defines the compatibility shape only: execution, persistence, workers, approvals, and recovery are Hermes-native. The capability stays at the edge through `hermes workflow ...` and bundled skills; it adds no permanent model-facing core tool.

## Enable and discover workflows

Enable the plugin once for the active profile:

```bash
hermes plugins enable workflow
```

Discovery is deterministic and the first matching workflow name wins:

1. an explicit file or package path supplied to a command;
2. `<working-directory>/.hermes/workflows/`;
3. `<HERMES_HOME>/workflows/`.

A package normally contains `workflows/<name>.yaml`, an optional `workflows/<name>.hermes.yaml` policy sidecar, and referenced `commands/`, `scripts/`, `mcp/`, or fixture resources. Copying an external package into the project or profile directory is an explicit import; Hermes never scans branded or legacy runtime directories. Use a path before installation to inspect it:

```bash
hermes workflow validate ./portable-package/workflows/check.yaml --json
hermes workflow doctor ./portable-package/workflows/check.yaml --compat-report --json
```

`list`, `show`, `validate`, and `doctor` do not contact a model or network service.

## Trust and immutable execution

External executable packages begin untrusted. `doctor` reports the exact package digest, a separately bound risk digest, shell/script use, tools, skills, local MCP processes, providers, outward actions, required secret *names*, execution mode, and effective limits without exposing prompt or secret bodies.

```bash
hermes workflow doctor my-workflow --compat-report --json
hermes workflow trust my-workflow --digest <exact-package-sha256> --json
```

Trust belongs to the active profile and exact executable-resource digest. Editing YAML, a sidecar, command, script, MCP definition, or another covered resource revokes it. Package metadata cannot declare itself trusted. A digest-trusted package may use the local Hermes environment subject to ordinary hardline and approval policy. An untrusted package requires an already configured backend advertising the complete isolation contract; otherwise execution fails closed. CPU, memory, timeout, process, and storage ceilings are availability controls, not a security sandbox.

Distribution-owned workflow packages and showcase bundles are authenticated byte-for-byte. Their repository paths are pinned to LF in `.gitattributes`, and the managed Windows installer disables `core.autocrlf` before the initial checkout. Preserve both controls: checkout-time CRLF conversion changes the authenticated bytes and intentionally makes capability staging fail closed.

Existing managed Git-for-Windows installs created before those controls may retain CRLF bytes for unchanged authenticated resources after an update. On a digest mismatch, Hermes repairs only a tracked resource tree whose working copy contains CRLF and whose content has no semantic difference from `HEAD` when end-of-line whitespace is ignored, then reruns the original byte-for-byte verification. If Git reports successful in-place checkout operations without rewriting those legacy bytes, Hermes materializes only that tree's already-verified tracked index entries under a same-volume temporary prefix, validates every output, atomically replaces the corresponding working-tree files, and verifies again. The repair also pins the managed checkout's local `core.autocrlf=false`. A real edit, untracked package content, non-Git install, or failed restoration is never accepted by this recovery path and continues to fail closed.

Capability startup isolates that package-authentication failure from independent
configuration migrations. An invalid package is never published or trusted, but
the bundled workflow plugin activation and missing MCP defaults are still seeded.
This lets an in-place update repair CLI/MCP availability without weakening the
digest boundary or requiring a clean reinstall.

At admission, Hermes snapshots the resolved definition, sidecar policy, command resources, and input manifest into the run directory. Workers read that immutable snapshot. Later source edits affect only later admissions; resume and recovery never silently switch definitions or inputs.

## Start and inspect runs

```bash
hermes workflow list --json
hermes workflow show my-workflow --topology both
hermes workflow run my-workflow --arguments "bounded input" --json
hermes workflow run my-workflow --idempotency-key source-event-42 --no-wait --json
hermes workflow runs --workflow my-workflow --limit 25 --json
hermes workflow status <run-id> --json
hermes workflow events <run-id> --tail 100 --json
```

An idempotency key represents one logical source delivery. Replaying it returns the existing admission rather than creating another run. The policy sidecar chooses matching-run overlap behavior:

- `queue`: preserve the new admission and start it when its blocker is terminal;
- `allow`: let independently admitted runs execute within global capacity;
- `forbid`: refuse a new overlapping admission with a typed reason.

Admission also enforces profile-wide executing, queued, paused, and nonterminal counts, start rate, global worker capacity, storage quota, and a free-disk watermark before allocating work. A queue/backoff/user wait holds no worker.

`status --json` reports immutable definition/input/policy/trigger identity, exact run and node states, current nodes, attempts, health, retry time, pending interaction, bounded graph progress, capacity-related diagnostics, verified artifact metadata, and valid next actions. Graph progress is completed nodes over total nodes; it is not an elapsed-time estimate. Hermes deliberately reports no completion ETA from node counts.

### Status and health

| State or health | Operator meaning |
| --- | --- |
| `queued` | Admitted but waiting for overlap or execution capacity. |
| `running` / `healthy` | Runnable work exists or a node owns a live bounded claim. |
| `waiting_retry` / `retry_wait` | A classified transient failure has a durable next-attempt time; no worker is held. |
| `paused` / `user_wait` | Approval, bounded loop input, or reconciliation is required. Inspect `pending_interaction` and `next_actions`. |
| `interrupted` | Ownership ended without a trustworthy result. Resume only after recovery checks. |
| `failed` | A terminal typed failure exists. Inspect the failing node, attempts, and artifacts before retry or abandon. |
| `succeeded`, `cancelled`, `abandoned` / `terminal` | No more graph execution is scheduled. Retained evidence remains available until cleanup. |

To answer “is there a problem?”, check `health`, `last_error`, and stalled semantic progress. To answer “where is it?”, check `current_nodes`, node states, and topology. To answer “do you need me?”, check `pending_interaction` and `next_actions`. For retries, check the classified error, consumed attempts, and `next_retry_at`. For reconciliation, verify the external outcome before choosing one of the explicit outcomes; an unknown outward-action result is never blindly retried.

## Topology and presentation surfaces

`show --json` always includes `topology_text`, `topology_mermaid`, and `topology_warnings`. Human output accepts `--topology text|mermaid|both`. Text is renderer-neutral and bounded to 12 KiB. Mermaid is emitted only through the strict graph/node/edge grammar at no more than 100 nodes and 200 edges; larger graphs return `null` plus warnings. The Python control plane never initializes Mermaid, a browser, a model, or the network. Desktop renders with Mermaid `securityLevel: "strict"`.

| Surface | Primary workflow path | Fallback when unavailable |
| --- | --- | --- |
| Shell CLI | `hermes workflow ...` | Enable the plugin, then retry the same command. |
| Classic CLI or TUI chat | `/workflow ...` or natural language using the bundled workflow skill | The skill explains the enable command and equivalent CLI operation. |
| Dashboard chat | The embedded Hermes TUI uses the same skill/CLI path | Use the shell CLI; no second dashboard workflow runtime exists. |
| Desktop chat | Curated `/workflow` extension command dispatched through the gateway | Use the native Workflows page or shell CLI. |
| Desktop Workflows page | Native portfolio, run inspector, attention inbox, and safe lifecycle actions | A non-destructive plugin-disabled message points to `hermes plugins enable workflow`. |

The Workflows board summarizes exact RunStore states; selecting a run opens node/timeline/definition/trigger/retry/artifact detail. The attention inbox identifies the run, node, state version, and interaction. Boards are read models, never execution authorities. Hidden windows suspend refresh; visible views use bounded pagination, cursor recovery, profile-scoped caches, and stale-write rejection.

Kanban is a separate native page and a separate lifecycle authority. A Kanban task is not a workflow node, and a workflow action is not a generic card move. Kanban compare-and-set mutations validate expected status, run ID, and event revision in the same SQLite transaction. Machine-shared physical-board selection remains Kanban configuration; switching a profile or board cannot merge Kanban state into RunStore.

## Approvals, input, retry, and recovery

```bash
hermes workflow approve <run-id> --comment "reviewed" --continue --json
hermes workflow reject <run-id> --reason "revise the plan" --continue --json
hermes workflow provide-input <run-id> <interaction-id> '<value>' --expected-version <n> --continue --json
hermes workflow retry <run-id> [node-id] --expected-version <n> --continue --json
hermes workflow resume <run-id> --json
hermes workflow reconcile <run-id> confirmed-succeeded --interaction-id <id> --expected-version <n> --continue --json
hermes workflow cancel <run-id> --json
hermes workflow abandon <run-id> --json
hermes workflow archive <run-id> --expected-version <n> --json
hermes workflow restore <run-id> --expected-version <n> --json
```

Approval/input/reconciliation decisions are compare-and-set transitions. Only one concurrent decision wins. Workflow approval permits graph progression; it does not bypass terminal hardline rules or tool approval policy.

Cancellation races are deterministic: a committed completion wins over a later cancel; a committed cancel rejects late worker success, removes queued/backoff/paused work, and terminates registered descendants. An outward action with an unknown result becomes reconciliation-required. Operating systems can temporarily refuse to reap an uninterruptible process; Hermes records cleanup failure and keeps reconciliation active rather than claiming success.

On renderer exit, the renderer stops its readers but does not silently cancel durable runs. On runtime/coordinator shutdown, admissions stop, active workers receive bounded cooperative/TERM/KILL escalation, and observed exits are reaped. Startup repairs expired claims, interrupted cancellation, suspend/wake gaps, wall-clock jumps, PID reuse, incomplete projections, and orphan identities from durable evidence. Provider DNS, TLS, disconnect, timeout, stalled-stream, and model errors retain typed failure classification; retry policy never converts unknown side effects into success.

## Artifacts, retention, and limits

Large outputs are stored beneath the run directory. Durable state contains only contained relative paths, media types, byte sizes, and SHA-256 digests. Reads revalidate containment, symlink policy, size, and digest. Prompts, reasoning, credentials, sudo values, and raw tool arguments are excluded from plugin-visible operational state.

Terminal status, archive visibility, evidence retention, and destructive cleanup are
separate state machines. The active board shows nonterminal work plus terminal runs
updated within seven UTC days by default. Older terminal runs move to History without
changing their execution state or deleting evidence. Archive is reversible visibility
metadata; restoring an archived run always returns it to History, never execution or
the active board.

Cleanup always begins with a non-destructive impact preview. The preview reports exact
run IDs, evidence kinds, files, bytes, integrity state, blocking claims/readers,
reconciliation and notification dependencies, and a short-lived confirmation token.
Execution requires that exact token; any evidence or safety-state change invalidates
it. Eligible evidence is atomically quarantined and recorded in cleanup history.
Missing, empty, corrupt, or uncertain admission authority never authorizes deletion.

```bash
hermes workflow cleanup --older-than 7d --json
hermes workflow cleanup --older-than 7d --execute \
  --confirmation-token <exact-token-from-preview> --json
hermes workflow reset-sessions my-workflow --scope <scope> --node <node-id> --yes --json
```

Persistent node sessions are reset separately and explicitly. There is no bare
destructive cleanup invocation and no piped confirmation contract.

## Durable notifications

Approval/input waits, failure, stall, completion, cancellation, and
reconciliation-required transitions are durable notification facts in RunStore.
External delivery uses a separate leased outbox: immutable transition identities
deduplicate retries, while failure/stall/retry delivery summaries may coalesce per
run and destination for 60 seconds without erasing individual facts. Human gates,
reconciliation, cancellation, and completion are never coalesced together.

Desktop delivery is leased for 30 seconds to a stable Electron client identity.
The row becomes delivered only after the Electron projection call acknowledges;
a renderer/backend crash before that acknowledgement returns the row to pending
after lease expiry. Dismissing a presentation records only dismissal metadata and
never approves, cancels, archives, or otherwise changes the workflow.

The coordinator owns reconciliation, retry, backoff, and dead-letter policy.
Desktop and future authenticated Gateway return-route adapters are projections,
not authorities. Hermes does not infer a Gateway destination from client claims:
Gateway delivery is created only when admission carries a verified stored return
route. A CLI-only install has neither coordinator nor delivery owner. Foreground
notification facts remain queryable but are delivery-suppressed; cron and other
background admission requires a healthy long-lived Web/Desktop or Gateway host.

Behavioral limits belong in `config.yaml`, not `.env`:

```yaml
plugins:
  entries:
    workflow:
      runtime:
        max_parallel_nodes: 4
        max_total_workers: 4
        max_executing_runs: 4
        max_queued_runs: 100
        max_paused_runs: 100
        max_nonterminal_runs: 200
        max_start_requests_per_minute: 60
        ai_idle_timeout_seconds: 300
        ai_wall_timeout_seconds: 1800
        provider_request_timeout_seconds: 300
        subprocess_timeout_seconds: 120
        resource_limits:
          process_tree_rss_bytes: 2147483648
          process_tree_cpu_seconds: 900
          max_descendants: 32
      retention:
        terminal_board_days: 7
```

A package sidecar may only tighten profile ceilings. Output, artifact, event, per-run/profile storage, retry, child-agent, and topology bounds are likewise hard-capped by validation/runtime policy.

## Cron scheduling

Schedule the ordinary CLI command with Hermes cron. One-shot showcase scheduling reuses existing `repeat=1` plumbing and a source key derived from the schedule ID plus UTC fire instant, so duplicate delivery is idempotent. `show --json` lists related schedules. Removing or resetting a workflow never removes an unowned cron job.

## Offline production showcase

The default installed check requires no workflow-node model, credentials, network, or external integration:

```powershell
hermes workflow showcase list --json
hermes workflow showcase preflight laptop-diagnostic --json
hermes workflow showcase run laptop-diagnostic --symptom "fictional slow startup" --json
hermes workflow showcase report <run-id> --json
```

Laptop Diagnostic reads only bundled sanitized fictional evidence. It never inventories, elevates on, or modifies the host. It pauses at a real workflow approval before finalizing the proposed plan. The controlled `resilience` showcase demonstrates `retry`, `timeout`, or `cancel`; timeout remains truthfully failed and proves typed timeout plus process cleanup.

`ai-extensions` and `scheduling` are optional. Preflight returns the exact digest-bound confirmation token; running either requires that token and explicit opt-in. AI may consume the selected provider's tokens/cost and skips cleanly when unavailable. Scheduling creates a one-shot Hermes cron job and reports its lifecycle. Neither opt-in weakens the offline default claims.

Reports derive every claim from RunStore events, attempts, interactions, observed cleanup, and verified artifact bytes—not catalog prose. `showcase cleanup` is a dry run unless `--execute` is supplied. `showcase reset` is scoped to owned showcase state and cannot delete user workflows or cron jobs. Live Windows PowerShell collection is a separate future capability. Corruption, forced termination, exhaustion, floods, destructive failures, and long soak tests are CI/release-only and are not addressable from the installed showcase catalog or skill.

## Compatibility and authorization

Compatibility is reported as supported, degraded with explicit mappings, or unsupported with blocking findings. Portable syntax is accepted only where Hermes can preserve the documented lifecycle and security contract; provider-specific options, tools, hooks, MCP, skills, and runtime dependencies are checked before execution. API and Desktop access uses Hermes' existing local token, remote token, or remote OAuth routing plus profile/operator scope. Cursors are opaque and scope-bound. Late responses from a previous profile/board and stale action versions are rejected rather than becoming authority.

## Upstream merge and release verification

The customization ledger separates generic managed processes, plugin agents, Kanban persistence, Kanban REST, Desktop composition, packaging, and native portability CI. The merge skill limits classification to the strict intersection of files changed upstream since `last_verified_upstream` and files explicitly owned by the ledger. It classifies those entries as `same_file` or `owned_symbol` (and all others as `none`); it does not search unrelated repository paths or generate `possible_upstream_equivalent`. Policy-relevant overlap requires an explicit `preserve`, `adapt`, or human-selected `remove-as-upstream-equivalent` decision. Whole-file `ours`/`theirs` resolution is forbidden for ledger-owned files. Entry-specific invariants and the base gate test compatibility outside that intersection before the exact tested base commit can propagate to a brand.

The lightweight gate is used inside normal merge work:

```bash
scripts/test_workflow_merge_gate.sh --phase base
scripts/test_workflow_merge_gate.sh --phase brand --brand otto --tested-base-sha <sha>
```

CI, release verification, or an explicitly requested preflight runs the full branch graph only in temporary worktrees:

```bash
scripts/test_workflow_upstream_merge.sh \
  --upstream-ref main \
  --base-ref base \
  --brand-ref otto \
  --brand-ref loop24 \
  --report-dir /tmp/workflow-merge-evidence
```

The rehearsal fetches no network, never advances real refs, gates the temporary base and each generated brand, and emits schema-validated evidence with immutable refs, overlap decisions, tests, tree/digest identities, and final ancestry. Wheel and sdist gates also verify every catalog, digest, YAML, sidecar, fixture, script, command, local MCP resource, and the built-in `workflow-showcase` skill from installation-shaped assets.
