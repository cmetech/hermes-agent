import { PassThrough } from 'stream'

import { renderSync } from '@hermes/ink'
import React from 'react'
import { expect, it } from 'vitest'

import { Md } from '../components/markdown.js'
import { DEFAULT_THEME } from '../theme.js'

it('renders Mermaid topology as source in the Ink surface', () => {
  const stdout = new PassThrough()
  let output = ''
  Object.assign(stdout, { columns: 100, isTTY: false, rows: 24 })
  stdout.on('data', chunk => {
    output += chunk.toString()
  })
  const instance = renderSync(
    React.createElement(Md, {
      t: DEFAULT_THEME,
      text: '```mermaid\ngraph TD\n  A --> B\n```'
    }),
    { patchConsole: false, stdout: stdout as NodeJS.WriteStream }
  )
  instance.unmount()
  instance.cleanup()
  expect(output).toContain('mermaid')
  expect(output).toContain('graph TD')
  expect(output).toContain('A --> B')
})
