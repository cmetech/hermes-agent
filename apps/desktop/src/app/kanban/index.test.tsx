import { expect, it } from 'vitest'

import { KanbanView } from './index'

it('exports a native physical Kanban page', () => {
  expect(typeof KanbanView).toBe('function')
})
