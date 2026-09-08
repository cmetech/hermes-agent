# Declarative reference scanner: independent plan review

Reviewed the complete replacement specification and plan against the unchanged source at `74fed08f91014ca7fc80ee9ea4427568ec95b04f` on `feat/workflow-reference-scanner-contract`. This is the single independent plan review, not another architecture review. No production or test files were changed or tests executed for this read-only review.

**Verdict: proceed after the one bounded correction below.** No architectural blocker was found. The seven historical authoring pairs, legacy corpus guarantee, v6-only reader/limit changes, 38 inventory-derived surfaces and container-major traversal, literal expectations, Bash/scalar coverage, Unicode profiles, and direct-wheel/sdist installation proof are represented coherently in the plan. Python 3.13 availability remains an execution prerequisite already handled by the plan, not a design finding.

## P2 — Authenticated-resource cases have no executable input/adapter path

**Locations:** `docs/superpowers/plans/2026-09-04-declarative-reference-scanner.md:189–192,224`; associated requirement at `docs/superpowers/specs/2026-09-04-declarative-reference-scanner-design.md:87`.

Task 4 assigns authenticated resource-policy coverage to the existing YAML workflow cases, but those cases cannot supply authenticated bodies. `_case` (`plugins/workflow/language_conformance.py:63`) accepts YAML, diagnostics and projection/provenance only. `_authority_outcome` (`tests/plugins/workflow/test_language_conformance.py:552`) calls `_compile_workflow_source_document` (`plugins/workflow/schema.py:2988`), whose inputs contain no authenticated resources. A named-script workflow can therefore compile successfully without ever checking the script reference that the new case claims to characterize.

The actual body checks are `validate_authenticated_resource_references` (`plugins/workflow/schema.py:2296`) and its command wrapper. Invalid script bytes additionally travel through `compute_package_digest` (`plugins/workflow/trust.py:461`), which decodes named scripts with `surrogateescape` at line 516. Encoding such decoded surrogates directly in published JSON would violate the specification's Unicode-scalar input boundary. Existing evidence is `test_named_script_scan_never_loses_a_valid_reference_around_other_bytes` (`tests/plugins/workflow/test_strict_output_references.py:873`).

**Concrete correction:** Add fixed authenticated-caller variants to the new literal `scanner_cases` and observation dispatch. Specify literal definition/companion YAML, normalizer version, command/named-script body maps, and expected ordered diagnostics for direct `validate_authenticated_resource_references` calls after package compilation. For the invalid-byte case, specify a fixed resource byte encoding such as hexadecimal, materialize those bytes in a real temporary package, and call `compute_package_digest`; do not decode them in the adapter. Permit that adapter to receive `tmp_path`, and explicitly serialize the existing `WorkflowValidationError.issues` and meaningful cause. Keep the old workflow-case shape and historical bytes intact. Map these IDs in the manifest and remove the implication that ordinary YAML-only workflow cases prove authenticated-body policy.

This correction exercises existing APIs and supplies missing literal test inputs. It requires no runtime changes or new execution architecture. Once incorporated in the existing spec/plan, implementation may proceed without another plan review.

## Disposition

The controller incorporated this correction into the specification's literal-data section and plan Task 3: fixed authenticated API variants, literal body maps/hex bytes, real temporary packages, runtime-owned decoding, ordered issue/cause observations, and `tmp_path` in the scanner adapter signature. Task 4 now assigns authenticated coverage to those scanner cases and preserves the YAML workflow-case shape. No second plan review was requested.
