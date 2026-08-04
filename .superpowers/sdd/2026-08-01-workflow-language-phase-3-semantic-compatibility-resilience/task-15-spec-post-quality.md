# Task 15 Post-Quality Specification Confirmation

**Implementation:** `b10ae833f40a2f09d529bc24744e8a3c8432c712`

**Tree:** `5a2f00e60b2c1e09d9284531330d78c6de7ed5f5`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

This confirmation is limited to ordinary functional, regression, and
compatibility validation. It performed no threat-model analysis, security
boundary testing, exploit/adversarial validation, or security-focused tests.

## Task 15 requirement confirmation

### Serialized editor contract is self-contained and profile-correct

The editor projection no longer depends on an in-process Python resolver.
Every serialized `node_kinds[].fields[].definition_ref` resolves inside the
same envelope's `field_definitions` object. Definition IDs include inventory
scope (`node.timeout`, `retry.max_attempts`,
`approval_reject.max_attempts`, etc.), so semantically distinct duplicate field
names cannot alias. The production catalog rejects duplicate scoped IDs.

Each referenced definition carries the central label, useful description,
examples, widget, section, applicable unit, and—only for Archon v3—the exact
structured semantic record. Tests compare timeout, idle timeout, retry
defaults/counting, direct dependencies, typed condition behavior, Bash
bounds/contexts, and persistent-session recovery against the unchanged inline
JSON Schema annotations after a JSON serialize/deserialize round trip. Legacy
retains seconds/count units and carries no v3 semantic records.

The breaking editor projection change is explicit: `contract_reader_version`
and `editor_projection_version` are both 2 while the language normalizer stays
profile-specific (legacy v2, Archon v3). The contract digest still covers the
full envelope.

### Generated durable-code authority remains complete

All 34 `PHASE3_DURABLE_CODES` remain in the generated Archon catalog. The
completeness gate begins with an empty observed set and populates it only by
invoking real normalization, resume, admission, resolver, condition,
durable-wait, Bash, and session/recovery behavior paths before asserting exact
set equality with the registry. The post-quality changes retain real emitters
on both POSIX and the native-Windows Bash branch rather than skipping the
integrity code.

### Portable and installed flows remain functional

The provenance-linked representative Archon flow still executes a sealed
120,000 ms timeout, one retry after the initial attempt, and a direct typed
condition. Functional scheduler paths retain the exact 32,768/32,769-byte Bash
boundary behavior and one confirmed-missing cross-run shared-to-fresh recovery.
Scheduler lifecycle is now explicitly shut down in every helper path.

The installed-distribution integration test still builds and imports the wheel
under a temporary clean `HERMES_HOME`, queries the generated Archon contract,
and executes the representative timeout/retry/condition flow. It also proves
the installed scheduler shuts down cleanly.

### Documentation, profile, and phase boundaries remain intact

- Public stable-code documentation continues to derive from
  `compatibility_codes`; no second exhaustive prose list exists.
- Archon publishes the generated `extension-options` topic from the central AI
  option and Phase 4 authorities: MCP/skills are options, not node kinds, and
  loop/include expansion remains Phase 4.
- Legacy omits the Archon-only extension and recovery topics.
- MCP and skills remain actual AI-node options in the field inventory; no new
  node kinds or Phase 4 behavior were introduced.
- No checked-in generated JSON Schema or raw behavioral environment variable
  was added.

## Exact contract invariants

| Profile | Normalizer | Reader/editor | Bytes | Definitions | Field refs | Codes | Extension topic |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `hermes-legacy` | 2 | 2 / 2 | 240,669 | 96 | 227 | 9 | absent |
| `archon-2026-07` | 3 | 2 / 2 | 249,509 | 96 | 214 | 39 | present |

Archon remains below both the 256,000-byte hard contract limit and the
252,000-byte reserved-growth target. Section sizes also remain below their
published budgets:

- `definition_schema`: 146,057 / 150,000 bytes;
- `node_kinds`: 58,655 / 72,000 bytes; and
- `compatibility_codes`: 13,604 / 15,000 bytes.

Every serialized field reference resolves to a local definition. Archon has
v3 semantic definitions; legacy has none.

## Fresh verification evidence

- Full eight-file Task 15 functional matrix with retries disabled:
  **1,764 passed, 0 failed**.
- Installed-distribution integration gate through the built wheel with retries
  disabled: **1 passed, 0 failed**.
- Direct serialized-envelope profile/version/size/section/reference/topic/code
  assertions passed for both profiles.
- Scoped Ruff passed for all post-quality Python source and test files.
- `git diff --check` passed for the full Task 15 range.
- No prohibited threat-model or security-focused validation was performed.
