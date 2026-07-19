# Portable Workflow Orchestration Production Remediation Verification

**Evidence date:** 2026-07-19

**Branch:** `feat/workflow-production-remediation`

**Approved refinement:** `1547d0c8e300fe2ba3a1733f7c9effdca3e37c8b`

**Final runtime commit:** `2b81fd45be40451f12fea90b2f9a5860042a68d3`

**Final code-and-test commit:** `546279a44db1de83cc3d72fcef5a0e75af39b4b0`

## Verdict

All nineteen remediation tasks are implemented and independently committed.
The two Critical, nine High, thirteen Medium, and nine Low findings from the
adversarial re-review are closed, as are prior release gates M-01, M-02, and
M-03. The fresh adversarial review reports **no Critical or High merge
blocker**.

This is evidence for maintainer merge review, not an authorization to merge or
release. Nothing in this remediation execution was merged, tagged, released,
or deployed.

## Environment

- Local OS: macOS 26.5 (build 25F71), Darwin arm64
- Python: 3.13.13
- Node.js: 22.22.3
- npm: 10.9.8
- SQLite CLI/library: 3.51.0
- Native CI: GitHub-hosted `ubuntu-latest`, `macos-latest`, and
  `windows-latest`

## Remediation commit ledger

| Task | Commit | Contract completed |
|---:|---|---|
| 1 | `499241941` | Immutable authority and distinct read/write/delivery/admin capabilities |
| 2 | `95ecbc421` | Descriptor-based contained evidence reads and shared sanitization |
| 3 | `305969d65` | Exact durable interaction identity on every decision |
| 4 | `4e7678e43` | Semantic idempotency separated from volatile provenance and delivery |
| 5 | `0aefd3a2c` | Showcase key/preflight/trust behavior without trust-store mutation |
| 6 | `5e79986c0` | Coordinator owner/epoch fencing, boot identity, and monotonic lease corroboration |
| 7 | `c2ad61dcc` | Spawn-intent, live-claim, and index-drift recovery windows closed |
| 8 | `3189c5818` | Expired foreground owner adoption and uncertain-effect reconciliation |
| 9 | `42dd0fba1` | Reserved terminal/recovery journal capacity and claim retention on failure |
| 10 | `6b4420499` | Bounded cursor sweeps and real runnable/semantic stall thresholds |
| 11 | `9d3b02815` | Central FIFO lane/capacity admission for every runnable transition |
| 12 | `fd6fa2ef9` | Windows Job Object ownership and uncertain termination behavior |
| 13 | `3cf5a4cb5` | One background-service generation and host-controlled provider reload |
| 14 | `da2d3f020` | Notification retry, pruning, paginated repair, and retention safety |
| 15 | `3b924f06d` | Authenticated plugin invocation and opaque Gateway delivery capability |
| 16 | `d23a80c19` | Authenticated coordinator-gated background-only `POST /runs` |
| 17 | `c9285a585` | Actionable Desktop attention and complete keyset history/archive traversal |
| 18 | `7dccde877` | Machine envelopes, typed failures, and bounded resource registries |
| 19 corrections | `2b81fd45b`, `2214f79bb`, `546279a44` | Migration digest repair, portable merge/native gates, and valid builder provenance |

The corrective commits discovered by Task 19 changed only branch-owned runtime,
gate, and test files. In particular, the final builder correction replaces the
obsolete test-only trigger value with the truthful local CLI/admin provenance
used by the real builder path; it does not broaden the production source
vocabulary.

## Local merge and focused gates

The final no-argument merge gate was run from clean code commit `546279a44`:

```text
scripts/test_workflow_merge_gate.sh
635 passed, 1 skipped in 49.96s
installed-distribution integration: 1 passed in 4.20s
Desktop: 6 files passed, 17 tests passed
TypeScript: tsc --noEmit passed
TESTED_BASE_SHA=546279a44db1de83cc3d72fcef5a0e75af39b4b0
```

Additional current-branch results:

```text
Task 19 corrective selection: 144 passed; Ruff passed
Cross-surface UAT and soak selection: 204 passed, 1 skipped in 66.36s
Expanded native-contract selection on macOS: 130 passed, 2 skipped in 30.78s
Native portability corrections: 31 passed, 1 skipped
Builder/provenance/admission regression selection: 38 passed in 2.55s
Scoped Task 17 Desktop ESLint: passed with zero errors
git diff --check: passed
```

The skips are explicit inverse-platform conditions (for example, a Windows-only
assertion on POSIX or a POSIX-only assertion on Windows). No workflow-native
test is skipped on the theory that Windows is unsupported.

The gate uses real SQLite files, filesystem operations, subprocesses,
multiprocess coordinator takeover, real FastAPI middleware, installed package
imports, and Desktop adapters. It is not a mock-only acceptance suite.

## Native operating-system evidence (M-01)

The first native run exposed four Windows-only test portability defects: a PID
disappearance assertion stronger than the Job Object contract, an unquoted
path in a Bash marker, CRLF conversion of hash-pinned evidence, and a test-held
SQLite handle blocking transactional table replacement. Each was reproduced,
corrected in `2214f79bb`, and rerun.

Corrective matrix run
[`29690187027`](https://github.com/cmetech/hermes-agent/actions/runs/29690187027)
passed every workflow portability job. Exact-final-code matrix run
[`29690480191`](https://github.com/cmetech/hermes-agent/actions/runs/29690480191)
then repeated the same result at `546279a44`:

| Runner | Job | Result |
|---|---:|---|
| Windows | `88202197519` | 127 passed, 5 platform-conditioned skips in 219.50s |
| Ubuntu | `88202197525` | 130 passed, 2 platform-conditioned skips in 38.29s |
| macOS | `88202197539` | 130 passed, 2 platform-conditioned skips in 53.68s |

That matrix exercises SQLite locking and shadow-table migration, atomic
replacement, process identity/tree termination, coordinator election and
takeover, foreground adoption, notification restart/repair, cleanup, and the
installed-showcase contract. Thus the native evidence covers the exact final
code-and-test commit, not merely a runtime-equivalent ancestor.

## Distribution, update, rollback, and evidence rehearsal (M-02)

Artifacts were built outside the repository under
`/tmp/hermes-workflow-rehearsal.EuBolo`.

| Version | Artifact | SHA-256 |
|---|---|---|
| refinement `1547d0c8` | wheel | `96fd176d61448400e244a75188958c8a2ae2611cb5c7798796828826b37e5c2e` |
| refinement `1547d0c8` | sdist | `96915f96fb3b23e179f5dd0fd1f32eee3573578c8a5b0b2a5cf9e72198dcc9cc` |
| remediated runtime | wheel | `06fd63827735d7a6fd0f615a5e870ade3bb3a60ffcc28c0496388252ddb6da7c` |
| remediated runtime | sdist | `a80c9c8d95db00c17f7255295fd562f3f5e5e25fc167eb6d65a2513aebd59781` |

A fresh Python 3.13.13 virtual environment installed the remediated wheel and
all 60 resolved dependencies. The installed module resolved from
`site-packages`, not the source worktree. The following installed paths passed:

- authoritative JSON CLI;
- foreground bundled showcase completion;
- real FastAPI local-admin middleware and plugin router;
- authenticated Gateway background admission with a server-minted opaque
  return-route capability; and
- bundled showcase preflight through ordinary trust/risk evaluation.

The update/rollback lifecycle used stable run
`e8249ac34ec44640a540387dbb094069`:

1. The refinement version created and completed the run.
2. The first remediated upgrade returned the same run as `existing` for the
   same stable intent key.
3. Rollback could read status and preserved the newer index and immutable
   evidence.
4. The second remediated upgrade failed closed until `repair_storage`
   corroborated every indexed projection, journal, and exact run-directory
   entry.
5. After repair, the same key again returned the original run as `existing`.

All seven recorded evidence-file hashes and bytes remained identical through
old/current/old/current transitions. Final SQLite checks reported
`integrity_check=ok`, no foreign-key violations, and schema version 12.

This rehearsal found one genuine legacy migration defect: an old
provenance-covered `start_digest` could conflict after the semantic namespace
migration. `2b81fd45b` now recomputes that digest only from corroborated immutable
`run.json` evidence and otherwise preserves the legacy digest, remaining
conflict-safe. The cumulative hash-pinned v2.0.9 migration test upgrades through
the complete schema twice, verifies the manifest/integrity/foreign keys, and
proves evidence bytes and hashes are unchanged.

## Surface and safety coverage

The acceptance selections cover CLI human/JSON operation, Desktop attention
and history, authenticated Gateway start/delivery, cron, background agent,
chat skill, direct API admission, host restart/reload, coordinator loss and
takeover, sweep pagination beyond 200 runs, lease expiry, retry wakes,
interaction continuation, FIFO promotion, notification receipt loss/retry/
dead-letter, archive/restore, cleanup confirmation, and evidence containment.

Manual source inspection additionally confirmed:

- no permanent model-facing workflow tool and no mid-conversation system
  prompt, toolset, or history mutation;
- no workflow imports in generic host/lifecycle modules;
- no new user-facing non-secret `HERMES_*` setting;
- HTTP and Gateway mutations persist bounded state, wake the coordinator, and
  never call `RunScheduler.advance`;
- descriptor-based `O_NOFOLLOW`/`fstat`/post-open evidence checks;
- owner-and-epoch transactional coordinator fencing;
- no evidence deletion from missing, empty, corrupt, or inconsistent indexes;
- no replay while an outward outcome is uncertain and no claim release before
  durable terminal/recovery evidence;
- server-derived authority/provenance and opaque delivery routes; and
- RunStore, journals, and notification outbox remain authoritative while UI
  and delivery transports remain projections.

## Accepted unrelated baselines (M-03)

These failures were recorded rather than hidden or swept into the workflow
branch:

- Full Desktop lint reports 21 errors and 170 warnings in untouched Electron,
  release-update, cron, icon, theme, and other baseline files. Every Task 17
  Desktop file passes scoped ESLint, and full Desktop TypeScript compilation
  passes.
- `scripts/check-windows-footguns.py --all` reports four pre-existing
  `os.geteuid` uses in `plugins/ericsson-teams/graph_auth.py`; that file is
  outside the remediation and unchanged by this branch.
- The overall repository CI workflow remains red in checks that compare this
  long-lived/private branch to current public `main`, including contributor
  attribution/history and unrelated branded configuration, Kanban, provider
  catalog, updater, and full-suite assumptions. The dedicated workflow native
  jobs, Desktop build, docs build, supply-chain scan, and branch-owned merge
  gate pass.

These are repository integration/baseline issues, not workflow Critical or
High findings. They remain visible for the maintainer; this review makes no
claim that the whole unrelated repository baseline is green.

## Final gate disposition

| Gate | Result |
|---|---|
| 2 Critical findings | Closed with current behavioral and integration evidence |
| 9 High findings | Closed with current behavioral and integration evidence |
| 13 Medium findings | Closed with current behavioral and integration evidence |
| 9 Low findings | Closed with current behavioral and integration evidence |
| M-01 native Linux/macOS/Windows | Passed |
| M-02 install/update/rollback | Passed with immutable-evidence comparison |
| M-03 branch-owned no-regression | Passed; unrelated baseline recorded |
| Fresh adversarial review | No Critical or High merge blocker |
| Merge/tag/release/deploy | Not performed |
