import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { stageSkills, excludedToolsets } from '../curation.mjs'

function tmp() { return fs.mkdtempSync(path.join(os.tmpdir(), 'brand-cur-')) }

test('stageSkills omits excluded skill paths', () => {
  const src = tmp(), dest = tmp()
  fs.mkdirSync(path.join(src, 'media/spotify'), { recursive: true })
  fs.mkdirSync(path.join(src, 'email/himalaya'), { recursive: true })
  fs.writeFileSync(path.join(src, 'media/spotify/SKILL.md'), 'x')
  fs.writeFileSync(path.join(src, 'email/himalaya/SKILL.md'), 'y')

  const d = { curation: { skills: { exclude: ['media/spotify'] } } }
  const r = stageSkills(d, { srcSkillsDir: src, destSkillsDir: dest })

  assert.equal(fs.existsSync(path.join(dest, 'email/himalaya/SKILL.md')), true)
  assert.equal(fs.existsSync(path.join(dest, 'media/spotify')), false)
  assert.deepEqual(r.excluded, ['media/spotify'])
})

test('excludedToolsets passes through the descriptor', () => {
  assert.deepEqual(excludedToolsets({ curation: { tools: { excludeToolsets: ['home_assistant'] } } }), ['home_assistant'])
})
