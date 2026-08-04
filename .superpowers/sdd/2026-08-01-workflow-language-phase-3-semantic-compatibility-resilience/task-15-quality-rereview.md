# Task 15 Code Quality Rereview

**Baseline:** `975f4c0843560500ade630f8536d40bafe6e24c7`

**Prior reviewed implementation:** `aee26cd914ebfaee777e66cdaf1d310552c7f92f`

**Correction implementation:** `b10ae833f40a2f09d529bc24744e8a3c8432c712`

**Reviewed tree:** `5a2f00e60b2c1e09d9284531330d78c6de7ed5f5`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

This rereview covered ordinary bugs, regressions, maintainability, generated
contract compatibility and size, resolver/API design, the Bash constant move,
documentation accuracy, emitter-test independence, installed-fixture realism,
platform behavior, and test performance across the full Task 15 range. Per the
user's instruction, it performed no threat-model, security-boundary, exploit,
adversarial, or security-focused validation and ran no security-focused tests.

## Prior finding disposition

### I1 — Closed: serialized editor definitions are self-contained and unambiguous

The editor projection now publishes a top-level `field_definitions` map and
each repeated field descriptor carries a `definition_ref`
(`plugins/workflow/language_schema.py:1075-1108,2309-2342,2783-2808`). IDs are
scope-qualified (`node.timeout`, `hook_entry.timeout`,
`retry.max_attempts`, and `approval_reject.max_attempts`) rather than bare YAML
names, and catalog construction rejects duplicate IDs before filtering.

The wire-level test serializes and deserializes the complete contract, resolves
every descriptor using only the resulting JSON, proves all references exist,
and covers the previously colliding names
(`tests/plugins/workflow/test_language_schema.py:72-109`). Semantic parity
tests now resolve through `field_definitions`; they no longer call the
in-process Python resolver to prove the wire contract. The breaking editor
projection shape is explicitly identified by `contract_reader_version: 2` and
`editor_projection_version: 2`, while the repository contains no production
v1 reader that would silently accept the new shape.

### I2 — Closed: descriptions are restored and the envelope has reserved growth capacity

`_field_description()` again returns the actual inventory description rather
than `"v3."` (`plugins/workflow/language_schema.py:1000-1005`). Shared labels,
descriptions, examples, widgets, sections, units, and semantic objects are
deduplicated into `field_definitions`; node descriptors retain only their
field-specific projection and reference. Tests prove representative semantic
fields retain useful prose and every descriptor resolves to nonempty
description/examples.

Fresh default-`json.dumps` measurements at the exact reviewed tree are:

- Archon: **249,509 bytes**, leaving **6,491 bytes** below the 256,000-byte
  ceiling and 2,491 bytes beyond the published 4,000-byte reserve.
- Legacy: **240,669 bytes**, leaving **15,331 bytes**.

All 214 Archon descriptor occurrences resolve to 96 unique field definitions.
The contract publishes its maximum, reserved growth, and section budgets, and
the tests enforce the 252,000-byte growth target plus section caps
(`tests/plugins/workflow/test_language_schema.py:50-69,292-314`). The actual
compact `hermes workflow schema --json` Archon output also parsed successfully
and contained reader/projection version 2 with 96 definitions.

### I3 — Closed: native-Windows Bash behavior is represented without skipping completeness

The portable boundary fixture now keeps 32,768-byte inline behavior, expects
the stable `bash_spill_integrity` failure for 32,769 bytes on native Windows,
and executes real spill-success workflows only on descriptor-capable POSIX
hosts (`tests/plugins/workflow/test_portable_compatibility_e2e.py:129-203`).
The producer uses the active Python executable rather than hardcoding
`/usr/bin/env python3`.

The durable-code helper no longer skips on Windows. It takes the native
fail-closed large-value branch to emit `bash_spill_integrity`, restores the
platform sentinel in `finally`, and has a dedicated no-skip assertion
(`tests/plugins/workflow/test_phase3_code_catalog.py:123-174,355-365`). The
34-code aggregate therefore retains exact executable-emitter equality on both
platform paths.

### M1 — Closed: every added scheduler follows the explicit shutdown lifecycle

The portable helper owns one scheduler across retry-time advancement and closes
it in `finally` with a two-second deadline
(`tests/plugins/workflow/test_portable_compatibility_e2e.py:22-53`). The
same-run, cross-run, and pending-registry catalog helpers retain and close all
four schedulers, with the aggregate asserting the shutdown calls
(`tests/plugins/workflow/test_phase3_code_catalog.py:218-331,334-353`). The
installed-wheel subprocess likewise reuses one scheduler and shuts it down in
`finally`, reporting the completed shutdown in its result
(`tests/plugins/workflow/test_installed_distribution_e2e.py:176-239`).

## Full-range quality conclusions

- The Bash limit constants remain mechanically shared between the language
  inventory and renderer without an import cycle or runtime name change.
- The exact 34-code completeness authority still begins empty and collects
  values from invoked normalization, resume, admission, resolver, condition,
  wait-state, Bash, and session/recovery behavior rather than seeding itself
  from the catalog.
- The installed-distribution flow still builds and extracts the wheel, replaces
  `PYTHONPATH` with the extracted site, proves the imported workflow plugin is
  under that site, uses a temporary clean `HERMES_HOME`, and executes the
  representative retry/timeout/condition flow.
- Website and workflow-builder guidance remains accurate for Archon v3 versus
  legacy units/defaults, direct references, typed conditions, retry accounting,
  bounded Bash behavior, persistent-session recovery, extension options, and
  later-phase loop/include expansion.
- No new environment variable, checked-in generated schema, core tool surface,
  or unrelated production dependency was introduced.

## Fresh functional evidence

- `test_language_schema.py`, `test_phase3_code_catalog.py`, and
  `test_portable_compatibility_e2e.py`, retries disabled: **629 passed / 0
  failed**.
- Installed wheel/clean-home integration, retries disabled: **1 passed / 0
  failed**.
- Full Task 15 eight-file matrix, retries disabled: **1,764 passed / 0 failed**.
- Scoped Ruff passed for the five correction source/test files.
- `git diff --check` passed for
  `975f4c0843560500ade630f8536d40bafe6e24c7..b10ae833f40a2f09d529bc24744e8a3c8432c712`.
- The worktree was clean after verification.

Task 15 is code-quality approved at the exact correction implementation and
tree above.
