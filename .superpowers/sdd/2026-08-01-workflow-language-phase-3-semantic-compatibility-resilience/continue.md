# Continue — Phase 3 / Task 11

## Last action

Closed Task 10 at `7a01b1d0fb1e590b7222e765b4c2f79556cf925e`: the final implementation is `9976161faf55f461f3f9ab3b56760a995bf6170f`, and fresh specification and quality rereviews both passed with 0 Critical, 0 Important, and 0 Minor findings. The focused controller gate passed 238 tests with retries disabled; the committed live gate passed 2,595 Python tests, 1 installed-distribution test, and 155 Desktop tests.

## Next action

Read `AGENTS.md`, the approved Phase 3 design and plan, and the final Task 10 rereviews; verify the exact branch/HEAD/tree and clean worktree; then begin Task 11 by adding the first real `/bin/sh` byte-bound and content-preservation tests and proving RED through `scripts/run_tests.sh` before any production edit.

## Why

Task 10 now provides the reviewed bounded, read-only, identity-pinned inherited-descriptor primitive that Task 11 must consume. Task 11 is the next approved plan boundary and has no production or test edits yet.

## Open threads

- Tasks 11–16 remain pending.
- Task 11 must independently close specification and quality review before Task 12 begins.
- The shared `base` checkout still contains unrelated user-owned documentation/review changes.

## Do not

- Do not run Python tests with direct `pytest`; use `scripts/run_tests.sh` with retries disabled.
- Do not modify, stash, commit, delete, overwrite, or reformat user-owned changes in the shared `base` checkout.
- Do not touch literal `main`, push, publish, or delete branches/worktrees.
- Do not begin Task 12, Phase 4 loops/includes, path-taking artifact endpoints, raw provider responses, MCP node kinds, or skills node kinds.
- Do not weaken prompt caching, the narrow core/tool waist, bounded API/evidence projections, command-byte authority, or legacy/unversioned compatibility.
