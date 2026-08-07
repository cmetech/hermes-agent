# Authoring checklist

Current/default Archon authoring and admission use normalizer v4; current
legacy uses v2. Explicit and sealed v1-v3 contracts remain compatible and keep
their pinned semantics.

<!-- workflow-language-version-selection -->
```json
{
  "current_normalizer_by_profile": {
    "hermes-legacy": 2,
    "archon-2026-07": 4
  },
  "supported_normalizer_versions": [1, 2, 3, 4, 5]
}
```

Before writing files:

- Resolve the branded executable and replace every `PRODUCT_CLI` placeholder.
- Query `PRODUCT_CLI workflow schema --profile archon-2026-07 --json` and
  report the resolved command, returned versions, and every proposed field's
  schema and compatibility annotation before files.
- Play the graph back in plain language after resolving outcome, inputs,
  outward effects, environment, dependencies, overlap, and ceilings one at a
  time.
- For a normal new package, plan the companion declaration
  `language_compatibility: archon-2026-07`.
- For a blocking field, stop and offer only: (1) omit it and remain Archon,
  optionally using companion execution-policy limits within that same choice,
  or (2) let the user deliberately choose its current `hermes-legacy` meaning
  and warning. Never present policy limits as a third choice.
- Treat delegated choice, “do not ask,” and deadline pressure as no profile
  selection. Legacy requires explicit selection after both choices and the
  blocking contract evidence are shown.
- Describe contract phase numbers only as enforcement-phase metadata, never as
  delivery dates, availability promises, or schedules.
- Archon timeout and retry fields are supported: they were introduced in v3 and
  are inherited by current v4. Author
  timeout values in milliseconds, remember the omitted Bash/script 120,000 ms
  default, and count `retry.max_attempts` as retries after the initial attempt.
  Legacy timeout values remain seconds and legacy attempts remain totals.
- Make every output producer a direct dependency. Use strict typed scalar
  conditions and structured output before field traversal.
- Review Bash values at the 32,768-byte UTF-8 boundary and author only bounded
  safe token contexts. Larger values are contents, never pathnames.
- Only a confirmed missing cross-run session may start fresh. Same-run missing
  context and session-store errors must remain failures.
- MCP and skills remain node options. `include` is a compile-only v4 directive,
  not an executable node kind; ordinary new Archon admissions and generated
  contracts use v4.
- Compile and admit the generated v4 contract and preserve it in the sealed run
  snapshot. Use explicit v1-v3 only for compatibility with an intentionally
  selected or already sealed historical contract.
- Give an include only `id`, literal `include`, optional `depends_on`, and
  optional `trigger_rule`. Do not add runtime fields, `with`, URLs, paths,
  expressions, or a `loop_group`.
- Treat the root companion as the only policy. Authenticate but ignore child
  companions; never import their required-secret declarations, services,
  limits, or profile choice.
- Keep the complete include closure within depth 3, 64 distinct dependencies,
  512 executable nodes, 4,096 edges, 2 MiB selected/expanded byte ceilings,
  512 authenticated files, 1 MiB per file, and 8 MiB total.
- Connect parent dependencies to every child entry and downstream consumers to
  every child sink. An include output alias means the first sink in definition
  order; it is not a deep-child reference.
- For a v4 loop, author exactly one of `prompt` or named `command`. Seal a named
  command from its logical package origin. Effective interactivity requires
  both workflow and loop `interactive` plus `gate_message`.
- Review `signal_completes`: it defaults false for effective interactivity and
  true otherwise. Before the final iteration, a confirmation can approve,
  provide-input, or cancel; the final iteration can only approve or cancel.
  Approval must not replay the provider.
- For shared package bytes, establish the oldest backend. Keep the companion
  unversioned while any consumer predates `language_compatibility` support.
- Translate any legacy `create-workflow` request into `nodes`; do not adopt
  OTTO V1 `steps`, `produces`, `context_from`, `verify`, or `iterate` fields.

Before offering execution or scheduling:

- Portable YAML and the companion match the selected generated schemas, with
  no blocking annotations or findings ignored.
- For v4, review the composite root-plus-dependency digest, stable
  include diagnostics, bounded logical origins, and all warnings before trust.
  Source deletion after admission is safe only because execution and resume
  verify the immutable dependency manifest and sealed resource bindings.
- Every referenced command, script, skill, MCP definition, hook, inline agent,
  runtime, service, provider field, and output schema has a doctor finding.
- Every referenced `commands/`, `scripts/`, and `mcp/` resource exists below
  the package root; no symlink or escaping path is present.
- Every mandatory immutable input has a kind, required flag, byte ceiling, and
  snapshot policy; nodes consume the admitted snapshot, not its source path.
- Overlap is explicit. New packages default to `queue`; `allow` is bounded and
  `forbid` explains active-run refusal.
- Cache-fingerprint changes use fresh context; shared context is byte-stable.
- Every outward action has an approval gate unless the user explicitly opted
  out after reviewing impact.
- Required credentials are names only. No values appear in the package,
  diagnostics, logs, or trust state.
- Resource ceilings cover workers, parallel nodes, lifecycle seconds, total
  attempts, descendants, CPU, memory, and admission capacity.
- Hermes `limits` and `resource_limits` are execution policy. They intersect
  Archon v3-v4 timeout/retry requests and do not imply budget or sandbox support.
- Run `PRODUCT_CLI workflow validate PACKAGE --json`, then
  `PRODUCT_CLI workflow doctor PACKAGE --compat-report --json`. Doctor remains
  model-free, network-free, and MCP-connection-free.
- Explain the effective profile, package digest, complete risk summary,
  immutable inputs, overlap, and effective ceilings; resolve every blocker.
- The user confirms the exact digest before trust. The package never writes
  trust, and every byte change repeats doctor and confirmation.
- Run or cron is offered only after the doctor gate reports runnable.
