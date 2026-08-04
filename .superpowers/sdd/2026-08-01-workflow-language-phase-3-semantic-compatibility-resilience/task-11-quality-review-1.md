# Task 11 Quality and Security Review 1

## Reviewed identities

- Base commit: `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Head commit: `6a824d52a0fab0bf7c4e50c39f4102d315e9c54d`
- Head tree: `c985c6f5d7a8818451bd6e2136eafcfd663f660a`
- Commits: `75a7523be`, `9e0e6cb3a`, and `6a824d52a`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Review package: `review-25fc0397a..6a824d52a.diff`
- The checked-out commits/tree matched the requested identities. The supplied package exactly matched `git diff -U10` for the range, and the worktree was clean before this permitted report write.

## Verdict and severity totals

**FAIL**

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 4 |
| Minor | 0 |

The four Important findings are correctness, security, or cleanup gaps in the Task 11 trust boundary. They must be corrected before the Archon normalizer-v3 renderer can be trusted.

## Scope and method

This was a read-only quality/security review of Task 11 only across the exact three-commit range. I read the task brief, approved Phase 3 design, implementation report, full diff package, and both specification-rereview reports, then independently traced the changed Bash admission, rendering, materialization, scheduler preflight, executor, evidence, version-gating, and test paths.

The attack surface considered was authenticated Bash template text plus dynamic workflow/user/output values, the same-host attempt filesystem, and the Task 10 inherited-descriptor launch seam. Outside the diff, I inspected only two concrete named authorities needed to resolve those risks: `iter_output_references()` in `plugins/workflow/language_schema.py:143-205` and descriptor pin/exec handoff in `tools/managed_process.py:776-960`.

I did not rerun the reported broad suites. Their totals remain author claims, as requested. No repository test was run during this quality review.

## Strengths and non-findings considered

- Archon behavior is gated on both `archon-2026-07` and sealed normalizer version 3; scheduler propagation at `plugins/workflow/scheduler.py:3187-3191` prevents admitted v1/v2 runs from silently acquiring Task 11 semantics.
- UTF-8 byte limits, NUL rejection, distinct-byte deduplication, the 32,768-byte inline boundary, per-value/total limits, and the fixed 64-descriptor ceiling are explicit and occur before launch.
- The ordinary admitted unquoted/double-quoted/single-quoted replacements are injection-resistant, and the sentinel/read-status prologue preserves trailing newlines and does not mask a failed `cat`.
- Creation uses descriptor-relative traversal, no-follow/exclusive flags, restrictive modes, regular/single-link checks, reopen identity/size/digest verification, and rewind. Task 10 pins only nominated read-only descriptors, remaps them to the exact target numbers, uses `close_fds=True`, and preserves process-tree containment.
- `argv[-1]` is the immutable rendered command. Attempt evidence is bounded to sizes, digests, counts, and descriptor numbers; it contains no command text, substituted values, or spill paths.
- Native Windows large spills fail before launch, while the inline argv branch remains on the existing Windows gate. No new core tool, API, prompt mutation, environment option, or Phase 4/5 implementation surface was added.
- All four Task 11 durable codes are catalogued for Archon normalizer v3, and the changed tests include real `/bin/sh`, version-compatibility, pathname-swap, symlink, descriptor-read, and evidence checks.

## Critical findings

None.

## Important findings

### I-1 — The bounded lexer can leave a command-substitution frame early and admit references that remain nested

**Location:** `plugins/workflow/bash_rendering.py:225-290`

**Defect:** A command-substitution frame is modeled as a raw parenthesis count. Every unquoted `)` decrements it, and depth zero restores the outer quote context. That is not a safe shell nesting rule: in `$(case x in x) printf '%s' $producer.output;; esac)`, the `)` ending the `case` pattern is not the command-substitution terminator, but lines 261-266 pop the frame there. The later reference is therefore classified as top-level (or as protected by an outer double quote) even though the real shell still evaluates it inside `$(...)`. The lexer also recognizes only `$(())`, `$()`, and `${}` at lines 225-238 and ordinary POSIX quote toggles at lines 191-205. On Bash-compatible `/bin/sh` paths, legacy arithmetic `$[...]` and ANSI-C `$'...'` are consequently treated as ordinary simple-token text; `$[$USER_MESSAGE]` is evaluated rather than preserved as data, while backslash escapes inside `$'...'` make inline content differ from spilled content.

**Impact:** An authenticated author can reasonably expect a recognized value in these locations to be rejected. Instead, dynamic data is rendered with the wrong quote table. In the `case` example, spaces and globs can split/expand and become additional command arguments; `$[...]` changes the value by arithmetic evaluation; and ANSI-C quoting makes semantics size-dependent. This violates exact-content preservation and the fail-closed injection boundary. Category: Tampering / command-argument injection. Reachability: an admitted Archon v3 Bash node with attacker-influenced output or scalar data.

**Correction:** Extend the bounded lexical state machine to recognize shell constructs that affect nesting on every supported shell path, or conservatively reject a reference whenever a construct cannot be proven simple. In particular, do not treat a `case` pattern terminator as the root `$()` close, and recognize/fail closed for `$[...]` and `$'...'`. Add real-shell admission and no-launch tests for `case` within command substitution, nested heredoc/case forms, legacy arithmetic, and ANSI-C quoting, including metacharacters and inline/spill boundary values.

### I-2 — Strict grammar parsing happens before escaped/comment context filtering

**Location:** `plugins/workflow/schema.py:1096-1135`; `plugins/workflow/resources.py:797-808,868-896`; `plugins/workflow/scheduler.py:1529-1547`; concrete parser authority at `plugins/workflow/language_schema.py:143-205`

**Defect:** Admission, direct runtime rendering, and scheduler preflight all call `iter_output_references()` before invoking `classify_bash_reference_spans()`. The parser raises immediately for a malformed reference-like candidate. The lexer can therefore ignore only grammar-valid references. For example, `# $producer.output[0]` and `printf '%s' \$producer.output[0]` raise `output_reference_path_unsupported` before their comment/escape context can be classified, even though both tokens are literal shell text under the Task 11 contract.

**Impact:** Exact “ignore escaped references and comment text” behavior is not implemented for the full reference-candidate space. Valid Archon v3 workflows containing literal documentation or escaped shell text are blocked at admission, and all three supposed shared authorities preserve the same wrong ordering. This is a backward/compatibility and authority-consistency failure. Category: Denial of service / compatibility rejection. Reachability: any Archon v3 Bash author using literal malformed reference-like text in a comment or escape.

**Correction:** Make lexical context the first authority over dollar-token candidate ranges, then apply strict reference grammar only to admitted non-literal candidates. Expose ignored/unsupported ranges or candidate decisions from the lexer so schema, runtime rendering, and scheduler preflight share the same parse order. Add admission, direct-executor, and scheduler tests for escaped/comment-only malformed candidates (brackets, slashes, dotted unsupported paths) with no declared producer.

### I-3 — Verified spill contents remain mutable through the published inode pathname

**Location:** `plugins/workflow/bash_rendering.py:410-471,474-518`

**Defect:** `_verified_spill_descriptor()` verifies the reopened read descriptor's identity, size, and digest and rewinds it, but leaves `variables-v3/spill-####` linked and writable by its owner until cleanup. A concurrent same-UID process can open that pathname for writing after line 464 and overwrite the same inode before `cat` consumes the inherited descriptor. The inode, link count, and descriptor identity do not change, and there is no digest check after the handoff. The existing pathname-replacement test unlinks and creates a different inode, which the open descriptor correctly defeats, but it does not exercise in-place mutation of the verified inode.

**Impact:** The shell can consume attacker-selected bytes while evidence still records the digest of the originally verified value. That breaks descriptor-authoritative content integrity and can alter command arguments after verification. Category: Tampering / evidence repudiation. Reachability: an adjacent same-host process running as the Hermes user (including another workflow/plugin process) that can discover the deterministic attempt spill path.

**Correction:** Use an anonymous immutable/sealable backing object where available, or otherwise remove the pathname descriptor-relatively as part of the verified handoff and prove that the child consumes an object that cannot retain an external writer; fail closed on hosts that cannot supply the required immutability. The final design must close the already-open-writer race as well as future pathname reopen. Add a concurrent test that overwrites/truncates the same inode after verification but before spawn and requires either the original bytes or `bash_spill_integrity`, never altered bytes with original evidence.

### I-4 — Spill descriptor ownership is not exception-safe before spawn

**Location:** `plugins/workflow/executors/bash.py:90-174`; `plugins/workflow/bash_rendering.py:572-630`

**Defect:** `BashExecutor` receives live spill descriptors, builds evidence, and only then creates a manual `owned_descriptors` list. The first encompassing spawn `try` starts at line 174. If `RenderedBashCommand.evidence()`, `BoundedProcessOutput(...)` at line 140, a deadline/callback, or another unexpected pre-spawn operation raises, no `finally` closes the descriptors. Likewise, after `render_v3_bash()` successfully materializes descriptors at lines 572-580, command/prologue/evidence construction through line 630 has no cleanup guard if a later exception occurs.

**Impact:** A filesystem/output-open failure or unexpected callback/error can leave up to the bounded spill set open in the long-lived worker, retaining secret-bearing objects and eventually exhausting the descriptor table across runs. The scheduler converts ordinary exceptions to `executor_crash`, so the leak can outlive the failed attempt without a typed cleanup signal. Category: Denial of service / information retention. Reachability: ordinary I/O failure or a same-host actor inducing output-open/callback failure.

**Correction:** Represent the rendered command as an explicit ownership context (or immediately register every descriptor with `ExitStack`) and wrap every operation after materialization through successful spawn in one `try/finally`. Transfer/close ownership only after Task 10 has pinned and exec-confirmed the child. Give `render_v3_bash()` its own post-materialization exception guard. Add fault-injection tests for evidence construction, stdout/stderr creation, timeout/cancellation callbacks, and post-materialization command construction; every observed descriptor must be closed.

## Minor findings

None.

## Test-quality assessment

The tests are materially stronger than mock-only coverage: they use real `/bin/sh` for content identity, demonstrate exact UTF-8 boundaries and quote contexts, validate the immutable `argv[-1]`, exercise Task 10 descriptor inheritance, and explicitly characterize legacy/v1/v2 behavior and native-Windows limitations. The catalog additions are behaviorally represented elsewhere in the focused file rather than being orphan strings.

The suite nevertheless overstates closure in four places corresponding to the findings:

- unsafe-context cases at `tests/plugins/workflow/test_phase3_bash_substitution.py:90-135` cover only structurally simple nesting and miss `case` pattern terminators and accepted shell-dialect forms;
- ignored-reference cases at lines 138-192 use only grammar-valid references, so they cannot expose parse-before-ignore ordering;
- the race test at lines 504-534 replaces the pathname with a new inode, not the verified inode's contents; and
- cleanup tests at lines 581-643 cover a closed descriptor and rejected spawn intent, but not exceptions between successful materialization and the spawn `try`.

The 64/65 materializer test calls the private materializer with one-byte entries. That honestly proves the private hard cap, but it is not an end-to-end public-renderer file-count test; public spills are each greater than 32,768 bytes, so the 2,000,000-byte total bound dominates before 64 public spill values are reachable.

## Cannot-verify items

- The reported `220 passed` Task 11 acceptance result, `173 passed` supplemental result, lint results, and RED history were not independently rerun and remain claims.
- Native Windows Job Object creation, suspended assignment, resume, and cleanup could not be executed on this Darwin host. The test accurately simulates only Windows argv selection before returning to POSIX containment.
- No hostile concurrent filesystem race or exception-fault suite exists for I-3/I-4, so those paths were verified by direct control-flow/descriptor analysis rather than an existing focused repository test.

## Final assessment

Task 11 has a sound overall architecture: version gating is correct, ordinary simple-token rendering preserves data, the Task 10 handoff is narrow, and evidence is bounded/private. It is not ready to trust, however. The lexer can misclassify real shell contexts, ignored literal candidates are parsed in the wrong order, verified inode contents can change after their digest is recorded, and descriptor cleanup is not total under exceptions. Correct all four Important findings and add behavioral regressions at admission, scheduler, real-shell, race, and fault-injection layers before rereview.
