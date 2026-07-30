# Task 7 report: centralized Archon output resolution

## Outcome

Task 7 is implemented in commits `908b00005`
(`refactor(workflow): centralize archon output resolution`) and `2ceae14ec`
(`fix(workflow): preserve resolved output authority`), with open output-type
boundary alignment in `eda27235c` (`fix(workflow): preserve open output types`).
Authoring is aligned to that boundary in `4d42af019`
(`fix(workflow): bound output type metadata`). Cache, read, and completion
hardening is in `cd520e993`
(`fix(workflow): harden resolved output caching`), with boundary validation in
`59306c605` (`fix(workflow): validate resolved output boundaries`) and owned
candidate normalization in `660cdf1b7`
(`fix(workflow): normalize cached output ownership`). Archon downstream
consumers now resolve the successful attempt's canonical
`output.*` descriptor once into a frozen `ResolvedNodeOutput`. The resolver
verifies containment,
regular-file identity, byte size, SHA-256, UTF-8, candidate/descriptor agreement,
and the winning node/attempt identity before exposing canonical bytes, a frozen
parsed value, deterministic text, media type, digest, producer, attempt, and
optional publication identity.

No Task 8 publication bundle, persistent recovery, API, Desktop, or Phase 3
condition/reference/Bash semantics were added.

## Strict TDD evidence

### Identity RED/GREEN

Command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_resources.py -q
```

Initial RED: 12 passed, 1 failed because `ResolvedNodeOutput` did not exist.
After the minimal frozen/slotted identity, the same command passed 13/13.

### Resolver RED/GREEN

Command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_resources.py -q
```

RED: 13 passed, 2 failed because `resolve_node_output(...)` did not exist.
After the verified candidate/descriptor resolver, the same command passed
15/15.

### Consumer-routing RED/GREEN

Command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_resources.py -q
```

RED: 20 passed, 2 failed. The scheduler selected a later raw `stdout.*`
artifact instead of the successful attempt's canonical `output.*`, and
`VariableContext` returned the unresolved object rather than its canonical
rendering. After routing conditions, variables, prompt/script rendering, Bash
rendering, nested fields, and predecessor evidence through the resolved value,
the same command passed 22/22.

### Compatibility-adapter RED/GREEN

Command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_resources.py -q
```

RED: 16 passed, 1 failed because JSON-looking `text/plain` Bash/script output
lost the existing nested-field behavior. The minimal Phase 2 adapter parses
canonical text for the immutable `value` while retaining the exact canonical
text for whole-output rendering. The same command passed 17/17.

### Evidence projection RED/GREEN

Command:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py -q
```

RED: 5 passed, 1 failed because predecessor evidence had no bounded projection
from the resolved identity. After adding immutable metadata-only evidence
(media type, byte size, digest, producer, winning attempt, publication ID), the
same command passed 6/6. Canonical bodies and parsed values are not copied into
evidence.

### Review fix round 1 RED/GREEN

The focused review suite initially passed 53 tests and failed 7. Those failures
proved that recursively frozen objects could not be rendered, the scheduler
dropped the executor's primary-output candidate, AI nodes could fall back to
raw stdout, and repeated consumers reread a mutable artifact. A separate
failure-stability test also failed before the scheduler cached integrity
failures for a descriptor identity.

The fix retains a validated, body-free winning-candidate identity in attempt
metadata and keeps the executor's authoritative parsed candidate only in a
bounded scheduler cache. AI consumers require a corroborating canonical
descriptor; Bash/script nodes retain the stdout compatibility adapter.
Successful resolutions and integrity failures are cached by immutable
run/node/attempt/descriptor/candidate identity in a bounded scheduler cache.

The final focused command passed 61/61:

```text
scripts/run_tests.sh tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_ai_e2e.py -q
```

At that intermediate revision, a deliberate mutation removing stale-key
pruning made the descriptor-change test fail with two retained cache entries
instead of one. The later quality review proved snapshot-driven pruning was
not concurrency-safe and superseded it with byte-weighted LRU eviction and
terminal-run lifecycle cleanup. A second targeted RED proved that preserving
the authoritative parsed value required avoiding a second freeze/copy of an
already immutable candidate value.

### Review fix round 2 RED/GREEN

The Archon scheduler E2E failed before completion when a valid 322-character
mixed-case Unicode `output_type` reached the winning-candidate identity. The
restore validator imposed an unsupported 256-character enum-style limit even
though the language declares this field open and case-sensitive.

Candidate validation now uses the existing durable attempt-metadata string
contract of 16,384 characters. The E2E preserves the authored Unicode value
exactly through schema loading, candidate retention, completion metadata, and
resolution. Direct boundary coverage accepts and round-trips exactly 16,384
characters without projection truncation, while non-string, empty,
whitespace-only, and 16,385-character values raise a bounded integrity error.
The whitespace-only case supplied a second RED before validation was aligned
with the authoring schema's non-empty-string rule.

The four-file focused suite passed 67/67 after the round-2 fix.

### Review fix round 3 RED/GREEN

The authoring boundary test initially failed because a 16,385-character
`output_type` loaded successfully even though completion metadata rejects that
value. The exact 16,384-character mixed-case Unicode boundary already loaded
and was preserved verbatim.

The durable 16,384-character limit now lives on the canonical
`language_schema.FIELD_INVENTORY` field specification. Loader validation reads
that direct field bound and emits `string_too_long` at
`nodes[0].output_type`; the runtime-generated JSON Schema publishes
`maxLength: 16384` from the same spec. It also publishes the existing nonblank
contract without normalizing authored values. Tests cover exact-boundary
Unicode/case preservation, 16,385 rejection, and existing empty/whitespace
diagnostics through both loader and JSON-Schema paths.

Round-3 focused schema/resource/AI coverage passed 704/704. The complete Task 2
language/schema regression gate passed 776/776. The live
`hermes workflow schema --profile archon-2026-07 --json` generation path
reported the supported direct field with `minLength: 1`, `pattern: "\\S"`, and
`maxLength: 16384`.

### Quality fix round 1 RED/GREEN

Five focused regressions were written before the cache/read hardening:

- The descriptor-read race test failed because pathname-based `read_bytes()`
  bypassed the injected descriptor read. Resolution now opens every component
  relative to a trusted directory descriptor with no-follow semantics, verifies
  the same file descriptor with `fstat`, and reads at most the declared byte
  count plus one byte. Unsupported hosts fail closed.
- A first-read `EIO` escaped and prevented retry. Transient host I/O now returns
  an uncached unavailable outcome; stable integrity failures remain cached.
- A stale parallel projection deleted a newer cached resolution. Projection
  snapshots no longer act as cache authority.
- The entry-count cache had no retained-byte bound. Resolved bytes, rendered
  text, parsed immutable values, and retained candidates now share one
  conservative 16 MiB byte-weighted LRU. Terminal runs purge their entries once
  no active or submitted execution can consume them.
- A deterministic completion barrier showed a resolver could observe durable
  completion before candidate registration. Candidate registration and
  `complete_node` visibility are now linearized by the resolution lock, and a
  failed durable completion rolls back the attempt's candidate and resolution
  entries.

The focused scheduler/resource/AI E2E suite passed 51/51 after these changes.

### Quality fix round 2 RED/GREEN

Three additional boundary regressions were driven from RED to GREEN:

- A completed descriptor whose file was initially absent resolved successfully
  after the file was later created. Read failures now use a narrow retryable
  host-error allow-list: `EIO`, `EMFILE`, `ENFILE`, `EAGAIN`, `ESTALE`, and
  exposed `EINTR` remain retryable, while namespace/path failures such as
  `ENOENT`, `ENAMETOOLONG`, `ELOOP`, `ENOTDIR`, and `EISDIR` are stable
  integrity failures and negative-cache for the immutable descriptor identity.
  The existing first-`EIO`-then-success control remains retryable.
- NUL and normalized-equivalent candidate paths were accepted, while a NUL
  descriptor escaped as `ValueError` and crashed scheduler resolution. Candidate
  and descriptor validation now share one canonical relative-path validator;
  pathname `ValueError` is converted to a stable integrity failure.
- A 6 KiB JSON body containing 2,048 empty objects demonstrated the same
  proxy/backing-dict undercount as the bounded 500 KiB adversarial shape without
  a large test allocation. Deep accounting now uses actual object sizes,
  includes each mapping proxy and an equivalent backing-dict table/capacity,
  traverses keys, values, and containers with shared-ID/cycle protection, and
  charges retained cache/LRU/key/object overhead. A single overweight entry is
  immediately evicted; ordinary weighted LRU behavior remains unchanged.

The focused scheduler/resource/AI E2E suite passed 60/60 after these changes.

### Quality fix round 3 RED/GREEN

Two final ownership and host-resource regressions were driven from RED to GREEN:

- A tuple of 128 `MappingProxyType` views over a grown-then-pop-emptied backing
  dict retained that external allocation by identity, escaped the compact-dict
  weight estimate, and reflected later external mutation. The candidate trust
  boundary now recursively rebuilds every mapping and sequence into fresh owned
  compact containers, including mappings nested in tuples. Shared mappings
  reuse only their owned normalized proxy; active graph identities reject
  cycles. Non-string keys, arbitrary objects, sets, and non-finite numbers are
  rejected as non-JSON with a bounded integrity error. Resolved output preserves
  the resulting normalized candidate identity without another copy. The test
  independently measures the owned graph and verifies its cache charge covers
  that graph while remaining below the shared cache budget.
- A fail-once `ENOMEM` read was incorrectly negative-cached even though a second
  read succeeded. `ENOMEM` now joins the narrow retryable host-resource errno
  allow-list. Both injected `EIO` and `ENOMEM` retry successfully, while the
  stable `ENOENT` control remains negative-cached.

The focused scheduler/resource/AI E2E suite passed 67/67 after these changes.

## Consumer invariants

- Successful-attempt identity wins; failed/losing attempts cannot supply a
  downstream value.
- A canonical `output.*` descriptor wins over a same-attempt raw `stdout.*`
  descriptor.
- Descriptor bytes, byte size, SHA-256, media type, node, attempt, and optional
  candidate/publication identity are corroborated before resolution.
- Parsed mappings and arrays are recursively frozen. Conditions, prompt/script
  substitution, Bash substitution, nested lookup, hashing, and bounded evidence
  derive from the same immutable resolved identity.
- Evidence projects metadata only and never reparses or embeds the provider's
  raw response or canonical body.

## Phase 2 compatibility and legacy proof

- `hermes-legacy` delegates to the extracted byte-for-byte equivalent artifact
  scan: artifact order still wins last, only `output.*`/`stdout.*` participate,
  UTF-8/text limits and exception skipping are unchanged, JSON-looking text is
  parsed exactly as before, and other text remains text.
- Archon retains Phase 2 missing-output/missing-field outcomes by treating an
  unavailable or invalid resolved descriptor as absent at the consumer; Phase 3
  will make those outcomes strict.
- Existing condition coercion and `&&`-before-`||` precedence are explicitly
  covered in `test_language.py`.
- Existing Bash quote-context handling and large-value spill behavior remain
  covered by `test_resources.py` and the Bash E2E suite.
- JSON-looking schemaless/Bash/script text retains nested-field behavior without
  changing its canonical text rendering.

## Verification

Exact required suite:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_language.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_script_executor.py
```

Fresh result after quality fix round 3: 5 files, 98 passed, 0 failed.

Focused cache/read/completion suite:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_ai_e2e.py -q
```

Result: 3 files, 67 passed, 0 failed.

Adjacent Archon/condition/shared-context suite:

```text
scripts/run_tests.sh tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_ai_e2e.py -q
```

Result: 3 files, 116 passed, 0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/output_resolution.py tests/plugins/workflow/test_resources.py tests/plugins/workflow/test_scheduler.py
git diff --check
```

Result: Ruff passed and the diff whitespace check was clean.

## Files

- `plugins/workflow/output_resolution.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/resources.py`
- `tests/plugins/workflow/test_scheduler.py`
- `tests/plugins/workflow/test_resources.py`
- `tests/plugins/workflow/test_script_executor.py`
- `tests/plugins/workflow/test_ai_e2e.py`
- `.superpowers/sdd/2026-07-30-workflow-language-phase-2-structured-data/task-7-report.md`

The report is the only file-map expansion; it is required by the task handoff.

## Concerns

None within Task 7 scope. Publication durability and recovery remain explicitly
deferred to Task 8.
