# Authoring checklist

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
- Treat node `idle_timeout` as a blocking Archon field in Phase 1. Its current
  `hermes-legacy` value is seconds (`legacy_idle_timeout_seconds`); Archon
  millisecond normalization remains deferred to Phase 3, so never reinterpret
  the authored value during profile conversion.
- For shared package bytes, establish the oldest backend. Keep the companion
  unversioned while any consumer predates `language_compatibility` support.
- Translate any legacy `create-workflow` request into `nodes`; do not adopt
  OTTO V1 `steps`, `produces`, `context_from`, `verify`, or `iterate` fields.

Before offering execution or scheduling:

- Portable YAML and the companion match the selected generated schemas, with
  no blocking annotations or findings ignored.
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
- Hermes `limits` and `resource_limits` are described as execution policy, not
  deferred Archon idle-timeout, timeout, budget, retry, or sandbox semantics.
- Run `PRODUCT_CLI workflow validate PACKAGE --json`, then
  `PRODUCT_CLI workflow doctor PACKAGE --compat-report --json`. Doctor remains
  model-free, network-free, and MCP-connection-free.
- Explain the effective profile, package digest, complete risk summary,
  immutable inputs, overlap, and effective ceilings; resolve every blocker.
- The user confirms the exact digest before trust. The package never writes
  trust, and every byte change repeats doctor and confirmation.
- Run or cron is offered only after the doctor gate reports runnable.
