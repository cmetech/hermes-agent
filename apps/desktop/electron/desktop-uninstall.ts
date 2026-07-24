/**
 * desktop-uninstall.ts
 *
 * Pure, electron-free helpers for the desktop Chat GUI uninstaller. These map
 * the three user-facing uninstall modes to the `hermes uninstall` CLI flags,
 * resolve the running app bundle/exe so a detached cleanup script can remove
 * it after the app quits, and build that cleanup script for each OS.
 *
 * Kept standalone (no ` import 'electron'`) so it can be unit-tested with
 * `node --test` — same pattern as connection-config.ts / backend-probes.ts.
 * main.ts requires these and wires them into the electron-coupled IPC layer.
 *
 * The three modes mirror the CLI's options exactly:
 *   - 'gui'  → remove ONLY the Chat GUI, keep the agent + all user data.
 *              `hermes uninstall --gui --yes`
 *   - 'lite' → remove the GUI + agent code, KEEP user data (config / sessions
 *              / .env) for a future reinstall. `hermes uninstall --yes`
 *   - 'full' → remove everything: GUI + agent + all user data.
 *              `hermes uninstall --full --yes`
 *
 * Why a detached cleanup script: 'lite'/'full' delete the very venv the
 * `hermes` command runs from, and every mode may need to delete the running
 * app bundle (locked on macOS/Windows while the process is alive). So we hand
 * the work to a detached child that waits for this app's PID to exit, runs the
 * Python uninstall, then removes the app bundle — then the app quits. Same
 * shape as the self-update swap-and-relaunch flow already in main.ts.
 */

import path from 'node:path'

const UNINSTALL_MODES = ['gui', 'lite', 'full']

/**
 * Map an uninstall mode to the `python -m hermes_cli.uninstall` argv (after the
 * python executable). Uses the dedicated lightweight module entrypoint (not
 * `hermes_cli.main`) so it can run under a system Python OUTSIDE the venv that
 * lite/full delete — see the Finding-3 note in buildWindowsCleanupScript.
 * Throws on an unknown mode so a typo can't silently become a full wipe.
 */
function uninstallArgsForMode(mode) {
  if (!UNINSTALL_MODES.includes(mode)) {
    throw new Error(`Unknown uninstall mode: ${mode}`)
  }

  return ['-m', 'hermes_cli.uninstall', '--mode', mode]
}

/** True when `mode` removes the agent (lite/full), false for gui-only. */
function modeRemovesAgent(mode) {
  return mode === 'lite' || mode === 'full'
}

/** True when `mode` removes user data (full only). */
function modeRemovesUserData(mode) {
  return mode === 'full'
}

/**
 * Resolve the on-disk app bundle/dir to remove for the running desktop app,
 * given the path to the running executable (`process.execPath`) and platform.
 *
 *   macOS:   …/Hermes.app/Contents/MacOS/Hermes  → …/Hermes.app
 *   Windows: …\Hermes\Hermes.exe                 → …\Hermes  (install dir)
 *   Linux:   AppImage → the APPIMAGE env path; unpacked → the *-unpacked dir
 *
 * Returns null when we can't confidently identify a removable bundle (e.g.
 * running from a dev checkout, or a system-package install we must not rmtree).
 */
function resolveRemovableAppPath(execPath, platform, env: any = {}) {
  const exe = String(execPath || '')

  if (!exe) {
    return null
  }

  // Use the path flavor that matches the TARGET platform, not the host running
  // this code — so the Windows branch parses backslash paths correctly even
  // when these pure helpers are unit-tested on Linux/macOS CI.
  const p = platform === 'win32' ? path.win32 : path.posix

  if (platform === 'darwin') {
    // …/Hermes.app/Contents/MacOS/Hermes → strip 3 segments to the .app
    const macOsDir = p.dirname(exe) // …/Contents/MacOS
    const contents = p.dirname(macOsDir) // …/Contents
    const appBundle = p.dirname(contents) // …/Hermes.app

    if (appBundle.endsWith('.app')) {
      return appBundle
    }

    return null
  }

  if (platform === 'win32') {
    // NSIS per-user installs Hermes.exe directly in the install dir.
    const dir = p.dirname(exe)

    if (/[\\/]Hermes$/i.test(dir) || /[\\/]hermes-desktop$/i.test(dir)) {
      return dir
    }

    return null
  }

  // Linux: an AppImage exposes its own path via the APPIMAGE env var.
  if (env.APPIMAGE) {
    return env.APPIMAGE
  }

  // Unpacked electron-builder tree: …/linux-unpacked/hermes
  const dir = p.dirname(exe)

  if (/-unpacked$/.test(dir)) {
    return dir
  }

  return null
}

/**
 * Should we even try to remove the running app bundle from a cleanup script?
 * Only when packaged AND we resolved a concrete removable path. Dev runs
 * (electron from node_modules) and system-package installs return null above
 * and are left to the OS package manager.
 */
function shouldRemoveAppBundle(isPackaged, appPath) {
  return Boolean(isPackaged) && Boolean(appPath)
}

/**
 * Build a POSIX cleanup shell script (macOS / Linux). It:
 *   1. waits (bounded ~30s) for the desktop PID to exit (venv/bundle unlock),
 *   2. runs the Python uninstall module with the mode,
 *   3. removes the agent dir (if `removeAgent`), the home dir (if
 *      `removeUserData`), then the app bundle if one was resolved,
 *   4. validates the paths that should now be gone and writes a result log,
 *   5. shows a native dialog (macOS `osascript`) reporting success/leftovers.
 *
 * `pythonExe` should be a Python OUTSIDE the venv for lite/full (the venv is
 * being deleted); `pythonPath` is prepended to PYTHONPATH so `import hermes_cli`
 * resolves from the agent source. `q()` single-quote-escapes for the shell
 * (closes-escapes-reopens any embedded apostrophe), defending against spaces.
 *
 * The outer script does the destructive removal itself (rather than leaving it
 * to the Python uninstaller) because it runs from `bash`, outside whatever
 * directory is being deleted — the Python step can still run first for its
 * non-filesystem cleanup, but the actual rm -rf of the agent/home dirs happens
 * here where nothing has that path open.
 */
function buildPosixCleanupScript({
  desktopPid,
  pythonExe,
  pythonPath,
  agentRoot,
  uninstallArgs,
  appPath,
  hermesHome,
  removeUserData = false,
  removeAgent = false,
  productName = 'OTTO'
}) {
  const q = s => `'${String(s).replace(/'/g, `'\\''`)}'`

  const lines = [
    '#!/bin/bash',
    'set -u',
    '# Wait (up to ~30s) for the desktop process to exit so the venv python',
    '# and the app bundle are no longer in use.',
    `pid=${Number(desktopPid) || 0}`,
    'if [ "$pid" -gt 0 ]; then',
    '  for _ in $(seq 1 60); do',
    '    kill -0 "$pid" 2>/dev/null || break',
    '    sleep 0.5',
    '  done',
    'fi',
    `export HERMES_HOME=${q(hermesHome)}`
  ]

  if (pythonPath) {
    lines.push(`export PYTHONPATH=${q(pythonPath)}\${PYTHONPATH:+:$PYTHONPATH}`)
  }

  lines.push(`cd ${q(agentRoot)} 2>/dev/null || true`, `${q(pythonExe)} ${uninstallArgs.map(q).join(' ')} || true`)

  // Destructive removal happens here, in the detached outer script — not the
  // Python uninstaller — since this script runs outside any dir being deleted.
  if (removeAgent) {
    lines.push(`rm -rf ${q(agentRoot)} || true`)
  }

  if (removeUserData) {
    lines.push(`rm -rf ${q(hermesHome)} || true`)
  }

  if (appPath) {
    lines.push(`rm -rf ${q(appPath)} || true`)
  }

  // Validation pass: check every path that should now be gone, write a result
  // log to temp, and surface a native dialog (macOS) with success or leftovers.
  const checks = [appPath, removeAgent ? agentRoot : null, removeUserData ? hermesHome : null].filter(Boolean)

  const logSlug = String(productName).toLowerCase()

  lines.push(
    'LEFT=""',
    `RESULT="$TMPDIR/${logSlug}-uninstall-result.log"`,
    `: > "$RESULT" 2>/dev/null || RESULT=/tmp/${logSlug}-uninstall-result.log`
  )

  for (const p of checks) {
    lines.push(
      `if [ -e ${q(p)} ]; then LEFT="$LEFT ${p}"; echo "LEFT: ${p}" >> "$RESULT"; else echo "GONE: ${p}" >> "$RESULT"; fi`
    )
  }

  lines.push(
    `if [ -n "$LEFT" ]; then MSG="${productName} was removed, but could not delete:$LEFT"; else MSG="${productName} was fully removed."; fi`,
    `if [ "$(uname)" = "Darwin" ]; then osascript -e "display dialog \\"$MSG\\" buttons {\\"OK\\"} with title \\"${productName} uninstall\\"" >/dev/null 2>&1 || true; fi`
  )

  // Self-delete the script LAST.
  lines.push('rm -f "$0" 2>/dev/null || true')
  lines.push('')

  return lines.join('\n')
}

/**
 * Build a Windows cleanup batch script. Same three steps, cmd.exe flavored.
 *
 * Finding 3 (venv self-deletion): for lite/full the agent uninstall rmtree's
 * the venv that contains `python.exe`. A running .exe is mandatory-locked on
 * Windows, so running the uninstall from the venv's OWN python half-fails. The
 * desktop passes a system Python (findSystemPython) as `pythonExe` for those
 * modes + `pythonPath`=agentRoot so `import hermes_cli` resolves from source
 * while the venv is torn down. gui-only doesn't touch the venv, so it can use
 * either interpreter.
 *
 * Wait-loop: bounded (matches POSIX's ~30s cap) so a never-exiting / mismatched
 * PID can't wedge the cleanup forever. The `/FI "PID eq"` filter is an EXACT
 * match, so no redundant `| find` (which would substring-match 99→990).
 *
 * Removal: even after the desktop PID is gone, Windows releases directory
 * handles lazily, so a single `rmdir /s /q` can half-fail — retry up to 10x.
 *
 * The script itself does the destructive removal (agent dir / home dir), not
 * the Python uninstaller — on Windows the Python step runs from the venv
 * inside the very dir(s) being deleted, so it can't reliably remove them. The
 * outer `.cmd` runs from `cmd.exe`, outside those dirs, so it can. After the
 * removals it validates the paths that should be gone, writes a result log to
 * temp, and pops a native `mshta` dialog reporting success or the leftovers.
 */
function buildWindowsCleanupScript({
  desktopPid,
  pythonExe,
  pythonPath,
  agentRoot,
  uninstallArgs,
  appPath,
  hermesHome,
  removeUserData = false,
  removeAgent = false,
  productName = 'OTTO'
}) {
  const pid = Number(desktopPid) || 0
  // cmd.exe has no string escaping inside quotes; strip embedded quotes (paths
  // under %LOCALAPPDATA% never contain them). `&`/`^` in a path would still be
  // a problem, but Hermes install paths don't use them.
  const q = s => `"${String(s).replace(/"/g, '')}"`
  const mode = (() => {
    const idx = (uninstallArgs || []).indexOf('--mode')
    return idx >= 0 ? uninstallArgs[idx + 1] : ''
  })()

  const logSlug = String(productName).toLowerCase()

  const lines = [
    '@echo off',
    'setlocal enableextensions',
    `set "HERMES_HOME=${String(hermesHome).replace(/"/g, '')}"`,
    `set "PID=${pid}"`,
    `set "MODE=${String(mode).replace(/"/g, '')}"`,
    // Visible banner. The window is intentionally shown (not minimized) so the
    // user can see progress — but a click inside a QuickEdit console pauses the
    // batch until a key is pressed, which looks like a hang mid-delete. Warn.
    'echo(',
    `echo   ${productName} uninstall in progress`,
    'echo   Please wait. Removing large folders can take a minute.',
    'echo   Do NOT click inside this window - it closes automatically.',
    'echo('
  ]

  if (pythonPath) {
    lines.push(`set "PYTHONPATH=${String(pythonPath).replace(/"/g, '')};%PYTHONPATH%"`)
  }

  lines.push(
    `echo   Waiting for ${productName} to close...`,
    'set /a waited=0',
    ':waitloop',
    'rem /FI "PID eq %PID%" is an EXACT filter — tasklist outputs the one task',
    'rem row for that PID, or "INFO: No tasks..." otherwise. /NH drops the',
    'rem header; findstr matches the PID as a whole space-delimited token so',
    'rem PID 99 cannot match 990 (the substring trap of a bare `find`).',
    'tasklist /NH /FI "PID eq %PID%" 2>nul | findstr /r /c:" %PID% " >nul',
    'if %ERRORLEVEL% neq 0 goto waited_done',
    'set /a waited+=1',
    'if %waited% geq 60 goto waited_done',
    'timeout /t 1 /nobreak >nul',
    'goto waitloop',
    ':waited_done',
    'echo   Removing runtime and cleaning up...',
    `cd /d ${q(agentRoot)}`,
    `${q(pythonExe)} ${uninstallArgs.map(q).join(' ')}`,
    // Leave the tree we are about to delete. cmd.exe re-reads the batch and
    // resolves paths against the CWD as it runs; if the CWD (agentRoot) or its
    // parent (hermesHome) is deleted out from under it, the run aborts with
    // "The batch file cannot be found" BEFORE the final dialog + self-delete.
    // Parking in %SystemRoot% also un-pins the tree so it can be removed.
    'cd /d "%SystemRoot%"'
  )

  // Destructive removal happens here, in the detached outer script — not the
  // Python uninstaller — since a running .exe/venv locks its own dir on
  // Windows. Retry loop: Windows releases directory handles lazily.
  //
  // Speed: a plain `rmdir /s /q` on the agent clone (which holds a ~1GB
  // node_modules with tens of thousands of tiny, deeply-nested files) can take
  // minutes on Windows. `robocopy /mir` from an EMPTY source empties the tree
  // far faster (parallel, no shell per-file overhead); the follow-up rmdir then
  // removes the now-empty skeleton instantly. Each step echoes so the (visible,
  // non-minimized) window never looks frozen.
  const hasTreeRemoval = removeAgent || removeUserData || Boolean(appPath)

  if (hasTreeRemoval) {
    lines.push(
      // Kill any lingering git holding a just-cloned .git pack open, else its
      // pack-*.idx stays locked (WinError 5) and blocks the tree removal.
      'taskkill /f /im git.exe /t >nul 2>&1',
      `set "EMPTYDIR=%TEMP%\\${logSlug}-uninstall-empty-%RANDOM%"`,
      'mkdir "%EMPTYDIR%" >nul 2>&1'
    )
  }

  const rmTreeFast = (target, label, human) => [
    `if not exist ${q(target)} goto ${label}done`,
    `echo   Removing ${human}...`,
    // Clear read-only/hidden/system attrs first — a stubborn .git pack (WinError
    // 5 "access denied") otherwise blocks the whole tree from being removed.
    `attrib -r -h -s /s /d ${q(target)} >nul 2>&1`,
    `set /a ${label}=0`,
    `:${label}loop`,
    `if not exist ${q(target)} goto ${label}done`,
    `robocopy "%EMPTYDIR%" ${q(target)} /mir /nfl /ndl /njh /njs /nc /ns /r:1 /w:0 >nul 2>&1`,
    `rmdir /s /q ${q(target)} >nul 2>&1`,
    `if not exist ${q(target)} goto ${label}done`,
    `set /a ${label}+=1`,
    `if %${label}% geq 10 goto ${label}done`,
    'timeout /t 1 /nobreak >nul',
    `goto ${label}loop`,
    `:${label}done`
  ]

  if (removeAgent) {
    lines.push(...rmTreeFast(agentRoot, 'rmagent', 'runtime files'))
  }

  if (removeUserData) {
    lines.push(...rmTreeFast(hermesHome, 'rmhome', 'user data'))
  }

  if (appPath) {
    lines.push(
      `if not exist ${q(appPath)} goto rmdone`,
      'echo   Removing application files...',
      `attrib -r -h -s /s /d ${q(appPath)} >nul 2>&1`,
      'set /a tries=0',
      ':rmloop',
      `if not exist ${q(appPath)} goto rmdone`,
      `robocopy "%EMPTYDIR%" ${q(appPath)} /mir /nfl /ndl /njh /njs /nc /ns /r:1 /w:0 >nul 2>&1`,
      `rmdir /s /q ${q(appPath)} >nul 2>&1`,
      `if not exist ${q(appPath)} goto rmdone`,
      'set /a tries+=1',
      'if %tries% geq 10 goto rmdone',
      'timeout /t 1 /nobreak >nul',
      'goto rmloop',
      ':rmdone'
    )
  }

  if (hasTreeRemoval) {
    lines.push('rmdir /s /q "%EMPTYDIR%" >nul 2>&1')
  }

  // Validation pass: check every path that should now be gone, write a result
  // log to temp, and pop a native modal dialog (via mshta) with success or
  // the leftover list — the app has already quit, so this is the only UI.
  // (logSlug is computed once at the top of this function.)
  const resultLog = `%TEMP%\\${logSlug}-uninstall-result.log`

  lines.push('set "LEFT="', `> "${resultLog}" echo ${productName} uninstall (%MODE%) result:`)

  const checkPaths = [appPath, removeAgent ? agentRoot : null, removeUserData ? hermesHome : null].filter(Boolean)

  for (const p of checkPaths) {
    const plain = String(p).replace(/"/g, '')

    lines.push(
      `if exist ${q(p)} ( set "LEFT=%LEFT% ${plain}" & >> "${resultLog}" echo LEFT: ${plain} ) else ( >> "${resultLog}" echo GONE: ${plain} )`
    )
  }

  lines.push(
    `if defined LEFT ( set "MSG=${productName} was removed, but these could not be deleted:%LEFT%  (delete them manually)" ) else ( set "MSG=${productName} was fully removed." )`,
    // JScript treats a bare `\x` as a hex-escape introducer, so a leftover path
    // containing e.g. `\x` (C:\Users\x\...) inside the single-quoted JS string
    // below would throw "Invalid hexadecimal escape sequence" and the dialog
    // would never show. Build a JS-string-safe copy by doubling every
    // backslash (cmd substring substitution) and interpolate THAT into the
    // JScript literal instead of the raw %MSG%.
    'set "MSGJS=%MSG:\\=\\\\%"',
    // mshta shows a modal even though the app has quit; escape quotes for VBScript.
    `mshta "javascript:var m=new ActiveXObject('WScript.Shell');m.Popup('%MSGJS%',0,'${productName} uninstall',64);close();" 2>nul`
  )

  // Self-delete the script LAST. `(goto) 2>nul` pops the batch execution
  // context (its intentional error is swallowed) BEFORE `del` removes the file,
  // so cmd is no longer reading this script when it vanishes. A bare
  // `del "%~f0"` with ANY line after it makes cmd delete the batch, fail to read
  // the next line ("The batch file cannot be found"), and drop to an interactive
  // prompt instead of closing the window. Nothing may follow this line.
  lines.push('(goto) 2>nul & del "%~f0"')

  return lines.join('\r\n')
}

/**
 * argv for launching the Windows cleanup .cmd via cmd.exe. Uses `start ""` to
 * spawn a NORMAL, visible console window (was `start "" /min` — a minimized
 * window the user had to hunt for, and clicking it to view triggered a
 * QuickEdit-mode output pause that looked like a hang). The empty `""` is the
 * (required) window-title placeholder so `start` doesn't treat the script path
 * as the title; the script prints its own banner for identity.
 */
function windowsCleanupRunnerArgs(scriptPath) {
  return ['/c', 'start', '""', scriptPath]
}

export {
  buildPosixCleanupScript,
  buildWindowsCleanupScript,
  modeRemovesAgent,
  modeRemovesUserData,
  resolveRemovableAppPath,
  shouldRemoveAppBundle,
  UNINSTALL_MODES,
  uninstallArgsForMode,
  windowsCleanupRunnerArgs
}
