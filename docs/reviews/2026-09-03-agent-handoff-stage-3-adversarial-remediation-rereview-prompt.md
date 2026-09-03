# Adversarial remediation re-review prompt — Agent Handoff Stage 3

Use this prompt in one fresh Claude session and one fresh Codex session. Review
the same clean detached checkout independently. This is review, not
implementation.

Do not modify code, tests, documents, Git state, refs, or worktrees. Do not use
live credentials, inference, services, or external network. Use only bounded
synthetic probes in temporary paths. Return one Markdown report to stdout.

## Immutable scope

- Original reviewed candidate: `2affe5e02307475274cb3d72c24af59f72682945`
- Remediated candidate: `02d655c9cde51dd66bb12f91c38131d7d5431e75`
- Remediated tree: `e915e8fa5a4101d7f45e289ea33d81f530bce5b1`
- Remediation range: `2affe5e02307475274cb3d72c24af59f72682945..02d655c9cde51dd66bb12f91c38131d7d5431e75`
- Range commits: 33
- Range paths: 21
- Range diff: `+2488/-87`; 708 inserted lines are review prompts, not
  production behavior.

Verify these facts and stop with `SCOPE ERROR` if they differ. The checkout
must be detached and clean before and after review.

Read completely, in order:

1. `docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`
2. `docs/assessments/2026-09-02-agent-handoff-stage-3-implementation-readiness.md`
3. `docs/superpowers/plans/2026-09-02-bot-mode-desktop-agent-handoff-stage-3.md`
4. `docs/reviews/2026-09-03-agent-handoff-stage-3-adversarial-code-review-prompt.md`
5. the complete remediation diff and every production caller of its changed
   functions

Do not read the pre-existing Stage 3 review, either independent reviewer
report, or the reconciliation before reaching your verdict.

## Findings to re-review

### S3-HANDOFF-001 — obsolete attention duplicated terminal wake

The original candidate retained both a pending `needs_input` wake and a later
terminal wake. Both delivery IDs loaded the current terminal snapshot and
started two identical model turns. The remediation atomically acknowledges
older unacknowledged delivery projections when a newer attention event commits,
while retaining the old source event and delivery row as evidence. A later
convergence pass proved that a gateway which had already validated the older
claim could cross the adapter boundary after supersession occurred during an
awaited target check. The final remediation adds a v2-to-v3 durable dispatch
reservation: supersession and dispatch are ordered by SQLite, a newer delivery
waits for an earlier reserved dispatch to settle, and an abandoned reservation
yields to the newer return after lease expiry. A later pass proved that the
messaging gateway used this reservation but the Desktop/TUI dispatcher did not.
The current candidate applies the same shared reservation immediately before
both gateway and Desktop/TUI model dispatch. The same pass also proved that a
successfully completed reservation kept its marker and could block a terminal
return created later. Terminal settlement now clears the active reservation;
new attention can acknowledge already-settled rows, and only an earlier pending
reservation can hold back a newer return.

A final convergence pass then proved that gateway adapter acceptance is not
turn admission: a busy adapter queued the older return, the immediate
transcript receipt was absent, and normal claim release cleared the reservation
and consumed another attempt on every suppressed replay. That allowed a newer
terminal return to cross the adapter boundary and could exhaust the older
return after only one actual submission. The current candidate marks handoff
returns as non-control internal events so the established identity-preserving
FIFO handles busy sessions. After adapter acceptance, it durably retains the
dispatch reservation in a receipt-pending state. Same-host transcript probes do
not consume attempts or admit newer returns; a visible delivery ID completes
the row without another model turn. A final controller probe then proved that
reloading the background service changes its supervisor owner UUID without
replacing the gateway process or its live in-memory identity guard. The current
candidate transfers a receipt-pending lease across that owner rotation without
clearing the dispatch reservation or consuming an attempt. The gateway uses its
process-local identity only to distinguish that live owner rotation from a true
process restart; after a true restart it releases the stale receipt reservation
for durable transcript reconciliation and normal keyed retry. A final
controller regression proved that this restart-only release left the temporary
identity which the new process had just installed, suppressing that process's
later retry. The current candidate removes the temporary identity before the
restart reconciliation return. An independent pass also proved that a full
32-event busy FIFO logged and dropped a handoff return while still reporting
adapter acceptance, leaving a receipt reservation whose receipt could never
appear. The current candidate makes FIFO admission observable to the handoff
producer. Capacity rejection releases the reservation and process identity,
restores the claim attempt, records bounded backpressure, and retries after
capacity is available; it does not weaken the queue cap. The next independent
pass proved that the bundled Raft adapter's wake-specific `handle_message()`
override bypassed the shared busy-session handler for these synthetic returns,
overwriting an older pending return while still reporting acceptance. The
current candidate delegates non-control internal events to the base adapter so
the same runner-owned FIFO and backpressure contract applies; ordinary Raft
wake hints retain their existing shortcut. The next independent pass proved
that the earlier restart-draining branch ignored FIFO rejection and reported a
dropped return as queued before reaching the observable internal-event branch.
The current candidate routes non-control internal events through the FIFO
before restart-drain messaging, so both busy states share one acceptance and
backpressure result. A fresh Claude pass then proved that an admitted return
could still be discarded before persistence by stale-lock healing, a true
conversation boundary, or the pending-turn drain during shutdown. The durable
receipt reservation and process identity then stayed live indefinitely,
blocking a newer terminal return. It also exposed that the recursive queued
turn did not carry the delivery ID or display metadata into agent persistence,
so even an ordinary successful drain could never create the receipt. The
current candidate preserves pending work during stale-lock healing, rejects
handoff admission while draining, carries the stable delivery ID and bounded
handoff metadata through the recursive turn, and explicitly releases the keyed
dispatch reservation when a conversation boundary or drain knowingly discards
that exact queued event. It does not use an elapsed-time receipt budget, which
could duplicate a legitimate return waiting behind a long-running turn.

Prove or falsify all of the following:

- `needs_input -> active -> succeeded`, with the host absent until terminal,
  yields one due delivery, one current Needs Attention row, and one terminal
  model wake;
- the older row remains queryable and its delivery truth is not rewritten;
- if supersession wins before dispatch reservation, the older claimed delivery
  is fenced before adapter/model submission on both initiating host kinds;
- if dispatch reservation wins first, the older return settles before the
  newer delivery becomes due, then the newer return remains deliverable;
- adapter queue acceptance without a transcript receipt retains the reservation
  and stable delivery identity, does not merge distinct handoff return texts,
  and does not consume additional delivery attempts while polling;
- background-service owner rotation in the same gateway process transfers the
  receipt-pending lease without reinjection, clearing the reservation, or
  consuming an attempt;
- true process restart releases both the receipt reservation and the temporary
  identity installed by the recovering process, permitting its later retry;
- a full busy FIFO reports backpressure rather than adapter acceptance during
  both ordinary busy handling and the live restart-drain window, consumes no
  delivery attempt, and permits the current or a newer superseding return to
  run exactly once after capacity is available;
- the bundled Raft adapter cannot bypass that FIFO/backpressure boundary or
  overwrite an older queued handoff return;
- a queued handoff return carries its delivery ID, display metadata, and hop
  count into the actual recursive agent turn so ordinary FIFO completion
  creates the authoritative transcript receipt;
- stale-lock healing preserves the queued event for the replacement owner;
- gateway draining rejects new handoff admission, while a return accepted
  before draining or a true conversation boundary releases only that discarded
  event's keyed dispatch reservation and process identity;
- an abandoned reserved dispatch reconciles through transcript receipt and
  restart/lease recovery without blocking or duplicating the newer return;
- replay of either observation cannot erase or duplicate the current delivery;
- acknowledgement, failed wake, attention-only policy, restart recovery,
  host/profile filtering, attempt limits, and one-delivery transcript replay
  remain correct; and
- the change does not suppress a later terminal return after an earlier return
  was already accepted by its consumer.

### S3-HANDOFF-002 — malformed YAML fell through to compatibility transport

The original candidate used the merged/LKG config loader, so syntactically
malformed YAML looked like an empty directory and could select a colliding
local compatibility target. The first remediation validated the initiating
profile's raw file before resolution; the first convergence pass then proved
that YAML null/empty documents were normalized before strict root validation.
The final remediation rejects those existing documents while preserving a
genuinely missing file and a valid `{}` mapping.

Prove or falsify all of the following:

- syntactically malformed, non-mapping, unreadable, and unavailable existing
  config (including a broken symlink) fail before local, peer, bare-peer, or
  relay fallback and before any handoff, subprocess, peer DM, relay,
  warning-backup, or other transport side effect;
- missing config and valid minimal config retain the documented empty-directory
  compatibility behavior;
- valid semantic directory errors still fail closed;
- named profiles read only their own directory and peer registry; and
- Bot and Desktop callers map the closed resolver error without leaking raw
  YAML, paths, credentials, or peer URLs.

### HOFF-G01 — accepted gateway return could be republished before persistence

The original candidate removed a delivery identity from the in-process guard
after the adapter accepted it but before the synthetic turn was visible in the
session transcript. A second queue/restart-scan publication could therefore
inject the same delivery again. The first remediation retained the identity
when a receipt read returned false; the first convergence pass proved that a
receipt-read exception still released it. The final remediation marks the
accepted interval before attempting that read, while releasing the durable
claim for later receipt reconciliation. A later convergence pass proved that
release was still wrong when a busy adapter had only queued the turn: it
cleared the dispatch reservation, enabled a newer return, and burned the retry
budget while the in-process guard suppressed duplicate submission. The current
candidate retains the durable reservation across this accepted-but-unpersisted
interval and uses receipt-only same-host retries that do not increment attempts.
Distinct handoff returns use the gateway's existing non-control internal FIFO,
not generic pending-text merging. Receipt-pending claims may transfer between
supervisor owners because a background-service reload rotates that owner while
the gateway and its in-memory identity guard remain live. A true gateway process
restart has no matching in-memory identity, so it releases and reconciles the
stale receipt reservation before retry instead of trusting vanished process
state. That restart-only path also removes the temporary identity installed by
the new process before returning, so it cannot suppress its own later retry.
The internal FIFO now also reports capacity rejection across the adapter
boundary. That path clears the durable reservation without consuming an
attempt; only actual FIFO admission enters receipt-pending reconciliation. The
bundled Raft adapter now forwards non-control internal returns through this
base-adapter boundary instead of applying its wake-only pending-slot shortcut.
Non-control internal events now reach that boundary before the generic
restart-draining response branch, which previously ignored FIFO rejection and
misclassified a dropped return as adapter-accepted. A later review proved that
post-acceptance loss could still leave receipt-pending state unbounded and that
the recursive queued turn omitted the delivery receipt metadata. The current
candidate closes those concrete drop sites without a speculative timer: drain
admission reports backpressure, stale-lock healing keeps its pending event,
queued recursion propagates the stable receipt identity, and known boundary or
drain discard atomically clears the matching durable dispatch reservation so a
normal keyed retry or a newer terminal return can proceed.

Prove or falsify all of the following:

- an adapter-accepted delivery absent from the transcript cannot be injected a
  second time in the same gateway process;
- a busy adapter's queue acceptance cannot release the durable dispatch
  reservation, unblock a newer return, merge two delivery identities, or
  consume the attempt budget while waiting for persistence;
- rotating the background-service supervisor owner inside the same gateway
  process cannot cause reinjection or clear the accepted dispatch reservation;
- a true process restart can release the stale reservation and subsequently
  retry in the same new gateway process without self-suppression;
- a full busy FIFO cannot masquerade as acceptance during ordinary busy handling
  or restart draining, retain an impossible receipt reservation, exhaust
  attempts, or block a newer terminal return;
- a bundled adapter override cannot replace a queued return or skip the shared
  backpressure signal while reporting acceptance;
- queued-turn persistence receives the stable delivery ID and bounded display
  metadata needed for transcript reconciliation;
- stale-lock repair cannot discard an accepted return, and a known boundary or
  drain discard cannot leave its receipt reservation or process identity live;
- once the delivery ID is visible in the persisted transcript, replay completes
  and acknowledges durable delivery without another model turn;
- adapter rejection, exceptions before acceptance, stale claims, gateway
  shutdown, and ordinary successful persistence do not leak the in-process
  identity or permanently suppress retry;
- process restart still relies on the durable claim/transcript receipt rather
  than in-memory state; and
- the bounded delivered-ID retention and session/profile ownership checks stay
  intact.

### HOFF-P01 — legacy peer DM stopped preserving ambient transport handlers

The original candidate reused the Workflow Runs client for legacy peer-DM
session lookup and chat, which forced the Workflow-only proxy-free opener.
Stage 2 explicitly keeps the peer CLI's installed proxy, cookie, TLS, and
instrumentation policy while requiring Workflow handoffs to bypass proxies.
The remediation gives the Runs client a closed opt-in used only by legacy peer
CLI calls; handoff clients remain proxy-free by default.

Prove or falsify all of the following:

- `hermes peer dm` session lookup/create and chat preserve the installed opener
  policy and still strip credentials on unsafe redirects;
- legacy peer run session lookup preserves the same policy as its other CLI
  requests;
- local and peer Workflow/Bot controlled handoffs still bypass ambient proxies;
- the peer CLI keeps its legacy environment credential fallback while handoff
  resolution remains profile-scoped and isolated; and
- timeout, response bounds, validation, hidden Bot Chat compatibility, and
  profile-specific routes remain unchanged.

## Binding clarification for the disputed model-contract report

The original shared prompt over-constrained invariant 1. The accepted Stage 3
plan says the return route is **optional**, and the implementation-readiness
assessment requires only that Bot/Desktop creation expose no deadline until a
consumer-neutral deadline policy exists. It does not require the shared
`HandoffSpec` type to reject every deadline or route-less internal conversation.

The live production constructors in `tools/bot_mode_dm.py` and
`tui_gateway/methods_agent_handoff.py` must still pass `deadline_at=None` and a
closed host-derived Bot/operator route. Report a defect only if a realistic
production caller can violate that accepted consumer boundary; do not promote
the prompt's stronger wording over the accepted plan.

## Binding clarification for the Windows compatibility report

Native Windows locking for the local CLI compatibility transport is explicitly
deferred to Stage 5. Stage 3 intentionally retains the Stage 1 POSIX-only
destination lock and fails closed with `local_cli_lock_unavailable` when that
lock cannot be provided. Do not recommend an unlocked Windows send or expand
this review into the deferred lock implementation. Report a defect only if the
candidate regresses an already-supported platform or bypasses the fail-closed
boundary.

## Required verification

```bash
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
"$HERMES_PYTHON" -c 'import hermes_cli.handoff, pathlib; print(pathlib.Path(hermes_cli.handoff.__file__).resolve())'

git status --short --branch
git rev-parse HEAD HEAD^{tree}
git rev-list --count 2affe5e02307475274cb3d72c24af59f72682945..HEAD
git diff --check 2affe5e02307475274cb3d72c24af59f72682945..HEAD
git diff --name-status 2affe5e02307475274cb3d72c24af59f72682945..HEAD

HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_directory.py \
  tests/hermes_cli/handoff/test_supervisor.py \
  tests/hermes_cli/handoff/test_bot_conversation_e2e.py \
  tests/hermes_cli/handoff/test_bot_return_recovery_e2e.py \
  tests/hermes_cli/handoff/test_runs_client.py \
  tests/hermes_cli/handoff/test_peer.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_peers.py \
  tests/gateway/test_completion_delivery.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/test_tui_gateway_queue_on_busy.py \
  tests/tools/test_bot_mode_dm.py \
  tests/hermes_cli/test_config_read_guard.py -q
```

Run additional deterministic probes needed to evaluate the bullets above.
Unavailable OS lanes or Desktop dependencies are limitations, not automatic
product findings.

## Proof and output standard

Return `BLOCK` only for a reproducible CRITICAL or IMPORTANT production defect
caused or left open by the remediation. A finding must include exact source and
caller, realistic trigger, wrong result, reproduction or rigorous interleaving,
why tests miss it, and the smallest root fix. Do not report style, desired
refactoring, speculative hardening, or a test gap without wrong production
behavior.

Return:

1. reviewer, exact model/version, date, scope verification;
2. `PASS` or `BLOCK` verdict;
3. findings with complete proof, if any;
4. a bullet-by-bullet disposition for all remediated findings and both binding
   clarifications;
5. exact commands and results;
6. residual platform/dependency limits; and
7. final detached clean-worktree proof.
