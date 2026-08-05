import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { $desktopVersion } from '@/store/updates'

import { StatusbarBoundary } from './statusbar-fallback'

function Bomb(): never {
  throw new Error('boom')
}

describe('StatusbarBoundary', () => {
  it('contains a crashing statusbar and keeps the version on screen', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    $desktopVersion.set({
      appVersion: '5.2.0',
      electronVersion: 'x',
      hermesRoot: '/tmp',
      nodeVersion: 'x',
      platform: 'test'
    })

    render(
      <StatusbarBoundary>
        <Bomb />
      </StatusbarBoundary>
    )

    expect(screen.getByText('v5.2.0')).toBeTruthy()
    expect(consoleError.mock.calls.some(args => String(args[0]).includes('[statusbar] crashed'))).toBe(true)
    consoleError.mockRestore()
  })
})
