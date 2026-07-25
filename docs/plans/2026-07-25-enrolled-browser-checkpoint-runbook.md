# Checkpoint Runbook — enrolled-browser profile on a corporate laptop

**Version under test:** OTTO / LOOP24 **v4.0.1**
**Source commits:** otto `ba7c0f4686130acffd8512dbeea3691246a4a301`, loop24 `484f80a764bc68c32651ee0c72cbe565e9bf36c3`
**Purpose:** validate the four things that are currently **unproven** — everything
below was verified against mocks only.

| # | Unproven claim | Step |
|---|---|---|
| 1 | Enrolled Edge carries CF Access + SSO + mTLS through a CDP attach | 4 |
| 2 | Internal pages are readable, and **forms** are fillable (a different code path) | 5–6 |
| 3 | The persistent profile survives; how long before Conditional Access re-prompts | 7 |
| 4 | agent-browser's daemon stays healthy on Windows across repeat runs | 8 |

Record the outcome of each step. A FAIL is useful data — capture the evidence
listed rather than retrying blindly.

---

## Step 0 — Confirm the DNS premise (already done once)

```bash
python3 -c "import socket,sys; h=sys.argv[1]; print(h,'->',sorted({a[4][0] for a in socket.getaddrinfo(h,None)}))" eteamspace.internal.ericsson.com
```

**Expected:** a `10.x` / `172.16–31.x` / `192.168.x` address.
Confirmed on 2026-07-25: `10.117.146.52` → private, so the SSRF guard **does**
fire and `trusted_origins` is **mandatory**, not optional.

If a host you care about returns a *public* IP, the guard won't fire for it and
it will work without being listed — but list it anyway for consistency.

> ⚠ Get the hostname exactly right. Matching is exact on scheme + host + port, so
> a typo means silent denial with no error. Use `https://*.internal.ericsson.com`
> to cover subdomains in one entry.

## Step 1 — Install

Download the **v4.0.1** installer from
<https://github.com/cmetech/otto/releases> (`OTTO-4.0.1-win-x64.exe`) and run it.
It is published as a **prerelease** — expected for a checkpoint build.

First launch clones the source and builds locally, so allow several minutes.

**Expected:** app opens; Settings → About shows **4.0.1**.

## Step 2 — Configure the profile

```bash
otto config edit
```

Add under the existing `browser:` block (mind the indentation — it is a child of
`browser:`, not a new top-level key):

```yaml
browser:
  default_profile: enrolled
  profiles:
    enrolled:
      kind: enrolled
      executable: auto
      user_data_dir: "${HERMES_HOME}/browser-profiles/enrolled"
      cdp_port: 9222
      headed: true
      trusted_origins:
        - "https://eteamspace.internal.ericsson.com"
        - "https://*.internal.ericsson.com"
```

**Do NOT set `browser.allow_private_urls: true`.** That is the blunt global
switch; it disables private-URL protection for every session including the
throwaway external browser. `trusted_origins` is its targeted replacement.

Verify it parsed as a **list**, not a string:

```bash
otto config get browser.profiles.enrolled.trusted_origins --json
```

**Expected:** a JSON array. If it prints a quoted string, the profile will trust
nothing — re-edit the YAML. (`otto config set` cannot create a list; that is why
this uses `config edit`.)

## Step 3 — Confirm the UI surface (Option A)

Settings → Advanced → **Safety**.

**Expected:** *Browser Profile For Internal Sites* (text, `enrolled`) and
*Trusted Internal Origins* (an **add/remove list**, showing your two entries).
*Allow Private URLs* should be **off**.

The origins field only appears because the profile now exists in config — that is
intended, not a bug.

## Step 4 — First sign-in ⭐ THE CRITICAL STEP

This is claim #1: the whole design rests on the enrolled browser's OS certificate
store carrying mTLS/SSO through a CDP attach.

In chat: **"Open https://eteamspace.internal.ericsson.com and tell me the page title."**

**Expected:** a **visible Edge window** opens (`headed: true`), you complete SSO
interactively, and the agent reports the real page title.

**Capture on failure:**
- A TLS/certificate error → mTLS is *not* carrying. Note the exact error.
- A login wall that never resolves → SSO isn't carrying, or the wrong browser
  launched. Check the window: is it **Edge** (correct) or a plain **Chromium/Chrome
  for Testing** (wrong — the unmanaged browser that cannot do mTLS)?
- `Blocked: URL targets a private or internal address` → the trust seam did not
  match. Re-check the hostname against `trusted_origins` character by character.
- `could not resolve the enrolled browser` → set
  `browser.profiles.enrolled.executable` to Edge's absolute path.

## Step 5 — Read an internal page

**"Summarise the main content of that page."**

**Expected:** real page content. A `Blocked: page URL targets a private or
internal address` here (after Step 4 succeeded) means the snapshot guard is
rejecting what navigation allowed — capture it verbatim; that is the
`_snapshot_blocked_url` path.

## Step 6 — Fill a form ⭐ SECOND CRITICAL STEP

Forms go through a **different guard** than reading
(`_blocked_private_page_action`), so Step 5 passing does **not** imply this passes.

Pick any internal page with an input — a Confluence search box is fine.

**"Type 'onboarding' into the search box on this page and press enter."**

**Expected:** the text lands and the search runs.

**Capture on failure:** `Refusing to type on this page in this browser mode` —
that is the page-action guard. Note which action was refused (`click`, `type`,
`press`).

## Step 7 — Session reuse (claim #3, unmeasured)

Close the app. **Wait at least an hour** (longer is more informative), then
reopen and repeat Step 5 **without** signing in again.

**Expected (hoped):** it just works — cookies persisted in the profile directory.

**Record either way:** if you are re-prompted, note roughly how long the session
lasted. This is the design's open question and there is no prior data. If
Conditional Access re-prompts every time, unattended/scheduled use is off the
table until we solve it.

Also try `headed: false` after a successful sign-in, to see whether a headless
reuse survives Conditional Access.

## Step 8 — Daemon health on Windows (claim #4)

Run steps 5–6 **five or six times in a row**, in one session.

**Expected:** consistent behaviour.

**Capture on failure:** a request that hangs and never returns — that is
agent-browser's daemon wedging (a known earlier failure). `_run_daemon_hygiene`
runs `close --all` on every acquire as the mitigation, but it has never been
tested against the real failure. Note how many runs it took, and whether a full
app restart clears it. If it recurs, the fix is a health-probe + relaunch loop
(design §8 q3).

## Step 9 — Negative controls ⭐ DO NOT SKIP

These prove the security boundary still holds. If any **succeeds**, that is a
security regression and more serious than any failure above.

1. **"Open https://intranet.some-other-internal-host/ and read it."**
   → **MUST be blocked** (private, not in `trusted_origins`).
2. **"Open https://example.com and tell me the title."**
   → **MUST work** (public browsing unaffected).
3. If you can reach any other `10.x` host by hostname, try it.
   → **MUST be blocked.**

## Step 10 — Confirm LOOP24 too

Install LOOP24 v4.0.1 from <https://github.com/cmetech/loop24/releases> and repeat
Steps 2–6. Both brands ship the identical capability from the same `base` commit;
this confirms the brand overlay didn't disturb it.

---

## Reporting back

For each step: **PASS / FAIL / NOT RUN**, plus for any failure the exact error
text, which browser window appeared (Edge vs Chromium), and the step number.

The highest-value single data point is **Step 4** — everything else is downstream
of whether the enrolled browser authenticates through a CDP attach.

## What each outcome unblocks

| Outcome | Consequence |
|---|---|
| Steps 4–6 PASS | The capability works. Proceed to Task 7 (Confluence backend on the shared manager) and Option C (Browser Profiles panel with a Sign-in button). |
| Step 4 FAILS on certs | mTLS is not carrying through CDP. The launcher approach needs rework before anything else matters. |
| Step 6 FAILS only | Reading works, forms don't — an isolated page-action guard bug, fixable without touching the launcher. |
| Step 7 re-prompts constantly | Interactive use is fine; unattended/scheduled use needs a different approach. |
| Step 8 wedges | Needs the health-probe + relaunch loop before this is dependable on Windows. |
| Any Step 9 control SUCCEEDS | **Stop and report immediately** — the origin boundary is not holding. |
