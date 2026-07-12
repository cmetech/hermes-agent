// scripts/brand/__tests__/active.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolveActiveBrand } from '../active.mjs'

function tmp() { return fs.mkdtempSync(path.join(os.tmpdir(), 'brand-active-')) }

function repoRoot() {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
}

test('resolveActiveBrand reads the committed marker when no env override is set', () => {
  const prev = process.env.OTTO_BRAND
  delete process.env.OTTO_BRAND
  try {
    assert.equal(resolveActiveBrand({ root: repoRoot() }), 'otto')
  } finally {
    if (prev === undefined) delete process.env.OTTO_BRAND
    else process.env.OTTO_BRAND = prev
  }
})

test('OTTO_BRAND env overrides the marker file', () => {
  const root = tmp()
  fs.mkdirSync(path.join(root, 'brand'), { recursive: true })
  fs.writeFileSync(path.join(root, 'brand', 'active'), 'otto\n')

  const prev = process.env.OTTO_BRAND
  process.env.OTTO_BRAND = 'loop24'
  try {
    assert.equal(resolveActiveBrand({ root }), 'loop24')
  } finally {
    if (prev === undefined) delete process.env.OTTO_BRAND
    else process.env.OTTO_BRAND = prev
  }
})

test('missing marker and no env falls back to otto', () => {
  const root = tmp()

  const prev = process.env.OTTO_BRAND
  delete process.env.OTTO_BRAND
  try {
    assert.equal(resolveActiveBrand({ root }), 'otto')
  } finally {
    if (prev === undefined) delete process.env.OTTO_BRAND
    else process.env.OTTO_BRAND = prev
  }
})
