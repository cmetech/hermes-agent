# Authoring checklist

Before offering execution or scheduling, verify all of these:

- The workflow was played back in plain language and the user resolved each
  decision one at a time.
- Portable YAML contains only supported Archon fields and aliases.
- Every referenced command, script, skill, MCP definition, hook, inline agent,
  runtime, service, provider field, and output schema has a doctor finding.
- Every mandatory immutable input has a kind, required flag, byte ceiling, and
  snapshot policy; nodes consume the snapshot, not its source path.
- The overlap choice is explicit. New packages default to `queue`; `allow` is
  bounded and `forbid` explains refusal while a matching run is active.
- Cache-fingerprint changes use fresh context; shared context is byte-stable.
- Every outward action has an approval gate unless the user explicitly opted
  out after reviewing the impact.
- Required credentials are listed by name only. No values appear in package,
  doctor output, logs, or trust state.
- Execution environment and resource ceilings cover workers, parallel nodes,
  timeouts, retries, descendants, CPU, memory, and admission capacity.
- `PRODUCT_CLI workflow doctor PACKAGE --json` is runnable without a model, remote
  MCP connection, network call, or provider request.
- The user reviewed the exact package digest and risk summary before trust.
  The package never writes trust for itself.
- Run or cron is offered only after the doctor gate is runnable.
