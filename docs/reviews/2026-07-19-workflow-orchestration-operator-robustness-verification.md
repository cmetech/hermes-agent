# Workflow Orchestration Operator Robustness Verification

**Date:** 2026-07-19
**Branch:** `feat/workflow-production-remediation`
**Design:** `docs/superpowers/specs/2026-07-19-workflow-orchestration-operator-robustness-design.md`
**Plan:** `docs/superpowers/plans/2026-07-19-workflow-orchestration-operator-robustness-plan.md`
**Verified code HEAD:** `7684c7733b163de7427575101a3cc6e0821dbbd1`

## Result

The approved operator-robustness batch is implemented and its strengthened base merge gate passes. The nine operator-facing fixes are in `f293f68d1`; the four production-invariant pins and merge-gate self-test are in `7684c7733`. No merge, tag, release, or push was performed during this batch.

The pre-existing reviewer-authored modification to `docs/reviews/2026-07-19-workflow-orchestration-followup-fixes-adversarial-review.md` was preserved and excluded from both implementation commits.

## Red/green evidence by behavior

| Behavior | Red cause established before production change | Focused green evidence | Commit |
|---|---|---|---|
| NF-L2 attention pagination | The endpoint sorted oldest-first, truncated the merged result at 100, and always returned `next_cursor=None`; newest items and continuation pages were unobservable. | Five focused Desktop/API pagination tests passed; the final Task 1 matrix also exercised the real store and authenticated API. | `f293f68d1` |
| NF2-L5 + NF-L13 queue admission | A queue-policy start at full executing capacity returned a typed capacity rejection instead of creating a queued run, even though the bounded nonterminal/storage/rate ceilings had room. | 29 admission and race tests passed in the focused cycle; the final Task 1 matrix re-ran admission and approval races. | `f293f68d1` |
| Foreground adoption notice | Coordinator adoption ended the foreground CLI without a human explanation and without durable handoff evidence in the JSON status payload. | Both human and JSON adoption tests passed, including `execution_mode=background` and `foreground_execution_adopted`; the real spawn adoption test passed. | `f293f68d1` |
| NF-L10 CLI JSON errors | Parse-time errors bypassed the JSON envelope through raw argparse stderr, and `OSError` text could reveal implementation paths. | The full workflow CLI file passed in the focused cycle, then passed again inside the 587-test Task 1 matrix. | `f293f68d1` |
| NF-L9 oversized repair journal | The first journal could exhaust the byte budget without its missing terminal notification being repaired, creating either cursor livelock or a silent per-run repair hole. | Notification repair tests passed with the oversized first journal actually projected before cursor advancement; the notification/retention focused set passed 26 tests. | `f293f68d1` |
| NF-L8 cleanup crash gap | Cleanup preview/execution trusted the retention window and could select a terminal run whose journal fact had not yet reached the notification outbox. | Cleanup tests passed for bounded reconciliation, repaired-row exclusion, authority-bound preview/execute, and fail-closed missing/empty/corrupt/oversized/unsafe evidence. | `f293f68d1` |
| NF-L6 foreground lease clock | Foreground leases had UTC-only freshness, so backward and forward wall-clock steps could recreate stuck or prematurely adopted runs; schema 12 lacked boot/monotonic corroboration. | 39 coordinator, multiprocess, and schema tests passed in the focused cycle. The final cumulative migration/process set passed 25 tests, and the installed-wheel test passed. | `f293f68d1` |
| NF-L7 provider reload | Concurrent provider changes could surface as 500 and leave saved configuration that the running host had not applied. | Seven reload/hot-add tests passed, including a typed 409, conditional leaf rollback, typed rollback failure, and the positive real provider dispatch after host-controlled reload. | `f293f68d1` |
| NF-L11 bounded pruning | Route/receipt tables had no bounded retention operation; no negative tests protected uncertain receipts or live capabilities. | The complete Gateway delivery and workflow notification delivery files passed in the focused cycle. Tests prove only old terminal receipts/expired unused routes are pruned, while `sending`, retryable, and unexpired pending/leased/dead routes survive. | `f293f68d1` |
| NF-L1 Windows fallback containment | Sensitivity check: after temporarily changing `_is_reparse_point` to return `False`, `test_fallback_reparse_attribute_is_rejected_before_open` failed before the read guard (`1 failed, 1 passed`). The mutation was restored and is absent from the diff. | The restored fallback reparse and identity-swap tests passed (`2 passed`). The complete invariant set passed with the native Windows test skipped on this macOS host. | `7684c7733` |
| NF-L3 cross-process idempotency | Coverage gap: no synchronized spawn race forced two independent CLI starts through the same semantic key and SQLite store concurrently. | The new race passed five consecutive runs: one `created`, one `existing`, one run ID, one durable row, and no raw lock/uniqueness failure each time. | `7684c7733` |
| NF2-L1 concurrent drainers | Coverage gap: the lease was code-reviewed but no test held one real drainer after lease acquisition while a second SQLite connection attempted the same outward send. | The test passed five consecutive runs: counts `[0, 1]`, one sender call, and one delivered history row each time. | `7684c7733` |
| NF2-L6 gate self-enforcement | The new meta-test failed because `scripts/test_workflow_merge_gate.sh` did not name `tests/scripts/test_workflow_merge_gate.py` (`1 failed`). | The meta suite passed `10 passed`; the FAST gate returned `TESTED_BASE_SHA=f293f68d1555fae607377b0bc4148ffbae18b991` without recursion. The final full gate includes the same meta-test at the final HEAD. | `7684c7733` |

## Final gate evidence

The strengthened base gate ran from the final code commit:

```text
scripts/test_workflow_merge_gate.sh --phase base

Python base selection:       667 passed, 1 skipped in 53.00s
Installed distribution:       1 passed in 3.81s
Desktop Vitest:               6 files passed, 17 tests passed
Desktop TypeScript:           exit 0
TESTED_BASE_SHA=7684c7733b163de7427575101a3cc6e0821dbbd1
```

The final focused real-process and migration rerun was:

```text
pytest -q tests/plugins/workflow/test_schema_migrations.py \
  tests/plugins/workflow/test_idempotency_multiprocess.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_notification_delivery.py

25 passed in 4.84s
```

The installed-distribution migration path was then run independently again:

```text
pytest -q -m integration tests/plugins/workflow/test_installed_distribution_e2e.py

1 passed in 3.67s
```

The Task 1 complete operator-robustness matrix passed `587 passed` with no failures. It was run with `OTTO_BRAND=unbranded-test` solely to make four pre-existing all-channel catalog assertions exercise their stated fail-open catalog contract instead of OTTO's intentional channel allowlist. Production brand filtering was not changed.

The Task 2 invariant set passed `33 passed, 1 skipped in 3.83s`. The one skip is the native Windows reparse assertion because this verification host is macOS. This record does not claim that native assertion ran locally. The test is selected by the repository's native workflow matrix and is written to fail, rather than skip, on Windows if neither a real symlink nor junction can be created. The two platform-neutral fallback probes ran and passed locally.

## Schema 13 and cumulative migration evidence

Schema 13 adds nullable `foreground_boot_id`, `foreground_heartbeat_monotonic`, and `foreground_lease_seconds` columns. Legacy rows with null corroboration retain their UTC-deadline behavior until that deadline passes; they are not mass-expired during upgrade.

`test_pre_amendment_v209_store_reaches_current_full_schema_idempotently` copies the hash-pinned v2.0.9 SQLite/evidence fixture, upgrades it through the complete current schema twice, verifies `PRAGMA integrity_check`, verifies foreign keys and the current schema manifest, and compares preserved evidence bytes and hashes. The installed-wheel test builds and installs the distribution, imports from `site-packages`, and verifies the same schema-13 target and columns. Both paths passed at the final code HEAD.

## Acceptance-criterion map

| Approved criterion | Final evidence |
|---|---|
| Newest-first deterministic attention with authorized cursors | `test_desktop_api.py` attention ordering, collision, composite cursor, scope-binding, and bounded-source tests; 587-test Task 1 matrix green. |
| Queue-policy starts queue unless a real bounded ceiling is reached | `test_admission.py` execution-capacity, nonterminal ceiling, held-lane, race, and coordinator-wake tests; final matrix green. |
| Human and machine-readable foreground adoption | `test_cli.py` human/JSON adoption tests plus `test_coordinator_multiprocess.py`; final matrix green. |
| Parse-time and `OSError` JSON contract | `test_cli.py` parser-envelope and sanitized-error tests; final matrix green. |
| Oversized first journal is repaired, not skipped | `test_notifications.py` oversized first-run repair and bounded cursor tests; final matrix green. |
| Cleanup preserves unprojected or uncorroborated evidence | `test_retention.py` crash-gap, unsafe evidence, bounded overflow, and preview/execute binding tests; final matrix green. |
| Foreground leases withstand clock steps and migrate cumulatively | `test_coordinator.py`, `test_coordinator_multiprocess.py`, `test_schema_migrations.py`, and installed-wheel integration; 25-test focused rerun plus installed-wheel pass. |
| Concurrent reload is typed and configuration-consistent | `test_plugin_provider_hot_reload.py` and reload middleware tests in `test_web_server.py`; final base gate green. |
| Retention is bounded and preserves uncertain/live authority | `test_plugin_delivery.py` and `test_notification_delivery.py`; final base gate and 25-test focused rerun green. |
| Windows fallback, spawn idempotency, and concurrent drainers are pinned | Platform-neutral fallback tests passed; native Windows test is CI-selected; both concurrency pins passed five times and are in the final invariant set. |
| Gate tests its own lock | `tests/scripts/test_workflow_merge_gate.py` is named by the non-FAST base selection; final gate passed at exact HEAD. |

The architectural constraints remain intact: workflow behavior stays plugin-owned; generic Gateway retention has no workflow import; no model-facing workflow tool, mid-conversation prompt/tool mutation, user-facing non-secret `HERMES_*` variable, synchronous HTTP/Gateway workflow tail, or cross-machine database support was added.

## Deliberately deferred non-blocking debt

The approved design explicitly leaves these five findings recorded rather than speculatively changing them:

- **NF-L4:** legacy-namespace retry duplication requires a pre-upgrade stable-key field path that did not ship.
- **NF2-L2:** upgrade-dedup one-shot resend depends on a pre-fix fielded build; none shipped.
- **NF-L5:** the remaining unfenced mutations are serialized and convergent; the disruptive pressure interrupt is tied to pressure both leaders are expected to observe.
- **NF-L12:** multi-profile Gateway delivery remains fail-closed until that topology is supported.
- **NF2-L3:** raw capability at rest in `facts.destination` remains defense-in-depth backlog; all read projections mask it and the local store is permission-restricted.

The optional deliver-only facade half of NF-L11 also remains backlog; this batch implements the approved bounded pruning half without weakening delivery authority.

No remaining item in this record is a Critical or High merge blocker. The prior adversarial follow-up verdict remains ready for maintainer merge review, subject to the maintainer's ordinary review of these additional low-severity fixes.
