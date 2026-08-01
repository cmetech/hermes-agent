# Phase 3 Semantic Compatibility and Resilience — Design Review 1

**Verdict:** Changes required before implementation planning.

**Severity count:** 0 Critical, 9 Important, 0 Minor.

## Important findings

### I-1 — The effective-semantics snapshot has no exact schema or sealed limit authority

**Design references:** lines 180–214 define an exact requested-semantics snapshot, while lines 218–235 require a separate `phase3_effective_node_semantics` projection to be intersected with “sealed `RunExecutionLimits`,” verified on resume, and executed instead of current config.

The requested snapshot is specified field-by-field, but the execution-authoritative projection is not. The design does not define its exact JSON shape, which timeout ceilings it contains (notably the provider-request ceiling required at lines 261–264), how profile defaults reach admission, or where the supposedly sealed `RunExecutionLimits` live. This matters because the current scheduler reconstructs limits from current `profile_execution_limits`; merely sealing requested node values would still let a resumed run change behavior after config changes. The stated mismatch check also cannot be implemented deterministically without a canonical limit authority.

**Remediation:** Define the exact versioned effective-projection schema and canonical encoding. At admission, resolve profile defaults plus sidecar limits once and store every Phase 3 execution input needed by a node—effective wall, idle, provider-request, subprocess, and combined-attempt ceilings, requested/effective values, and `capped`. On resume, authenticate and consume that projection directly; do not call current-config limit resolution for Phase 3 semantics. Add entry-point parity and changed-config resume tests.

### I-2 — Omitted Bash/script timeout discards Archon’s 120-second default

**Design references:** line 36 records Archon’s 120,000 ms default, but lines 246–250 say an absent Bash/script `timeout` has no authored value and that the sealed Hermes subprocess default applies.

Those contracts diverge whenever the Hermes subprocess ceiling is not exactly 120 seconds. For example, a 300-second sealed ceiling would make an omitted Archon timeout run for 300 seconds, although a ceiling should cap the Archon default rather than replace it. This silently changes accepted upstream behavior and conflicts with the parent design’s normalize-then-cap contract.

**Remediation:** Normalize an omitted Archon Bash/script timeout as a requested default of `120.0` seconds, record its default source, and compute `effective = min(120.0, sealed_subprocess_ceiling)`. Keep legacy omission behavior unchanged. Test ceilings below, equal to, and above 120 seconds.

### I-3 — The non-resetting node deadline is not durable across retries or process restart

**Design references:** lines 261–264 require one absolute monotonic deadline and state that a retry cannot reset or extend it; lines 653–680 define crash behavior without defining deadline persistence.

An absolute monotonic timestamp is process-local and cannot itself survive restart. The design does not say when the node deadline is established, which durable run field anchors it, how retry backoff consumes it, or how a new coordinator reconstructs remaining time. The current execution path creates a new `DeadlineBudget` for each claimed invocation, so an implementation following only the specified snapshot values can reset the full duration on every retry.

**Remediation:** Define a claim-fenced, first-attempt deadline record in `RunStore` that is created once and never replaced. Persist a restart-safe wall deadline or conservatively journal elapsed/remaining duration; convert only the remaining duration to a process-local monotonic budget on each claim. Retry delay and pauses must not extend it. Specify clock-discontinuity handling and test retry, coordinator restart, and crash recovery near expiry.

### I-4 — The “one strict resolver” contract is not closed over accepted identifiers or named script resources

**Design references:** lines 367–402 require every authenticated interpolation surface to use one resolver, but lines 371–393 neither define a single versioned reference grammar nor include authenticated named `scripts/` bodies (only inline script bodies at line 374).

Today, accepted node IDs and the existing parsers are not equivalent: schema IDs may contain any non-whitespace character except slash/backslash, condition references accept a broader Unicode/dot/colon form, and runtime substitution accepts only ASCII identifiers beginning with a letter or underscore. A valid node such as `review.v2`, `2nd`, or a Unicode ID can therefore be a declared dependency but not be scanned and rendered consistently. Dotted mapping keys are also ambiguous with path separators. Separately, named script bytes execute without interpolation today; omitting them allows an admitted `$producer.output` in an authenticated script body to remain silently ineffective.

**Remediation:** Specify one v3 reference lexer and path grammar in the central field inventory and use it for schema, admission scanning, conditions, and rendering. Either narrow v3 node IDs without changing legacy or define an escaping/bracket syntax for IDs and mapping keys. Scan sealed named script bytes before promotion and either render a new attempt-local byte-authoritative script through the strict resolver or reject references in named scripts with an explicit compatibility blocker. Add cross-surface identifier and named-script tests.

### I-5 — Whole-output resolution returns text, but typed conditions require the canonical value

**Design references:** line 408 says a whole-output reference returns verified canonical text; lines 455–473 require type-directed string/numeric comparisons derived from the canonical value and prohibit returning to provider text.

For a structured root scalar, these rules conflict. If `$producer.output` for canonical JSON number `5` returns text, comparison to numeric `5` becomes a type mismatch; a structured root string would include JSON string quoting if canonical text is used. Phase 2 already distinguishes the parsed canonical value from deterministic rendering, but Phase 3 does not state which resolver facet each consumer receives.

**Remediation:** Make the immutable resolver result expose both `typed_value` and `rendered_text`. Conditions consume `typed_value` when a declared structured contract exists; prompt/script/Bash substitution consumes the deterministic rendering. Schemaless whole output remains a string and never gains field semantics. Add root number, string, boolean, null, array, and object condition tests.

### I-6 — Transient output reads have contradictory outcomes and no bounded wake protocol

**Design references:** lines 417–429 say `output_reference_temporarily_unavailable` makes the scheduler yield without changing node state; lines 431–434 say resolver failure becomes a terminal `NodeExecutionResult`; lines 797–798 require yielding without hot loops.

The transient case is simultaneously non-terminal and terminal. If the node simply remains pending/ready, every coordinator sweep—and every competing coordinator—can resolve it again immediately, creating a durable hot loop. If it is converted to the terminal result described at lines 431–434, the documented transient behavior is lost.

**Remediation:** Explicitly exclude the transient code from terminal conversion. Add a fenced store transition that releases or avoids the claim without attempt/retry charge and persists a bounded `next_resolution_at` wake with capped backoff. Suppress ordinary runnable selection until that wake, clear it on success or terminal failure, and define restart/multiprocess behavior.

### I-7 — Bash spill creation is descriptor-safe, but consumption reopens an attacker-swappable pathname

**Design references:** lines 516–526 require descriptor-relative, no-follow creation and fail-closed integrity, but the prologue at lines 530–535 later invokes `cat '/contained/file'` by pathname.

The pathname can be unlinked or replaced after verification and before `/bin/sh` opens it. That defeats the stated single-link/regular-file authority, can substitute different or unbounded contents, and leaves no digest check at consumption. The existing symlink/escape tests at lines 811–815 do not cover this post-render race.

**Remediation:** Keep the verified spill descriptor open through process launch and have the shell read from an inherited, fixed read-only descriptor (or another genuinely handle-based mechanism), not by reopening a pathname. Bind the descriptor identity and content digest to the rendered-command evidence and fail closed when the platform cannot provide the primitive. Add a swap/symlink race between materialization and spawn.

### I-8 — Three top-level quote states are insufficient to preserve values in arbitrary POSIX shell bodies

**Design references:** lines 47–55 and 541–551 claim exact value preservation in every quote context, while the renderer distinguishes only unquoted, double-quoted, and single-quoted states; Bash tests at lines 811–815 cover only those three cases.

POSIX shell also has quoted and unquoted here-documents, comments, escapes, and nested command/arithmetic/parameter substitutions. In an unquoted here-document, inserted double quotes can become literal output; in a quoted here-document, the generated `${spill}` is not expanded at all. A three-state quote walk therefore cannot support the design’s exact-content and no-evaluation claims for all admitted Bash text.

**Remediation:** Either implement and specify a bounded shell lexer that recognizes here-document delimiters/bodies, comments, escaping, and nesting with context-specific replacements, or fail admission for output references outside a deliberately small set of proven-safe simple-token contexts. Add real `/bin/sh` tests for quoted/unquoted heredocs, comments, escaped delimiters, and nested substitutions.

### I-9 — Session-registry CAS is not durably ordered with winning node completion

**Design references:** lines 609–622 perform session CAS after provider success; lines 632–647 journal selection/outcome evidence; lines 653–676 cover a crash before CAS but not CAS success followed by a crash before node completion.

The session registry is separate SQLite state from the run journal. If CAS succeeds and the process crashes before the node’s successful result is durably completed, the registry points at a session produced by an unjournaled or uncertain attempt. If provider success occurs and the process crashes before CAS, the exact new session identity is unavailable for an idempotent recovery obligation. Merely journaling a hashed recovery outcome cannot reconcile either boundary. The current executor also performs registry CAS before scheduler completion, so this ordering must be designed explicitly rather than left to implementation.

**Remediation:** Before external CAS, atomically journal the winning node result plus a claim-fenced pending registry-update obligation containing the protected exact new session ID, key, expected generation, and fingerprint. Recovery executes that obligation idempotently; CAS success/loss is then journaled and the obligation cleared. Never advance registry state from an unjournaled result. Define CAS operational failure, cancellation, and crash behavior on both sides of every write, and verify them with real store/registry processes.

## Required disposition

Resolve all nine Important findings in the design before plan approval. Each remediation changes an execution or persistence contract; none is only editorial.
