/**
 * Tests for electron/brand-scope.ts — pure predicates that identify a path
 * living under a DIFFERENT brand's %LOCALAPPDATA%\<seg> per-brand home.
 *
 * Run with: npx tsx --test electron/brand-scope.test.ts
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { isForeignBrandLocalAppDataPath, localAppDataBrandSegment } from './brand-scope'

const LAD = 'C:\\Users\\splunk\\AppData\\Local'

test('localAppDataBrandSegment returns the first segment under LOCALAPPDATA', () => {
  assert.equal(
    localAppDataBrandSegment('C:\\Users\\splunk\\AppData\\Local\\otto\\hermes-agent\\venv\\Scripts\\hermes.exe', LAD),
    'otto'
  )
  assert.equal(localAppDataBrandSegment('C:\\Users\\splunk\\AppData\\Local\\loop24', LAD), 'loop24')
})

test('localAppDataBrandSegment is case-insensitive and slash-agnostic', () => {
  assert.equal(localAppDataBrandSegment('c:/users/splunk/appdata/local/OTTO/hermes-agent', LAD), 'otto')
})

test('localAppDataBrandSegment returns null off LOCALAPPDATA or for empty inputs', () => {
  assert.equal(localAppDataBrandSegment('C:\\tools\\hermes.exe', LAD), null)
  assert.equal(localAppDataBrandSegment('/usr/local/bin/hermes', '/usr/local/bin'), 'hermes') // sanity: posix-ish
  assert.equal(localAppDataBrandSegment('', LAD), null)
  assert.equal(localAppDataBrandSegment('C:\\x\\hermes', ''), null)
  assert.equal(localAppDataBrandSegment(null, LAD), null)
})

test('localAppDataBrandSegment respects segment boundaries (otto is not under otto2)', () => {
  // candidate exactly equal to LOCALAPPDATA (no child) -> null
  assert.equal(localAppDataBrandSegment(LAD, LAD), null)
  // a sibling seg 'otto2' is its own segment, not 'otto'
  assert.equal(localAppDataBrandSegment('C:\\Users\\splunk\\AppData\\Local\\otto2\\x', LAD), 'otto2')
})

test('isForeignBrandLocalAppDataPath: another brand clone is foreign', () => {
  assert.equal(
    isForeignBrandLocalAppDataPath({
      candidate: 'C:\\Users\\splunk\\AppData\\Local\\otto\\hermes-agent\\venv\\Scripts\\hermes.exe',
      ourHome: 'C:\\Users\\splunk\\AppData\\Local\\loop24',
      localAppData: LAD
    }),
    true
  )
})

test('isForeignBrandLocalAppDataPath: our own venv is NOT foreign', () => {
  assert.equal(
    isForeignBrandLocalAppDataPath({
      candidate: 'C:\\Users\\splunk\\AppData\\Local\\loop24\\hermes-agent\\venv\\Scripts\\hermes.exe',
      ourHome: 'C:\\Users\\splunk\\AppData\\Local\\loop24',
      localAppData: LAD
    }),
    false
  )
})

test('isForeignBrandLocalAppDataPath: an external/pip hermes is NOT foreign (honored)', () => {
  assert.equal(
    isForeignBrandLocalAppDataPath({ candidate: 'C:\\tools\\hermes.exe', ourHome: 'C:\\Users\\splunk\\AppData\\Local\\loop24', localAppData: LAD }),
    false
  )
})

test('isForeignBrandLocalAppDataPath: no LOCALAPPDATA (non-Windows) -> not foreign', () => {
  assert.equal(
    isForeignBrandLocalAppDataPath({ candidate: '/home/x/.otto/hermes-agent/venv/bin/hermes', ourHome: '/home/x/.loop24', localAppData: undefined }),
    false
  )
})
