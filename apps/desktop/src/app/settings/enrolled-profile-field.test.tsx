import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EnrolledProfileField } from './enrolled-profile-field'

// OTTO: `browser.default_profile` holds a profile NAME, but users need a switch,
// not a text box asking them to know the word "enrolled".
//
// It cannot simply be declared `type: 'boolean'`: the generic Switch branch in
// config-field.tsx would write `true`, and the backend would look up a profile
// called "True" (browser_session_registry.default_profile_name coerces with
// str()), find none, and trust nothing. It fails closed — but for the wrong
// reason, and silently. Hence a dedicated field that maps both directions.
//
// Design: docs/plans/2026-07-26-enrolled-browser-profile-seeding-design.md
describe('EnrolledProfileField', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('is on when the enrolled profile is the default', () => {
    render(<EnrolledProfileField onChange={() => {}} value="enrolled" />)

    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('true')
  })

  it.each([undefined, '', 'ephemeral'])('is off for %s', value => {
    render(<EnrolledProfileField onChange={() => {}} value={value} />)

    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('false')
  })

  it('writes the profile name when switched on', () => {
    const onChange = vi.fn()
    render(<EnrolledProfileField onChange={onChange} value="" />)

    fireEvent.click(screen.getByRole('switch'))

    expect(onChange).toHaveBeenCalledWith('enrolled')
  })

  // Empty string is what the backend reads as "no profile":
  // default_profile_name() returns `name or None`. Writing `false`, or deleting
  // nothing at all, would leave the profile active.
  it('clears the value when switched off', () => {
    const onChange = vi.fn()
    render(<EnrolledProfileField onChange={onChange} value="enrolled" />)

    fireEvent.click(screen.getByRole('switch'))

    expect(onChange).toHaveBeenCalledWith('')
  })
})
