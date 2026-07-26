import { beforeEach, describe, expect, it } from 'vitest'

import { group, split } from '@/components/pane-shell/tree/model'
import { $selectedStoredSessionId } from '@/store/session'
import {
  $sessionTiles,
  focusOpenSession,
  orderTilesByTree,
  selectionHomesToWorkspace,
  type SessionTile
} from '@/store/session-states'

const tile = (storedSessionId: string): SessionTile => ({ storedSessionId })
const tilePane = (id: string) => `session-tile:${id}`

beforeEach(() => {
  $selectedStoredSessionId.set(null)
  $sessionTiles.set([])
})

describe('focusOpenSession', () => {
  it('leaves a primary session unresolved when a page hides the workspace chat', () => {
    $selectedStoredSessionId.set('primary')

    expect(focusOpenSession('primary', false)).toBe(false)
  })

  it('focuses a primary session when its workspace chat is visible', () => {
    $selectedStoredSessionId.set('primary')

    expect(focusOpenSession('primary', true)).toBe(true)
  })

  it('focuses an open session tile even when a page hides the primary workspace chat', () => {
    $selectedStoredSessionId.set('primary')
    $sessionTiles.set([tile('secondary')])

    expect(focusOpenSession('secondary', false)).toBe(true)
  })
})

describe('orderTilesByTree', () => {
  it('no-ops (null) without a tree or below two tiles', () => {
    expect(orderTilesByTree(null, [tile('a'), tile('b')])).toBeNull()
    expect(orderTilesByTree(group([tilePane('a')]), [tile('a')])).toBeNull()
  })

  it('reorders tiles to layout-tree encounter order across a split', () => {
    const tree = split('row', [group(['workspace', tilePane('b')]), group([tilePane('a')])])

    expect(orderTilesByTree(tree, [tile('a'), tile('b')])).toEqual([tile('b'), tile('a')])
  })

  it('returns null when the array already matches strip order (skip persist)', () => {
    const tree = split('row', [group([tilePane('b')]), group([tilePane('a')])])

    expect(orderTilesByTree(tree, [tile('b'), tile('a')])).toBeNull()
  })

  it('sorts not-yet-adopted tiles after placed ones, stably', () => {
    const tree = group(['workspace', tilePane('b')])

    expect(orderTilesByTree(tree, [tile('a'), tile('b'), tile('c')])).toEqual([tile('b'), tile('a'), tile('c')])
  })
})

describe('selectionHomesToWorkspace', () => {
  const tiles = [tile('a'), tile('b')]

  it('homes for a null selection or a non-tile session', () => {
    expect(selectionHomesToWorkspace(null, tiles)).toBe(true)
    expect(selectionHomesToWorkspace('c', tiles)).toBe(true)
  })

  it('skips homing when the selected id is already an open tile', () => {
    expect(selectionHomesToWorkspace('a', tiles)).toBe(false)
  })
})
