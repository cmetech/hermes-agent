import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const mainSource = fs.readFileSync(path.join(__dirname, 'main.ts'), 'utf8')
const preloadSource = fs.readFileSync(path.join(__dirname, 'preload.ts'), 'utf8')

function between(source: string, startMarker: string, endMarker: string): string {
  const start = source.indexOf(startMarker)
  const end = source.indexOf(endMarker, start + startMarker.length)
  assert.notEqual(start, -1, `missing source marker: ${startMarker}`)
  assert.notEqual(end, -1, `missing source marker: ${endMarker}`)

  return source.slice(start, end)
}

test('structured authenticated channel is additive to the legacy API channel', () => {
  assert.match(preloadSource, /api: request => ipcRenderer\.invoke\('hermes:api', request\)/)
  assert.match(preloadSource, /apiStructured: request => ipcRenderer\.invoke\('hermes:api:structured', request\)/)

  const structuredHandler = between(mainSource, "ipcMain.handle('hermes:api:structured'", "ipcMain.handle('hermes:api'")

  assert.match(structuredHandler, /fetchJsonViaOauthSession/)
  assert.match(structuredHandler, /fetchJson\(url, connection\.token/)
  assert.equal(structuredHandler.match(/structured: true/g)?.length, 2)
})

test('both structured transports share response collection and retain adapter timeout aborts', () => {
  const local = between(mainSource, 'function fetchJson(', 'function fetchPublicJson(')
  const oauth = between(mainSource, 'function fetchJsonViaOauthSession(', '// Mint a single-use WS ticket')

  for (const transport of [local, oauth]) {
    assert.match(transport, /if \(options\.structured\)/)
    assert.match(transport, /collectStructuredJsonResponse\(/)
    assert.equal(transport.match(/if \(options\.structured\)/g)?.length, 1)
    assert.doesNotMatch(transport, /options\.structured \?/)
  }

  assert.match(mainSource, /import \{ collectStructuredJsonResponse \} from '\.\/structured-api-response'/)
  assert.match(local, /req\.destroy\(new Error\(`Timed out connecting to Hermes backend/)
  assert.match(oauth, /res\.on\('error', error =>/)
  assert.match(oauth, /request\.abort\(\)/)
  assert.match(oauth, /Timed out connecting to Hermes backend/)
})
