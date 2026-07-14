import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { vendor } from '../vendor-ericsson.mjs'

function tmpSource() {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'ecsrc-'))
  const w = (p, c) => { fs.mkdirSync(path.dirname(path.join(d, p)), { recursive: true }); fs.writeFileSync(path.join(d, p), c) }
  w('sets/ericsson.json', JSON.stringify({
    name: 'ericsson', version: '0.2.0',
    skills: ['skills/ericsson/workflow-orchestrator'],
    plugins: ['plugins/ericsson-jira'],
    mcpServers: 'mcp/mcp-servers.yaml', mcpLocal: ['mcp/outlook-mcp'],
    workflows: ['workflows/w.yml'], personas: [],
    env: [{ key: 'JIRA_PAT', description: 'x', category: 'tool', password: true }]
  }))
  w('skills/ericsson/workflow-orchestrator/SKILL.md', '---\nname: workflow-orchestrator\n---\n')
  w('plugins/ericsson-jira/plugin.yaml', 'name: ericsson-jira\n')
  w('plugins/ericsson-jira/__init__.py', '')
  w('mcp/outlook-mcp/run_server.py', '# srv')
  w('mcp/mcp-servers.yaml', 'mcp_servers:\n  outlook: {}\n')
  w('workflows/w.yml', 'name: w\n')
  w('tests/should_not_copy.py', 'x')       // repo-only, must be stripped
  return d
}

test('vendor maps manifest paths into the hermes-agent tree', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  vendor({ sourceDir: src, destRoot: dst, sourceCommit: 'abc1234' })
  assert.ok(fs.existsSync(path.join(dst, 'skills/ericsson/workflow-orchestrator/SKILL.md')))
  assert.ok(fs.existsSync(path.join(dst, 'plugins/ericsson-jira/plugin.yaml')))
  assert.ok(fs.existsSync(path.join(dst, 'plugins/outlook-mcp/run_server.py')))    // mcpLocal -> plugins/
  const man = JSON.parse(fs.readFileSync(path.join(dst, 'capabilities/ericsson.json'), 'utf8'))
  assert.equal(man.vendoredFrom, 'abc1234')
  assert.deepEqual(man.env.map(e => e.key), ['JIRA_PAT'])
  assert.ok(!fs.existsSync(path.join(dst, 'tests/should_not_copy.py')))            // stripped
})
