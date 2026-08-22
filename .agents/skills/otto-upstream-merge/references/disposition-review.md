# Upstream behavior disposition review

This review decides how behavior is reconciled. It never decides whether an
upstream commit remains in history: full upstream ancestry is preserved.

## Evidence first

For each meaningful overlap:

1. Read the upstream commit and issue intent, not only the conflict hunk.
2. Read the downstream introducing commit with `git log -p -S '<symbol>'`.
3. Trace callers and the real runtime path.
4. Run the downstream motivating test against upstream behavior when feasible.
5. Run upstream and downstream invariant tests against the proposed result.

## Classify

Importance:

- `critical` — security, credential exposure, data loss, or release integrity.
- `high` — correctness, compatibility, availability, or core workflow failure.
- `medium` — performance, operability, resilience, or bounded UX degradation.
- `low` — cosmetic UX, documentation, or behavior-neutral refactoring.

Applicability: `applicable`, `conditional`, or `not-applicable`.

Relationship: `independent`, `convergent`, `contradictory`, `downstream-superset`,
or `unclear`.

## Choose one disposition

- `take-upstream` — no fork contract is displaced.
- `union-adapt` — keep upstream's structure and add only the proven fork delta.
- `remove-downstream-as-equivalent` — upstream passes the fork's motivating
  contract; remove redundant downstream code and tests that no longer own a
  distinct invariant.
- `retain-downstream-behavior` — the upstream behavior is not applicable,
  violates a documented fork invariant, or is strictly covered by a verified
  downstream superset. Preserve unrelated upstream edits. Record machine
  decision `adapt`.
- `blocked-unclear` — evidence cannot establish applicability or relationship;
  do not perform the real merge.

For ledger decision-required rows, map the human disposition to the existing
machine evidence as follows:

- `take-upstream` → `preserve` when the ledger-owned fork invariant remains
  unchanged and the upstream edit is independent; otherwise `n/a` for an
  unledgered surface.
- `union-adapt` → `adapt`.
- `remove-downstream-as-equivalent` → `remove-as-upstream-equivalent`.
- `retain-downstream-behavior` → `adapt`.
- `blocked-unclear` → no machine decision; the merge remains blocked.

Machine `preserve` describes preservation of the ledger invariant while the
upstream commit remains in history; it never means excluding upstream ancestry.

Conflict difficulty, implementation size, feature disinterest, and deadlines
never justify `retain-downstream-behavior`. If an upstream subsystem is unused,
take it intact unless its behavior crosses an active fork boundary.

## Durable report row

Record one row per meaningful overlap:

| Field | Required content |
|---|---|
| Upstream | commit/issue and problem fixed |
| Surface | files, symbols, or invariant ids |
| Importance | critical/high/medium/low with one-line impact |
| Applicability | applicable/conditional/not-applicable with evidence |
| Relationship | independent/convergent/contradictory/downstream-superset/unclear |
| Disposition | one of the five outcomes above |
| Machine decision | preserve/adapt/remove-as-upstream-equivalent, or n/a |
| Verification | motivating and post-reconciliation tests |
| Residual risk | remaining uncertainty and blast radius |
| Revisit | concrete condition, owner, or upstream reference |

Update a customization manifest's `merge_guidance` or `removal_condition` only
when the result changes ongoing ownership. The per-release report owns
historical decisions; do not create a second decision database.
