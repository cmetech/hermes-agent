/**
 * Tests for electron/desktop-uninstall.ts.
 *
 * Run with: node --test electron/desktop-uninstall.test.ts
 * (Wired into npm test:desktop:platforms in package.json.)
 *
 * These are the pure helpers behind the desktop Chat GUI uninstaller: the
 * mode → CLI-flag mapping, the running-app-bundle resolution per OS, and the
 * cleanup-script builders (POSIX + Windows).
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildPosixCleanupScript,
  buildWindowsCleanupScript,
  modeRemovesAgent,
  modeRemovesUserData,
  resolveRemovableAppPath,
  shouldRemoveAppBundle,
  UNINSTALL_MODES,
  uninstallArgsForMode,
  windowsCleanupRunnerArgs
} from './desktop-uninstall'

// --- uninstallArgsForMode ---

test('uninstallArgsForMode maps each mode to the module-runner argv', () => {
  assert.deepEqual(uninstallArgsForMode('gui'), ['-m', 'hermes_cli.uninstall', '--mode', 'gui'])
  assert.deepEqual(uninstallArgsForMode('lite'), ['-m', 'hermes_cli.uninstall', '--mode', 'lite'])
  assert.deepEqual(uninstallArgsForMode('full'), ['-m', 'hermes_cli.uninstall', '--mode', 'full'])
})

test('uninstallArgsForMode throws on an unknown mode (no silent full wipe)', () => {
  assert.throws(() => uninstallArgsForMode('nuke'), /Unknown uninstall mode/)
  assert.throws(() => uninstallArgsForMode(''), /Unknown uninstall mode/)
})

test('UNINSTALL_MODES lists exactly the three supported modes', () => {
  assert.deepEqual([...UNINSTALL_MODES].sort(), ['full', 'gui', 'lite'])
})

// --- modeRemovesAgent / modeRemovesUserData ---

test('mode predicates classify what each mode removes', () => {
  assert.equal(modeRemovesAgent('gui'), false)
  assert.equal(modeRemovesAgent('lite'), true)
  assert.equal(modeRemovesAgent('full'), true)

  assert.equal(modeRemovesUserData('gui'), false)
  assert.equal(modeRemovesUserData('lite'), false)
  assert.equal(modeRemovesUserData('full'), true)
})

// --- resolveRemovableAppPath ---

test('resolveRemovableAppPath finds the .app bundle on macOS', () => {
  assert.equal(
    resolveRemovableAppPath('/Applications/Hermes.app/Contents/MacOS/Hermes', 'darwin'),
    '/Applications/Hermes.app'
  )
  assert.equal(
    resolveRemovableAppPath('/Users/x/Applications/Hermes.app/Contents/MacOS/Hermes', 'darwin'),
    '/Users/x/Applications/Hermes.app'
  )
})

test('resolveRemovableAppPath: dev-run .app resolves (safety is shouldRemoveAppBundle, not null)', () => {
  // A dev run from node_modules' Electron DOES resolve to a .app — the real
  // dev-run safety gate is shouldRemoveAppBundle(isPackaged=false,...), not a
  // null return here. This test documents that contract.
  assert.equal(
    resolveRemovableAppPath('/repo/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron', 'darwin'),
    '/repo/node_modules/electron/dist/Electron.app'
  )
  assert.equal(shouldRemoveAppBundle(false, '/repo/node_modules/electron/dist/Electron.app'), false)
  // A bare path with no .app ancestor → null.
  assert.equal(resolveRemovableAppPath('/usr/bin/electron', 'darwin'), null)
})

test('resolveRemovableAppPath finds the install dir on Windows', () => {
  assert.equal(
    resolveRemovableAppPath('C:\\Users\\x\\AppData\\Local\\Programs\\Hermes\\Hermes.exe', 'win32'),
    'C:\\Users\\x\\AppData\\Local\\Programs\\Hermes'
  )
  assert.equal(
    resolveRemovableAppPath('C:\\Users\\x\\AppData\\Local\\hermes-desktop\\Hermes.exe', 'win32'),
    'C:\\Users\\x\\AppData\\Local\\hermes-desktop'
  )
})

test('resolveRemovableAppPath returns null for an unrecognized Windows dir', () => {
  assert.equal(resolveRemovableAppPath('C:\\Temp\\foo\\Hermes.exe', 'win32'), null)
})

test('resolveRemovableAppPath uses APPIMAGE on Linux when set', () => {
  assert.equal(
    resolveRemovableAppPath('/tmp/.mount_HermesXXXX/hermes', 'linux', { APPIMAGE: '/home/x/Apps/Hermes.AppImage' }),
    '/home/x/Apps/Hermes.AppImage'
  )
})

test('resolveRemovableAppPath finds the unpacked dir on Linux', () => {
  assert.equal(resolveRemovableAppPath('/opt/hermes/linux-unpacked/hermes', 'linux', {}), '/opt/hermes/linux-unpacked')
  // A system-package install (/usr/bin) → null, left to apt/dnf.
  assert.equal(resolveRemovableAppPath('/usr/bin/hermes', 'linux', {}), null)
})

test('resolveRemovableAppPath returns null for an empty exe path', () => {
  assert.equal(resolveRemovableAppPath('', 'darwin'), null)
  assert.equal(resolveRemovableAppPath(null, 'win32'), null)
})

// --- shouldRemoveAppBundle ---

test('shouldRemoveAppBundle requires packaged AND a resolved path', () => {
  assert.equal(shouldRemoveAppBundle(true, '/Applications/Hermes.app'), true)
  assert.equal(shouldRemoveAppBundle(false, '/Applications/Hermes.app'), false)
  assert.equal(shouldRemoveAppBundle(true, null), false)
  assert.equal(shouldRemoveAppBundle(false, null), false)
})

// --- buildPosixCleanupScript ---

test('buildPosixCleanupScript waits for the PID, runs the uninstall module, removes bundle', () => {
  const script = buildPosixCleanupScript({
    desktopPid: 4321,
    pythonExe: '/home/x/.hermes/hermes-agent/venv/bin/python',
    pythonPath: null,
    agentRoot: '/home/x/.hermes/hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'gui'],
    appPath: '/opt/hermes/linux-unpacked',
    hermesHome: '/home/x/.hermes'
  })

  assert.match(script, /^#!\/bin\/bash/)
  assert.match(script, /pid=4321/)
  assert.match(script, /kill -0 "\$pid"/)
  // bounded wait (~30s), not unbounded
  assert.match(script, /seq 1 60/)
  assert.match(script, /'-m' 'hermes_cli\.uninstall' '--mode' 'gui'/)
  assert.match(script, /rm -rf '\/opt\/hermes\/linux-unpacked'/)
  assert.match(script, /export HERMES_HOME='\/home\/x\/\.hermes'/)
})

test('buildPosixCleanupScript exports PYTHONPATH when pythonPath is set (lite/full)', () => {
  const script = buildPosixCleanupScript({
    desktopPid: 1,
    pythonExe: '/usr/bin/python3',
    pythonPath: '/home/x/.hermes/hermes-agent',
    agentRoot: '/home/x/.hermes/hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: null,
    hermesHome: '/home/x/.hermes'
  })

  // System python + source on PYTHONPATH so import hermes_cli works while the
  // venv is torn down.
  assert.match(script, /export PYTHONPATH='\/home\/x\/\.hermes\/hermes-agent'/)
  assert.match(script, /'\/usr\/bin\/python3' '-m' 'hermes_cli\.uninstall' '--mode' 'full'/)
})

test('buildPosixCleanupScript omits PYTHONPATH when pythonPath is null (gui)', () => {
  const script = buildPosixCleanupScript({
    desktopPid: 1,
    pythonExe: '/p/python',
    pythonPath: null,
    agentRoot: '/a',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'gui'],
    appPath: null,
    hermesHome: '/h'
  })

  assert.doesNotMatch(script, /export PYTHONPATH/)
})

test('buildPosixCleanupScript omits the bundle rm when appPath is null', () => {
  const script = buildPosixCleanupScript({
    desktopPid: 1,
    pythonExe: '/p/python',
    pythonPath: null,
    agentRoot: '/a',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'lite'],
    appPath: null,
    hermesHome: '/h'
  })

  assert.doesNotMatch(script, /rm -rf '\//)
  // Still runs the uninstall.
  assert.match(script, /'-m' 'hermes_cli\.uninstall' '--mode' 'lite'/)
})

test('buildPosixCleanupScript single-quote-escapes paths with apostrophes', () => {
  const script = buildPosixCleanupScript({
    desktopPid: 1,
    pythonExe: "/home/o'brien/python",
    pythonPath: null,
    agentRoot: '/a',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'gui'],
    appPath: null,
    hermesHome: '/h'
  })

  // The apostrophe is closed-escaped-reopened so the shell sees the literal.
  assert.match(script, /'\/home\/o'\\''brien\/python'/)
})

// --- buildWindowsCleanupScript ---

test('buildWindowsCleanupScript waits (bounded) for PID, runs uninstall, rmdir bundle', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 9988,
    pythonExe: 'C:\\Python313\\python.exe',
    pythonPath: 'C:\\hermes',
    agentRoot: 'C:\\hermes',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: 'C:\\Users\\x\\AppData\\Local\\Programs\\Hermes',
    hermesHome: 'C:\\Users\\x\\AppData\\Local\\hermes'
  })

  assert.match(script, /@echo off/)
  assert.match(script, /set "PID=9988"/)
  // PYTHONPATH set so a system python can import hermes_cli from source.
  assert.match(script, /set "PYTHONPATH=C:\\hermes;%PYTHONPATH%"/)
  assert.match(script, /"C:\\Python313\\python.exe" "-m" "hermes_cli\.uninstall" "--mode" "full"/)
  // Bounded wait-loop (no infinite loop), whole-token PID match (no substring).
  assert.match(script, /if %waited% geq 60 goto waited_done/)
  assert.match(script, /findstr \/r \/c:" %PID% "/)
  assert.doesNotMatch(script, /find "%PID%"/) // the old substring-prone form is gone
  // Removal is a retry loop (Windows releases dir handles lazily).
  assert.match(script, /:rmloop/)
  assert.match(script, /rmdir \/s \/q "C:\\Users\\x\\AppData\\Local\\Programs\\Hermes" >nul 2>&1/)
  assert.match(script, /if %tries% geq 10 goto rmdone/)
  assert.match(script, /del "%~f0"/)
})

test('buildWindowsCleanupScript omits PYTHONPATH + rmdir when not needed (gui, no bundle)', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 2,
    pythonExe: 'C:\\h\\venv\\Scripts\\python.exe',
    pythonPath: null,
    agentRoot: 'C:\\h',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'gui'],
    appPath: null,
    hermesHome: 'C:\\h'
  })

  assert.doesNotMatch(script, /rmdir/)
  assert.doesNotMatch(script, /set "PYTHONPATH=/)
})

// --- outer-script removal + validation + native dialog (Task 2) ---

test('windows full script removes home + agent + app, validates, and shows a dialog', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 123,
    pythonExe: 'py.exe',
    pythonPath: null,
    agentRoot: 'C:\\h\\hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: 'C:\\Users\\x\\AppData\\Local\\Programs\\OTTO',
    hermesHome: 'C:\\Users\\x\\AppData\\Local\\hermes',
    removeUserData: true,
    removeAgent: true
  })
  assert.match(script, /rmdir \/s \/q "C:\\Users\\x\\AppData\\Local\\hermes"/) // home removed by the script
  assert.match(script, /rmdir \/s \/q "C:\\h\\hermes-agent"/) // agent removed
  assert.match(script, /mshta/) // native dialog
  assert.match(script, /otto-uninstall-result\.log/) // result log
})

test('windows lite script keeps the home (only agent + app removed)', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 1,
    pythonExe: 'py.exe',
    pythonPath: null,
    agentRoot: 'C:\\h\\hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'lite'],
    appPath: 'C:\\p\\OTTO',
    hermesHome: 'C:\\h',
    removeUserData: false,
    removeAgent: true
  })
  assert.doesNotMatch(script, /rmdir \/s \/q "C:\\h" /) // home NOT removed
  assert.match(script, /rmdir \/s \/q "C:\\h\\hermes-agent"/)
})

test('windows full script dialog is JS-string-safe for a \\x path (Finding: mshta hex-escape crash)', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 42,
    pythonExe: 'py.exe',
    pythonPath: null,
    agentRoot: 'C:\\h\\hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: 'C:\\Users\\x\\AppData\\Local\\Programs\\OTTO',
    // The fixture that triggers the bug: a leftover path containing `\x`,
    // which JScript reads as a hex-escape introducer inside a plain '%MSG%'.
    hermesHome: 'C:\\Users\\x\\AppData\\Local\\hermes',
    removeUserData: true,
    removeAgent: true
  })

  // A JS-string-safe copy of MSG (every backslash doubled) must be built...
  assert.match(script, /set "MSGJS=%MSG:\\=\\\\%"/)
  // ...and the mshta/JScript line must interpolate %MSGJS%, not a raw %MSG%
  // (which would embed single backslashes — e.g. `\x` — into the JS string
  // literal and throw "Invalid hexadecimal escape sequence").
  assert.match(script, /Popup\('%MSGJS%',0,'OTTO uninstall',64\)/)
  assert.doesNotMatch(script, /Popup\('%MSG%'/)
})

test('windows script uses the passed productName for the log path, header, and dialog title', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 7,
    pythonExe: 'py.exe',
    pythonPath: null,
    agentRoot: 'C:\\h\\hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: 'C:\\Users\\x\\AppData\\Local\\Programs\\LOOP24',
    hermesHome: 'C:\\Users\\x\\AppData\\Local\\loop24',
    removeUserData: true,
    removeAgent: true,
    productName: 'LOOP24'
  })
  assert.match(script, /loop24-uninstall-result\.log/)
  assert.match(script, /LOOP24 uninstall/)
  assert.doesNotMatch(script, /OTTO uninstall/)
})

test('posix script uses the passed productName for the log path, header, and dialog title', () => {
  const script = buildPosixCleanupScript({
    desktopPid: 7,
    pythonExe: '/usr/bin/python3',
    pythonPath: null,
    agentRoot: '/Users/x/.loop24/hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: '/Applications/LOOP24.app',
    hermesHome: '/Users/x/.loop24',
    removeUserData: true,
    removeAgent: true,
    productName: 'LOOP24'
  })
  assert.match(script, /loop24-uninstall-result\.log/)
  assert.match(script, /LOOP24 uninstall/)
  assert.doesNotMatch(script, /OTTO uninstall/)
})

test('scripts default productName to OTTO when omitted (backward compatible)', () => {
  const winScript = buildWindowsCleanupScript({
    desktopPid: 1,
    pythonExe: 'py.exe',
    pythonPath: null,
    agentRoot: 'C:\\h',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'gui'],
    appPath: null,
    hermesHome: 'C:\\h'
  })
  assert.match(winScript, /otto-uninstall-result\.log/)
  assert.match(winScript, /OTTO uninstall/)

  const posixScript = buildPosixCleanupScript({
    desktopPid: 1,
    pythonExe: '/usr/bin/python3',
    pythonPath: null,
    agentRoot: '/a',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'gui'],
    appPath: null,
    hermesHome: '/h'
  })
  assert.match(posixScript, /otto-uninstall-result\.log/)
  assert.match(posixScript, /OTTO uninstall/)
})

test('posix full script rm -rf home + agent and shows osascript dialog on mac', () => {
  const script = buildPosixCleanupScript({
    desktopPid: 5,
    pythonExe: '/usr/bin/python3',
    pythonPath: null,
    agentRoot: '/Users/x/.hermes/hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: '/Applications/OTTO.app',
    hermesHome: '/Users/x/.hermes',
    removeUserData: true,
    removeAgent: true
  })
  assert.match(script, /rm -rf '\/Users\/x\/\.hermes'/)
  assert.match(script, /osascript/)
})

// --- Option A: launcher window is NOT minimized (was `start "" /min`) ---

test('windowsCleanupRunnerArgs opens a normal (non-minimized) console for the script', () => {
  const args = windowsCleanupRunnerArgs('C:\\Temp\\otto-uninstall-1.cmd')
  // cmd.exe /c start "" <script>  — a normal window, no /min
  assert.deepEqual(args, ['/c', 'start', '""', 'C:\\Temp\\otto-uninstall-1.cmd'])
  assert.ok(!args.includes('/min'), 'must NOT launch the progress window minimized')
})

// --- Option A: visible progress so a silent rmdir never looks hung ---

test('windows full script prints a do-not-click banner + per-step progress', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 123,
    pythonExe: 'py.exe',
    pythonPath: null,
    agentRoot: 'C:\\h\\hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: 'C:\\Users\\x\\AppData\\Local\\Programs\\OTTO',
    hermesHome: 'C:\\Users\\x\\AppData\\Local\\hermes',
    removeUserData: true,
    removeAgent: true
  })
  assert.match(script, /Do NOT click/i) // QuickEdit-pause warning banner
  assert.match(script, /echo\s+Removing/i) // a status line before the slow deletes
})

// --- Option C: robocopy empty-mirror fast-deletes large trees (node_modules) ---

test('windows full script fast-deletes large trees via a robocopy empty-mirror', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 123,
    pythonExe: 'py.exe',
    pythonPath: null,
    agentRoot: 'C:\\h\\hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: 'C:\\Users\\x\\AppData\\Local\\Programs\\OTTO',
    hermesHome: 'C:\\Users\\x\\AppData\\Local\\hermes',
    removeUserData: true,
    removeAgent: true
  })
  // an empty scratch dir is made, then mirrored over each big tree to empty it fast
  assert.match(script, /mkdir "%EMPTYDIR%"/)
  assert.match(script, /robocopy "%EMPTYDIR%" "C:\\h\\hermes-agent" \/mir/)
  // then the emptied tree is still removed with rmdir (kept for compatibility + final sweep)
  assert.match(script, /rmdir \/s \/q "C:\\h\\hermes-agent" >nul 2>&1/)
  // the scratch dir is cleaned up afterward
  assert.match(script, /rmdir \/s \/q "%EMPTYDIR%"/)
})

test('windows gui script (nothing to remove) adds no robocopy or scratch dir', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 2,
    pythonExe: 'C:\\h\\venv\\Scripts\\python.exe',
    pythonPath: null,
    agentRoot: 'C:\\h',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'gui'],
    appPath: null,
    hermesHome: 'C:\\h'
  })
  assert.doesNotMatch(script, /robocopy/)
  assert.doesNotMatch(script, /EMPTYDIR/)
})

// --- Fix (v1.0.6): cd OUT of the tree before deleting it, else cmd.exe loses its
// CWD mid-run and aborts with "The batch file cannot be found" before the final
// dialog + self-delete. Also un-pins the dir so it can actually be removed. ---

test('windows full script leaves the deleted tree (cd %SystemRoot%) before removals', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 123,
    pythonExe: 'py.exe',
    pythonPath: null,
    agentRoot: 'C:\\h\\hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: 'C:\\Users\\x\\AppData\\Local\\Programs\\OTTO',
    hermesHome: 'C:\\Users\\x\\AppData\\Local\\hermes',
    removeUserData: true,
    removeAgent: true
  })
  assert.match(script, /cd \/d "%SystemRoot%"/)
  // ordering: python uninstall (needs cwd=agentRoot) -> safe cd -> first removal
  const pyIdx = script.indexOf('hermes_cli.uninstall')
  const cdOutIdx = script.indexOf('cd /d "%SystemRoot%"')
  const firstRemovalIdx = script.search(/attrib|robocopy|rmdir \/s \/q/)
  assert.ok(
    pyIdx < cdOutIdx && cdOutIdx < firstRemovalIdx,
    'safe cd must sit between the python call and the first removal'
  )
})

// --- Fix (v1.0.6): clear read-only/hidden/system attrs before deleting, so a
// stubborn .git pack (WinError 5) doesn't block the tree removal. ---

test('windows full script clears file attributes before deleting each tree', () => {
  const script = buildWindowsCleanupScript({
    desktopPid: 123,
    pythonExe: 'py.exe',
    pythonPath: null,
    agentRoot: 'C:\\h\\hermes-agent',
    uninstallArgs: ['-m', 'hermes_cli.uninstall', '--mode', 'full'],
    appPath: 'C:\\Users\\x\\AppData\\Local\\Programs\\OTTO',
    hermesHome: 'C:\\Users\\x\\AppData\\Local\\hermes',
    removeUserData: true,
    removeAgent: true
  })
  assert.match(script, /attrib -r -h -s \/s \/d "C:\\h\\hermes-agent"/)
  assert.match(script, /attrib -r -h -s \/s \/d "C:\\Users\\x\\AppData\\Local\\Programs\\OTTO"/)
})
