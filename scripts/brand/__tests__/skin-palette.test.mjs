// scripts/brand/__tests__/skin-palette.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { OTTO_PALETTE, OTTO_SPINNER } from '../skin-palette.mjs'

test('palette carries the OTTO gold accents', () => {
  assert.equal(OTTO_PALETTE.ui_accent, '#FAD22D')
  assert.equal(OTTO_PALETTE.banner_title, '#FAD22D')
  assert.equal(OTTO_SPINNER.waiting_faces[0], '(◎)')
})
