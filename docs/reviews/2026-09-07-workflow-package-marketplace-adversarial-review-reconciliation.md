# Workflow Package Marketplace adversarial review reconciliation

Date: 2026-09-07

Candidate commit: `38c702203abbe213049eea4731750133508c0960`

Candidate tree: `642e9cb29e1e1a7cdc717df3e849be621f4cdf5a`

Inputs:

- `2026-09-07-workflow-package-marketplace-adversarial-code-review-codex.md`
- `2026-09-07-workflow-package-marketplace-adversarial-code-review-claude.md`

## Consolidated verdict

**BLOCK — 0 Critical, 2 Important, 2 Minor findings.**

Both reviewers examined the same immutable candidate and ultimately completed the
full merge gate successfully. Claude found no qualifying defect. Codex found four
defects with narrower counterexamples. Reconciliation is evidence-based rather
than vote-based: the broad successful gate does not disprove an input class that
the gate never exercises.

The candidate remains unmodified. This document accepts no production fix and
does not authorize merge, push, publication, release, base mutation, Workflow
Studio changes, dependency installation, or worktree deletion.

## Accepted findings

### CWM-001 — Important — registered update checks can falsely report “current”

**Decision: accepted.**

For direct installs, `WorkflowMarketplaceService.check_updates()` calls
`_fetch_update()` and validates freshly fetched package bytes. For registered
sources, the method instead calls `self.catalog.inspect()`
(`service.py:1970`). That catalog method reads the existing cached projection;
it performs no Git fetch (`catalog.py:352`). The result is then allowed to become
the definitive `current` status (`service.py:1985-1996`).

Codex reproduced this with a real temporary Git repository: after installing
1.0.0 and advancing the registered source's branch to a valid 2.0.0, fresh
package inspection and update preparation both saw 2.0.0, while update check
reported 1.0.0 as current. Existing tests cover this moving-ref behavior only for
direct installs.

Claude's successful source-cache and lifecycle tests do not cover the decisive
sequence: registered source cached at v1, upstream branch advanced to v2, no
manual source refresh, then Update/Check. The counterexample therefore survives
the PASS report.

Required correction: establish fresh bounded candidate truth for registered
update checks using the same exact source/ref fetch and package validation
boundary used by preparation. A fetch or validation failure must become an
error, never `current`. Preserve cancellation, identity correlation, resource
budgets, and non-mutation of installed state.

Required regression: real registered-source Git test for v1 install followed by
moving branch v2, API/operation projection proof, and Desktop user-visible proof
that Update reaches the v2 review without a manual catalog refresh.

### CWM-002 — Important — the browser release gate accepts the wrong Playwright

**Decision: accepted.**

`local_playwright()` verifies containment, regular files, and the manifest name,
but does not verify the pinned version, the manifest's `bin` mapping, or duplicate
JSON fields (`workflow_gate_build.py:132-153`). The candidate is pinned to
`@playwright/test` 1.62.1 with `playwright: cli.js`, but the helper accepts a
contained package with another version and another declared executable.

Codex demonstrated that a manifest naming `@playwright/test` with version
`0.0.0-review`, a wrong `bin`, and a no-op `cli.js` is accepted and exits zero
when invoked with the production browser arguments. The release helper has no
later proof that the required scenarios actually ran.

Claude attributed Playwright identity to `workflow_gate_clis.py`; that helper
strictly validates TypeScript, Vitest, and tsx, but its `CLI_IDENTITIES` contains
no Playwright entry. It therefore does not close this path. The genuine local
Playwright did run in both reviewers' successful gates, but that proves this
checkout's current dependency, not the gate's fail-closed identity rule.

Required correction: validate a duplicate-free manifest against the exact pinned
Playwright package name/version/bin mapping and retain a contained, regular,
non-symlink executable identity through launch. Keep the offline/no-installer/
no-package-runner policy.

Required regression: missing/wrong version, missing/wrong bin, duplicate keys,
wrong executable, and zero-exit canary cases must fail before browser work and
before any `TESTED_BASE_SHA` receipt.

### CWM-003 — Minor — valid long package metadata is clipped

**Decision: accepted.**

Package display names and publishers are valid up to 256 characters. The detail
heading and publisher cell have no wrapping/bounding policy
(`package-detail.tsx:138-140`, `:203-205`), and the list renders publisher text in
the shared non-wrapping, shrink-zero Badge (`package-list.tsx:100-102`).

Codex rendered actual Electron content with legal 111-character unbroken values
at 320, 768, and 1440 CSS pixels, LTR and RTL, light and dark, plus 200% zoom. At
320 pixels the detail title measured 244 client pixels versus 1001 scroll pixels,
and the publisher cell 136 versus 699. Root/body width remained correct, which is
why the existing page-level overflow assertion did not detect the contained
clipping.

Claude's narrow-layout pass used short fixture metadata and root overflow. The
presence of `min-w-0`, `truncate`, or `break-all` elsewhere does not apply those
rules to the failing cells, so the measured counterexample survives.

Required correction: wrap/bound full detail metadata. For the compact list,
choose an explicit accessible wrap or truncate-plus-full-name disclosure policy;
do not merely hide overflow.

Required regression: rendered legal long/unbroken package and publisher values
in list and detail at supported widths, 100%/200%, and LTR/RTL, checking the
individual cells as well as page dimensions.

### CWM-004 — Minor — Arabic review surfaces expose English diagnostics and raw codes

**Decision: accepted within the Marketplace presentation scope.**

`ReviewDiagnostics` prints `item.code` and `item.message` verbatim
(`review-sections.tsx:58-64`), and package detail does the same for blocker,
advisory, and compatibility messages. Those diagnostics are controlled Hermes
output, not publisher-authored descriptions. Codex's real Arabic/RTL
accessibility tree contained Arabic headings and controls around English
`legacy_language_profile`, `missing_runtime`, `missing_secret`, and
`missing_tool` messages.

Claude verified that the five locale dictionaries contain translated Marketplace
labels, but did not render backend-generated diagnostics in the actual Arabic
locale. That narrower evidence does not disprove the mixed-language review.
This finding also follows the adversarial prompt's explicit localization rule:
no English fallback or raw enum/error code should be the primary user-facing
explanation.

Required correction: render a localized, user-oriented meaning for every
supported diagnostic code while preserving dynamic identifiers such as runtime,
tool, or secret names as literal protocol values. Unknown safe diagnostics need
honest localized generic copy. Raw codes may remain in optional technical detail,
not as the warning a user must interpret. Do not parse English prose to recover
parameters; if the existing wire data cannot preserve the required dynamic facts,
pause for an explicit bounded diagnostic-projection design amendment.

Required regression: backend-generated blocker/advisory/compatibility examples in
Arabic, Japanese, Chinese, and Traditional Chinese, including screen-reader text,
unknown-code fallback, and preservation of literal identifiers.

## Reconciled non-findings and limitations

- No additional credential, path, trust, transaction, admission, operation
  correlation, cache-barrier, Escape/focus, or cross-profile defect was proved.
- The older V1 review decoder's workflow-digest equality rule is unreachable for
  mutation starts because V1 mutations are retired. It is not a release blocker.
- The original design document's four-locale wording is historical drift; the
  implemented Marketplace has five locale catalogs. CWM-004 concerns diagnostic
  content, not missing catalogs.
- Native Windows and Linux Desktop execution remain unverified. They are not
  silently upgraded to passes by either macOS gate.
- The inherited whole-repository portability/formatting/test debt retains its
  recorded attribution. It is not part of these four feature findings.

## Recommended remediation sequence

1. Fix CWM-001 test-first at the backend/API boundary, then obtain a fresh
   independent review.
2. Fix CWM-002 test-first in the release helper, then obtain a different fresh
   independent review.
3. Fix CWM-003 with rendered reflow tests, then obtain a fresh UI/accessibility
   review.
4. Resolve CWM-004 test-first without guessing structured diagnostic parameters;
   stop for a small contract amendment only if the current safe projection is
   insufficient. Obtain a fresh localization/accessibility review.
5. Run a consolidated whole-branch adversarial re-review and the exact full safe
   merge gate at the final candidate before presenting the branch again.

Use one implementation agent at a time, preserve the existing commit history,
record each decision/evidence receipt in the SDD ledger, and accept no fix solely
on its implementer's own tests.
