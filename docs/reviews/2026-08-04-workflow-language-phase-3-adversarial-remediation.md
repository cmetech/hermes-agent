# Phase 3 Adversarial Review Remediation

Remediation date: 2026-08-04

## Outcome

The `BLOCK` verdict in the immutable Fable 5 review of `8a1fe704` was valid.
All one CRITICAL and seven HIGH findings were confirmed against the reviewed
implementation and are resolved on
`feat/workflow-language-phase-3-semantic-compatibility-resilience` at
`060f60f5429c5250d018e4efe61f1a22edc05102`.

No threat-model or security-focused validation was performed. The release gate
explicitly excludes `test_phase3_bash_lexer_security.py` and the mixed
`test_persistent_session_recovery.py` suite. Crash and accounting corrections
were verified only through exact ordinary functional test nodes where needed.

## Finding disposition

| ID | Disposition | Resolution | Commit |
|---|---|---|---|
| C1 | Confirmed, resolved | Reject references in `let` and integer declaration-builtin arithmetic operands before Bash execution. | `11f00788b` |
| H1 | Confirmed, scope corrected, resolved | A pause is a continuation for approval, loop input, and AI action grants; paused results no longer consume the workflow retry grant. Feature-specific continuation limits remain authoritative. | `101c0a7c6` |
| H2 | Confirmed, resolved | Reference waits and terminal reference transitions preserve prior attempts and durable retry consumption instead of rejecting the transition. | `4c0eb65a9` |
| H3 | Confirmed, resolved | Private session authority is committed before journal activation, followed by a new transaction and revalidation of the same execution fence. Both selection and winning completion survive the crash window. | `85ed05039` |
| H4 | Confirmed, resolved | Typed preflight session-store failures retain exact zero-call recovery classification; post-worker protocol failures and empty returned session IDs are conservatively classified as outcome-unknown. | `b7e2aab1c`, `0ff1063b5` |
| H5 | Confirmed, resolved | All five provider-transport files are owned by a dedicated upstream-customization entry. A base-gated contract verifies callback ordering and every production reservation seam. | `539117ec7` |
| H6 | Confirmed, resolved | The final rendered Bash command now has a platform-independent 96 KiB UTF-8 ceiling and fails as `bash_substitution_limit` before launch; generated and installed contracts publish the bound. | `cea038c4d`, `060f60f54` |
| H7 | Confirmed, resolved | Loop `until_bash` passes the original template and dependencies to the strict executor for one render only. | `cea038c4d` |

## Verification

The final clean base release gate completed successfully at
`TESTED_BASE_SHA=060f60f5429c5250d018e4efe61f1a22edc05102`:

- Python base selection: 57 files, 3,857 passed, 0 failed.
- Installed-wheel integration: 1 passed, 0 failed.
- Desktop selection: 11 files, 159 passed, 0 failed.
- Upstream-customization checker: passed.
- `git diff --check`: passed.

The gate's new `tests/agent/test_provider_attempt_transport.py` contract passed
7/7 and is selected exactly once. The prohibited suites remain excluded from
the executable gate and its contract test.

## Final state

The adversarial findings no longer block Phase 3. Integration, push,
publication, brand propagation, and worktree cleanup remain separate actions
requiring user authorization.
