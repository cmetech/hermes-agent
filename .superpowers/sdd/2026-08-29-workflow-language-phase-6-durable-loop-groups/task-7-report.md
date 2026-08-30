# Task 7 Report: Bounded Loop-Group Progress

Status: COMPLETE

## Changes

- Added one optional, strict `loop_group` summary to the existing public parent-node projection. The run schema remains version 1 and no route, query parameter, child endpoint, card, selection state, or cache was added.
- Projected at most 512 definition-ordered current body nodes and the latest 25 supplied iteration summaries. Durations are derived only from valid bounded timestamps; failures remain categorical.
- Added one optional, exact loop-group scope to the existing timeline event/evidence projection. Top-level events omit the scope.
- Added matching Pydantic DTOs and exact-key Desktop decoders with all approved numeric/string/list ceilings.
- Added one active-group iteration badge to the existing parent run card and one small semantic body-state table to the existing parent inspector.
- Added backend profile isolation and Desktop late-response/mutation tests without changing the existing profile query keys or mutation behavior.

## RED Evidence

- Python RED command ran after tests were written and before production changes. It reported five intended missing-contract failures: no nested parent summary, no closed scope, and no new strict DTO. Existing relevant tests passed. One new profile test also exposed a test-fixture error (`RunAdmissionResult` has no `state_version`); the fixture was corrected to read the authenticated stored version.
- The first exact Desktop RED attempt could not initialize Vitest because the shared workspace lacked the lockfile-declared `@rolldown/plugin-babel`. The parent installed root dependencies with `npm ci --ignore-scripts` without changing manifests. This environment interruption was reported immediately. Desktop tests had already been authored before production changes; after setup, the exact suite exercised the new codec/render/profile boundaries.

## GREEN Evidence

- Prescribed Python gate: `209 passed, 0 failed, 1 skipped in 70.5s`.
- Prescribed Desktop gate: `95 passed, 0 failed` across the four required files.
- Prescribed Desktop typecheck: passed all three TypeScript projects.
- Ruff check on all changed Python files: `All checks passed!`.
- Prettier on the changed Desktop files: passed/normalized only Task 7 files.
- ESLint on changed Desktop files: zero errors; remaining warnings pre-existed in the large test files.
- `git diff --check`: passed.

## Profile Race / Isolation Proof

- Profile B lists only its own run and receives `run_not_found` for Profile A detail, event, and mutation requests through the unchanged routes.
- The Profile B not-found mutation is sent once, is not retried, and cannot alter Profile A's profile-keyed cached snapshot.
- Late Profile A list, detail, and event promises resolve only into Profile A query keys; Profile B's board, drawer, and event view remain on Profile B data.
- Switching back to Profile A returns the original state version and valid action set.

## Bounded / Private-Data Proof

- Current body projection is capped at 512 entries and preserves the authenticated mapping's definition order.
- Iteration history accepts only closed summaries and retains the latest 25.
- Malformed nested group data is omitted while the enclosing run remains valid; malformed Desktop nested data rejects the complete DTO.
- Prompt, command/script, tool, feedback, output, credential, environment, path, attempt metadata, and previous-output canaries do not survive the public projection.
- Scope exposes only group ID, controller generation, iteration, and optional body node ID. It cannot carry raw attempt/output material.

## Exact Tests

```text
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
scripts/run_tests.sh tests/plugins/workflow/test_phase6_public_projection.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_phase5_public_projection_contract.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_workflow_language_desktop_e2e.py -v

cd apps/desktop && npm test -- --run src/lib/workflow-public-codec.test.ts src/app/workflows/adapter.test.ts src/app/workflows/workflow-run-drawer.test.tsx src/app/workflows/index.test.tsx
cd apps/desktop && npm run typecheck
```

## Self-review

- The implementation reuses the existing sanitizer, strict Pydantic response models, public codec, board card, inspector, REST routes, and profile-keyed TanStack queries.
- There is still exactly one card per outer run and all actions remain outer-run actions.
- The public run schema stays at version 1. No child enumeration, route vocabulary, query parameter, selection state, or cross-profile board/cache was introduced.
- Unknown/private nested fields are selected away rather than recursively copied.
- Invalid booleans, identifiers, bounds, body entries, iteration entries, or scopes fail closed.

## Concerns

- The durable controller currently supplies the current body but no historical `iterations` list; the sanitizer therefore publishes an empty history for current runtime state and applies the required latest-25 contract when authenticated history is present. Task 7 did not own the durable store. No history was invented from incomplete data.
