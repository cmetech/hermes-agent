# Workflow Studio loop-group contract adversarial review remediation

Date: 2026-09-04

## Outcome

All seven candidate-caused findings in
`2026-09-04-workflow-studio-loop-group-contract-adversarial-review-reconciliation.md`
were remediated with behavior-first tests. A fresh task reviewer initially
rejected the first six-finding implementation for two incomplete
interpolation-authority details, rejected the first correction for a broader
container-order regression, and passed the second correction with no Blocker,
Important, or Minor findings. The authenticated Fable rerun then found the
installed-resource defect LGR-007; independent reproduction confirmed it and
a separate TDD/review cycle remediated it.

No duplicate-dependency admission change was made. The pre-existing raw string retry failure was not changed. No native Windows verification was performed or claimed.

## Finding disposition

| Finding | Resolution |
|---|---|
| LGR-001 | Runtime enumeration and Phase 6 publication now share one declarative interpolation-surface authority. It publishes exact wildcard paths, authored-reference versus literal roles, and a versioned inline-script code-point discriminator. Ordered traversal preserves starting-HEAD behavior for all eight executable node types, multiple agents, hook events, and hook entries. |
| LGR-002 | The contract and conformance corpus publish body-over-outer collision precedence and first-iteration `$LOOP_PREV` whole-output/structured-path behavior. |
| LGR-003 | Scoped companion semantic codes require exactly one slash and two reference-safe segments; malformed and assignment forms retain their native diagnostic behavior. |
| LGR-004 | `kind` is Phase-6-only. The legacy canonical contract is compared byte-for-byte with a compressed full golden produced from merge base `c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d`. |
| LGR-005 | Raw structural work arithmetic again matches the merge base, restoring `archon_retry_invalid` issue paths and CLI envelopes for boolean, negative, and fractional retry values while retaining normalized work bounds. |
| LGR-006 | The contract publishes structured tri-state lookup/terminal metadata and the corpus includes the two ambiguous accepted schemas, exercised by an independent public-contract interpreter and the Hermes compiler. |
| LGR-007 | The conformance module now resolves authoring resources from the source tree when present and otherwise from `sys.prefix`, matching the wheel data-file layout. A real ordinary-venv installed-console regression removes `PYTHONPATH`, proves module/resource origins, and compares deterministic Archon corpus bytes with the source authority. |
| FCR-001 | The shared work selector treats a retry mapping as explicit only when `retry.max_attempts` is present. Partial command/prompt mappings use the sealed AI default of two retries, and the contract publishes the exact presence predicate. Work admission, capacity admission, and executable semantics now agree. |

## Review corrections

The first review found that the contract did not expose the inline-script predicate and that loop command enumeration order had changed. The correction introduced `script-inline-v1`, proved it equivalent to the prior regular expression across all Unicode code points, and restored loop order.

The second review found that multiple agents and hooks were still traversed leaf-first rather than container-first. The correction represented wildcard containers and their ordered leaves in the shared authority, restored insertion/index order, and added ordered differential coverage for every executable node type with Phase 4 disabled and enabled.

The final task review passed LGR-001 through LGR-006 with no remaining finding.

The LGR-007 task reviewer rejected the first regression because it still used
uv's special `--prefix` mode. The corrected test creates a normal venv,
populates dependencies offline, then uses that venv's standard pip to install
the Hermes wheel through the wheel data scheme. It proves `sys.prefix` and
module/resource origins, and reports each subprocess failure independently.
The scoped re-review passed both findings with no new Critical or Important
breakage.

The complete-branch reviewer then found FCR-001. Its focused probe showed
1,400 admitted versus 4,200 executable attempts for fourteen partial-retry AI
children over 100 iterations. A separate TDD fix added command/prompt partial
mapping coverage, 3,900/4,200 boundary coverage, and a public
`retry.max_attempts` presence predicate. The final scoped re-review marked the
finding addressed with no new Critical or Important issue.

## Verification evidence

The final implementer-owned verification reported:

- 24 focused interpolation/publication tests passed.
- 1,232 tests passed across the 11 affected workflow language, schema, compatibility, corpus, and CLI files.
- The installed-distribution corpus integration passed.
- Ruff passed for all changed Python files.
- `git diff --check` passed.
- The final LGR-007 controller-owned verification passed 5 installed-
  distribution integration tests, 16 language-conformance tests, and 5
  schema-corpus CLI tests; focused Ruff and `git diff --check` passed.
- The final FCR-001 controller-owned four-file battery passed 852 tests; its
  final scoped reviewer independently passed 20 focused behavior tests.
- The Archon contract measured 283,440 bytes against its unchanged 284,000-byte usable ceiling.
- The corpus measured 48 cases and 126,533 compact JSON bytes against its 64-case and 160,000-byte limits.
- The legacy golden decoded to 226,976 bytes with SHA-256 `f4de08444f110fb8c4e2b36d9246df4194b8a70dde8bae495a381d757fdf6e07`.

Controller-owned pre-commit verification repeated the 11-file battery with 1,232 passing tests, the installed-distribution integration with 1 passing test, and repository-wide Ruff with no findings. `git diff --check` also passed. The Windows footgun audit remains red on 33 known branch-range findings documented by the immutable review prompt; it is not counted as a pass, and no native Windows execution occurred.

The remediation commit still requires complete-branch review and handoff verification. This report does not authorize merging, pushing, releasing, or beginning Workflow Studio Phase B.
