// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { NodeContextMenu } from './node-context-menu'

afterEach(cleanup)

describe('NodeContextMenu', () => {
  it('does not offer mutations for a profile-managed skill', () => {
    render(
      <NodeContextMenu
        onClose={() => undefined}
        onNodeRemoved={() => undefined}
        target={{
          id: 'oscar-rules',
          kind: 'skill',
          label: 'oscar-rules',
          readOnly: true,
          x: 10,
          y: 10
        }}
      />
    )

    expect(screen.getByText('Profile-managed skill')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /edit/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /archive/i })).toBeNull()
  })
})
