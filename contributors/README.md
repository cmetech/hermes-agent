# Contributor email → GitHub login mappings

This directory replaces appending entries to `AUTHOR_MAP` in
`scripts/release.py`. The old dict caused constant merge conflicts when
several salvage PRs landed at once — every PR edited the same lines of the
same file. Here, **each mapping is its own file**, and file additions never
conflict.

## Adding a mapping

One file per commit-author email, under `emails/`:

```bash
python3 scripts/add_contributor.py <email> <github-login>
# or by hand:
echo "<github-login>" > contributors/emails/<email>
```

- File **name** = the exact commit-author email (as shown by `git log --format='%ae'`).
- File **content** = the GitHub login on the first non-comment line.
  Lines starting with `#` are comments (use them for the PR reference).

Example — `contributors/emails/jane.doe@example.com`:

```
janedoe
# PR #12345 salvage (gateway: fix session key routing)
```

## Rules

- Do NOT add new entries to `AUTHOR_MAP` in `scripts/release.py`. That dict
  is frozen legacy data; the release tooling merges it with this directory
  (directory entries win on duplicates).
- GitHub noreply emails (`<id>+<login>@users.noreply.github.com` and
  `<login>@users.noreply.github.com`) auto-resolve — no file needed.
- The `Contributor Attribution Check` CI job fails a PR whose commits carry
  an unmapped email; the failure message prints the exact command to run.

## Emails that differ only in case

Two emails differing only in case (the same Mac reporting its hostname as both
`Foo-Mac-mini.local` and `foo-Mac-mini.local`) would need two filenames
differing only in case. Git tracks those fine, but NTFS and APFS cannot check
out both, so a clone on Windows or macOS prints

```
warning: the following paths have collided (e.g. case-sensitive paths
on a case-insensitive filesystem) ...
```

and leaves one entry permanently `modified` in `git status`.

Those entries live in `contributors/case-collisions.tsv` instead — one
`<exact email><TAB><login>` row each. `add_contributor.py` routes them there
automatically, moving the already-present file into the same file, so you do
not need to notice the collision yourself.

The entries are **not** merged. Emails differing only in case can belong to
different people (`agent@Agents-Mac-mini.local` and
`agent@agents-Mac-mini.local` are two distinct GitHub users), so each exact
spelling keeps its own login. Release tooling matches the exact email first
and only falls back to a case-insensitive match when that email's casefolded
group maps to a single login.
