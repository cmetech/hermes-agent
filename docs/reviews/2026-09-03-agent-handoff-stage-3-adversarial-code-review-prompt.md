# Adversarial code-review prompt — Agent Handoff Stage 3

Paste this prompt into a fresh Codex or Claude coding session. The reviewer
gets read and shell access to one clean detached checkout at the exact Stage 3
candidate. This is independent functional-correctness review, not
implementation work.

Do not modify production code, tests, generated files, Git history, branches,
refs, or worktrees. Do not merge, push, publish, deploy, use live credentials,
contact live services, or invoke another model. Disposable probes must use
synthetic data, isolated temporary paths, and no external network. Return the
report to stdout; the launcher owns report persistence.

This is not penetration testing. Inspect authentication, authority, credential
scope, containment, redaction, and fail-closed behavior as product invariants
using benign synthetic fixtures only.

## Role and review posture

You are a hostile principal reviewer of Python durable orchestration, SQLite
state machines, authenticated HTTP, idempotent distributed admission, process
supervision, profile isolation, messaging gateways, TypeScript/React Desktop
clients, and crash recovery.

Try to falsify Stage 3 rather than confirm its implementation narrative. Treat
plans, comments, commit messages, green totals, the customization ledger, the
existing controller review, and test names as unproved assertions. Read the
final production tree and relevant unchanged callers. A finding requires a
realistic trigger, a complete production path, and a concrete wrong result. Do
not report style preferences, speculative hardening, deferred features, or test
gaps without a demonstrated production defect.

Do not read any other review lane's report, the existing
`docs/reviews/2026-09-02-agent-handoff-stage-3-adversarial-review.md`, any
reconciliation or remediation report for this review, or `.superpowers/sdd/`
progress files before reaching your independent verdict.

## Immutable scope

```text
Project repository: /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
Review checkout: launcher's working directory, detached at the candidate
Development branch: base (literal main is synchronization-only)
Merge base: c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d
Merge-base tree: 986b9b76f06b562ccc914318507c26dd95cb6d49
Candidate: 2affe5e02307475274cb3d72c24af59f72682945
Candidate tree: 390603dadb2ff6cb8373e4751b6b097bea0ce6b6
Review range: c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..2affe5e02307475274cb3d72c24af59f72682945
Expected range: 24 commits, 62 changed paths, 10,200 insertions, 415 deletions
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
3. `docs/reviews/2026-09-02-local-workflow-agent-handoff-stage-1-adversarial-review-remediation.md`
4. `docs/assessments/2026-09-02-agent-handoff-stage-2-implementation-readiness.md`
5. `docs/superpowers/plans/2026-09-02-remote-workflow-agent-handoff-stage-2.md`
6. `docs/assessments/2026-09-02-agent-handoff-stage-3-implementation-readiness.md`
7. `docs/superpowers/plans/2026-09-02-bot-mode-desktop-agent-handoff-stage-3.md`

The consolidated proposal defines accepted architecture and stage boundaries.
The Stage 1 remediation and Stage 2 artifacts define the delivered foundation.
The Stage 3 readiness assessment and plan define this implementation slice.
Where prose and live code differ, trace the real path and report the exact
conflict rather than silently choosing one.

After reaching an independent code verdict, inspect
`docs/upstream-customizations/agent-handoff.yaml` only to verify that its Stage
3 ownership, commit, protocol, exclusion, verification, and review claims match
the candidate. Do not let its verdict substitute for review.

## Delivered behavior to falsify

Stage 3 claims that supported Bot Mode and Desktop callers can use the existing
consumer-neutral handoff service for durable local or authenticated peer
conversation handoffs. New controlled conversations use canonical endpoints or
configured directory aliases, stable host-derived operation identity, keyed
Runs admission, correlated controls, and the already-delivered peer registry,
authentication, redirect, and credential boundaries.

Legacy friendly local sends, bare peers, explicit `peer/profile` direct DMs,
and Desktop relay remain compatibility transports. They are not silently
upgraded, retired, or used as fallback after a durable submission may have
occurred. Classic CLI Bot Chat has no durable return supervisor: it may start a
new friendly local compatibility send, but controlled endpoints and correlated
continuations fail without delivery.

Terminal results and Needs Attention state are durable in the initiating
profile's handoff ledger. One gateway- or web-owned supervisor advances work and
publishes a return only to the initiating host. In-process queues accelerate
delivery but are not truth. Transcript persistence precedes acknowledgement so
restart replay does not run the model twice.

Desktop claims profile-scoped `agent_handoff.*` operations and a bounded
inspector beside the existing Bot roster. The renderer supplies neither actor,
credentials, transport data, nor return-host identity. Existing Workflow,
`message_agent` containment, peer DM, relay, prompt caching, role alternation,
and session isolation remain compatible.

## Locked invariants

Return `PASS`, `FAIL`, or `UNPROVEN` for every invariant. A matching test name
is not proof.

1. `HandoffSpec` accepts only task or conversation mode. Conversation mode has
   no structured-output schema or deadline and uses one closed immutable Bot or
   operator return route. Existing task serialization and fingerprints remain
   byte-compatible.
2. The v1-to-v2 ledger migration is atomic, repeatable, preserves existing
   rows, rejects future versions, and creates at most one delivery for one
   source event. Terminal result text is not duplicated into the delivery row.
3. Delivery attention, acknowledgement, claims, leases, attempts, retry,
   terminal failure, and completion are durable and fenced. Stale owners cannot
   mutate truth, and acknowledgement cannot erase handoff evidence or result.
4. Only strict `hermes://local/<profile>` and
   `hermes://peer/<configured-peer>/<profile>` endpoints are accepted. No raw
   URL, host, port, credential, userinfo, query, fragment, encoded ambiguity,
   invalid name, deleted profile, or unknown peer crosses the boundary.
5. `handoff.agents` is profile-scoped, closed, deterministic, and validated
   against the real local-profile and peer registries. Invalid or ambiguous
   configuration fails closed without legacy fallback or side effects.
6. Bot target resolution order is explicit URI, configured directory alias,
   legacy peer/profile, local roster, bare peer, then Desktop relay. Bare peers
   never receive a guessed profile.
7. The model-visible `message_agent` schema remains available only in the
   managed canonical Bot Chat and remains byte-stable after the one Stage 3
   protocol epoch change. Execution repeats the containment gate.
8. The initiating profile, Bot session, gateway/TUI session key, tool-call ID,
   hop count, host kind, actor, and operation identity are host-derived,
   bounded, and cannot be forged by model arguments, the renderer, destination,
   ambient environment, or another profile.
9. A new durable conversation derives one stable content-bound handoff key.
   Retry reuses the same key; conflicting payload reuse fails closed; a lost or
   ambiguous submission response reconciles without an unkeyed replacement.
10. Local and peer controlled conversations reuse the delivered Runs
    authorities, lazy destination credential resolution, proxy bypass, and
    redirect-safe authenticated HTTP. Credentials, authorization headers, and
    peer URLs are never persisted in handoff state or public evidence.
11. Mechanism binding is immutable once submission may have occurred. Legacy
    local CLI and peer-DM paths retain their timing and restart semantics, and
    no durable failure switches to peer DM, relay, or an unkeyed replacement.
12. A correlated continuation validates exact handoff, endpoint, profile,
    session, host, and nonterminal state. Follow-up, steer, exact approval
    response, stop, interruption, and authoritative status mapping use only
    advertised channel capabilities.
13. Any compatibility path receiving a nonempty `handoff_id` either enters the
    already-bound durable mechanism or fails without sending. It never discards
    correlation into a new legacy message.
14. Classic CLI Bot Chat cannot create a return that no host can supervise.
    New friendly local compatibility sends still work; controlled endpoint and
    directory sends, and every correlated continuation, fail clearly without a
    service call or transport spawn.
15. The supervisor is one bounded extension of the shared service, scans served
    profiles fairly, closes every store on shutdown, and leaves no service or
    health thread alive. CLI-only installs do not gain a daemon.
16. A Bot return route is bound to exactly one initiating host kind. A
    non-owning gateway/web process cannot select, claim, consume, increment
    attempts, or receive another host's delivery.
17. The durable delivery row and restart scan are authoritative. Queue loss,
    initiating-process exit before observation/claim, destination restart,
    lease expiry, and publish failure converge without losing or duplicating a
    return.
18. Gateway and TUI/Desktop delivery prove session/profile ownership, persist
    the delivery ID with the synthetic user turn before acknowledgement, and
    acknowledge an already-persisted replay without a second model turn.
19. Automatic return wake is a profile-scoped `config.yaml` policy with a fixed
    hop ceiling. Missing valid config uses the documented default; malformed or
    unreadable config, disabled wake, exhausted hops, or failed delivery retain
    durable Needs Attention and fail closed.
20. Public CLI/RPC/list/evidence projections are bounded and redact secrets,
    authorization material, credential-shaped values, raw remote errors,
    unrestricted prompts, and unrestricted results. Result preview appears
    only in detail/evidence, is forcibly redacted, and is at most 8 KiB.
21. Desktop exposes only the six profile-scoped `agent_handoff.*` operations,
    derives operator authority server-side, rejects renderer profile/actor and
    payload forgery, and keeps the inspector out of the transcript/transport
    path. Existing three `handoff.*` session-transfer RPCs retain their meaning.
22. Stage 1/2 Workflow ownership, deadlines, cancellation races, remote status,
    structured output, profile isolation, peer auth, existing peer DM/relay,
    ordinary chats, prompt caching, role alternation, packaging, and installed
    registration remain compatible. No Stage 4/5 channel registry, repository
    transport, A2A, relay retirement, Windows lock, new core tool, or non-secret
    `HERMES_*` setting was added.

## Attack campaign A — contract, migration, ledger, and fencing

- Trace spec normalization, canonical JSON, fingerprinting, v1 database open,
  migration transaction, delivery creation beside source events, attention,
  claims, retry, acknowledgement, and terminal result lookup.
- Attack unknown/extra route keys, bool-as-int, oversized/control-bearing
  identifiers, malformed legacy rows, unsupported DB versions, duplicate event
  replay, stale leases, repeated terminal observations, and concurrent opens.
- Enumerate durable cuts before and after every external call. Prove exactly one
  safe next action after restart.

## Attack campaign B — Bot containment, target resolution, and compatibility

- Trace canonical Bot Chat discovery, schema injection, protocol epoch, executor
  dispatch, target resolution, self-target/ambiguity handling, legacy local
  CLI, peer DM, bare-peer, and relay paths.
- Attack malformed directory config, alias collisions, deleted profiles,
  unknown peers, canonical URI variants, absent/invalid host identity, forged
  calls from ordinary sessions, repeated tool IDs, and `handoff_id` on every
  legacy path.
- Compare classic CLI, messaging gateway, and Desktop-hosted Bot Chats. Prove
  each surface selects only a mechanism it can supervise.

## Attack campaign C — authenticated local/peer Runs and controls

- Trace endpoint validation, capability negotiation, session resolution, keyed
  submission, status by Run ID, approval, response, message/steer, stop,
  interrupted mapping, result normalization, and cancellation races.
- Use temporary profiles/gateways with distinct credentials. Attack unsafe
  redirects, ambient proxies, wrong-profile keys, missing capabilities, lost
  responses, duplicate and conflicting keys, malformed/oversized responses,
  destination restart, and elapsed total budgets.
- Prove credentials resolve lazily from the initiating profile and never enter
  durable state, errors, logs, return events, or Desktop projections.

## Attack campaign D — supervision, return delivery, and crash cuts

- Trace background-service registration through web and gateway hosts,
  profile enumeration, fair advancement, terminal delivery creation, host
  selection, claim, queue publication, consumer claim, transcript persistence,
  completion/release, and acknowledgement.
- Cut the initiating process before observation, before delivery claim, after
  claim, after queue publication, after transcript persistence, and before
  acknowledgement. Restart with a competing wrong host and with the owning
  host.
- Race cancellation with destination completion and delivery acknowledgement.
  Verify one authoritative result and one return, with no second wake turn.

## Attack campaign E — gateway, TUI/Desktop, and session ownership

- Trace gateway session-source routing, compression continuation resolution,
  TUI session lookup, profile-home matching, delivery-ID deduplication, hop
  propagation, and synthetic-turn role/display metadata.
- Attack missing, stale, ended, rotated, foreign-profile, foreign-host, and
  simultaneously live sessions. Try to make one tab/profile consume another's
  return or burn its attempts.
- Prove a fresh process can reopen the real handoff and session databases and
  acknowledge a persisted transcript receipt without invoking inference.

## Attack campaign F — Desktop RPC, projections, and operator actions

- Trace `agent_handoff.create/get/list/evidence/command/directory` from renderer
  request through profile scoping, server-derived actor, service/store, safe
  projection, and roster inspector state.
- Attack unknown fields, renderer-supplied actor/profile/transport data,
  cross-profile IDs, invalid commands, concurrent acknowledgement, huge or
  secret-bearing result/error text, and list-versus-detail leakage.
- Confirm UI failures are non-destructive to Bot Chat and existing
  `handoff.request/state/fail` behavior is unchanged.

## Attack campaign G — compatibility, packaging, and test integrity

- Search every changed generic seam and relevant unchanged caller. Confirm no
  Workflow deadline/cancellation/status regression, prompt mutation, role
  alternation break, global tool registration, peer-DM replacement, relay
  retirement, speculative registry, or environmental configuration leak.
- Build/extract the installed distribution in a clean temporary home and prove
  Stage 3 modules, schema migration, RPC registration, CLI registration, and
  Stage 1/2 Workflow registration resolve from the installed wheel.
- Audit load-bearing tests with mutation reasoning. Identify mocks that hide
  HTTP, authentication, SQLite, filesystem, process, host, session, or restart
  composition. A missing test is a finding only when paired with a proved
  production defect.
- Distinguish candidate failures from the documented merge-base macOS
  SQLite/background-thread lifecycle defect and unrelated Desktop parallel
  ordering sensitivity. Do not excuse any candidate regression as inherited.

## Required verification

Use `scripts/run_tests.sh` for Python. Record exact commands, exit codes, skips,
warnings, retries, and unavailable platforms. Do not silently substitute a
narrower command and claim equivalence.

The detached review worktree intentionally has no private virtualenv. Export
the candidate repository's existing test interpreter before running the
commands below, and verify a load-bearing Hermes module resolves from the
detached checkout rather than the launcher's checkout. A zero-test or
missing-pytest exit is not a passing result.

```bash
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
"$HERMES_PYTHON" -c 'import hermes_cli.handoff, pathlib; print(pathlib.Path(hermes_cli.handoff.__file__).resolve())'

git status --short --branch
git rev-parse HEAD HEAD^{tree}
git merge-base c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d HEAD
git rev-list --count c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
git diff --check c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
git diff --name-status c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD

HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_peers.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/hermes_cli/test_web_server_plugin_services.py \
  tests/tools/test_bot_mode_dm.py \
  tests/tools/test_bot_mode_probe.py \
  tests/tools/test_bot_relay.py \
  tests/agent/test_tool_executor_middleware.py \
  tests/agent/test_synthetic_turn_display_kind.py \
  tests/agent/test_turn_context.py \
  tests/run_agent/test_run_agent.py \
  tests/test_hermes_state.py \
  tests/gateway/test_completion_delivery.py \
  tests/gateway/test_plugin_background_services.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/tui_gateway/test_bot_relay_methods.py \
  tests/tui_gateway/test_protocol.py \
  tests/test_tui_gateway_queue_on_busy.py \
  tests/plugins/workflow/test_handoff_executor.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_remote_handoff_e2e.py -q

HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration -k \
  'extracted_wheel_registers_workflow_cli_from_a_clean_home or extracted_wheel_registers_agent_handoff_stage_3' -q

(cd apps/desktop && npm run test:ui -- \
  src/plugins/hermes-bots/handoffs.test.tsx \
  src/plugins/hermes-bots/plugin-panes.test.tsx \
  src/plugins/hermes-bots/bot-row.test.tsx \
  src/plugins/hermes-bots/plugin.mentions.test.ts \
  src/plugins/hermes-bots/relay.test.ts \
  src/plugins/hermes-bots/roster-actions.test.ts)

(cd apps/desktop && npm run typecheck)
```

Run only additional focused deterministic tests or disposable synthetic probes
needed to prove or refute candidate findings. Do not run live services or broad
destructive/release commands. A failing test is not automatically a product
finding: trace changed-code causality and the wrong production result.

## Severity and proof standard

- **CRITICAL**: cross-profile, cross-host, or credential-scope breach;
  duplicate destination execution; unauthorized remote action; wrong-handoff
  result adoption; secret or unrestricted private-content disclosure; durable
  corruption/data loss; or realistic systemic unbounded resource exhaustion.
- **IMPORTANT**: violated locked invariant with realistic production impact;
  fail-open resolution/recovery; lost durable state; blind resubmission; false
  terminal/delivery state; stuck active handoff; broken correlation,
  cancellation, installed path, Bot/Desktop/Workflow compatibility, or material
  operator evidence.
- **MINOR**: reproducible localized correctness defect with bounded impact. Do
  not use Minor for style, refactoring taste, speculative defense, or a
  test-only omission.

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
5. Twenty-two-row invariant matrix with `PASS`, `FAIL`, or `UNPROVEN` and
   concise evidence.
6. Top adversarial reproductions and concrete wrong observable results.
7. Test-integrity and unchanged-caller assessment.
8. Verification ledger with exact commands and results.
9. Unverified platforms, dependencies, and residual uncertainty.
10. Final worktree status proving the detached checkout remains clean.

If no qualifying finding exists, say so explicitly and still provide the full
invariant matrix, verification ledger, limitations, and clean-status proof.
