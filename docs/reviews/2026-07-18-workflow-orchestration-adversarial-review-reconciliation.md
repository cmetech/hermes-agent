# Workflow Orchestration Adversarial Review Reconciliation

**Date:** 2026-07-18
**Disposition:** Review accepted with the corrections below; implementation remains blocked pending design approval
**Reviewed artifact:** `docs/reviews/2026-07-18-workflow-orchestration-operator-experience-adversarial-review.md`

## Repository baseline

The planning pass started from the following exact state:

- checkout: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`
- branch: `fix/windows-index-materialization`
- HEAD: `7d0039b6a` (`docs(workflow): add operator experience implementation plan`)
- upstream relation: four commits ahead of `origin/fix/windows-index-materialization`
- implementation worktree: `.worktrees/workflow-operator-experience`
- implementation branch/HEAD: `feat/workflow-operator-experience` at `43edb4d4b`
- candidate commits: `a9ccb7e91` and `43edb4d4b`; neither is approved for merge
- unrelated untracked paths preserved: `dist/`, `docs/2026-07-16-hermes-provider-registry-fixes.md`, and other review artifacts

The implementation worktree was clean. The main checkout contained only the
untracked paths above. Commit `bb75289a5` from the production-review worktree
is already an ancestor of the planning baseline.

## Verdict reconciliation

The `NOT READY` verdict is confirmed. The review's headline count and its
later primary-finding tally disagree, so severity counts are not used as a
completion metric. Release readiness is instead governed by the ordered
blocker checklist in the amended implementation plan.

The review identified the correct defect classes. Two statements need narrower
wording, and one repository-hygiene subclaim does not apply:

- lease expiry does not erase every process-identity field; it removes the
  active claim while the attempt record retains identity. The remaining defect
  is still release-blocking because normal cancellation and stop-recording look
  through the active claim, so an executor can remain live and an outward
  effect can be replayed with an uncertain predecessor.
- the absent repository `CLAUDE.md` is not a demonstrated violation of this
  repository's governing instructions. The uncommitted review and missing
  change-ledger work are valid findings.
- prior-review remediation in F-28 is present and remains a verified positive,
  not new work for this plan.

## Finding-by-finding disposition

| Finding | Disposition | Verified code or artifact | Required amendment |
|---|---|---|---|
| F-01 admission evidence deletion | Confirmed, Critical | `plugins/workflow/store.py:RunStore._reconcile_admission` treats run directories absent from SQLite as disposable and removes quarantined content | Reconciliation must corroborate independent evidence, preserve uncertain data, mark repair required, and never infer deletion authority from a missing, empty, replaced, corrupt, or inconsistent index |
| F-02 cleanup default inversion | Confirmed, Critical | `RunStore.cleanup_runs` defaults to dry-run, but `plugins/workflow/cli.py:build_parser` uses `--dry-run` as `store_true` and `_cmd_cleanup` passes the resulting false default | Replace with preview-by-default plus explicit execute/confirmation after a stable impact summary |
| F-03 lease expiry/orphan process | Partially overstated; underlying risk confirmed, Critical | `RunStore.expire_stale_claims` removes `node["claim"]`; attempt identity survives, while `cancel_run` and `record_process_stopped` require the active claim | Preserve executor identity as active recovery evidence; prove termination where possible; route uncertain effectful attempts to reconciliation instead of replay |
| F-04 no durable continuation owner | Confirmed, Critical | `RunScheduler` is constructed by workflow CLI/showcase paths and tests; no long-lived host owns promotion, retries, continuation, or recovery | Add a workflow-plugin coordinator hosted by a generic plugin-service lifecycle |
| F-05 synchronous Desktop continuation | Confirmed against candidate commit, Critical | `a9ccb7e91:plugins/workflow/dashboard/plugin_api.py` calls `continue_run`, which invokes `RunScheduler.advance` inside the request and can execute the remaining graph | REST mutations commit a bounded state transition plus durable wake and return; only the coordinator continues work |
| F-06 random idempotency | Confirmed, High | `plugins/workflow/cli.py:_cmd_run` and showcase run use `secrets.token_urlsafe` when no key is supplied | Require caller-stable identity for JSON/non-interactive/background admission; interactive generation must be explicit and returned |
| F-07 false trigger provenance | Confirmed, High | production calls to `RunStore.admit_run` in workflow CLI/showcase hardcode `trigger_source="cli"` | Define server-recorded `desktop`, `chat`, `background_agent`, `cron`, `cli`, and `api` provenance with authenticated actor and return-route metadata |
| F-08 no durable notification substrate | Confirmed, High | no RunStore outbox or delivery ownership exists; the prior plan names UI projections only | Add transactional outbox, workflow-owned leasing/dedup/retry state, and destination-specific projection owners |
| F-09 torn journal tail | Confirmed, High | journal replay in `RunStore` treats malformed trailing JSON as a run-fatal load error | Add framed/checksummed or safely recoverable append semantics and quarantine only the incomplete tail with evidence |
| F-10 stale SQLite projection | Confirmed, High | run status is persisted in both `run.json`/journal and admission SQLite without a complete repair contract | Make the journal/run record authoritative, verify projection generations, repair safely, and fail closed for capacity decisions |
| F-11 operator scope is client asserted | Confirmed, High | `plugins/workflow/dashboard/plugin_api.py:_operator_scope` trusts `X-Hermes-Operator-Scope`; Desktop does not establish a verified principal boundary | Derive scope from authenticated server identity; headers may narrow but never grant authority; retain local-admin CLI policy explicitly |
| F-12 untruthful `next_actions` | Confirmed, High | `RunStore._next_actions` advertises unavailable actions and omits retry/reconcile/provide-input paths; REST and CLI vocabularies differ | Generate actions from one authoritative transition table shared by projections and handlers |
| F-13 unstable CLI machine contract | Confirmed, High | CLI exception handling emits plain stderr, CAS failures can escape, doctor returns zero on blocking findings, and `events --tail` calls head-oriented `tail_events` | Stable JSON success/error envelopes, documented exit codes, correct tail semantics, CAS conflict objects, and doctor nonzero on blocked readiness |
| F-14 prose-only skill enforcement | Confirmed, High | current skill tests assert Markdown phrases rather than command construction/results; candidate `43edb4d4b` remains prose-led | Add reusable operator helper/fixtures and behavioral CLI tests; keep showcase guidance separate |
| F-15 insufficient Windows coverage | Confirmed, High | native Windows CI selects only a narrow workflow subset and omits coordinator, locking, recovery, cleanup, and process tests | Add real Windows matrix coverage and platform fault tests; avoid POSIX-only election assumptions |
| F-16 archive/history lacks substrate | Confirmed, High | current status model has no reversible archive metadata/history policy; cleanup can leave projection inconsistencies | Separate lifecycle, visibility, retention, and destructive cleanup state |
| F-17 incomplete evidence read model | Confirmed, High | attempts/artifacts/log paths exist, but bounded sanitized evidence queries and a single redaction boundary do not | Define durable evidence classes, sensitivity, query limits, hashes, sanitizer, retention, and authorization |
| F-18 expensive Desktop read path | Confirmed, Medium | `plugin_api.py:_authorized_runs` reloads many runs; events use repeated journal scans; Desktop polls list/attention and selected detail independently | Add bounded indexed summaries, cursors, and one shared refresh path that cannot impair chat |
| F-19 null interaction identity | Confirmed, Medium | `RunStore._already_decided` with no interaction ID can match an unrelated earlier decision | Require the current durable interaction identity for every decision CAS |
| F-20 abandon TOCTOU | Confirmed, Medium | `RunStore.abandon_run` prechecks outside the terminal-state CAS and `_set_terminal` does not revalidate abandon eligibility | Perform eligibility, process safety, transition, event, wake, and projection update under one lock/transaction |
| F-21 interaction/capacity edges | Confirmed, Medium | paused, retry-wait, and interrupted runs occupy the admission lane; interaction quota and queued promotion are not one durable invariant | Release execution lanes at non-executing waits, enqueue resumed work fairly, and make quota/interaction transitions atomic |
| F-22 inspector cannot answer incident questions | Confirmed, Medium | Desktop `run-inspector.tsx` exposes a subset of actions and summaries; `attention-inbox.tsx` is count-oriented; evidence/health are incomplete | Build inspector only after authoritative evidence/health APIs; offer actions from server state |
| F-23 auth not exercised end-to-end | Confirmed, Medium | workflow plugin API tests bypass or incompletely exercise the actual mounted authentication middleware | Use real FastAPI lifespan, auth middleware, profile isolation, and denied-scope tests |
| F-24 cron/background journeys unsupported | Confirmed, Medium | no durable coordinator or outbox exists, and provenance is hardcoded | Route cron/background/chat admission through the same coordinator-availability, provenance, wake, and notification contracts |
| F-25 command namespace ambiguity | Confirmed, Medium | `workflow run` and `workflow showcase run` accept different identifiers while skills blur them | Preflight publishes exact commands; generic lifecycle always uses run ID; showcase commands remain explicitly namespaced |
| F-26 process/ledger hygiene | Partially confirmed | adversarial review was untracked and the base/downstream ledger was not updated; no repository rule was found requiring a repo-local `CLAUDE.md` pair | Commit the exact review/reconciliation artifacts and maintain the ledger; do not invent a repository policy |
| F-27 trust/lock/path nits | Confirmed as lower-severity hardening | trust labels, lock naming, and path naming are not consistently represented in current workflow evidence | Normalize terminology and validate path/lock behavior during the safety phases; do not promote cosmetic naming above data safety |
| F-28 prior remediation | No longer applicable as a gap | relevant code/history confirms the earlier remediation is present | Preserve with regression tests; no replacement implementation task |

## Candidate-commit disposition

`a9ccb7e91` is useful evidence that state mutation alone does not continue a
workflow, but it is not mergeable because it:

- creates no durable owner;
- continues only a subset of interaction paths;
- runs the workflow tail synchronously from Desktop approval;
- provides no coordinator availability or wake acknowledgement;
- does not solve queued promotion, due retries, crash recovery, or stalls.

`43edb4d4b` contains useful wording, but it is not mergeable as the skill fix
because it does not enforce the machine contract or behaviorally test command
construction, idempotency, human gates, or no-progress handling.

## Production decision

The review is reconciled by the focused generic-service/coordinator design and
the amended operator-experience design. Implementation must follow the
risk-ordered plan. No finding is considered fixed until its full cross-surface
acceptance criteria pass on the authoritative runtime path.

## Adversarial re-review amendment

The follow-up artifact
`2026-07-18-workflow-orchestration-operator-experience-adversarial-rereview.md`
accepted this reconciliation and returned `READY WITH CONDITIONS` for the plan.
The conditions are incorporated without changing the ten-phase order or the
17-item release gate:

- generic lifecycle conformance now measures first-party factory dormancy and
  guarantees cached snapshots cannot block on `health()`;
- retry wait releases its lane, while pause release is explicit policy and an
  interrupted outward attempt holds serialization until reconciliation;
- live matching attempts can be reclaimed after suspend/wake, and idle sweeps
  back off to 60 seconds;
- foreground execution has a run-level exclusive owner lease and cannot race a
  healthy coordinator;
- exit-code-3 migration and bounded authorized not-found candidates are explicit;
- provenance distinguishes authenticated/system facts from local-admin claims;
- Desktop API store reuse and long-poll capacity are assigned to Phase 6;
- notification coalescing, Desktop lease/ack receipts, and CLI-only posture are
  normative;
- retention uses UTC with an injected clock; a committed pre-amendment v2.0.9
  database/run fixture proves the complete upgrade path on all three platforms.

Gateway election preference is recorded as a non-blocking optimization, not a
release condition. Implementation remains blocked until explicit maintainer
authorization; none of these document amendments approves code execution or
release.
