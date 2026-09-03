# Agent Handoff Stage 3 adversarial-review reconciliation

**Original candidate:** `2affe5e02307475274cb3d72c24af59f72682945`

**Final remediation candidate:** `7520760ff03f8c6b355b250cca51b741b8f56539`

**Final remediation tree:** `854382759c79fec11087b9057bd9b7b35fdc62bc`

**Merge base:** `c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d`

**Independent lanes:** Claude Code with Claude Opus 5 and Codex CLI with the
GPT-5 family, each reading the same prompt in a separate clean detached worktree.
Neither lane received the other lane's report or the controller's conclusions.

**Initial combined verdict:** `BLOCK` pending controller validation and
remediation.

**Final combined verdict:** `PASS`; both fresh independent lanes found no
remaining Critical or Important production defect on the exact final candidate.

## Review authority and source reports

The shared original prompt is
[`2026-09-03-agent-handoff-stage-3-adversarial-code-review-prompt.md`](2026-09-03-agent-handoff-stage-3-adversarial-code-review-prompt.md).
The bounded remediation prompt is
[`2026-09-03-agent-handoff-stage-3-adversarial-remediation-rereview-prompt.md`](2026-09-03-agent-handoff-stage-3-adversarial-remediation-rereview-prompt.md).

The original independent reports are:

- [`2026-09-03-agent-handoff-stage-3-adversarial-code-review-claude.md`](2026-09-03-agent-handoff-stage-3-adversarial-code-review-claude.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-code-review-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-code-review-codex.md)

The successive scoped reports are:

- [`2026-09-03-agent-handoff-stage-3-adversarial-remediation-rereview-claude.md`](2026-09-03-agent-handoff-stage-3-adversarial-remediation-rereview-claude.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-remediation-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-remediation-rereview-codex.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-convergence-rereview-claude.md`](2026-09-03-agent-handoff-stage-3-adversarial-convergence-rereview-claude.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-convergence-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-convergence-rereview-codex.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-dispatch-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-dispatch-rereview-codex.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-queue-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-queue-rereview-codex.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-capacity-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-capacity-rereview-codex.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-owner-restart-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-owner-restart-rereview-codex.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-raft-rereview-claude.md`](2026-09-03-agent-handoff-stage-3-adversarial-raft-rereview-claude.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-raft-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-raft-rereview-codex.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-drain-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-drain-rereview-codex.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-receipt-rereview-claude.md`](2026-09-03-agent-handoff-stage-3-adversarial-receipt-rereview-claude.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-receipt-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-receipt-rereview-codex.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-interrupt-rereview-claude.md`](2026-09-03-agent-handoff-stage-3-adversarial-interrupt-rereview-claude.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-tui-receipt-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-tui-receipt-rereview-codex.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-tui-session-rereview-claude.md`](2026-09-03-agent-handoff-stage-3-adversarial-tui-session-rereview-claude.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-tui-session-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-tui-session-rereview-codex.md)

The final independent reports are:

- [`2026-09-03-agent-handoff-stage-3-adversarial-final-rereview-claude.md`](2026-09-03-agent-handoff-stage-3-adversarial-final-rereview-claude.md)
- [`2026-09-03-agent-handoff-stage-3-adversarial-final-rereview-codex.md`](2026-09-03-agent-handoff-stage-3-adversarial-final-rereview-codex.md)

The 2026-09-02 controller review predates this independent gate. It is retained
as a historical preliminary review and points here for the authoritative
multi-LLM result.

## Controller reconciliation rules

1. A reviewer report is evidence, not an automatic code change. The controller
   validates each reported defect against the accepted architecture and live
   caller path.
2. A finding is confirmed only with a realistic wrong production result and a
   deterministic reproduction or complete concurrency interleaving.
3. Each confirmed finding receives a RED regression before the smallest shared
   root fix, then focused GREEN verification and another clean detached review.
4. Provider failures produce no verdict. A 404, 529, interrupted session, or
   unavailable dependency is recorded as a limitation and retried; it is never
   converted into either `PASS` or `BLOCK`.
5. Native-Windows-only behavior, real credentials, external inference, and
   multi-machine operation stay within their accepted platform or test lanes.

## Original findings and dispositions

| ID | Reviewer finding | Controller disposition | Result |
|---|---|---|---|
| AR-01 | Codex `S3-HANDOFF-001`: stale `needs_input` and terminal rows could both load the terminal snapshot and wake twice. | **Confirmed Important** | New attention atomically supersedes older undispatched projections. |
| AR-02 | Codex `S3-HANDOFF-002`: malformed agent-directory YAML could look empty and select a compatibility transport. | **Confirmed Important** | Existing malformed, non-mapping, null, empty, dangling, or unreadable config fails closed before fallback. |
| AR-03 | Codex `S3-HANDOFF-003`: every conversation value should reject a deadline or missing route. | **Rejected as prompt overconstraint** | The accepted Stage 3 plan makes `return_route` optional in the shared model and constrains live Bot/Desktop constructors. Those constructors use no deadline and a closed host-derived route. Workflow remains deadline owner. |
| AR-04 | Claude `HOFF-W01`: native Windows friendly-local delivery fails closed without a destination lock. | **Rejected as explicitly deferred** | Stage 5 owns native Windows destination locking. Sending without serialization would violate the accepted boundary; fail-closed behavior is correct meanwhile. |
| AR-05 | Claude `HOFF-G01`: adapter acceptance preceded transcript persistence, so an immediate replay could inject the same return twice. | **Confirmed Important** | Delivery identity and durable reservation now span adapter queueing and transcript receipt, with separate same-process owner-rotation and real-restart handling. |
| AR-06 | Claude `HOFF-P01`: legacy `hermes peer dm` unintentionally inherited the controlled handoff client's ambient-proxy bypass. | **Confirmed Minor** | The shared client has a closed legacy opt-in that preserves the installed opener and ambient proxy policy; redirect credential stripping remains mandatory. |

## Convergence findings

The re-review loop exercised progressively narrower race and trust-boundary
cases. Each `BLOCK` below was validated, reproduced, and fixed before advancing
the immutable candidate.

| Finding | Wrong result | Remediation commit |
|---|---|---|
| Null or empty directory document reached compatibility routing. | A configured trust-boundary file without a mapping could select a colliding local/peer/relay target. | `acbae1949b`, `9c31748e83` |
| Receipt-read exception reopened delivery. | An ambiguous post-adapter response could be treated as an ordinary retry and duplicate a model turn. | `dab94bd589` |
| Supersession won after gateway target lookup. | An obsolete input return could cross the adapter boundary after a newer terminal observation committed. | `62bd97a2c2` |
| A settled reservation blocked later terminal attention. | A successfully delivered input row retained a marker that suppressed the later terminal return. | `2234ab62f8` |
| Desktop/TUI omitted the shared dispatch CAS. | Supersession could win durably while an obsolete Desktop return still started a model turn. | `cf01f53a99` |
| Existing dangling or unreadable directory config looked absent. | Compatibility fallback could bypass an operator-present but unavailable trust-boundary file. | `f711df25a9` |
| Busy adapter acceptance was not model-turn admission. | The gateway released identity before the queued turn persisted its receipt; later returns could overtake or merge with it and receipt polling could consume attempts. | `83da572667` |
| Same-process supervisor ownership changed while receipt was pending. | A replacement supervisor could release another live producer's reservation or fail to reconcile its receipt. | `91820352f2` |
| Fresh-process recovery retained a temporary identity. | The next legitimate keyed retry self-suppressed and conflicted instead of entering the adapter. | `db5da1c2ab` |
| The bounded 32-entry busy FIFO reported false acceptance when full. | A dropped return entered impossible receipt-pending state and could block later attention indefinitely. | `c8ed1474fd` |
| The bundled Raft adapter bypassed the shared busy handler. | Its one-slot merge replaced an older handoff return while the gateway reported acceptance and retained an unreconcilable pending receipt. | `ff05217f57` |
| Restart draining bypassed observable capacity rejection. | A full FIFO dropped the return while the earlier draining branch reported adapter acceptance, consumed an attempt, and retained an impossible receipt reservation. | `951fc20680` |
| An accepted queued return was later discarded before transcript persistence. | Stale-lock healing or conversation reset could leave an unbounded receipt reservation that blocked a newer terminal return for the life of the gateway process. | `02d655c9cd` |
| Busy `/stop`, `/new`, and `/reset` discarded the adapter head before boundary reconciliation. | An accepted handoff return could be popped while its receipt reservation and identity remained live, again blocking every newer return until process restart. | `c3e7de1817` |
| Busy `/stop` reconciled only the adapter head, not the runner-owned FIFO tail. | A handoff return accepted behind an ordinary queued event could remain receipt-pending and block newer returns after the command discarded the complete queue. | `907b47b6ae` |
| Desktop/TUI retained an ordinary lease for the full model turn. | A legitimate multi-minute turn could exhaust eight delivery attempts before its transcript receipt settled. | `5af122a0fd` |
| Persisted Desktop/TUI receipt replay retained the poller's busy claim. | Durable settlement started no model thread, so later user prompts and returns could remain queued forever behind `running=True`. | `7520760ff0` |

The reviewer suggested bounding receipt polling. The controller rejected that
remedy because elapsed time cannot distinguish a discarded event from a slow
but live queued turn. The smaller deterministic fix releases only the exact
durable reservation at the concrete gateway discard boundaries. A follow-up
controller trust-boundary pass also required queued metadata to be authenticated
as an internal, non-control handoff return before it may authorize release.

## Remediation ledger

All production changes were test-first and committed atomically:

| Commit | Root behavior |
|---|---|
| `e46e501f84` | Supersede stale return attention. |
| `8986696272` | Fail closed on malformed directory YAML. |
| `acbae1949b` | Reject non-mapping directory configuration. |
| `52898e408a` | Suppress same-process replay while a gateway receipt is pending. |
| `fcbe98ad87` | Preserve legacy peer CLI transport policy. |
| `dab94bd589` | Retain uncertain gateway receipts after receipt-read exceptions. |
| `9c31748e83` | Reject null and empty directory documents. |
| `62bd97a2c2` | Fence superseded gateway returns with a durable dispatch reservation. |
| `cf01f53a99` | Apply the same supersession fence to Desktop/TUI returns. |
| `f711df25a9` | Reject existing unavailable directory configuration. |
| `2234ab62f8` | Release settled dispatch reservations so later attention can progress. |
| `83da572667` | Retain queued-return reservations, preserve FIFO identity, and poll receipts without burning attempts. |
| `91820352f2` | Reconcile a pending return across same-process supervisor owner rotation. |
| `db5da1c2ab` | Release temporary receipt identity during true process-restart recovery. |
| `c8ed1474fd` | Turn full-FIFO rejection into retryable, non-attempt-consuming backpressure. |
| `ff05217f57` | Route bundled Raft non-control returns through the shared FIFO/backpressure boundary. |
| `951fc20680` | Route durable returns through observable FIFO admission during gateway restart draining. |
| `02d655c9cd` | Reconcile an accepted handoff return at known gateway discard boundaries and preserve its queued turn context. |
| `ad4c53bac8` | Correct the queued-return test contract to inspect temporary hop context. |
| `d09c9aff09` | Require trusted internal delivery metadata before a discard can release a durable reservation. |
| `c3e7de1817` | Reconcile the exact queued handoff return discarded by the shared busy-command interruption helper. |
| `907b47b6ae` | Reconcile authenticated handoff returns across the complete interrupted FIFO. |
| `5af122a0fd` | Defer long Desktop/TUI turns into receipt-only delivery recovery. |
| `7520760ff0` | Release the TUI poller's no-turn session claim after persisted receipt settlement. |

The final remediation range contains 45 commits and 22 paths with
`+2832/-98`; 783 inserted lines are the two review prompts rather than
production behavior.

## Review-round history

| Candidate | Claude | Codex | Disposition |
|---|---|---|---|
| `2affe5e023` | `BLOCK` | `BLOCK` | Four accepted findings; two binding design clarifications. |
| `fcbe98ad87` | `PASS` | `BLOCK` | Closed YAML-null and receipt-exception residuals. |
| `9c31748e83` | `PASS` | `BLOCK` | Closed gateway supersession race. |
| `62bd97a2c2` | Provider failure; no verdict | `BLOCK` | Closed settled reservation, Desktop/TUI fence, and dangling-config defects. |
| `2234ab62f8` | Provider failure; no verdict | `BLOCK` | Closed accepted-but-queued return ordering and attempt accounting. |
| `83da572667` | Provider failure; no verdict | `BLOCK` | Closed full-FIFO false acceptance. |
| `91820352f2` | Provider failure; no verdict | `BLOCK` | Closed fresh-process identity leak; capacity finding remained open. |
| `db5da1c2ab` | Provider failure; no verdict | Capacity remediation still pending | Advanced only after the controller retained the open finding. |
| `c8ed1474fd` | Provider overload/interrupted obsolete attempt; no verdict | `BLOCK` | Closed the bundled Raft adapter bypass. |
| `ff05217f57` | `PASS` | `BLOCK` | Closed restart-draining false acceptance. |
| `951fc20680` | `BLOCK` | `PASS` | Closed accepted-then-discarded queued-return reconciliation. |
| `d09c9aff09` | `BLOCK` | Interrupted after candidate advanced; no verdict | Closed busy-command discard reconciliation. |
| `c3e7de1817` | Interrupted after controller RED; no verdict | Interrupted after controller RED; no verdict | Closed the sibling FIFO-overflow discard before accepting a verdict. |
| `907b47b6ae` | Interrupted after candidate advanced; no verdict | `BLOCK` | Closed long Desktop/TUI turn attempt exhaustion. |
| `5af122a0fd` | `BLOCK` | `BLOCK` | Both lanes independently reproduced the persisted-receipt TUI busy-session wedge. |
| `7520760ff0` | `PASS` | `PASS` | Authoritative final convergence. |

## Final controller verification

All commands used `HERMES_TEST_FILE_RETRIES=0`; Python tests ran through
`scripts/run_tests.sh` and collected real tests.

- Final 14-file required adversarial gate: 395 passed, 0 failed, 1
  native-Windows skip.
- Final complete affected Python gate: 1,404 passed, 0 failed, 5
  platform-specific skips across 34 collected files.
- Installed-wheel registration and Stage 3 surface: 2 passed, 0 failed.
- Complete Desktop Bot-plugin gate: 579 passed, 0 failed; TypeScript typecheck
  passed.
- The final approval/restart, cancellation, transcript-crash, TUI long-turn,
  TUI receipt-session, and busy-interrupt stress set ran 9 tests per pass for
  7 passes: 63 passed, 0 failed.
- Ruff on every remediation production/test path and `git diff --check`:
  passed.

The macOS whole-Workflow SQLite/background-thread lifecycle diagnostic remains
an inherited issue and is not worsened by Stage 3. An unchanged Desktop
group-round ordering assertion and a macOS local-CLI PID-file read have shown
load sensitivity in broader parallel diagnostics but pass immediately in their
focused/repeated gates. Linux-only process cases and native-Windows locking
remain covered by their platform lanes; native-Windows destination locking is
still intentionally deferred to Stage 5. The independent review lanes used no
live credentials, external inference destinations, or multi-machine peers.

## Final disposition

**PASS.** Claude and Codex independently returned explicit `PASS` verdicts on
`7520760ff03f8c6b355b250cca51b741b8f56539` and its exact tree. The final
affected, installed-wheel, Desktop, typecheck, stress, Ruff, and whitespace
gates are green. No Critical or Important Stage 3 finding remains unresolved.
