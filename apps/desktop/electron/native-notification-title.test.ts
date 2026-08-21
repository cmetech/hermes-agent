// Run with: npx tsx --test
import assert from 'node:assert/strict'
import test from 'node:test'

import { nativeNotificationTitle } from './native-notification-title'

test('native notification titles replace the upstream name with the active brand', () => {
  assert.equal(nativeNotificationTitle('Hermes finished', 'LOOP24'), 'LOOP24 finished')
  assert.equal(nativeNotificationTitle('Hermes', 'OTTO'), 'OTTO')
})

test('native notification titles preserve generic copy and fall back to the active brand', () => {
  assert.equal(nativeNotificationTitle('Approval needed', 'LOOP24'), 'Approval needed')
  assert.equal(nativeNotificationTitle('', 'LOOP24'), 'LOOP24')
})
