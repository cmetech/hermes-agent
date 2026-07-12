# Running OTTO from source (no global install)

OTTO is a branded fork of the Hermes agent. This guide runs it straight from the
repo — CLI **and** desktop — in an **isolated** setup that does not touch any
existing `~/.hermes` install.

## What "isolated" means here

- **Own venv** — an editable install in a dedicated virtualenv, so your Python
  edits are live and deps don't collide with anything else.
- **Own home** — `HERMES_HOME=~/.otto` (seeded from `cli-config.yaml.example`,
  which already defaults `display.skin: otto` and `model.provider: otto`), so the
  OTTO skin + OTTO gateway are the defaults. Your real `~/.hermes` is untouched.
- **Own desktop identity** — `appId io.cmetech.otto`, so the OTTO desktop app
  installs/runs side-by-side with any Hermes desktop.

## Prereqs

- Node `^20.19 || >=22.12` + npm, `uv`, Python 3.11
- The **OTTO gateway running** (else the default model returns 503):
  `cd ../otto-gateway && make run` with `kiro-cli` on `PATH`.

## One-time setup

```bash
REPO="$(pwd)"                                  # run from the hermes-agent repo root

# 1. Isolated venv + editable install (live source)
uv venv ~/.hermes/venvs/otto-dev --python "$(command -v python3.11)"
uv pip install --python ~/.hermes/venvs/otto-dev/bin/python -e ".[all]"

# 2. Expose the venv to the desktop backend resolver as <repo>/venv
ln -s ~/.hermes/venvs/otto-dev "$REPO/venv"    # (git-excluded locally)

# 3. Seed the isolated OTTO home (does NOT overwrite ~/.hermes)
mkdir -p ~/.otto/{cron,sessions,logs,memories,skills}
cp cli-config.yaml.example ~/.otto/config.yaml
touch ~/.otto/.env
# only if your gateway sets AUTH_TOKEN (no-auth gateway needs nothing):
# echo 'OTTO_API_KEY=your-token' >> ~/.otto/.env

# 4. `otto` launcher on PATH (isolated home + pinned source root)
cat > ~/.local/bin/otto <<EOF
#!/usr/bin/env bash
unset PYTHONPATH PYTHONHOME
export HERMES_HOME="\${HERMES_HOME:-\$HOME/.otto}"
export HERMES_DESKTOP_HERMES_ROOT="\${HERMES_DESKTOP_HERMES_ROOT:-$REPO}"
exec "\$HOME/.hermes/venvs/otto-dev/bin/otto" "\$@"
EOF
chmod +x ~/.local/bin/otto

# 5. Desktop deps
( cd apps/desktop && npm install )
```

> Note: if a previous `otto` command exists on your PATH, back it up first
> (`mv ~/.local/bin/otto ~/.local/bin/otto_bkp`).

## Run it

**Desktop (the main event):** builds `apps/desktop` on first launch, then opens
Electron; the backend is spawned from this repo's `venv` automatically.

```bash
otto desktop
```

**CLI:**

```bash
otto                     # interactive — shows the OTTO banner/skin
otto chat -q "hello"
otto doctor              # should list "OTTO Gateway" as the provider
```

**Hot-reload desktop dev loop** (live renderer reload while editing UI):

```bash
cd apps/desktop
HERMES_HOME=~/.otto npm run dev
```

**Throwaway sandbox** (fresh temp home, distinct app name, won't fight a running
instance's single-instance lock):

```bash
HERMES_DEV_SANDBOX_NAME=OTTO scripts/dev-sandbox.sh otto desktop
```

## Updating from upstream

Python is an editable install, so `git pull` / merging `main` into `otto` makes
CLI changes live immediately. After a pull that touched the desktop, rebuild it
(or just run `otto desktop` — it rebuilds when the source hash changes). If deps
changed: `uv pip install --python ~/.hermes/venvs/otto-dev/bin/python -e ".[all]"`.

## Good to know

- `otto --version` still prints "Hermes Agent …" — the version string is an
  intentionally un-rebranded internal surface. The interactive banner, prompt,
  labels, and desktop identity all show **OTTO**.
- The desktop uses `HERMES_DESKTOP_HERMES_ROOT` (pinned to this repo by the
  launcher) so it always runs *this* branded checkout's backend, never another
  `hermes` on your PATH.

## Reverting

```bash
rm ~/.local/bin/otto && [ -e ~/.local/bin/otto_bkp ] && mv ~/.local/bin/otto_bkp ~/.local/bin/otto
rm "$REPO/venv"
rm -rf ~/.hermes/venvs/otto-dev ~/.otto      # optional: drop the venv + OTTO home
```
