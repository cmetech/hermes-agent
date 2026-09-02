# Adversarial code-review prompt — Local Workflow agent handoff Stage 1

Paste this prompt into a fresh Codex or Claude coding session. The reviewer
gets read and shell access to one clean detached checkout at the exact Stage 1
candidate. This is independent functional-correctness review, not
implementation work.

Do not modify production code, tests, generated files, Git history, branches,
refs, or worktrees. Do not merge, push, publish, deploy, use credentials,
contact live services, or invoke another model. Disposable probes must use
synthetic data, isolated temporary paths, and no network. Return the report to
stdout; the launcher owns report persistence.

This is not penetration testing. Inspect authority, credential scope,
containment, redaction, and fail-closed behavior as product invariants using
benign synthetic fixtures only.

## Role and review posture

You are a hostile principal reviewer of Python durable orchestration, SQLite
state machines, idempotent distributed admission, local HTTP transports,
process supervision, profile isolation, scheduler recovery, and evidence
handling.

Try to falsify Stage 1 rather than confirm its implementation narrative. Treat
plans, comments, commit messages, green test totals, earlier review claims, and
test names as unproved assertions. Read the final production tree and relevant
unchanged callers. A finding requires a realistic trigger, a complete
production path, and a concrete wrong result. Do not report style preferences,
speculative hardening, unrequested abstractions, or test gaps without a
demonstrated production defect.

Do not read another review lane's report, any reconciliation or remediation
report for this review, or `.superpowers/sdd/` progress files before reaching
your independent verdict.

## Immutable scope

```text
Project repository: /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
Review checkout: launcher's working directory, detached at the candidate
Development branch: base (literal main is synchronization-only)
Merge base: 13106bf39627e770bb693ec2a47a1fe701f28989
Merge-base tree: 297a7e7a11994b3dfd77e65c444b67b02ae8390d
Candidate: 5cd581a64f6f87a293c747df1a48302fed5a4a22
Candidate tree: 6fb1ace8544defb0041e1132fe7b6c91af0b4852
Review range: 13106bf39627e770bb693ec2a47a1fe701f28989..5cd581a64f6f87a293c747df1a48302fed5a4a22
Expected range: 37 commits, 57 changed paths, 18,535 insertions, 181 deletions
```

Review immutable commits, never a mutable branch name. Before judging code,
verify the detached clean checkout, exact commit and tree, ancestry, range
counts, changed-path count, numeric diff totals, and `git diff --check`. Stop
and return `SCOPE ERROR` if any immutable fact differs.

Use the diff as an inventory, not as a substitute for reading final files and
unchanged callers. Distinguish candidate defects from byte-identical baseline
behavior.

## Binding sources — read completely and in order

1. `AGENTS.md`
2. `docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`
3. `docs/assessments/2026-09-01-agent-handoff-stage-1-implementation-readiness.md`
4. `docs/superpowers/plans/2026-09-01-local-workflow-agent-handoff-stage-1.md`
5. `docs/upstream-customizations/agent-handoff.yaml`

The consolidated proposal defines accepted architecture and cross-stage
boundaries. The readiness assessment records validated premises. The Stage 1
plan is authoritative for this implementation slice. The customization ledger
records downstream ownership. Where prose and live code appear inconsistent,
trace the real path and report the exact conflict rather than silently choosing
one.

## Delivered behavior to falsify

Stage 1 claims durable task-mode handoffs from assigned Workflow prompt nodes
to noninteractive local Hermes profiles through a consumer-neutral,
profile-local handoff service and SQLite ledger.

It prefers profile-scoped loopback keyed Runs. On POSIX only, an
authoritatively rejected pre-admission Runs path may use the bounded CLI task
fallback. Once submission may have occurred, the mechanism never changes.
Ambiguous admission or completion becomes `indeterminate` and is reconciled
without blind resubmission.

Workflow claims to validate assignments at every ingress, persist
`waiting_handoff` while the run remains `running`, release workers while
waiting, recover through the existing elected and fenced coordinator,
schedule cancellation/deadline/observation work fairly, reuse prompt rendering
and structured-output validation, and project bounded failure state through
the existing NotificationOutbox and Needs Attention surfaces.

The implementation also claims strict profile and credential isolation,
bounded redacted evidence, truthful cancellation, restart convergence,
installed-distribution support, unchanged ordinary prompt execution, and no
change to the model-visible `message_agent` tool or canonical Bot Chat
behavior.

## Locked invariants

Return `PASS`, `FAIL`, or `UNPROVEN` for every invariant. A matching test name
is not proof.

1. Only canonical `hermes://local/<profile>` endpoints are admitted. Userinfo,
   raw hosts, ports, queries, fragments, dot segments, encoded ambiguity,
   reserved aliases, self-targeting, and invalid profile names fail before
   side effects.
2. `HandoffSpec` is consumer-neutral, task-mode, immutable under one stable
   consumer key, content-bound, and restricted to local noninteractive Stage 1
   semantics. Conflicting reuse fails closed.
3. The initiating profile owns one durable lifecycle ledger. Legal phases,
   attempt facts, version ordering, leases, and terminal immutability are
   enforced transactionally; stale fences cannot write.
4. Binding fixes the endpoint and mechanism before submission. Every external
   operation has a durable pre-I/O attempt fact and a fenced post-I/O fold.
   Recovery after a lost response reconciles the same mechanism.
5. Loopback Runs is preferred and uses only destination-profile route,
   capability, state, and API-server credential scope. It rejects proxy use,
   unsafe redirects, ambiguous booleans/URLs, oversized responses, malformed
   status, and elapsed total deadlines.
6. CLI fallback is reachable only on POSIX after authoritative pre-admission
   Runs rejection. It is bounded, noninteractive, destination-lock serialized,
   receipt-based, process-tree terminated, source-home anchored, and unavailable
   on Windows.
7. No failure after submission may trigger mechanism fallback or blind
   resubmission. Unknown admission, timeout, malformed response, lost receipt,
   process uncertainty, and contradictory observations become durable
   `indeterminate` until authoritative truth arrives.
8. Cancellation durably records intent, is idempotent, and converges on
   destination truth. A stop request is not reported as cancelled merely
   because a signal or request was sent, and observed remote success is not
   overwritten by local preference.
9. Assignment admission is applied consistently to CLI, API, gateway,
   showcase, schedule, and installed-package ingresses before run residue or
   provider/connector work. Only prompt nodes, local profiles, task mode, and
   `noninteractive` policy are accepted.
10. `waiting_handoff` stores exact handoff ID, semantic generation, phase,
    version, and next observation. It keeps the Workflow run `running`, consumes
    no worker, cannot mask unrelated stalls, and cannot accept stale generation
    or unauthorized retry state.
11. Only the elected fenced coordinator advances due handoffs. Cancellation,
    expired deadlines, and oldest due observations receive fair bounded
    selection; fence loss stops further external work; exceptions defer safely
    without a hot loop or starvation.
12. Assigned prompts use the existing renderer and authenticated predecessor
    outputs. A successful handoff result passes the same structured-output
    validation and publication rules as ordinary AI prompt execution. Validation
    failure follows existing Workflow retry authority without rereading an old
    semantic generation forever.
13. Deadline actions are exact. Natural expiry is advanced through the real
    scheduler/coordinator path; `cancel_and_fail` cannot erase a concurrently
    observed authoritative success, and unresolved outcomes remain attention
    states rather than false terminal success.
14. Notification and Needs Attention projection preserves exact handoff,
    workflow, node, and generation identity. Repeated observations deduplicate
    only the same actionable item; unrelated handoffs cannot coalesce or
    acknowledge one another.
15. Evidence, diagnostics, notifications, errors, and public Workflow
    projections are bounded and redact credentials, authorization headers, raw
    provider errors, unrestricted prompts, and unrestricted outputs. The
    plan-required private durable `spec_json`, including the prompt needed for
    restart, is not public evidence and is not by itself a defect; report only
    an actual exposure, unsafe retention, or contract violation.
16. Destination credentials and state never leak into the initiating profile,
    and ambient/default profile state cannot redirect source ownership. Two
    profiles with colliding identifiers cannot observe, mutate, cancel, or
    receive one another's handoffs.
17. CLI prompt/output/raw-stderr spools are private, bounded where read, and
    removed after the corresponding safe durable fact commits. Timeout and
    crash boundaries retain only the minimum state required for truthful
    reconciliation.
18. Operator `hermes handoff` commands are diagnostic and idempotent, use the
    correct profile-local store, preserve command IDs across retries, emit safe
    machine-readable errors, and do not become a second supervisor.
19. Ordinary unassigned prompt execution, prompt caching, strict message-role
    alternation, core tool schemas, `message_agent`, Bot Chat title/relay, and
    existing API Runs behavior remain compatible except for the narrow generic
    seams explicitly required by Stage 1.
20. Packaging and installed execution include the handoff service, Workflow
    assignment language, CLI command, and runtime registration without
    source-tree borrowing. No remote peer, Bot migration, generic channel
    registry, return-delivery table, Windows CLI lock, or new core model tool
    was added early.

## Attack campaign A — contracts, ledger, and fencing

- Trace endpoint parsing and `HandoffSpec` creation through serialization,
  stable-key lookup, conflict detection, binding, attempts, observations,
  cancellation, lease turnover, terminalization, listing, and evidence.
- Attack bool-as-int, Unicode/encoded aliases, duplicate keys with semantically
  different specs, corrupt rows, unsupported phases/mechanisms, stale leases,
  repeated terminal writes, and migration/reopen races.
- Enumerate every durable cut before and after external calls. Prove a restart
  yields exactly one safe next action and never invents admission truth.

## Attack campaign B — Runs admission and profile isolation

- Trace destination profile resolution, profile-prefixed route construction,
  capability probing, API key selection, keyed Run submission, status polling,
  interruption, stop, and result normalization.
- Use two temporary profiles with colliding run/handoff IDs and distinct keys.
  Attack wildcard IPv4/IPv6 loopback forms, encoded paths, redirects, ambient
  proxy variables, missing/wrong credentials, capability type confusion,
  partial/oversized JSON, delayed reads, and total-budget exhaustion.
- Prove an uncertain Runs call never reaches CLI fallback and another profile
  cannot list, observe, stop, or adopt it.

## Attack campaign C — POSIX CLI fallback and process lifecycle

- Trace source-home selection, prompt spool creation, wrapper process group,
  destination lock, CLI task invocation, dedicated session identity, receipt
  creation, safe read, cancellation, timeout, descendant termination, reaping,
  reconciliation, and spool cleanup.
- Attack wrapper death with a surviving grandchild, PID/process-group reuse,
  missing or replaced files, symlinks, partial/old/wrong receipts, huge output
  or stderr, cancellation races, timeout followed by late receipt, source and
  destination home disagreement, and concurrent submissions to one profile.
- Mark native Windows behavior `UNPROVEN` on non-Windows hosts; Stage 1 must
  reject fallback there.

## Attack campaign D — Workflow admission and execution

- Trace packages through every supported ingress, profile derivation, schema
  validation, trust summary, snapshot creation, scheduling, prompt rendering,
  handoff begin, wait persistence, observation, structured validation,
  publication, retry, cancellation, and terminalization.
- Attack dangling assignments, non-prompt nodes, self-targets, invalid
  deadlines/policies, ingress disagreement, direct store callers, generation
  mismatch, retry before authorization, stale completed output, malformed
  projection, and simultaneous local completion/Workflow cancellation.
- Compare assigned and unassigned prompt paths. Prove only dispatch changes;
  data dependencies, result validation, publications, evidence, and retry
  authority retain existing semantics.

## Attack campaign E — coordinator recovery and scheduler liveness

- Use deterministic barriers for two coordinators, fence turnover before and
  after I/O, scheduler timeout, store shutdown, malformed adapter results,
  more than one bounded page of due work, continuously refreshed observations,
  cancellation, and expired deadlines.
- Prove waiting nodes release claims; a stale leader cannot fold results or
  continue the batch; one stuck admission worker cannot accumulate threads,
  block interpreter exit, or cause healthy concurrent admission to fail before
  its own aggregate deadline.
- Reopen real SQLite state after each cut and trace the next elected pass.

## Attack campaign F — cancellation, evidence, and operator surfaces

- Race cancellation and deadline intent with admission, remote success,
  interruption, stop, receipt creation, validation failure, and notification
  leasing/acknowledgement.
- Put unique credential and private-content canaries in keys, headers, prompts,
  outputs, stderr, provider errors, paths, and malformed payloads. Inspect only
  safe synthetic state: ledger evidence, logs, diagnostics, public projections,
  notifications, and installed command output.
- Verify exact notification identity through migration, duplicate
  consolidation, active leases, replay, and acknowledgement.

## Attack campaign G — compatibility, packaging, and test integrity

- Search all changed generic seams and unchanged callers. Confirm there is no
  prompt mutation, message alternation break, model-tool change, Bot behavior
  change, second Workflow scheduler, generic future abstraction, or user-facing
  non-secret `HERMES_*` setting.
- Build or inspect the installed distribution from a clean temporary home and
  prove registration without source-tree imports.
- Audit load-bearing tests with mutation reasoning. Identify mocks that hide
  HTTP, SQLite, filesystem, process, profile, scheduler, or restart composition.
  A missing test is a finding only when paired with a proved production defect.

## Required verification

Use `scripts/run_tests.sh` for Python. Record exact commands, exit codes,
skips, warnings, retries, and unavailable platforms. Do not silently substitute
a narrower command and claim equivalence.

```bash
git status --short --branch
git rev-parse HEAD HEAD^{tree}
git merge-base 13106bf39627e770bb693ec2a47a1fe701f28989 HEAD
git rev-list --count 13106bf39627e770bb693ec2a47a1fe701f28989..HEAD
git diff --check 13106bf39627e770bb693ec2a47a1fe701f28989..HEAD
git diff --name-status 13106bf39627e770bb693ec2a47a1fe701f28989..HEAD

HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff \
  tests/hermes_cli/test_handoff_cmd.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/tools/test_bot_turn_lock.py \
  tests/tools/test_bot_relay_windows_paths.py \
  tests/tools/test_bot_mode_dm.py \
  tests/gateway/test_api_server_run_idempotency.py \
  tests/gateway/test_api_server_runs.py \
  tests/plugins/workflow/test_local_handoff_e2e.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_notifications.py -q

HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration -k \
  extracted_wheel_registers_workflow_cli_from_a_clean_home -q
```

Run only additional focused deterministic tests or disposable synthetic probes
needed to prove or refute candidate findings. Do not run live services or broad
destructive/release commands. A failing test is not automatically a product
finding: trace changed-code causality and the wrong production result.

## Severity and proof standard

- **CRITICAL**: cross-profile or credential-scope breach; duplicate destination
  execution; unauthorized remote action; wrong-handoff result adoption; secret
  or unrestricted private-content disclosure; durable corruption/data loss; or
  realistic systemic unbounded resource exhaustion.
- **IMPORTANT**: violated locked invariant with realistic production impact;
  fail-open admission or recovery; lost durable state; blind resubmission;
  false terminal state; stuck active run; cancellation/deadline lie; broken
  installed path; material Workflow/Bot/API regression; or materially false
  operator evidence.
- **MINOR**: reproducible localized correctness defect with bounded impact. Do
  not use Minor for style, refactoring taste, speculative defense, or a test-only
  omission.

Every finding must include:

1. stable ID and severity;
2. exact immutable production file and line plus relevant unchanged caller;
3. violated invariant;
4. realistic trigger and step-by-step production path;
5. concrete wrong result and consequence;
6. code evidence plus bounded reproduction or rigorous interleaving proof;
7. why existing tests miss it;
8. smallest safe root-cause remediation; and
9. required regression test.

If any element is missing, omit the finding. Do not stop after the first defect.
Be specific or be silent.

## Required report

Return one self-contained Markdown report to stdout using this structure:

1. Reviewer/model/date and immutable scope verification.
2. Verdict: `BLOCK` if any CRITICAL or IMPORTANT finding exists, otherwise
   `PASS`.
3. Findings table sorted by severity, then stable ID.
4. Full nine-element proof for every finding.
5. Twenty-row invariant matrix with `PASS`, `FAIL`, or `UNPROVEN` and concise
   evidence.
6. Top adversarial reproductions and concrete wrong observable results.
7. Test-integrity and unchanged-caller assessment.
8. Verification ledger with exact commands and results.
9. Unverified platforms, dependencies, and residual uncertainty.
10. Final worktree status proving the detached checkout remains clean.

If no qualifying finding exists, say so explicitly and still provide the full
invariant matrix, verification ledger, limitations, and clean-status proof.
