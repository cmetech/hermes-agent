# Troubleshooting Taxonomy

Choose the narrowest supported category, retain “unchecked” separately from
“failed,” and collect only redacted diagnostics.

- `missing configuration`: A required protected setting is absent or a non-secret
  setting is incomplete. Direct secret entry to Tools & Keys; never request values.
- `rejected/expired authentication`: The source system rejects a credential or an
  interactive session is absent/expired. Reauthenticate through the approved flow.
- `insufficient permission`: Authentication works but the user lacks access to the
  selected resource or operation. Do not guess the organization's approval process.
- `network/TLS`: DNS, proxy, reachability, certificate validation, or TLS negotiation
  prevents a connection. Keep diagnostic output free of headers and credentials.
- `missing local dependency/application`: A required package, executable, desktop
  application, COM surface, or local server is absent/unavailable. Ask before repair.
- `invalid input`: Scope, identifier, file, schema, filter, format, or destination is
  invalid or ambiguous. Correct only the smallest missing input.
- `source-system failure`: The remote/local source system returns an error or is
  unavailable after connection and authentication. Preserve the original status.
- `workflow-state failure`: A workflow checkpoint, approval state, transition, or
  resume record is invalid/stale. Inspect state before restarting.
- `partial side effect`: A timeout, interruption, or uncertain response may have
  changed the source system. Inspect before rerun to prevent duplication.
- `ambiguous artifact destination`: The output location, filename, overwrite
  behavior, or authoritative result cannot be determined. Resolve before writing.

For escalation, summarize category, capability/maturity, timestamp, redacted error
class, safe checks, and remaining uncertainty. Omit raw payloads and secrets.
