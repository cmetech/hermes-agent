# Agent Handoff Stage 3 Preliminary Controller Review

**Date:** 2026-09-02

**Scope:** Stage 3 Bot Mode and Desktop consumption of the shared handoff
service, durable return delivery, restart recovery, and compatibility with the
delivered Stage 1 and Stage 2 Workflow paths.

**Architecture authority:**
[`2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`](../proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md)

**Implementation authority:**
[`2026-09-02-agent-handoff-stage-3-implementation-readiness.md`](../assessments/2026-09-02-agent-handoff-stage-3-implementation-readiness.md)
and
[`2026-09-02-bot-mode-desktop-agent-handoff-stage-3.md`](../superpowers/plans/2026-09-02-bot-mode-desktop-agent-handoff-stage-3.md)

## Verdict

**Historical preliminary review only.** This controller-led pass was not the
independent Claude-and-Codex gate. The authoritative Stage 3 adversarial result
is the later
[`2026-09-03-agent-handoff-stage-3-adversarial-review-reconciliation.md`](2026-09-03-agent-handoff-stage-3-adversarial-review-reconciliation.md),
which records the shared prompt, independent reports, remediation, and final
convergence verdict.

## Review method

This preliminary review traced the live Bot target resolver, local CLI and peer-DM
compatibility paths, local and peer Runs channels, shared store/service and
supervisor, gateway and TUI completion consumers, Desktop RPC and UI, Workflow
remote handoff tests, packaging, and host lifecycle. It treated the durable
ledger as authoritative, the in-process completion queue as acceleration only,
and the established peer registry/authentication/redirect-safe Runs transport
as fixed security boundaries.

The controller pass found two Critical, five Important, and one Minor
issue. Two follow-up passes found classic-CLI surface and correlation gaps. Each
behavior change was made test-first and committed separately. That pass closed
its own Critical and Important findings; the later independent gate found and
remediated the additional races recorded in the reconciliation.

## Findings and dispositions

### Critical: legacy explicit peer/profile timing was broken

The first implementation routed legacy `peer/profile` syntax through a
synchronous full peer conversation inside a supervisor tick whose budget is two
seconds. A normal peer turn can take up to 600 seconds, so the supervisor could
misclassify ordinary work as `submission_indeterminate`.

**Disposition:** Remediated in
`4853f5ddd216cba9b41b550b60df5108acd9c5f2`. Legacy bare-peer and
`peer/profile` targets again use the established background `hermes peer dm`
path and timing. Only a canonical endpoint URI or configured directory alias
opts into the durable controlled Runs conversation. The module contract and
tests now state that boundary explicitly.

### Critical: a non-initiating host could claim a return delivery

Gateway and web/TUI hosts can share a profile-local ledger, but the initial
return route did not identify which host owned the initiating session. Either
process could therefore claim a row into its own process-local queue, causing
the wrong host to consume retry attempts without being able to deliver the
return.

**Disposition:** Remediated in
`4ad22a301d79ddca0b5565dc1f7351f43e4faa4e`. Bot return routes now contain a
closed, host-derived `host_kind` (`gateway` or `web`). The store filters by that
field before claim; the supervisor and completion consumer both enforce it.
The shared-ledger regression proves that the wrong host neither claims the row
nor increments its attempts and that the initiating host can publish it.

### Important: malformed directory configuration fell through to legacy routing

An invalid `handoff.agents` entry could be mistaken for a missing alias and
then fall through to legacy local/peer/relay resolution. That weakened the
configured directory trust boundary.

**Disposition:** Remediated in
`4648d038ac2f2dd803694218b0ec13e412e959e2`. Invalid configuration now fails
closed; only a genuine missing lookup may continue to compatibility routing.

### Important: return-wake policy failed open

Parse errors or a non-mapping `bot_mode` value could fall back to automatic
wake even though the operator configuration was invalid.

**Disposition:** Remediated in
`5e0456f9ec1d39449cfdbf90c2b444ed912062dd`. The initiating profile's raw
configuration is validated before the read-only merged value is used. Read or
shape failures retain durable Needs Attention state and do not wake the model.
An omitted valid setting keeps the documented default.

### Important: Desktop detail omitted the bounded result

The Desktop inspector exposed state and evidence but not the terminal result,
so an operator could acknowledge durable attention without seeing the reply.

**Disposition:** Remediated in
`3973be3d404d238a4e81ca4057b05adb1e85a67a`. `agent_handoff.get` and
`agent_handoff.evidence` expose an at-most 8 KiB, forcibly redacted
`result_preview`. List responses remain metadata-only, and Desktop renders the
preview only after detail is opened.

### Important: restart and race coverage stopped short of real boundaries

The original Task 10 tests did not prove initiator-ledger restart after real
authenticated Runs admission, destination completion racing cancellation, or
Desktop/TUI process disconnect and reconnect.

**Disposition:** Remediated in
`d1b53b2cd4640e1313232d233fd084abffa12d20`. New tests cross the real local
authenticated Runs boundary, close and reopen the initiating store before
observation and delivery claim, race cancellation against destination
completion, and use two fresh TUI gateway subprocesses to reopen the same
durable result and attention state. Follow-up commit
`f168ca873d9603559ce4001344e155e846a4a8ba` crosses the stronger process cuts:
a gateway-owned supervisor observes and claims across fresh processes, and a
fresh TUI process acknowledges a real persisted transcript receipt after its
predecessor exits without acknowledgement, without running another model turn.

### Important: the aggregate gate exposed host-lifecycle coupling

The first affected-file gate completed 1,344 assertions but bus-erred on macOS
before the generic web background-service lifecycle file could report. Stage 3
had introduced the real core supervisor into that generic test, while the
repository already had a separate whole-Workflow SQLite/background-thread
lifecycle defect on the merge base.

**Disposition:** Remediated for the Stage 3 change in
`7bb611b434100da321bcdc278d4b96a22ca9acf2`. The generic web lifecycle test
uses inert service and reconciliation fakes to test its own contract. A focused
test starts and stops the real handoff supervisor five times, asserts both of
its threads terminate, and reopens the database. The final 34-file aggregate
gate is clean. This does not claim to fix the inherited whole-Workflow macOS
defect.

### Minor: Bot DM module documentation was stale

The module description still implied only the pre-Stage-3 transport behavior.

**Disposition:** Corrected with the peer timing remediation in
`4853f5ddd216cba9b41b550b60df5108acd9c5f2`.

### Follow-up Important: classic CLI had no durable return host

Classic `hermes ... chat -c "Bot Chat"` has no gateway/web background host.
Canonical and directory targets therefore created an invalid empty-host return
route, while the fixture had masked the gap by always supplying `gateway`.

**Disposition:** Remediated in
`e415cbb17158ff1572dee7eec968167678de1a97`. A classic CLI Bot Chat keeps the
existing background local delivery for a new friendly-name send. Canonical and
directory targets fail with an explicit unsupported-surface error and create no
handoff until the Bot Chat runs under Gateway or Desktop.

### Follow-up Important: legacy fallback could discard correlation

A classic CLI friendly-name continuation with `handoff_id` could bypass the
durable service and start a new legacy delivery, losing both correlation and
mechanism immutability. The same class included legacy `peer/profile`, bare-peer,
and relay fallbacks.

**Disposition:** Remediated in
`ae5f8da5b6c09e096eb6ae897557c9ebe01f6a9b`. Any nonempty `handoff_id` is now
rejected before any compatibility transport can run. Canonical/directory
continuations with a trusted Gateway/Desktop host still enter the durable
service first. Regressions prove no service call or legacy process spawn occurs
for all three directly resolvable fallback shapes.

## Clarifications established by review

1. Legacy local names keep Bot Chat CLI compatibility. Legacy bare-peer and
   `peer/profile` names keep direct Peer DM timing. Canonical URIs and configured
   directory aliases are the explicit durable controlled-conversation path.
2. A Bot return is bound to the initiating host as well as its profile and
   session. Host identity is supplied by trusted gateway/TUI context, never by
   the model, Desktop renderer, or remote destination.
3. Invalid directory or wake configuration fails closed. The durable attention
   record remains available when automatic wake is disabled or unsafe.
4. Handoff lists remain bounded metadata. Detail/evidence may expose only the
   bounded and redacted terminal result preview, never credentials, raw
   authorization material, or unrestricted remote errors.
5. Stage 3 Bot/Desktop creation deliberately exposes no deadline. Workflow
   remains the deadline authority, and its authenticated remote E2E continues
   to cover deadline/cancellation behavior.

## Verification evidence

The Stage 3 planning baseline was:

```text
Focused Python: 421 passed, 0 failed, 1 Windows-only skip
Installed wheel: 1 passed, 0 failed
Desktop focused: 4 files passed, 51 tests passed, 0 failed
```

After remediation, the exact affected Python gate ran through
`scripts/run_tests.sh` with `HERMES_TEST_FILE_RETRIES=0` and collected 34 files:

```text
1,371 passed, 0 failed, 5 platform-specific skips
```

The real-boundary Task 10 gate reported:

```text
161 passed, 0 failed, 1 platform-specific skip
```

The cancellation/completion and transcript-acknowledgement crash stress cases
were repeated seven times with file retries disabled:

```text
14 of 14 test executions passed
```

The installed-distribution gate built and extracted the wheel, imported the
installed handoff store/supervisor, exercised the v2 ledger and attention
contract, and verified the six Stage 3 `agent_handoff.*` RPC methods alongside
the three existing session-transfer `handoff.*` methods:

```text
2 passed, 0 failed
```

The exact Stage 3 Desktop files and typecheck reported:

```text
6 files passed, 66 tests passed, 0 failed
TypeScript typecheck passed
```

An earlier complete Bot-plugin run passed all 60 files and 579 tests. A later
diagnostic passed 578 and failed one unchanged asynchronous ordering assertion
in `group-rounds.test.ts`; that test passed all 43 cases immediately in
isolation, while the exact Stage 3 files remained green. Stage 3 does not alter
the room-round code or test.

Targeted Ruff checks and `git diff --check` passed for the remediated paths.

## Remaining platform and inherited risks

- POSIX CLI destination locking and restart recovery remain intentionally
  unavailable on native Windows. Windows-specific cases stay skipped and the
  path fails closed rather than pretending to serialize access.
- Linux-specific process-group recovery remains covered by its platform case
  and should run in Linux CI.
- The previously recorded whole-Workflow macOS SQLite/background-thread defect
  remains present from the Stage 3 merge base. Stage 3 neither depends on it nor
  expands scope to repair it; the dedicated supervisor shutdown regression
  proves the new Stage 3 threads quiesce.
- The unchanged Desktop group-round ordering test can be load-sensitive in the
  full parallel Bot-plugin suite. It passes in isolation and is outside the
  Stage 3-owned Desktop files; the focused Stage 3 UI gate and typecheck are
  clean.
- Peer DM replacement, relay retirement, generic channels, repository-mediated
  handoffs, A2A, and Stage 4-5 migration work remain deferred.
