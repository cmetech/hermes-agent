# Workflow Language Phase 5 Design and Plan Review

**Date:** 2026-08-06

**Reviewed artifacts:**

- `docs/superpowers/specs/2026-08-06-workflow-language-phase-5-provider-portability-design.md`
- `docs/superpowers/plans/2026-08-06-workflow-language-phase-5-provider-portability.md`

**Review method:** Independent read-only high-reasoning Sol review against the
approved umbrella design, Phase 4 design/plan/validation/remediation, root and
Desktop development contracts, workflow documentation, customization ledger,
and relevant live provider/runtime code. The reviewer made no edits and ran no
implementation tests.

## Final verdict

**PASS — 0 Critical and 0 Important findings remain.**

The reviewed design and plan are implementation-ready only after explicit user
approval and Task 0's clean no-retry baseline. Normalizer v5 remains dormant;
this review authorizes no implementation, activation, merge, push, tag, release,
or brand mutation.

## Findings and dispositions

| Severity | Finding | Disposition |
|---|---|---|
| Critical | Cache identity did not bind the actual model-visible prefix. | Resolved with a two-stage identity: sealed intended authority plus runtime `model_visible_prefix_digest` over exact rendered system bytes and complete final tool schemas after MCP discovery. Task 8 owns real assembly paths and schema/prompt drift tests. |
| Important | Provider declarations lacked non-forgeable provenance. | Resolved with loader-owned mandatory complete declaration/encoder code-closure digests. Unhashable closures are limited to generic Hermes adapters; user plugins cannot assert billing/sandbox guarantees. Same-version and imported-helper mutation tests are planned. |
| Important | OpenRouter/BYOK billing authority was overbroad. | Resolved. BYOK blocks. Shared-credit OpenRouter remains unsupported at activation unless an authoritative contractual guarantee plus adapter tests, or authoritative per-attempt reconciliation, covers every billable terminal outcome. Fixtures alone are insufficient. |
| Important | Local MCP executable closure was underspecified. | Resolved by limiting v5 execution to a sealed package-contained Python entry point under the exact Hermes interpreter, `-I -S`, and a lifetime import-root guard. Standalone/shebang/dynamically dependent executables and ambient dependency forms block. |
| Important | Remote MCP mutability lacked trust/cache semantics. | Resolved. Remote shapes normalize but remain unsupported in Phase 5 pending a version-pinned adapter design. |
| Important | Alias configuration scopes were invented rather than tied to real loaders. | Resolved. Phase 5 uses only active-profile and managed `config.yaml` scopes with existing managed-leaf precedence; no project/package alias authority is invented. |
| Important | Raw provider/model identifiers were unsafe for public projection. | Resolved with a separate whole-value display-identifier redactor and adversarial URI/path/credential/control/high-entropy cases. |
| Important | Desktop's authoritative run-review surface was missing. | Resolved by adding the review dialog, runtime guards, localized copy, and a no-POST fail-closed test to Task 14. |
| Important | Installed-distribution coverage was only a final command. | Resolved by modifying the wheel test in Tasks 2, 3, 6, and 15 for packaged declarations, config resolution, explicit v5, and v1-v4 recovery without repository imports. |
| Important | Generic changes were ledgered retroactively. | Resolved. Every generic task updates and tests its symbol-level upstream customization entry in the same atomic commit; Task 15 only reconciles. |
| Important | Baseline and brand/nonpublication gates were not executable against the exact candidate. | Resolved with blocking Task 0, dynamic descriptor discovery, activation committed before post-activation rehearsal, and before/after snapshots of configured source remotes, brand release repositories, releases, refs, tags, worktrees, branch, and status. |
| Minor | Scoped hook cleanup could clobber foreign registrations. | Resolved with token-owned synchronized removal and concurrent foreign-registration survival tests. |
| Minor | Existing test files staged by a task were labeled read-only. | Resolved with an explicit taxonomy and `Modify:` labels for staged existing tests. |

## Review closure

The reviewer performed two closure passes after the initial report. The first
confirmed the cache fix and narrowed four remaining Important findings. The
final pass confirmed those four were closed and returned:

> PASS — 0 Critical, 0 Important findings remain.

Residual implementation risks are intentionally converted into gates rather
than waived: a red Task 0 baseline stops work; unprovable provider code or cost
authority blocks capability support; unsupported MCP forms block; and any
post-rehearsal external-state difference blocks activation handoff.
