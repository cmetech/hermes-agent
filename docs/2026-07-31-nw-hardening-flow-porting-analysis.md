# NW Hardening — LangFlow Reference Implementation, and How to Port It to the Hermes Workflow Engine

**Date:** 2026-07-31
**Source repo (read it — all paths below are real):** `/Users/coreyellis/code/gitlab.rosetta.ericssondevops.com/loop_24`
**Target:** the Hermes portable workflow engine (`docs/workflow-orchestration.md`, plugin `workflow`) plus a bundled skill so a natural-language request triggers the flow.

---

## 0. Prompt for the working session

> You are working in the `hermes-agent` repo. Read this document in full, then read the
> referenced source files in the `loop_24` repo (paths are absolute). Your task is to
> design — not yet implement — the best way to reproduce the **NW Hardening** assistant
> as a Hermes workflow package:
>
> 1. Map every LangFlow "block" flow to a Hermes workflow node (or tool/skill/MCP
>    resource) and justify each mapping. Distinguish LLM nodes from deterministic nodes.
> 2. Define the canonical spec-JSON contract (§4) as the artifact that moves between
>    nodes, and decide where it is validated.
> 3. Map the two human gates (§5) onto Hermes `approve` / `provide-input` interactions.
> 4. Decide how the natural-language entry point works: a bundled skill that gathers
>    the change request conversationally, then admits a `hermes workflow run` with the
>    drafted spec as input.
> 5. Produce the Archon-shaped YAML topology (nodes, edges, approvals, artifacts,
>    retry classes), the policy sidecar, and the list of new tools/scripts/MCP entries
>    the package needs.
> 6. Call out every place the LangFlow design does something the Hermes engine does
>    better natively (durable runs, typed failures, artifact digests, reconciliation)
>    and simplify accordingly — do not transliterate LangFlow mechanics.
>
> Deliverable: a design doc + draft `workflows/nw-hardening.yaml` skeleton under a new
> portable package directory, ready for review. Ask before writing implementation code.

---

## 1. What the thing does (business flow)

A network-hardening change request (e.g. "mitigate CVE-X on the CCD cluster at site Y")
normally requires an engineer to: understand the request, pull site details and
distribution lists from Confluence, find the right document template in SharePoint,
search internal docs (Glean) for the vulnerability and workaround, assemble a
**Method of Procedure (MoP)** document, generate an **Ansible playbook** implementing
the change, and write **release notes** — all while keeping customer-identifying data
out of any LLM.

The loop_24 implementation turns that into a chat assistant: the user describes the
change in natural language; an orchestrator agent fills out a **spec JSON** by calling
single-purpose tools; the user reviews and confirms the JSON; only then are the
document / playbook / release notes generated from it.

## 2. Architecture: one orchestrator agent + a folder of tool-flows

Two LangFlow folders (see `flows/manifest.json`):

- `flows/nw_hardening_main/` — the orchestrator ("NW Hardening - Main")
- `flows/nw_hardening_blocks/` — ten single-purpose flows

The key mechanism: LangFlow exposes **every flow in a project folder as an MCP tool**
(server name `lf-nw_hardening_blocks`, spawned via `uvx`). Each block flow's *name and
description* become the MCP tool name/description; its ChatInput is the tool argument;
its ChatOutput is the tool result. The main flow contains exactly four nodes:

```
ChatInput ──► Agent (Ollama, tool-calling) ──► ChatOutput
                 ▲
             MCP Tools node → server "lf-nw_hardening_blocks"
```

Source: `flows/nw_hardening_main/NW Hardening - Main.json` — the `MCP` node's template
has `mcp_server = {"name": "lf-nw_hardening_blocks", "config": {"command": "uvx", ...}}`
and the `Agent` node runs Ollama (`model: [{provider: "Ollama", name: "auto"}]`,
`max_iterations: 15`, streaming on).

The orchestrator's entire behavior is its system prompt (verbatim, from the Agent node):

> **Tool Access** — You have access to the following tools: Ingesting metadata for
> template identification from sharepoint, ingesting data from confluence for site
> details and distribution lists, user intention identification, internal
> documentation search via glean
>
> **Overall Goal** — You will ingest a change request for which you must understand
> the intent. Then a json will be created that will be worked on. Then using the data
> from above your goal is to fill out the json for the user. If the user wants to
> change something or mentions you pulled the wrong information from one of the
> sources, ONLY run that tool again with user input to get the right info. If
> additional info is provided by the user you can just add it to the json without
> pulling data using the tools. Perform the tool calls one at a time not
> simultaneously. Until the user confirms that the json is correct do not proceed to
> the next step. Before you read any document in Glean, ensure that you get the users
> permission to ensure that the right document is being ingested.

This is the pattern to preserve: **the LLM owns conversation and data-gathering; it
never owns generation.** Generation is deterministic code fed by the confirmed JSON.

## 3. Block catalog (the tools)

All files under `flows/nw_hardening_blocks/`. "ext:" components are repo custom
components under `custom_components/`.

| Flow file | Tool purpose | Composition | LLM? |
|---|---|---|---|
| `nw_hardening_intention.json` | Turn the user's change request into the starter spec JSON | SmartRouter + Agent (system prompt embeds the base schema, §4) → `Base JSON: {json}` | Yes |
| `nw_hardening_sharepoint_data_f.json` | Template metadata from SharePoint | `SharePointFilesFetcher` (`custom_components/ericsson_parsers/sharepoint_files_fetcher.py:53`) → ChatOutput | No |
| `nw_hardening_confluence_data_f.json` | Site details from Confluence | `ConfluencePageFetcher` (`custom_components/ericsson_parsers/confluence_fetcher.py:23`) → Prompt `Data Dump: {Data}` | No |
| `nw_hardening_confluence_distro.json` | Distribution lists from Confluence | Same fetcher → Prompt `Distribution List: {Distro}` | No |
| `nw_hardening_glean.json` | Search internal Ericsson docs | Agent + MCP node → server `glean-mcp`; prompt restricts to `search` and `read_document` tools only | Yes |
| `nw_hardening_anon.json` | Mask secrets/PII before LLM contact | `FileAnonymizer` (`custom_components/ericsson_privacy/file_anonymizer.py:684`) | No |
| `nw_hardening_re_id.json` | Restore masked values | `FileDeAnonymizer` (`custom_components/ericsson_privacy/file_deanonymizer.py:242`) | No |
| `nw_hardening_docgen.json` | Build the MoP .docx from spec + template link | `SpecDocumentBuilderComponent` (`custom_components/ericsson_docgen/document_generator_component.py:32`) | Yes (section writing) |
| `nw_hardening_rn.json` | Release notes from the same spec | Same component with `release_notes_only=True` — **no model call**, fixed Ericsson delivery-note skeleton (`document_generator_component.py:96-109`) | No |
| `nw_hardening_ansible.json` | Playbook + inventory + revert from the spec | `SpecAnsibleBuilderComponent` (`custom_components/ericsson_docgen/ansible_generator.py:24`) | No — deterministic by design |

External MCP dependency (`flows/attributes.json`):

```json
"mcp_servers": {
  "glean-mcp": {
    "command": "npx",
    "args": ["mcp-remote", "https://be.everyday-assistant.ericsson.net/mcp/EEA-KIRO-MCP"],
    "env": {"ANALYTICS_USERNAME": "TBD"}
  }
}
```

SharePoint downloads go through a subprocess script `utils/sp_files.py` (Azure/MSAL
auth); Confluence through `utils/confluence_page.py`.

## 4. The spec JSON — the contract everything moves through

The intention agent seeds this skeleton (system prompt in
`nw_hardening_intention.json`):

```json
{
  "name": "",
  "site_details": "",
  "components": [{ "name": "", "version": [] }],
  "author": "",            // "Loop24 <user email>"
  "description": ""
}
```

The *full* schema is defined implicitly by what the deterministic consumers read.
From `custom_components/ericsson_docgen/ansible_generator.py` (lines 513–520,
582–599, 626–643, 772, 832–854):

```jsonc
{
  "name": "...",                       // change/spec name
  "document_name": "...",              // output file naming (document_generator_component.py:2870)
  "author": "...",                     // revision history + core props (…:2591)
  "description": "...",
  "site_details":         { "site_id": "..." /* … */ },
  "vulnerability_details": { /* CVE ids, severity, … */ },
  "components":           [ { "name": "...", "version": ["..."] } ],
  "ccd_details":          { /* hosts by role → inventory.ini groups */ },
  "mitigation_steps": {
    "<workaround-name>": {
      "steps": ["exact shell command …"],
      "revert_command": "..."
    }
  },
  "is_disruptive": true,
  "is_automated": true
}
```

Design property worth keeping: **fields the generators read are the schema.** The
Ansible builder never invents a command — a step it cannot extract with confidence
becomes a visible manual checkpoint in the playbook rather than a guess
(`ansible_generator.py:37-41`). Change control gets character-for-character parity
between the approved MoP and what executes.

## 5. Control-flow patterns to port (these are the actual value)

1. **Human confirmation gate.** The agent may not generate anything until the user
   confirms the spec JSON. In Hermes this is a first-class `approve`/`reject`
   interaction on a workflow node — better than prompt-enforced discipline.
2. **Read-permission gate.** Before ingesting any Glean document, the agent asks the
   user which document is correct. Maps to `provide-input`.
3. **Selective re-run.** "Wrong site details" re-runs *only* the Confluence tool, not
   the whole pipeline. Maps to node-level `retry <run-id> <node-id>` or a bounded
   gather-loop node.
4. **One tool at a time, sequential.** Prompt-enforced in LangFlow; a DAG edge in
   Hermes.
5. **Privacy envelope.** Anonymize → LLM → re-identify, keyed by a session key
   (`file_anonymizer.py`: format-preserving IP pseudonyms, key-name redaction for
   YAML/JSON/XML/CSV, `strict` re-scan that refuses output if a secret survived).
   In Hermes this is a pair of deterministic nodes bracketing every LLM node that
   touches customer data.
6. **Deterministic generation from confirmed data.** Docgen (template held in memory
   only, never persisted — `document_generator_component.py:33-55`), release notes
   (zero model calls), Ansible (zero model calls). These become script/command nodes
   whose outputs are workflow **artifacts** (paths + SHA-256 digests — Hermes already
   does this natively).
7. **Fan-out from one artifact.** MoP, release notes, and playbook are all projections
   of the same confirmed spec — three parallel nodes downstream of the approval gate.

## 6. Suggested Hermes topology (starting point, to be challenged)

```
[skill: NL intake] ──► admit run(spec_draft)
      draft-spec (LLM)            ← intention block
      gather: sharepoint-meta     ← deterministic fetch
      gather: confluence-site     ← deterministic fetch
      gather: confluence-distro   ← deterministic fetch
      gather: glean-search (LLM)  ← needs read-permission input gate
      merge-spec (LLM)            ← fills spec from gathered data
  ⏸  approve-spec                 ← user reviews JSON; reject → bounded revise loop
      ├─ build-mop (LLM+script, anonymize/re-id bracket)
      ├─ build-release-notes (script, deterministic)
      └─ build-ansible (script, deterministic)
      collect-artifacts → .docx, playbook.yml, inventory.ini, revert.yml, README.md
```

Entry: since Hermes triggers workflows from natural language via bundled skills
(`docs/workflow-orchestration.md`, "Classic CLI or TUI chat" row), the skill's job is
the conversational part of the intention block — gather the request, draft the spec,
then `hermes workflow run nw-hardening --arguments <spec-draft>`. The workflow owns
everything durable: fetches, approval, generation, artifacts, retries.

Open questions the design must answer:

- Does the gather phase live inside the workflow (nodes with retry/typed failures) or
  inside the skill conversation (more fluid, less durable)? The LangFlow version puts
  it in-conversation; Hermes approvals + `provide-input` may allow it in-workflow.
- Glean/Confluence/SharePoint access: local MCP process definitions in the package's
  `mcp/` resources vs Hermes-native tools. Secrets are *names* in the policy sidecar.
- Where the spec schema is validated (admission input manifest vs a validate node).
- Whether the anonymize/re-id bracket is middleware for every LLM node or explicit
  nodes (explicit = visible in topology and auditable; the loop_24 team chose explicit).

## 7. Reading list (priority order)

| File (in loop_24) | Why |
|---|---|
| `flows/nw_hardening_main/NW Hardening - Main.json` | Orchestrator prompt + MCP wiring |
| `flows/nw_hardening_blocks/*.json` | Tool contracts (name/description/IO per block) |
| `custom_components/ericsson_docgen/ansible_generator.py` | Spec schema ground truth + deterministic-generation philosophy |
| `custom_components/ericsson_docgen/document_generator_component.py` | Docgen contract; in-memory template handling; release-notes mode |
| `custom_components/ericsson_privacy/file_anonymizer.py`, `file_deanonymizer.py` | Privacy envelope contract (session key, strict mode, in-memory mode) |
| `flows/attributes.json` | External MCP server + global variable requirements |
| `flows/README.md` | How flow JSON is treated as source of truth / seeded / pushed |
| `custom_components/ericsson_parsers/{confluence_fetcher,sharepoint_files_fetcher}.py` | Fetch-side contracts |

Relevant loop_24 commits if history is needed: `bf7271a` (initial flows + docgen),
`6baa858` (ansible builder), `17cf852` (release notes, de-id, streamed data),
`f79e385` (torch for presidio, glean sync).
