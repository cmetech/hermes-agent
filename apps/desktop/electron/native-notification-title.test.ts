import { describe, expect, it } from 'vitest'

import { nativeNotificationTitle } from './native-notification-title'

describe('nativeNotificationTitle', () => {
  it('replaces the upstream name with the active brand', () => {
    expect(nativeNotificationTitle('Hermes finished', 'LOOP24')).toBe('LOOP24 finished')
    expect(nativeNotificationTitle('Hermes', 'OTTO')).toBe('OTTO')
  })

  it('preserves generic copy and falls back to the active brand', () => {
    expect(nativeNotificationTitle('Approval needed', 'LOOP24')).toBe('Approval needed')
    expect(nativeNotificationTitle('', 'LOOP24')).toBe('LOOP24')
  })
})
