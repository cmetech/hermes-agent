---
id: glean-search
display_name: Glean Search
aliases: [internal knowledge search, Glean MCP, enterprise document search]
goals:
  - Search our internal knowledge for this product.
  - Find internal documents that answer my question.
  - Summarize top internal search results with source links.
maturity: available
recommendation_eligible: true
source_flows: []
implementation:
  skills: []
  plugins: []
  mcp_servers: [glean]
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration:
  - {name: GLEAN_MCP_URL, kind: static-setting, required: true, guidance: Configure the organization-provided endpoint in protected Tools & Keys.}
  - {name: GLEAN_API_TOKEN, kind: static-secret, required: true, guidance: Enter the token only in protected Tools & Keys and never in chat.}
reads: [runtime-discovered Glean search results and documents permitted to the user]
writes: []
artifacts: [conversation results, optional user-requested local summary]
demonstrations: [read-only-live]
troubleshooting: [endpoint connection failure, authentication rejected, permission limits, expected search tool absent]
---

# Glean Search

## What it solves

Connects to the configured remote Glean MCP endpoint for read-only internal knowledge
discovery using whatever search tools the server actually exposes at runtime.

## Try saying

- “Search our internal knowledge for this product.”
- “Find internal documents that answer this question.”
- “Summarize the top Glean results with source links.”

Follow up with query or source filters supported by the discovered schema, request a
preview, choose result format/destination, ask about exclusions/warnings, or rerun.

## Questions

The Co-Worker asks for the topic and only the scope or result limit supported by the
connected server; it does not invent organization-specific access procedures.

## Reads and writes

Search reads only content the signed-in token permits. This repository declares no
Glean write path; optional summaries require a user-selected safe destination.

## Readiness

Check protected key presence without values, connect, list the server's actual tools,
then run a narrow read-only search. A variable name does not prove authentication.

## Demonstration

The current supported live demonstration is a permitted, narrowly scoped read-only
search. Do not claim synthetic or live success when the server cannot connect.

## Artifacts

Results and source links appear in chat unless the user chooses another format and
destination. Explain omitted or inaccessible sources as exclusions or warnings.

## Troubleshooting

Separate DNS/TLS/network, authentication, permission, and missing-tool-schema cases.
Re-discover tools before a rerun if the endpoint changed.
