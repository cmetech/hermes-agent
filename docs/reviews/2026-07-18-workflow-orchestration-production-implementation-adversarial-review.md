# Portable Workflow Orchestration Final Remediation Adversarial Review

**Fresh review date:** 2026-07-19

**Reviewed branch:** `feat/workflow-production-remediation` through
`2feebb17343142d25271bd27590a1d687ebfa616`

**Verdict:** **READY FOR MAINTAINER MERGE REVIEW. No Critical or High merge
blocker remains.**

## Adversarial follow-up correction

The initial fresh-review conclusion at `546279a44` was not the final word. The
independent remediation-fix review at `aea2d9d95`, preserved in
`2026-07-19-workflow-orchestration-remediation-fix-adversarial-review.md`,
reopened one High (NF-H1) and identified NF-M1–NF-M5. That correction is
accepted rather than obscured.

The six post-review commits close each item with focused red/green evidence:

| Finding | Commit | Post-fix evidence |
|---|---|---|
| NF-H1 | `1be33f389` | A real web-leader/Gateway-follower topology delivers and durably acknowledges the Gateway projection from the port-bearing standby. |
| NF-M1 | `326193c63` | Real read APIs redact the provenance route and mask/hash Gateway transition keys without breaking delivery. |
| NF-M4 | `e7e9db9b7` | SQLite contention before a durable `sending` row returns retryable; post-send receipt loss still remains outcome-uncertain. |
| NF-M2 | `883b33ad5` | A stalled foreground scheduler cannot claim after exact owner/epoch adoption in the competing transaction. |
| NF-M3 | `fefed8eb2` | Promotion and new admission skip older waiters blocked on unrelated held lanes while preserving FIFO among eligible waiters. |
| NF-M5 | `ace002436` | Gateway reload, provider hot-add, and Gateway delivery suites are mandatory members of the no-argument merge gate. |

The post-fix no-argument gate passed 652 tests with one intentional platform
skip, the installed-distribution test, 17 Desktop tests, and TypeScript
compilation. The focused cross-finding selection passed 102 tests, and the
hash-pinned cumulative v2.0.9 migration test passed separately. The thirteen
new Low findings remain explicitly backlogged as non-blocking follow-ups; none
is silently described as fixed.

## Follow-up-fixes correction

The independent follow-up-fixes review at `b850e2cc7`, preserved in
`2026-07-19-workflow-orchestration-followup-fixes-adversarial-review.md`,
verified NF-H1 and NF-M1–NF-M5 closed and found one new Medium, NF2-M1. The
synchronous standby delivery drain could occupy the election thread for the
full adapter timeout budget and delay failover by minutes.

`2feebb173` moves standby delivery onto one bounded worker. The election loop
continues to observe and contend while that worker is blocked; the existing
per-row outbox lease and receipt machinery remains unchanged. A real two-host
test stops the web leader while the Gateway adapter is blocked and proves the
Gateway acquires leadership before the adapter is released. A companion test
pins `retryable_failure` to `outbox.fail()` and verifies the row returns to
pending with its attempt and error recorded. The coordinator/lifecycle
selection passed 63 tests, and the exact-commit no-argument gate again passed
652 tests with one platform-conditioned skip, the installed-distribution
test, 17 Desktop tests, and TypeScript compilation.

## Fresh-review scope and method

This review re-ran the branch-owned merge gate, inspected the complete Task
1–19 diff and sibling paths, checked the native Linux/macOS/Windows evidence,
and reviewed the clean-environment install/update/rollback rehearsal. It
specifically re-audited authorization, evidence containment, interaction
binding, idempotency/provenance, showcase trust, coordinator leases and epochs,
process recovery, terminal journal capacity, scheduling fairness, notification
repair/delivery, provider reload, authenticated Gateway/API admission, Desktop
attention/history, machine envelopes, resource bounds, packaging, and
migrations.

The architecture still has the required shape: Hermes core remains a narrow
generic plugin host; workflow policy and execution stay in the plugin; there is
no permanent model-facing workflow tool; and no prompt, model toolset, or
conversation-history mutation was introduced. HTTP and Gateway mutations are
bounded persistence-and-wake operations and do not execute workflow tails.

## Fresh-review disposition

| Prior finding group | Fresh-review result |
|---|---|
| C-01 authorization bypass | Closed: immutable authority distinguishes read, write, delivery, and admin; real middleware denies sibling mutations for read-only and unbound delivery principals. |
| C-02 evidence exfiltration | Closed: descriptor-based no-follow reads verify type/identity/containment before and after open. |
| H-03/H-04 coordinator fencing | Closed: execution, completion, retry, and shutdown are transactionally bound to exact owner and epoch. |
| H-05 foreground orphan | Closed: expired foreground ownership is adopted or reconciled without replaying uncertain effects. |
| H-06/H-07 idempotency/provenance | Closed: volatile PID, actor, source instance, and return route are outside the semantic start identity; stable cross-process retries join. |
| H-08/H-09 Desktop attention/history | Closed: attention is itemized/actionable and board/history/archive use complete keyset traversal. |
| H-10/H-02 direct API | Closed: plugin-owned authenticated `POST /runs` is background-only, coordinator-gated, server-derived, and never advances synchronously. |
| H-11/H-01 Gateway delivery | Closed after NF-H1: authenticated invocation carries a server-minted opaque return-route capability into durable destination-bound delivery, and a port-bearing standby drains it when a web host owns coordinator leadership. |
| M-04–M-16 | Closed; NF-M1–NF-M5 also fixed: bounded cursor sweeps, eligible-waiter FIFO, exact foreground claim fencing, retry-safe pre-send storage handling, opaque projections, and mandatory async gate coverage have current tests. |
| L-01–L-09 | Closed: machine envelopes/failures, legacy provenance, sanitization, bounded registries/caches, Desktop resilience/accessibility, and gate quality were completed in Tasks 17–19. |
| Prior M-01 | Closed: native Windows, Ubuntu, and macOS workflow portability jobs passed. |
| Prior M-02 | Closed: clean install plus old/current/old/current rehearsal preserved seven evidence hashes and stable idempotent lookup. |
| Prior M-03 | Closed for this branch: scoped Desktop lint and full typecheck pass; the unrelated repository lint baseline is recorded, not hidden. |

The original two High product-surface gaps were implemented in Tasks 15 and
16 using the approved amendments. Client strings do not become authenticated
identity, authority, provenance, scope, or delivery routes. The Gateway port
accepts only an opaque server-minted return-route capability, and remote hosted
sessions do not inherit local-admin capability merely by existing.

## Fresh verification conclusion

The final no-argument merge gate at `2feebb173` passed 652 Python tests with
one platform-conditioned skip, the installed-distribution integration test, 17
Desktop tests, and TypeScript compilation. Native workflow portability passed
on Windows, Ubuntu, and macOS. The clean distribution rehearsal passed CLI,
foreground showcase, real FastAPI middleware/router, authenticated Gateway,
and trust preflight paths, then preserved immutable evidence and stable
idempotency through upgrade, rollback, repair, and re-upgrade.

The overall repository CI workflow also reports unrelated historical/private-
branch baseline failures. They are enumerated in the companion
`2026-07-18-workflow-orchestration-production-remediation-verification.md` and
do not reopen a workflow Critical or High finding. No merge, tag, release, or
deployment was performed.

## Release-blocker status after remediation

| # | Status | Current evidence |
|---|---|---|
| 1 | Complete | Index loss/corruption preserves evidence and requires exact corroborated repair. |
| 2 | Complete | Cleanup is authority-bound preview plus exact single-use confirmation. |
| 3 | Complete | Spawn intent, process identity, Job Objects, and uncertain-effect replay fences pass. |
| 4 | Complete | Journal/projection/index recovery fails closed and cumulative migration preserves bytes. |
| 5 | Complete | Generic host lifecycle, safe mode, health, overlap refusal, and real provider reload pass. |
| 6 | Complete | Native multiprocess coordinator and process tests pass on all three OS runners. |
| 7 | Complete | Every runnable transition is durable, wake-driven, fenced, bounded, and FIFO-admitted. |
| 8 | Complete | Background admission refuses an unhealthy/missing coordinator. |
| 9 | Complete | CLI and authenticated API stable-key admission join across process/reload boundaries. |
| 10 | Complete | Skill/showcase behavior uses the real command/parser/store path. |
| 11 | Complete | Sources and authenticated actors/routes are server-derived and durable. |
| 12 | Complete | Capability, evidence, sanitizer, store-cache, and long-poll bounds pass real middleware tests. |
| 13 | Complete | Desktop attention, inspector, evidence, archive/history, and state-valid actions pass. |
| 14 | Complete | Retention, notification repair/retry/prune, cleanup, and UTC boundaries pass. |
| 15 | Complete | Desktop and Gateway each have authenticated destination-bound delivery owners. |
| 16 | Complete | Native, cross-surface UAT/soak, packaging, and rollback evidence is recorded. |
| 17 | Complete | Fresh adversarial review finds no Critical or High merge blocker. |

## Historical pre-remediation review (superseded)

The remainder of this document is the 2026-07-18 review retained as historical
evidence. Its open findings and NOT READY verdict describe the pre-remediation
commit range and are superseded by the fresh review above.

**Historical date:** 2026-07-18

**Historical verdict:** NOT READY FOR RELEASE — implementation was
substantially complete, but two High product-surface gaps and the
cross-platform/UAT gates remained open.

## Reader and required action

This review is for the maintainer deciding whether the implementation may move
to release-candidate status. After reading it, the maintainer should either
approve the two focused design amendments below or choose a narrower supported
surface and amend the product promises before further implementation.

## Scope and baseline

The review covers the implementation commits after
`c147d2ba8` through `d44b7e14d` on
`feat/workflow-production-remediation`. It checks the approved production plan,
the re-review conditions, the shared/downstream ledger, and the actual CLI,
Desktop REST, Gateway lifecycle, workflow coordinator, evidence, retention,
notification, skill, and test code.

No existing implementation worktree commits were merged. No release, tag, or
remote deployment was performed.

## Confirmed implementation outcomes

- Base Hermes hosts generic plugin background services through the approved
  blocking `run(stop_event)` and cached `health()` protocol. The generic host
  modules contain no workflow imports, scheduler types, agent instances,
  conversation state, model credentials, prompt data, or workflow decisions.
- Web/Desktop and Gateway start and stop applicable generic services through
  their existing long-lived lifecycles. Startup failure is isolated, safe mode
  skips services, and reload cannot overlap an unquiesced generation.
- The workflow plugin owns coordinator election, heartbeat, wake processing,
  retry wake-up, queued promotion, continuation, stall health, foreground
  arbitration, process fencing, evidence, retention, and notification state.
- Missing, empty, corrupt, replaced, and inconsistent admission indexes preserve
  run evidence and fail closed. Cleanup is preview-only until an exact,
  confirmation-bound execution request is supplied.
- Expired executor leases retain process identity. Live work may be reclaimed;
  uncertain outward effects require reconciliation instead of automatic replay.
- Background admission is refused without a fresh coordinator. Foreground
  execution is explicit and fenced against a live background leader.
- CLI JSON envelopes, exit codes, CAS conflicts, event tailing, doctor status,
  next actions, and source-scoped deterministic idempotency have behavioral
  coverage.
- Provenance distinguishes verified adapters, system schedules, local-admin
  claims, and legacy unknown state. Chat or agent skills that spawn the CLI are
  recorded as local-admin claims, not authenticated Gateway actors.
- The authenticated workflow REST surface provides bounded store reuse,
  bounded long polling, sanitization, evidence queries, reversible archive,
  history views, cleanup preview/execution, and state-valid mutations. Desktop
  mutations return promptly and continuation stays outside the HTTP request.
- Desktop provides board/history/archive views, evidence inspection, valid
  recovery actions, and durable leased notification projection. Electron must
  acknowledge projection before the server marks delivery complete, and a
  bounded persistent receipt cache covers projection-success/server-ack failure.
- Notification transition facts are immutable and distinct from coalesced
  delivery summaries. The final review found and fixed a newest-200-only repair
  defect: journal-to-outbox reconciliation now uses a durable paginated cursor
  with wraparound, so older crash gaps are eventually revisited.
- The reusable workflow skill and showcase skill use runtime-supported commands,
  stable identities, one mutation at a time, authoritative JSON, human-gate
  stopping, and explicit unavailable/no-progress handling.

## Open findings

### H-01 — Gateway notification delivery has no authenticated projection owner

The durable outbox currently creates and reconciles only `desktop`
destinations. Its REST lease endpoint also leases only that destination. No
plugin-owned adapter projects a verified stored return route through a live
Gateway transport, and no test covers send-success/receipt-loss deduplication on
a real Gateway destination.

The repository has platform senders and in-process adapter state, but it does
not expose one public, authenticated, restart-safe outbound capability to a
plugin background service. A chat or background-agent skill that shells out to
the CLI has only `local_admin_claim` assurance and therefore cannot safely mint
the verified return route that Gateway delivery requires.

**Impact:** approval, input, failure, stall, completion, cancellation, and
reconciliation attention remain durable and visible in Desktop, but a
Gateway-originating operator is not durably notified when Desktop is closed.
Release-blocker items 15, 16, and 17 remain open.

**Required action:** follow the Phase 9 stop condition and approve a generic
Gateway invocation/delivery amendment. Do not import workflow code into Gateway
core and do not weaken return-route verification.

### H-02 — Direct authenticated API admission is absent and mutation provenance is Desktop-specific

The plugin REST router exposes cleanup, notification, list, detail, attention,
events, evidence, and `POST /runs/{run_id}/{action}` routes. It does not expose
`POST /runs`. Production run creation remains in the CLI and showcase adapters.

The mutation route also records approval and rejection channel evidence as
`desktop` even when called by a non-interactive authenticated API principal.
The authentication boundary verifies session/token/local-admin principals, but
it does not currently derive a truthful Desktop-versus-API invocation kind for
run admission and decision evidence.

**Impact:** the direct authenticated API admission UAT cannot run, API
idempotency/coordinator refusal is unproved, and API-origin evidence would be
mislabelled on decision paths. Release-blocker items 9, 11, 16, and 17 remain
open.

**Required action:** add a plugin-owned background-only admission endpoint that
uses the same package preparation, trust, immutable-input, idempotency,
coordinator-health, and `RunStore.start_run` contracts as the CLI. Derive
`source=api`, verified principal, operator scope, and decision channel from the
server authentication context. Do not execute a workflow tail in the request.

### M-01 — Native Linux and Windows gates have not executed

The local pass is macOS. Native Linux and Windows SQLite locking, atomic replace,
process-start identity, termination, coordinator takeover, migration fixture,
and notification/restart behavior remain CI/UAT evidence requirements.

### M-02 — Update and rollback rehearsal is incomplete

Wheel and sdist builds succeeded. A wheel extracted into an isolated target with
a clean Hermes home registered the workflow plugin and returned a valid JSON
CLI envelope. The permanent merge gate now repeats that test. A complete
dependency install plus update and rollback rehearsal has not run.

### M-03 — Repository-wide Desktop lint has a pre-existing red baseline

All task-owned Desktop files pass scoped ESLint and full TypeScript checking.
The repository-wide Desktop lint command still reports unrelated existing
errors in untouched Electron, release-update, cron, icon, and theme files. This
review does not sweep those unrelated changes into the workflow branch; the
maintainer must decide whether the release gate accepts a scoped no-regression
comparison or requires a separate baseline-cleanup change.

## Focused amendment choices

### Gateway invocation and delivery

1. **Generic authenticated plugin-command context plus Gateway delivery port
   (recommended).** Extend the existing generic plugin slash-command invocation
   contract with a bounded immutable context containing boundary kind, verified
   actor/session identity, and an opaque return-route capability. Expose a
   generic Gateway delivery port that accepts only that capability, sanitized
   text, and a durable idempotency key, and returns a transport receipt. The
   workflow plugin becomes the immediate consumer for native Gateway admission
   and outbox projection. This is the smallest approach that preserves
   authenticated route provenance across the start and delivery halves, but it
   adds a second carefully scoped generic plugin contract.
2. **Generic durable Gateway delivery broker.** Base Gateway owns a general
   delivery-request queue and transport workers; plugins enqueue opaque verified
   routes and payloads. This centralizes restart and receipt semantics but
   duplicates part of the workflow outbox authority and creates broader
   infrastructure than the immediate consumer needs.
3. **Declare Gateway notifications unsupported.** Keep chat starts as local-admin
   CLI claims and make all Gateway delivery suppressed/query-only. This avoids a
   shared change but contradicts the approved notification and surface-UAT
   requirements, so it is acceptable only with an explicit scope reduction in
   the design, plan, and user documentation.

### Direct REST admission

Use a plugin-owned `POST /runs` contract with required caller idempotency,
background execution only, verified server-derived `api` provenance, and stable
success/error envelopes. Desktop may use a distinct authenticated adapter route
or a server-issued client classification; a caller-controlled source header is
not authoritative. Foreground REST admission remains unsupported because a
bounded HTTP request cannot own execution safely.

## Verification evidence

- Full workflow suite: 59 files, 421 tests passed, including real SQLite,
  filesystem, subprocess, multiprocess coordinator, migration fixture,
  notification, retention, and resource-soak tests.
- Base merge gate at `f7677e382`: 623 Python tests, one extracted-wheel clean-home
  integration test, 17 Desktop workflow/Kanban tests, and TypeScript compile.
- Full Desktop script: 39 workflow/UI tests and 355 platform tests passed, with
  one intentional skip.
- Expanded lifecycle, auth, skill, background-agent, and prompt-alternation
  selection: 170 tests passed.
- Scoped Desktop ESLint, full Desktop typecheck, customization-ledger checker,
  and `git diff --check` passed.
- Wheel and sdist were built outside the repository. Both contain the workflow
  coordinator, notification modules, manifest, showcase packages, and skills.
  Extracted-wheel execution resolved the plugin from the installed target, not
  the source tree.

## Ordered release-blocker status

| # | Status | Evidence or blocker |
|---|---|---|
| 1 | Implemented; local gate passes | Index loss/corruption preserves evidence and requires repair. |
| 2 | Implemented; local gate passes | Cleanup defaults to preview and exact-token execution. |
| 3 | Implemented; local gate passes | Process identity and uncertain-effect reconciliation are fenced. |
| 4 | Implemented; local gate passes | Torn journal/projection recovery fails closed. |
| 5 | Implemented; local gate passes | Real-thread generic, web, Gateway, safe-mode, health, and reload tests pass. |
| 6 | Blocked | Local two-process tests pass; native Windows remains unexecuted. |
| 7 | Implemented; local gate passes | Durable wake coverage includes all mutation and lane-release paths. |
| 8 | Implemented; local gate passes | No healthy leader means background admission refusal. |
| 9 | Blocked | CLI contract passes; direct authenticated API admission is absent. |
| 10 | Implemented; local gate passes | Behavioral skill tests use the real parser/command seam. |
| 11 | Blocked | Existing sources are truthful, but verified direct API/Gateway admission adapters are incomplete. |
| 12 | Implemented; local gate passes | Bounded evidence/store/long-poll/auth paths pass real middleware tests. |
| 13 | Implemented; local gate passes | Desktop inspector/actions and bounded mutations pass. |
| 14 | Implemented; local gate passes | Archive/history/cleanup and UTC clock boundaries pass. |
| 15 | Blocked | Desktop owner passes; Gateway destination owner is absent. |
| 16 | Blocked | Native matrix, Gateway/API UAT, and update/rollback remain. |
| 17 | Blocked | H-01 and H-02 require maintainer-approved amendments. |

## Reader-test result

A fresh maintainer can identify the exact two design decisions, the unsupported
claims they affect, the completed safety mechanisms, the commands already
exercised, and the remaining release gates without relying on session context.
No placeholder API is presented as implemented, and no release readiness is
claimed from local-only evidence.
