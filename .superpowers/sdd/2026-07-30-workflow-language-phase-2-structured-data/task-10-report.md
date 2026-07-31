# Task 10 report: expose bounded evidence, preview, and download

## Outcome

Task 10 is implemented. Typed-artifact evidence now exposes only bounded,
backend-confirmed publication metadata and never exposes a body, filesystem
path, metadata digest, or other internal publication fields. Authorized
operators can request bounded previews or download verified immutable bytes by
opaque `(run_id, publication_id)` identity.

The publication lookup authorizes the run before validating the caller's
publication ID, resolves only the projection-confirmed canonical
`content.json` or `content.md`, opens it through the existing contained
regular-file reader, and rechecks the recorded size and SHA-256 on every
access. Unknown, malformed, ambiguous, swapped, symlinked, or corrupt
publications fail closed.

## Implementation details

- Added an immutable `VerifiedPublication` value and store-backed publication
  lookup to `EvidenceReader`.
- Preserved legacy raw-artifact evidence byte-for-byte while projecting typed
  artifacts through an explicit metadata allowlist with verified integrity and
  recovery status.
- Added authenticated `preview` and `download` routes under each run's
  server-resolved artifact identity.
- JSON previews are parsed only when the complete canonical body fits the
  64-KiB preview limit; larger JSON is omitted instead of partially parsed.
- Text previews are sanitized and byte-bounded. Downloads stream the already
  verified bytes with the recorded safe media type, exact content length, and
  an ASCII-safe opaque-ID/canonical-name filename.
- Added metadata-only characterizations proving coordinator candidate scans
  and workflow catalog listing do not open artifact bodies.
- Aligned the workflow-detail response contract with the Phase 2 normalizer
  version and updated stale Phase 1 integration expectations for structured
  output capacity and sealed execution identities.

## Strict TDD evidence

Initial artifact-evidence RED:

```text
scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py
```

Observed expected RED: 1 failed, 7 passed, 1 skipped. Typed evidence leaked
internal fields and did not expose the required integrity/recovery metadata.

Descriptor hardening RED:

```text
scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py -k untrusted_descriptor_fields
```

Observed expected RED: 1 failed and 4 passed because an unhashable hostile
media type escaped as `TypeError`. The lookup now rejects it before any body
open.

Non-finite JSON RED:

```text
scripts/run_tests.sh tests/plugins/workflow/test_desktop_api.py -k noncanonical_nonfinite
```

Observed expected RED: the preview returned an internal error instead of the
typed publication-integrity conflict. Strict JSON parsing now rejects
non-finite constants and returns the stable 409 contract.

Focused GREEN results:

- Evidence API: 23 passed, 1 platform skip.
- Artifact endpoint focus: 8 passed.
- Non-finite JSON focus: 1 passed.
- Coordinator metadata-only characterization: 1 passed.
- Catalog metadata-only characterization: 1 passed.
- Workflow-detail API: 37 passed.

## Exact Task 10 acceptance

```text
scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_security_boundaries.py
```

Final fresh result: 5 files, 219 passed, 0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/evidence.py plugins/workflow/dashboard/plugin_api.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py
git diff --check
```

Result: Ruff and the whitespace check passed.

## Self-review

- Confirmed authentication and read capability checks precede store access,
  and run authorization precedes opaque-ID validation or file access.
- Confirmed callers cannot supply a path, media type, content name, digest, or
  size; all are taken from the checked run projection.
- Confirmed only the two canonical publication media/name pairs are accepted
  and descriptor text, size, and digest fields are bounded and typed.
- Confirmed containment, regular-file identity, recorded size, and content
  digest are rechecked after authorization on every preview/download.
- Confirmed JSON is never partially parsed, text is sanitized within the
  preview limit, and full artifact bodies never enter evidence, catalog, or
  coordinator list responses.
- Confirmed wrong operator scope/profile and unknown/path-like identities do
  not disclose publication ownership.
- Confirmed legacy raw-artifact sanitization remains unchanged.

## Files changed

- `plugins/workflow/evidence.py`
- `plugins/workflow/dashboard/plugin_api.py`
- `tests/plugins/workflow/test_evidence_api.py`
- `tests/plugins/workflow/test_api_runtime.py`
- `tests/plugins/workflow/test_desktop_api.py`
- `tests/plugins/workflow/test_workflow_detail_api.py`
- `.superpowers/sdd/2026-07-30-workflow-language-phase-2-structured-data/task-10-report.md`

## Concerns

None within Task 10 scope.

## Specification fix round 1/5

The specification-review findings are corrected in a separate follow-up
commit. `RunStore` now owns authoritative publication lookup: it authorizes
the run and operator scope first, checks a metadata-only projection against
the journal head, corroborates only the requested descriptor against sealed
output authority and winning-attempt metadata, and then opens only that
publication's canonical body. `EvidenceReader` and the HTTP routes consume
the store-owned result.

Run listing and queued coordinator candidate selection now use a checked
metadata-only load path. Explicit `load_run()` retains the existing
typed-publication recovery and mirror-recovery behavior. The new
characterizations use real checked typed publications on still-actionable
runs and fail if the listing or coordinator paths open those bodies.

Publication metadata validation now uses the canonical producer/store bound
of 16,384 characters through the existing store validator. Preview and
download accept valid `output_type` and `session_id` values at that boundary;
typed evidence remains body-free and bounded. Both producer fields are
rejected at 16,385 characters, and a forged checked descriptor over the same
bound fails before its body is opened.

### Fix-round TDD evidence

The following focused tests were written and observed failing before the
production correction:

- queued coordinator scan with a real publication: 1 failed, 0 passed;
- `GET /runs` with a real publication: 1 failed, 0 passed;
- preview/download at the canonical metadata boundary: 1 failed, 0 passed;
- selective store lookup and oversized checked descriptor: 2 failed,
  0 passed.

After implementation, the combined five-regression focus passed: 5 passed,
0 failed. The producer-bound characterization passed: 2 passed, 0 failed.

### Fix-round verification

Exact Task 10 acceptance:

```text
scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_security_boundaries.py
```

Fresh result: 5 files, 226 passed, 0 failed.

Store/coordinator regression suite:

```text
scripts/run_tests.sh tests/plugins/workflow/test_store.py tests/plugins/workflow/test_coordinator.py tests/plugins/workflow/test_coordinator_multiprocess.py
```

Fresh result: 3 files, 59 passed, 0 failed.

Task 9 typed-publication recovery/security regression suite:

```text
scripts/run_tests.sh tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_security_boundaries.py
```

Fresh result: 3 files, 86 passed, 0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/store.py plugins/workflow/evidence.py plugins/workflow/dashboard/plugin_api.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py
git diff --check
```

Result: Ruff and the whitespace check passed.

### Fix-round files changed

- `plugins/workflow/store.py`
- `plugins/workflow/evidence.py`
- `plugins/workflow/dashboard/plugin_api.py`
- `tests/plugins/workflow/test_evidence_api.py`
- `tests/plugins/workflow/test_api_runtime.py`
- `tests/plugins/workflow/test_desktop_api.py`
- `.superpowers/sdd/2026-07-30-workflow-language-phase-2-structured-data/task-10-report.md`

## Quality fix round 1/5

The quality/security review findings are corrected in a second follow-up
commit.

Metadata-only loading now separates three concerns. Versioned typed
descriptors are always validated against the sealed declarations, descriptor
schema, publication-ID uniqueness, winning attempt, and attempt-local path.
Legacy migration remains enabled only for explicit recovery loads because it
may open legacy bundles. Publication bundle and mirror recovery likewise
remain exclusive to explicit recovery loads. Metadata-only loads reject
unversioned typed descriptors without opening bodies, and semantic validation
finishes before any index synchronization or repair-verification transition.

Retryable descriptor-relative read failures now retain a distinct
`PublicationUnavailableError` at the store boundary. Preview and download
return HTTP 503 with
`artifact_temporarily_unavailable` and `retryable: true`, without creating a
durable `typed_publication_integrity` marker. This applies to both requested
content and transient sealed-definition access, including store startup
reconciliation. Removing the injected fault allows the same request to
succeed. Deterministic descriptor and content contradictions retain their
409 integrity response and repair transition.

JSON preview now rejects duplicate keys and non-finite values recursively,
reserializes with sorted keys, compact separators, UTF-8, and `allow_nan=False`,
and requires exact byte equality with the verified publication. Overflowing
numbers, excessive nesting, duplicate keys, noncanonical whitespace/order,
Unicode errors, and canonicalization failures are converted to the bounded
409 typed-publication-integrity response.

The non-authoritative workflow-catalog body test was removed. It used the
wrong store root and tested a workflow-definition catalog that does not
consume run publications. The real run-list and coordinator tests remain and
use checked `RunStore` publications with exact canonical body-read traps.

### Quality-round TDD evidence

Focused tests were added and observed failing before production edits:

- queued coordinator checked-descriptor matrix: 0 passed, 7 failed; all seven
  corrupt variants were accepted instead of raising `JournalRecoveryError`;
- `GET /runs` checked-descriptor matrix: 0 passed, 7 failed; all seven variants
  were returned as authoritative metadata;
- transient requested-content and sealed-definition access across preview and
  download: 0 passed, 4 failed; every response was 409 instead of 503;
- unsafe/noncanonical JSON: 0 passed, 4 failed; overflowing numbers and deep
  nesting returned 500, while duplicate keys and noncanonical bytes returned
  200.

The checked-descriptor variants cover unknown media, boolean size, duplicate
publication IDs, a non-winning attempt, sealed `output_type` mismatch, sealed
schema mismatch, and an unversioned legacy descriptor.

Focused GREEN:

```text
scripts/run_tests.sh tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py -k 'queued_coordinator_rejects_corrupt_checked_typed_metadata_without_body_reads or runs_list_rejects_corrupt_checked_typed_metadata_without_body_reads or artifact_endpoints_preserve_retryable_publication_unavailability or json_artifact_preview_rejects_noncanonical_or_unsafe_json or json_artifact_preview_is_complete_or_omitted or json_artifact_preview_rejects_noncanonical_nonfinite_content or never_opens_real_artifact_bodies' -vv
```

Fresh result: 2 files, 27 passed, 0 failed. This includes the 14 descriptor
cases, four transient cases, four new JSON cases, three existing JSON
contracts, and two real-publication body-free checks.

### Quality-round verification

Exact Task 10 acceptance:

```text
scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_security_boundaries.py
```

Fresh result: 5 files, 247 passed, 0 failed.

Store, coordinator, and Task 9 typed-publication recovery/security:

```text
scripts/run_tests.sh tests/plugins/workflow/test_store.py tests/plugins/workflow/test_coordinator.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_security_boundaries.py
```

Fresh result: 6 files, 145 passed, 0 failed.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/store.py plugins/workflow/evidence.py plugins/workflow/dashboard/plugin_api.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py
git diff --check
```

Result: Ruff and the whitespace check passed.

### Quality-round files changed

- `plugins/workflow/store.py`
- `plugins/workflow/dashboard/plugin_api.py`
- `tests/plugins/workflow/test_api_runtime.py`
- `tests/plugins/workflow/test_desktop_api.py`
- `tests/plugins/workflow/test_workflow_detail_api.py`
- `.superpowers/sdd/2026-07-30-workflow-language-phase-2-structured-data/task-10-report.md`

## Quality fix round 2/5

The residual retryable sealed-definition stat taxonomy is corrected.
`_sealed_typed_output_declarations()` now classifies `OSError` from
`definition.yaml` `lstat()` with the same authoritative retryable errno set
used by descriptor-relative reads. Retryable stat failures raise
`ArchonOutputUnavailableError` before deterministic journal-integrity
handling, so preview and download return the bounded retryable 503 contract
without creating a durable `typed_publication_integrity` transition.

Non-retryable stat failures retain the deterministic `JournalRecoveryError`
path. Existing missing, unsafe/nonregular, reparse-point, oversized, and
malformed sealed-definition behavior is unchanged.

### Round 2 TDD evidence

The endpoint matrix was added before production changes and injects each
portable retryable errno available on this host:

- `EAGAIN`
- `EIO`
- `EMFILE`
- `ENOMEM`
- `ENFILE`
- `ESTALE`

Each errno is exercised through both preview and download. Initial RED:
0 passed, 12 failed; every case returned 409 instead of 503. The tests also
require a bounded
`artifact_temporarily_unavailable`/`retryable: true` response, no active typed
integrity repair marker, and success from the same endpoint after the fault is
removed.

Focused GREEN:

```text
scripts/run_tests.sh tests/plugins/workflow/test_desktop_api.py -k 'artifact_endpoints_preserve_retryable_publication_unavailability or artifact_endpoints_preserve_retryable_sealed_definition_lstat_errors' -vv
```

Fresh result: 1 file, 16 passed, 0 failed. This includes the four existing
content/sealed-definition read cases and the twelve new stat cases.

### Round 2 verification

Exact Task 10 acceptance:

```text
scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_security_boundaries.py
```

Fresh result: 5 files, 259 passed, 0 failed.

Requested ten-file regression matrix:

```text
scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_coordinator.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py
```

Fresh result: 10 files, 375 passed, 0 failed, with one platform-specific
skip. The prior baseline contained 363 tests; this round adds 12 portable
stat-error cases.

Static verification:

```text
.venv/bin/ruff check plugins/workflow/store.py plugins/workflow/evidence.py plugins/workflow/dashboard/plugin_api.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py
git diff --check
```

Result: Ruff and the whitespace check passed.

### Round 2 files changed

- `plugins/workflow/store.py`
- `tests/plugins/workflow/test_desktop_api.py`
- `.superpowers/sdd/2026-07-30-workflow-language-phase-2-structured-data/task-10-report.md`

## Quality fix round 3/5

This round closes a regression-coverage gap only; production behavior was
already correct and no production file changed. The sealed-definition
`lstat()` preview/download matrix now includes `EINTR`, giving both endpoint
paths dynamic coverage for all seven portable retryable errno values available
on this host:

- `EAGAIN`
- `EINTR`
- `EIO`
- `EMFILE`
- `ENOMEM`
- `ENFILE`
- `ESTALE`

The table is also guarded by a completeness test against the authoritative
`_RETRYABLE_READ_ERRNOS` set. The guard excludes only the set's `-1` sentinel
used when `ESTALE` is unavailable on a platform. This makes a future production
taxonomy addition fail the endpoint matrix until matching cases are present.
Every endpoint case retains the bounded 503 body, no active
`typed_publication_integrity` marker, and same-endpoint recovery assertions.

### Round 3 TDD evidence

The completeness test was introduced while the endpoint table still contained
the prior six values:

```text
scripts/run_tests.sh tests/plugins/workflow/test_desktop_api.py -k retryable_sealed_definition_lstat_matrix_matches_authoritative_set -vv
```

RED result: 1 file, 0 passed, 1 failed. The assertion reported authoritative
errno `4` (`EINTR`) as the sole value missing from the matrix. Adding `EINTR`
to the test table was the only change needed for GREEN; production remained
unchanged.

Focused taxonomy verification:

```text
scripts/run_tests.sh tests/plugins/workflow/test_desktop_api.py -k 'artifact_endpoints_preserve_retryable_publication_unavailability or artifact_endpoints_preserve_retryable_sealed_definition_lstat_errors or retryable_sealed_definition_lstat_matrix_matches_authoritative_set' -vv
```

Fresh result: 1 file, 19 passed, 0 failed. No file retry and no flaky summary
were emitted. This includes four existing transient read cases, fourteen
preview/download stat cases across seven errno values, and the completeness
guard.

### Round 3 verification

Exact Task 10 acceptance:

```text
scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_security_boundaries.py
```

Fresh result: 5 files, 262 passed, 0 failed. No file retry and no flaky
summary were emitted.

Requested ten-file regression matrix:

```text
scripts/run_tests.sh tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_coordinator.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py
```

Fresh result: 10 files, 378 passed, 0 failed, with one platform-specific
skip. The round 2 baseline contained 375 passing tests; this round adds the two
`EINTR` endpoint cases and one completeness guard. No file retry and no flaky
summary were emitted.

### Round 3 files changed

- `tests/plugins/workflow/test_desktop_api.py`
- `.superpowers/sdd/2026-07-30-workflow-language-phase-2-structured-data/task-10-report.md`
