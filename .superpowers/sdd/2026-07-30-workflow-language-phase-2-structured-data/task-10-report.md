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
