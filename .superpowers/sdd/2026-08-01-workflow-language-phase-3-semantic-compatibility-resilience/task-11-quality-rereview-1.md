# Task 11 Quality and Security Rereview 1

## Reviewed identities

- Fix base: `6a824d52a0fab0bf7c4e50c39f4102d315e9c54d`
- Head: `0afb27113e97278394d5e2b680cde66bbc02bb72`
- Head tree: `776a59d3d00750593af55b57cfd26f12c5a3cde4`
- Commit: `0afb27113 fix(workflow): harden bash reference and spill boundaries`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Review package: `review-6a824d52a..0afb27113.diff`
- The checked-out identities matched the request. The package's diff body and
  `git diff -U10 6a824d52a..0afb27113` had the same SHA-256,
  `fb2a8eb9d743e319e6a0dba04ab077bf4633164b2bf7e6dd1be1a2dcb736027a`.
  The worktree was clean before this permitted report write.

## Final verdict and totals

**FAIL**

| Finding | Verdict |
|---|---|
| I-1 — premature command-substitution exit / missed shell contexts | **NOT ADDRESSED** |
| I-2 — strict parsing before escaped/comment filtering | **ADDRESSED** |
| I-3 — mutable verified spill inode after digest | **ADDRESSED** |
| I-4 — pre-spawn descriptor ownership not exception-safe | **ADDRESSED** |

| Classification | Critical | Important | Minor |
|---|---:|---:|---:|
| Unresolved original findings | 0 | 1 | 0 |
| New fix-range breakage | 0 | 1 | 0 |
| Unique total | 0 | 2 | 0 |

## Scope and method

I read the requested repository rules, Task 11 brief, approved Phase 3 design,
original quality review, Fix Round 3 claims, and complete exact diff package.
I traced the complete changed renderer and executor plus the relevant parser,
admission, resource-rendering, scheduler-preflight, tests, and Task 10
descriptor-pinning/exec handoff. I treated all reported test results as claims.
No test was run: both blocking defects below follow directly from the changed
state machine and descriptor handoff, so a runner invocation was unnecessary.

## Per-finding disposition

### I-1 — NOT ADDRESSED: bare Bash arithmetic syntax is still admitted

**Locations:** `plugins/workflow/bash_rendering.py:517-522,607-633,838-848`

The new state machine recognizes arithmetic only when a dollar introduces
`$((...))` or `$[...]`. At top level, bare Bash arithmetic commands and
arithmetic `for` clauses are never framed: `(` and `)` fall through as ordinary
separators. Consequently the candidate in `(( $producer.output ))`, or the
scalar in `(( $USER_MESSAGE ))`, is classified with top-level quote context and
is admitted. `for ((i=$producer.output; i<2; i++)); do ...; done` has the same
defect.

These are arithmetic-evaluation contexts, not proven simple command-word data
contexts. On Bash-compatible execution paths the substituted bytes are parsed
as an arithmetic expression; values can therefore be semantically reinterpreted
and Bash arithmetic supports recursively evaluated names/subscripts. On other
`/bin/sh` implementations the same admitted package can instead become a
platform-dependent syntax failure. Neither outcome is the required
`bash_reference_context_unsupported` before launch. This is the same missed
shell-context class as the original `$[...]` defect.

**Required correction:** frame or conservatively reject bare `((...))`,
`for ((...))`, and sibling grammar-level arithmetic/subscript contexts before
admitting any reference. Add admission and direct-executor no-launch tests for
both output and scalar references at inline and spill sizes.

### I-2 — ADDRESSED

**Locations:** `plugins/workflow/language_schema.py:168-218`;
`plugins/workflow/bash_rendering.py:869-894`;
`plugins/workflow/resources.py:797-812,870-903`;
`plugins/workflow/schema.py:1092-1117`;
`plugins/workflow/scheduler.py:1522-1537`

Candidate discovery is now grammar-neutral and forward-bounded. The shared
`bash_output_references()` classifies escape, comment, and unsupported shell
context first, then applies strict grammar only to admitted candidate spans.
Successful token coordinates and syntax-error offsets are rebased to the
authored template. Admission, secure direct rendering, and Bash scheduler
preflight all call this same authority in the same order. Dollar-dense work is
linear in input size; lexical nesting is iterative and fail-closed at the fixed
frame/case bound. I found no coordinate-authority or parse-order bypass in the
changed paths.

### I-3 — ADDRESSED as written; see new Important N-1

**Locations:** `plugins/workflow/bash_rendering.py:941-1016,1019-1074`

The original same-inode race is closed. Creation remains descriptor-relative,
exclusive/no-follow, mode `0600`, regular and single-link checked, fsynced,
reopened read-only, identity/size checked, and digested. The exact bytes read
during digest verification are copied into a bounded immutable `bytes`
snapshot. Both file descriptors are closed and the filename is unlinked
descriptor-relatively before the spill directory is removed. A retained writer
can mutate only the orphaned file, not the snapshot later published to the
child.

### I-4 — ADDRESSED

**Locations:** `plugins/workflow/bash_rendering.py:71-95,102-215,1128-1193`;
`plugins/workflow/executors/bash.py:92-110,151-245,393-416`

Rendering closes the transport on every post-materialization construction
fault. Executor ownership begins immediately after render return and an outer
`finally` covers evidence, argv/env/deadline work, output setup, callbacks,
spawn, artifacts, and returns. Emergency process/output cleanup covers faults
before normal teardown. One non-daemon selector publisher handles the bounded
set of pipes, uses nonblocking writes, is stopped and synchronously joined, and
read descriptors are released only after Task 10 returns from its pin-and-exec
handoff. I found no descriptor, thread, or process leak in those exception
paths.

## New Important breakage

### N-1 — The anonymous read-descriptor manifest is not bound to the verified transport

**Locations:** `plugins/workflow/bash_rendering.py:54-83,121-127,218-262`;
`plugins/workflow/executors/bash.py:194-245`; inherited Task 10 authority at
`tools/managed_process.py:230-268,870-904`

The renderer records only descriptor numbers and digests. Publication starts,
then `ManagedProcessTree.spawn()` duplicates whatever object those numbers name;
Task 10 verifies only that each duplicated descriptor is open and read-only.
There is no expected FIFO identity carried into the atomic duplicate. If an
in-process fault or callback closes a nominated pipe read descriptor and
`dup2()` reuses its number for a read-only regular file before Task 10 pins it,
the child inherits that file. The prologue's `cat <&N` can then succeed on
replacement bytes while evidence still records the verified snapshot digest.

The publisher does not reliably detect this substitution: `BrokenPipeError` is
explicitly discarded without setting publication error, and a pipe-sized
snapshot may finish publication before the replacement occurs. Thus this is
not merely a clean launch failure; it permits successful altered output with
original evidence, contrary to the Task 11 requirement that a corrupted
descriptor read the verified handle or fail.

**Required correction:** bind each exposed read descriptor to a private
expected pipe identity and have Task 10 verify the identity of its newly pinned
duplicate before exec (the duplicate makes the check race-safe), or transfer a
pre-pinned authenticated handle through an equivalent atomic seam. Add a fault
test that replaces the nominated descriptor with a readable regular file after
render/publication start and requires original bytes or terminal
`bash_spill_integrity`, never replacement bytes.

## Critical findings

None.

## Minor findings

None.

## Compatibility, privacy, and out-of-scope observations

- The secure renderer remains gated by both Archon profile and recorded
  normalizer version 3. V1/v2 and legacy pathname-spill selection remain on the
  prior branch; native Windows still rejects large v3 values before launch and
  retains the existing inline argv gate.
- `argv[-1]` remains the immutable rendered command. Bash evidence contains
  sizes, digests, counts, and descriptor numbers only; I found no raw value,
  command text, or spill pathname disclosure.
- Task 10 remains read-only and unchanged in this fix range. Its access-mode
  pinning is preserved, but it is insufficient to detect N-1 without an
  expected transport identity.
- The documented `loop.until_bash` comment-boundary observation is out of
  scope. No loop production or test file changed in this range, and the fix
  retains the explicit Phase 4 boundary.
- Native Windows descriptor/job-object behavior was not executable on this
  Darwin host; the local Windows-path results remain claims/simulations.

## Test-evidence assessment

The claimed final result is 467 passed across 11 files, plus lint/format and
whitespace checks. The added tests materially improve real-shell nesting,
malformed-literal ordering, same-inode mutation, publisher faults, and callback
cleanup coverage. They do not exercise a bare arithmetic command or arithmetic
`for` clause, and descriptor corruption is represented by a directory/closed
descriptor rather than replacement with a readable regular file. Those gaps
map exactly to I-1 and N-1. The green claims therefore do not establish the
remaining fail-closed and verified-byte handoff contracts.

## Final assessment

Fix Round 3 correctly repairs lexical-first reference parsing, the original
mutable-inode race, and exception-safe publisher/descriptor ownership. It is
not ready to merge: the lexer still admits grammar-level Bash arithmetic
contexts, and the new anonymous transport can hand the child a substituted
read-only descriptor without invalidating original digest evidence. Correct
both Important defects and add the two focused behavioral regressions before
another rereview.
