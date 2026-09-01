import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'

import { forceNoPublishArgs } from './electron-builder-args.mjs'

const require = createRequire(import.meta.url)
const { configureBuildCommand, createYargs } = require('electron-builder/out/builder')

test('release callers cannot turn the forced no-publish policy into a yargs array', () => {
  const argv = forceNoPublishArgs(['--mac', '--publish', 'never'])
  const parsed = configureBuildCommand(createYargs().exitProcess(false)).parse(argv)

  assert.equal(parsed.publish, 'never')
})
