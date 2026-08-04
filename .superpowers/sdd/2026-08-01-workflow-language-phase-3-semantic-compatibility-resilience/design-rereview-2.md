# Phase 3 Semantic Compatibility and Resilience — Design Rereview 2

**Reviewed HEAD:** `cc57c369db00a3ba59ca18e25a3f118225842e84`

**Scope:** Focused closure check of N-I-1 only.

**Verdict:** Approved for this focused closure check. N-I-1 is closed without contradiction.

**Severity count:** 0 Critical, 0 Important, 0 Minor.

## Closure verification

The revised exact snapshot contract now defines field-specific ranges at lines 270–275:

- `requested_retries`: 0 through 5;
- `requested_total_attempts`: 1 through 6; and
- `effective_total_attempts` and `combined_total_attempts`: 1 through 5.

Those ranges agree with the retry normalization at lines 363–374: authored `max_attempts: 5` means five retries after the initial execution, so the requested total is six and the sealed effective total is capped at five. The snapshot contract explicitly records this boundary as requested total 6, effective total 5, and `capped: true`.

Lines 995–1005 now require an exact snapshot round-trip and changed-config resume test for requested retries 5, requested total 6, effective total 5, and `capped: true`. This directly verifies both serialization and immutable resume behavior at the previously contradictory boundary.

No further remediation is required for N-I-1. No other design areas were reviewed in this focused check.
