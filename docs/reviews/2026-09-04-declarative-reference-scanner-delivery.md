# Declarative reference-scanner Hermes delivery

Status: implementation and required verification complete; final branch review approved the feature commit with no open findings. The complete repository suite remains red for reproduced baseline/environment failures. Merging and Workflow Studio work require separate user approval.

## What changed

Hermes now publishes a description of its existing reference rules and a collection of literal compatibility examples. Workflow Studio can later implement the same rules in TypeScript and test its answers against those examples. Hermes continues using its existing Python scanner; this amendment introduces no VM, interpreter, new workflow syntax, or runtime scanner replacement.

Amended Archon normalizer 6 requires contract reader 3 and publishes the inventory-derived 18 root fields, 18 loop-body fields, and two group controls. Its conformance corpus uses format 2, binds the exact contract digest, and carries a separate corpus digest. Unsupported reader, metadata, and Unicode capabilities must prevent Studio activation. Studio consumption remains separately approved work.

Work is isolated to `feat/workflow-reference-scanner-contract` in the existing Hermes worktree, starting from `74fed08f91014ca7fc80ee9ea4427568ec95b04f`. No merge, push, other-worktree change, literal-main change, or Workflow Studio edit is authorized by this delivery.

## Artifact evidence

The following values were recalculated from the final working tree and verified by publication and real installed-package checks.

| Artifact | Canonical bytes | Digest |
| --- | ---: | --- |
| Amended Archon authoring contract | 315,756 | embedded `sha256:02830acc6e3c797cec14e7debc3b2d15f91eca514bb9d2ffe31bd3b74e2549ed` |
| Archon corpus, format 2 | 200,496 | embedded `sha256:df14cc2dc8adec213e98a126360943b9b831426ef43304632a7582288ecd6384` |
| Legacy corpus, format 1 | 7,265 | full SHA-256 `c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b` |
| Literal scanner/substitution/path resource | 61,895 | canonical SHA-256 `8e9d4588b3642d48fd8cd51bbdfcbc96b216d211b1254b001f51f5f287c3f949` |

Archon corpus counts are 54 workflow, 132 scanner, 23 substitution, and 29 structured-path cases. There are 184 literal API cases, including 14 authenticated-resource cases. Each supported Python/Unicode profile has 182 applicable cases. Only the Kawi alphabetic pair demonstrates different API outcomes; the digit pair preserves identical observations across profiles.

Contract headroom is 8,244 bytes after the required 4,000-byte reserve. The scanner section is 31,879 bytes with 121 bytes of headroom. Corpus headroom is 183,504 canonical bytes and 512,269 pretty-JSON bytes (255,731 emitted JSON bytes, excluding the print newline). These are publication bounds, not new runtime input limits.

Seven immutable authoring outputs remain protected by full canonical byte fixtures: legacy normalizers 1–2 and Archon normalizers 1–5. Old Archon v6 is an intentional-delta characterization; its schemas and executable-node semantics remain unchanged. The legacy corpus retains format 1, 11 cases, and its exact historical bytes.

## Requirements-to-tests disposition

This is the original requirements matrix with delivery evidence, not a new architecture expansion. Hermes publication obligations are proved by the evidence below; Studio acceptance remains deferred.

| ID | Hermes evidence | Studio obligation |
| --- | --- | --- |
| H1 | Seven immutable byte fixtures, exact legacy corpus, bounded v6 metadata delta | Historical activation and pinned artifacts |
| S1 | Required metadata, exact grammar/scalars, reader and trusted-bound checks | Reject missing or uninterpretable required metadata |
| S2 | Literal malformed suffix/candidate/presence cases | Execute identical corpus |
| S3 | Reviewed Bash state families, lexical-before-grammar and lazy/eager observations | Corpus plus adversarial Python/TypeScript differential tests |
| S4 | Literal constraint/ref/union/numeric static proofs and separate runtime resolution | Preserve constraints in TypeScript proof |
| S5 | Inventory-derived 18+18+2 parity and container-major traversal | Complete applicability-aware traversal |
| S6 | Root systemPrompt/agents/hooks workflow cases and exact ordered diagnostics | Match root validation |
| S7 | Current/outer/previous, shadowing, controls and competing-failure cases | Match portable codes, paths and precedence |
| S8 | Multigroup and multisurface cases are published | Deferred: prove contract/document indexing once |
| S9 | Existing Hermes performance suite passed in the full workflow run; dense scanner cases published | Deferred: prove 250 nodes/500 edges and pointer-frame constraints |
| S10 | Three Python/Unicode profiles, scalar offsets and direct-surrogate characterization | Deferred: code-point internals and UTF-16 editor boundary |
| H2 | Public scanner/scalar APIs, first/drain behavior, overlap, masking and causes | Equivalent TypeScript callers |
| H3 | Static reviewed literals, manifest, deterministic digests and runtime-call tripwires | Pin both digests and run differential tests |
| H4 | Direct wheel/sdist/sdist-built wheel membership, real offline installs and guarded console byte parity | Bundle offline artifacts |
| H5 | Runtime source continuity and include-v4/scheduler-v3 tests; full workflow passed; all remaining repository failures reproduced at baseline | Preserve runtime advisories |

## Verification

All test commands used `scripts/run_tests.sh`.

| Check | Result |
| --- | --- |
| Focused publication/contract/baseline suite | 506 passed |
| Explicit supported-Python matrix | 1 passed, covering CPython 3.11.16/Unicode 14.0.0, 3.12.14/Unicode 15.0.0, and 3.13.15/Unicode 15.1.0 |
| Explicit installed integration selection | 2 passed |
| Full workflow suite | 136 files; 6,305 passed, 0 failed, 44 host-marker skips on Darwin; exit 0 |
| Affected workflow release-gate tests | 51 passed; shell syntax and whitespace checks passed |
| Complete repository suite | 3,750 files; 51,850 passed, 103 failed, 657 skipped; exit 1, plus one file-descriptor exhaustion file |

No flaky files were reported. All 103 exact failing node IDs reproduce at starting commit `74fed08f` with matching causes. The separate API-server file-descriptor exhaustion also reproduces at that commit. Independent temporary Git metadata removed archive-context ambiguity for the update tests. The complete repository suite is **not green**; no current-only failing node remains in the comparison.

The initial workflow run had 6,289 passing tests and two collection errors because the isolated environment lacked declared `aiohttp==3.14.3`. That pin and six other already-declared optional prerequisites were provisioned in this worktree's verified local `.venv`. No tracked dependency declarations or other worktree environment changed. The complete [verification record](2026-09-04-declarative-reference-scanner-verification.md) preserves commands, exact baseline failure IDs and causes, interpreter identities, artifact hashes, and prerequisite details.

The runtime scanner/rendering, resource, condition and schema files have no diff. An AST audit confirms existing scanner/candidate/parser definitions in `language_schema.py` are unchanged. Its changes are confined to inert inventory annotations, metadata/projection, and publication controls.

## Review decisions and limits

One architecture review and one plan review preceded implementation. Focused reviews corrected concrete metadata and CLI mismatches without changing scanner behavior. Scalar witnesses were strengthened with distinct values and evidence citations corrected. Whole-workflow diagnostics remain in the existing workflow case section; authenticated body maps and bytes use fixed existing-API adapters.

A packaging plan prediction was corrected by actual artifact inspection: existing package rules already shipped the scanner JSON in wheel and sdist. The absent packaged README supplied the intended publication failure; no missing-resource failure was manufactured. The explicit JSON glob documents packaging intent. Both installed wheel paths proved module/resource origins outside the checkout, exact source-byte parity, offline/no-index installation, and sixteen successful authoring commands under a demonstrated socket guard with no PYTHONPATH.

A finite corpus cannot establish equivalence for every possible input. Studio implementation, differential testing, indexing and canvas performance remain deferred. The scanner metadata section fits its approved bound with little headroom; future additions must respect that bound.

## Handoff

The delivery targets the feature branch only. See the [complete-branch review](2026-09-04-declarative-reference-scanner-branch-review.md) and [focused task reviews](2026-09-04-declarative-reference-scanner-task-reviews.md). Explicit user approval is required before merging into Hermes `base` or beginning Workflow Studio changes. The abandoned VM records remain preserved and excluded from this feature.

## Implementation rulings

These decisions preserve the execution ledger in chronological order, including their rework costs.

- Keep whole-workflow diagnostic cases in existing corpus cases section under Task4; Task3 adds no duplicate compile_workflow_source case API/section. Task4 explicitly owns rootPhase4/scoped workflow integration, while Task3 owns scanner/substitution/path/authenticated variants. Cost if wrong: extend existing workflow adapter/fixtures to close any demonstrated coverage gap, not a new runtime.

- Task5 missing-JSON RED prediction is replaced by actual archive characterization plus absent packaged README RED — baseline direct wheel and sdist already contain reference_scanner_v1.json under existing MANIFEST.in graft, while README is absent. Retain explicit package-data glob and full offline install proof. Cost if wrong: inspect artifact membership/parity and fix a demonstrated packaging defect; do not manufacture test failure.

- Permit declared aiohttp==3.14.3 provisioning into this feature worktree .venv after explicit prefix verification — root ls confirms real isolated directory, not linked/shared worktree environment; preserves repository dependency declarations and resolves concrete prerequisite. Supersedes earlier controller no-existing-venv blanket restriction only for needed test prerequisite. Cost if wrong: restore this local test environment; no other worktree/package policy changes.

- Overlap the single final branch code review with the long full repository run — all implementation tasks and fullworkflow verification are complete and code is frozen; reviewer must receive repository evidence before final readiness verdict. This avoids idle serial waiting without repeating review. Cost if repository regression requires a fix: provide that scoped fix plus affected evidence within the same review/final correction round.

- Fresh mechanical-fix agent spawn failed with agent-thread limit; reuse idle /root/review_reference_metadata for this new bounded three-line gate-list correction, with exact fresh brief and independent final-reviewer check. This preserves subagent implementation without stalling or spawning extra architecture review. Cost if wrong: revert/review the three-line script diff; no runtime scope expansion.
