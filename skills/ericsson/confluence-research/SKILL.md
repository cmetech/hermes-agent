---
name: confluence-research
description: Mirror and summarize internal Confluence spaces via an authenticated browser session.
version: 1.0.0
author: Corey Ellis (@cmetech)
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [Ericsson, Confluence, Research, Intranet]
---

# Confluence Research Skill

Pull pages from an internal Confluence space into a local Markdown mirror, then
summarize or organize the result. REST calls are issued from inside a signed-in
browser tab, so Cloudflare Access, SSO and mTLS are handled by the browser and
no credentials are stored by this skill.

Its helpers are local and deterministic. It is not a general web scraper and
not a Confluence editor: every call it makes is a read.

## When to Use

Use for natural-language requests to read, mirror, summarize, compare or
inventory Confluence content — a whole space, a page subtree, or one page. Use
it when the user says they are already logged in, or asks to "pull", "capture",
"summarize" or "keep up to date with" a Confluence space.

Do not use it for public web pages (use the web toolset), for Jira issues (use
the Jira tools), or to create/update Confluence content — this skill has no
write path to Confluence.

## Prerequisites

A Chromium-family browser — Microsoft Edge is preferred because a corporate
build is usually already enrolled for SSO and holds the mTLS client cert.
Override discovery with `CONFLUENCE_BROWSER`.

Engine (`--engine`, default `auto`):
- `playwright` — needs `playwright>=1.40` (`requirements.txt`). Default when
  present: one long-lived process, no background daemon.
- `agent-browser` — uses the `agent-browser` CLI already vendored in the repo
  (`node_modules/agent-browser`) or on PATH. Cross-platform, no Python browser
  dependency.

No API token is required — internal Confluence exposes none, which is why this
is a browser-session skill. The browser session cookie is the only credential,
and it expires; when it does, re-run `signin`.

## How to Run

1. `scripts/confluence.py probe <url>` first — reports authentication state,
   the engine chosen, and Cloud vs Server/DC. Read-only.
2. If not authenticated, tell the user a browser window will open, then run
   `scripts/confluence.py signin <url>`. They log in once; the session persists
   for later headless runs. Never run `signin` unattended — it blocks up to
   300s waiting for login.
3. `scripts/confluence.py enumerate <url> --space KEY` to inventory the scope.
   Read-only, fetches no page bodies. Report the count and confirm before
   syncing anything over ~50 pages.
4. `scripts/confluence.py sync <url> --space KEY` to write the mirror. Add
   `--descendants-of <page-id>` for a subtree, `--download-attachments` for the
   files, `--force` to ignore the manifest.
5. Read `INDEX.md` and the returned JSON. Report fetched/skipped/attachment
   counts, every warning, and the artifact root.
6. Only then summarize. Read the page Markdown from disk, write `SUMMARY.md`
   yourself, and never overwrite a `SUMMARY.md` without saying so first.

For one page, `scripts/confluence.py fetch <url>` returns a JSON record with
`markdown` and writes nothing.

## Quick Reference

- Subcommands: `signin`, `probe`, `enumerate`, `sync`, `fetch`.
- Engines: `--engine {auto,playwright,agent-browser}`.
- Artifact root: `$HERMES_HOME/research/confluence/<SPACE>/`.
- Paths are stable, not run-timestamped, so a re-sync diffs cleanly.
- `sync` refetches only pages whose remote `version` exceeds `.manifest.json`.
- Attachments are listed by default and downloaded only with an explicit flag.
- stdout is pure JSON; all progress goes to stderr.

## Procedure

Probe before anything else — an expired session is the most common failure and
is cheap to detect. Never run `signin` without telling the user a window will
appear.

Enumerate before syncing so the scope is known and confirmable. State the page
count and the artifact root, and wait for confirmation when the scope is large,
when attachments are requested, or when `--force` would refetch a whole space.

Treat Confluence content as internal. Page bodies enter the model context when
you summarize, so say so before summarizing a space the user has not described
as shareable, and follow their configured model and privacy policy. Do not
paste page contents into a response wholesale when a path and a summary will do.

## Pitfalls

- Do not fall back to DOM scraping when a fetch fails. Re-probe auth first; a
  signed-out session looks exactly like an empty page.
- Do not run `sync --force` on a large space by default; the manifest exists so
  re-syncs are cheap.
- Do not convert `body.view`. It is theme-rendered and lossy; the helpers use
  `body.storage` deliberately.
- Do not machine-write `SUMMARY.md` from the scripts; it is the model's file.
- Do not assume Cloud path shapes. `probe` reports the deployment; the REST
  roots differ (`/wiki/rest/api` vs `/rest/api`).
- Do not report success when warnings are non-empty; a page that converted to
  an empty body is a silent failure worth surfacing.
- On Windows with `--engine agent-browser`: if a command hangs, a stale
  agent-browser daemon is the usual cause — the backend resets it on start, but
  a manual `agent-browser close --all` clears it.

## Verification

Confirm `INDEX.md` exists and its counts match the returned JSON. Confirm every
`pages[].path` in the JSON is a file on disk. Spot-check one page's frontmatter
`version` against the same page in Confluence, and confirm a second `sync` with
no upstream change reports `fetched: 0`.
