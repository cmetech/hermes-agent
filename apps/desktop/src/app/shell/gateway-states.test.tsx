import { describe, expect, it } from 'vitest'

import { gatewayAutomationLabel } from './hooks/use-statusbar-items'

const copy = {
  automationRunning: 'running',
  automationStopped: 'stopped',
  automationUnknown: 'unknown'
}

describe('gatewayAutomationLabel', () => {
  it('reports stopped when the messaging gateway is down', () => {
    // The case that misled a real user: the chip said "ready" from the
    // websocket + inference legs while this was false.
    expect(gatewayAutomationLabel(false, copy)).toBe('stopped')
  })

  it('reports running when it is up', () => {
    expect(gatewayAutomationLabel(true, copy)).toBe('running')
  })

  it('reports unknown before the first status response', () => {
    expect(gatewayAutomationLabel(undefined, copy)).toBe('unknown')
  })
})
