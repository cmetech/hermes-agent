# Portable package shape

Use this directory layout:

```text
package/
├── workflows/name.yaml
├── workflows/name.hermes.yaml
├── commands/long-prompt.md
├── scripts/helper.py or helper.ts
└── mcp/server.yaml
```

Only create `commands/`, `scripts/`, or `mcp/` resources when referenced, but
when referenced they must exist inside the package and are included in its
digest. Symlinks and escaping paths are rejected.

## Portable YAML

Required top-level fields are `name`, `description`, and `nodes`. Each node has
an `id`, exactly one node-type field, and optional `depends_on`. Supported node
types are `command`, `prompt`, `bash`, `script`, `loop`, `approval`, and
`cancel`. Conditions use `$node.output`; graph references must be upstream.

AI node fields include `context`, `persist_session`, `provider`, `model`,
`output_format`, `allowed_tools`, `denied_tools`, `hooks`, `mcp`, `skills`, and
`agents`. Use Archon tool aliases such as `Bash`, `Read`, `Write`, `Edit`,
`Glob`, `Grep`, `WebFetch`, `WebSearch`, and `Agent`; doctor reports the Hermes
mapping. Unknown capitalized aliases are blocking.

Use `context: shared` only when every cache-fingerprint field is identical to
the compatible predecessor. Otherwise use `context: fresh`. `script` nodes
declare `runtime: uv` or `runtime: bun`; named scripts resolve below `scripts/`.
Long `command` prompts resolve below `commands/`. An `mcp` reference resolves
below `mcp/` and starts only within the isolated worker.

## Neutral sidecar

`workflows/name.hermes.yaml` may declare delivery defaults, required services,
retention, tags, outward-action nodes/policy, execution environment, overlap
policy, concurrency key, lifecycle limits, resource limits, required secret
names, and scheduling policy. It cannot change `nodes` or `depends_on`, contain
secret values, or declare trust.

Input declarations live at `delivery_defaults.inputs.NAME` with:

```yaml
delivery_defaults:
  inputs:
    evidence:
      kind: file       # text | file | directory | json
      required: true
      max_bytes: 1048576
overlap_policy: queue  # queue | allow | forbid
```

Package trust is profile-owned and digest-bound outside the package.

