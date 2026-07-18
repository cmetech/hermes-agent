# Portable Workflow Orchestration Production Implementation Review

**Date:** 2026-07-18  
**Verdict:** NOT READY FOR RELEASE — implementation is substantially complete,
but two High product-surface gaps and the cross-platform/UAT gates remain open.

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
