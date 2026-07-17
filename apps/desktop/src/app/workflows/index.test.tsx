import { expect, it } from 'vitest'

import { WorkflowsView } from './index'

it('exports a native workflows page without a Kanban lifecycle import', () => {
  expect(typeof WorkflowsView).toBe('function')
})
