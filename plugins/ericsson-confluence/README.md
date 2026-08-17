# Ericsson Confluence connector

## When the PAT path cannot reach Confluence

This connector authenticates with a bearer PAT, which works headless — the desktop-spawned backend, cron, containers — and supports writes. It cannot get past Cloudflare Access, an mTLS client-certificate requirement, or an SSO interstitial that never issues a usable token.

For those cases `hermes-agent/skills/ericsson/confluence-research` takes a different approach: it drives the enrolled corporate browser over CDP and issues same-origin `fetch` calls from inside a signed-in tab, so authentication is the browser's problem and no credential is stored. It is **read-only** and requires a live browser, so it is not a replacement for this connector — it is the escape hatch when the PAT path is blocked.

This connector's converter was ported from that browser skill. The connector and skill keep separate copies, so changes should remain behaviorally aligned.

Related precedent: `ericsson-jira` ships Cloudflare-1010 detection and a curl transport fallback for the same class of problem, and `ericsson-sharepoint` exposes browser enrolment as a `setup_action`.
