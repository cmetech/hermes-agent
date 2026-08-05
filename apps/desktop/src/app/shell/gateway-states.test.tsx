import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { gatewayAutomationLabel } from './gateway-states'
import { useStatusbarItems } from './hooks/use-statusbar-items'

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

/**
 * The footer chip itself, not just the label helper.
 *
 * The whole point of this surface is that a user glancing at the footer learns
 * automation is down. A correct `gatewayAutomationLabel` that the chip never
 * calls delivers nothing -- that is exactly the state this branch shipped in
 * before this test existed. Assert the rendered chip.
 */
function gatewayChip(options: { gatewayRunning?: boolean; inferenceReady: boolean }) {
  const { result } = renderHook(() =>
    useStatusbarItems({
      agentsOpen: false,
      chatOpen: true,
      commandCenterOpen: false,
      extraLeftItems: [],
      extraRightItems: [],
      freshDraftReady: false,
      gatewayState: 'open',
      inferenceStatus: { ready: options.inferenceReady } as never,
      openAgents: () => undefined,
      openCommandCenterSection: () => undefined,
      requestGateway: async () => undefined as never,
      statusSnapshot:
        options.gatewayRunning === undefined ? null : ({ gateway_running: options.gatewayRunning } as never),
      toggleCommandCenter: () => undefined
    })
  )

  const chip = result.current.leftStatusbarItems.find(item => item.id === 'gateway-health')

  if (!chip) {
    throw new Error('gateway-health chip missing from the statusbar')
  }

  return chip
}

const AMBER = 'text-amber-600 hover:text-amber-600'

describe('gateway statusbar chip', () => {
  it('does not read as ready when the messaging gateway is down', () => {
    // Websocket open + inference ready + automation stopped. Before this, the
    // chip read "ready" in normal colour -- the display that misled the user.
    const chip = gatewayChip({ gatewayRunning: false, inferenceReady: true })

    expect(chip.detail).not.toBe('ready')
    expect(chip.detail).toContain('stopped')
    expect(chip.className).toBe(AMBER)
  })

  it('still reads ready when all three legs are healthy', () => {
    const chip = gatewayChip({ gatewayRunning: true, inferenceReady: true })

    expect(chip.detail).toBe('ready')
    expect(chip.className).toBeUndefined()
  })

  it('does not raise a false alarm before the first status response', () => {
    // `undefined` means "no answer yet", not "down".
    const chip = gatewayChip({ inferenceReady: true })

    expect(chip.detail).toBe('ready')
    expect(chip.className).toBeUndefined()
  })
})
