# Workflow Upstream Overlap Scope Design

**Date:** 2026-07-29
**Status:** Approved
**Scope:** Upstream customization overlap classification and release rehearsal

## Problem

The Workflow feature is a downstream customization. Upstream merge validation
needs to answer a bounded question: which files changed by upstream are also
files this fork declares as customized, and did upstream change an owned part
of those files?

The current checker asks a broader question. For every JavaScript, TypeScript,
and Markdown file changed anywhere in upstream, it searches for every owned
symbol from every customization entry. It uses that repository-wide search to
infer `possible_upstream_equivalent` changes outside the files the fork touched.

At the Task 10 rehearsal boundary, the ledger contained 98 entries covering 233
unique files. The upstream range changed 2,527 files, including 1,045 parser
files. Only 49 upstream-changed files intersected the ledger, and only 17 of
those needed the parser. The current algorithm nevertheless created 2,090
parser requests over 1,045 files using a global union of 360 symbols. The
rehearsal failed closed at the 16 MiB parser output limit before it could publish
overlap or merge evidence.

Increasing the output limit would mask the scope error. The checker should not
parse unrelated upstream files in the first place.

## Goals

- Restrict overlap analysis to the intersection of upstream-changed paths and
  ledger-declared customization paths.
- Preserve exact symbol-level classification inside intersecting files.
- Preserve explicit decisions for material overlap and file-level policies.
- Treat an upstream addition at a custom-only path as a normal intersection.
- Keep post-merge invariant, brand, ancestry, and evidence validation intact.
- Make the real release rehearsal complete without scanning unrelated upstream
  source or increasing parser safety limits.

## Non-goals

- Inferring that upstream independently implemented an equivalent Workflow
  feature in an unrelated file.
- Searching the upstream repository for coincidentally matching symbol names.
- Raising parser input/output/time limits.
- Weakening committed-byte authority, parser fail-closed behavior, atomic report
  publication, merge decisions, invariant execution, or cleanup requirements.
- Automatically removing a downstream customization because upstream appears
  semantically similar.

## Selected approach

Use strict path intersection followed by exact owned-symbol analysis.

Given resolved endpoints `left` and `right`:

1. Compute the paths changed by `left..right`.
2. Compute the union of every ledger entry's declared `files`.
3. Set `candidate_paths = changed_paths & ledger_owned_paths`.
4. Build parser requests only for parser-backed candidate paths.
5. For each candidate path, request only symbols from entries that declare that
   path. Do not use the global union of all ledger symbols.
6. Classify each entry from its own intersecting paths:
   - `owned_symbol` when a changed byte range overlaps one of that entry's owned
     symbols;
   - `same_file` when the file changed without an owned-symbol overlap;
   - `none` when none of the entry's files changed.

Do not scan `changed_paths - ledger_owned_paths`. The checker will no longer
produce `possible_upstream_equivalent`.

## Decision behavior

Decision requirements remain conservative within the actual merge surface:

- `owned_symbol` requires an explicit `preserve`, `adapt`, or
  `remove-as-upstream-equivalent` decision.
- `same_file` requires a decision only for an entry whose `overlap_policy` is
  `any_owned_file`.
- `same_file` with `owned_symbol` policy and `none` require no decision.

`remove-as-upstream-equivalent` remains a valid human decision. A maintainer may
determine that an upstream change to an intersecting file replaces the local
seam. What is removed is the checker's speculative inference that matching
names in unrelated files prove equivalence.

## Components and data flow

### Candidate selection

`classify_upstream_overlaps` remains the public plural classifier and resolves
the Git endpoints once. It derives the changed paths once, derives the ledger
path inventory once, and passes only the intersection to parser preparation and
character-diff calculation.

### Parser request construction

`_collect_overlap_parser_requests` accepts only candidate paths. It builds a
per-path symbol map from entries that own each path, then creates at most two
requests per parser-backed candidate path: one for each revision endpoint.
Absent blobs retain the existing exact absent-path representation, so an
upstream-created custom path is still checked.

### Entry classification

Each entry intersects its declared files with the already bounded changed-path
set. `_owned_symbol_hits` is called only for those intersecting paths. Entries
with no intersecting paths return `none` immediately; they never invoke a
cross-repository symbol search.

### Evidence schema and documentation

New evidence supports only `none`, `same_file`, and `owned_symbol` overlap
classes. Remove `possible_upstream_equivalent` from the current schema branches
and executable tests. Retain `remove-as-upstream-equivalent` as an allowed
decision value.

Update the customization README and the machine-readable checker invariant to
state that overlap detection is path-intersection bounded. Existing
`removal_condition` prose remains useful maintainer guidance; it does not grant
the checker authority to search unrelated files.

Historical implementation plans remain historical records. This approved
design supersedes their repository-wide possible-equivalence requirement.

## Error handling and safety

- Parser, Git, schema, invariant, and cleanup failures remain terminal.
- No fallback lexical scanner is introduced.
- No failed invariant is retried, weakened, skipped, or deleted.
- A conflict in an intersecting owned file still requires explicit resolution;
  blanket whole-file `ours` or `theirs` resolution remains forbidden.
- Real `main`, `base`, `otto`, and `loop24` refs remain immutable during the
  rehearsal.
- The existing parser size, batch, output, and timeout limits remain unchanged.
  Hitting one after this scope correction remains honest terminal evidence.

## Testing strategy

Implementation follows test-driven development.

### RED controls

1. A changed unrelated parser file containing an owned symbol must classify the
   entry as `none`, and the parser batch must not receive that file.
2. A scale-shaped fixture with many unrelated parser files and one owned
   intersection must create exactly two parser requests for the owned file.
3. Two entries owning different candidate files must receive only their own
   per-path symbol sets; no global symbol union may leak between requests.
4. An upstream addition at a declared custom path must still classify as
   `owned_symbol` or `same_file` according to its contents.
5. The evidence schema must reject newly generated
   `possible_upstream_equivalent` evidence.

Each control must fail for the expected behavioral reason before production
code changes.

### GREEN and regression coverage

- Run the focused overlap and evidence-schema tests.
- Run the complete customization checker and upstream-rehearsal test files.
- Run the exact no-retry three-file Task 9 acceptance gate.
- Run strict committed-ledger validation.
- Run the controlled Task 10 rehearsal once from the new committed candidate.
- Independently validate the resulting evidence, exact single-attempt invariant
  rows, brand containment, generic runtime equality, ancestry, ref immutability,
  and cleanup.

## Acceptance criteria

- Unrelated upstream paths never enter parser requests or symbol-hit analysis.
- Parser symbols are scoped per candidate path and owning ledger entries.
- The checker produces only `none`, `same_file`, or `owned_symbol`.
- Existing decision behavior within intersecting files remains unchanged.
- Focused and full no-retry tests pass without raising safety limits.
- The controlled rehearsal publishes schema-valid merge evidence.
- Every executed invariant passes once without flake or truncation.
- Both brand rehearsals contain the tested base and match its generic runtime.
- Protected refs and non-task worktrees remain unchanged, with no owned process
  or temporary-worktree residue.
