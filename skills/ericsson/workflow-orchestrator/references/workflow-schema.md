# Workflow YAML schema (v1)

A workflow is a YAML mapping executed as a DAG in file order by
`workflow_ctl.py`. Single source of truth — the workflow-builder skill and
all authored workflows follow this document.

## Top-level keys

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | slug (`[a-z0-9][a-z0-9_-]*`); run dirs key off it |
| `description` | yes | one-line human description |
| `version` | yes | semver string, bump on edit |
| `tags` | no | list; shown in listings (always include `ericsson`) |
| `requires.toolsets` | no | toolsets the nodes use (documentation only — NOT validated; availability is checked by the agent at run time) |
| `requires.env` | no | env vars needed; unset → validate WARNING (not error) |
| `inputs` | no | list of `{name, default?}`; set at start with `--input name=value` |
| `nodes` | yes | ordered list (see below) |
| `report.kanban` | no | `auto` (default) \| `on` \| `off` — kanban mirroring |
| `report.notify` | no | reserved for future hooks (e.g. Multica); keep `[]` |

## Nodes

Common: `id` (slug, unique), `kind`, optional `depends_on` (list of ids), optional `toolset` (documentation hint naming the toolset the node uses),
`when` (condition), `output` (bare filename written into the run dir),
`side_effects: true` (outward action — send/post/create; resume will never
silently re-run it).

| kind | Required field | Semantics |
|---|---|---|
| `prompt` | `prompt` | agent does the work with judgment |
| `tool` | `prompt` | same, but names the tool(s) to use in the prompt |
| `script` | `command` | agent runs the command verbatim in the run dir, captures stdout |
| `approval` | `message` | human gate: run parks until approve/reject |

## `when` conditions

`$inputs.<name>` or `$<node-id>.output` (a node's recorded one-line summary),
compared with `==` / `!=` against a bare word or `'quoted string'`; combine
with `&&` (binds tighter) and `||`. No parentheses. Invalid or unresolvable
expressions evaluate FALSE (fail-closed) and the node is skipped. A skipped
node skips all its dependents.

## Execution semantics

- One node at a time, in file order among ready nodes.
- A node runs when all `depends_on` are `ok`; any skipped dependency skips it.
- A failed node fails the run (recover with `resume`).
- Approval nodes park the run (`waiting_approval`) until `approve`/`reject`;
  reject ends the run as `rejected`.
- State lives in `$HERMES_HOME/workflows/runs/<name>/<run-id>/state.json`,
  written only by workflow_ctl (atomic). Every run dir is new — runs never
  share state.
- The YAML is hashed at start; editing it mid-run requires
  `resume --accept-changes` or `restart`.

## v2 (not yet supported — do not use)

Loops, retries with backoff, `on_reject` rework, parallel branches,
JSON-schema-enforced node outputs, `report.notify` implementations.
