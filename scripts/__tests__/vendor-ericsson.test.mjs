import assert from 'node:assert/strict'
import test from 'node:test'
import { execFileSync, spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import {
  managedDestinations,
  reconcileManagedPaths,
  vendor,
} from '../vendor-ericsson.mjs'

const SCRIPT = fileURLToPath(new URL('../vendor-ericsson.mjs', import.meta.url))
const TRANSACTION_MARKER = '.ericsson-vendor-owned.json'
const VENDOR_LOCK = '.ericsson-vendor-lock'
const VENDOR_LOCK_MARKER = 'owner.json'

function transactionName(id) {
  return `.ericsson-vendor-txn-${id}`
}

function seedOwnedTransaction(dst, id = '11111111-1111-4111-8111-111111111111') {
  const name = transactionName(id)
  write(dst, `${name}/${TRANSACTION_MARKER}`, JSON.stringify({
    schemaVersion: 1,
    transactionId: id,
  }) + '\n')
  return name
}

function write(root, rel, contents) {
  const target = path.join(root, rel)
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.writeFileSync(target, contents)
}

function pluginPath(entry) {
  return typeof entry === 'string' ? entry : entry?.path
}

function pluginId(entry) {
  return typeof entry === 'string' ? path.basename(entry) : entry?.id
}

function tmpSource(manifestOverrides = {}) {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'ecsrc-'))
  const manifest = {
    name: 'ericsson', version: '0.2.0',
    skills: ['skills/ericsson/opportunity-visuals'],
    plugins: ['plugins/workflow', 'plugins/ericsson-jira'],
    mcpServers: 'mcp/mcp-servers.yaml', mcpLocal: ['mcp/outlook-mcp'],
    workflowPackages: [{
      path: 'capabilities/workflow-packages/ericsson',
      digestManifest: 'capabilities/workflow-packages/ericsson/digests.json',
    }],
    personas: [],
    env: [{ key: 'JIRA_PAT', description: 'x', category: 'tool', password: true }],
    ...manifestOverrides,
  }
  write(d, 'sets/ericsson.json', JSON.stringify(manifest))
  for (const rel of manifest.skills || []) write(d, `${rel}/SKILL.md`, `---\nname: ${path.basename(rel)}\n---\n`)
  for (const entry of manifest.plugins || []) {
    const rel = pluginPath(entry)
    if (typeof rel !== 'string' || !rel.startsWith('plugins/')) continue
    const id = pluginId(entry)
    const kind = typeof entry === 'object' && entry?.enabled === false
      ? 'standalone'
      : 'backend'
    write(d, `${rel}/plugin.yaml`, `name: ${id}\nkind: ${kind}\n`)
    write(d, `${rel}/__init__.py`, '')
  }
  for (const rel of manifest.mcpLocal || []) write(d, `${rel}/run_server.py`, '# srv')
  if (manifest.mcpServers) write(d, manifest.mcpServers, 'mcp_servers:\n  outlook: {}\n')
  for (const entry of manifest.workflowPackages || []) {
    write(d, `${entry.path}/workflows/w.yaml`, 'name: w\ndescription: fixture\nnodes:\n  - id: start\n    bash: "true"\n')
    write(d, entry.digestManifest, JSON.stringify({ schemaVersion: 1, packages: { w: '0'.repeat(64) } }))
  }
  write(d, 'tests/should_not_copy.py', 'x')       // repo-only, must be stripped
  return d
}

function readInventory(dst) {
  return JSON.parse(fs.readFileSync(
    path.join(dst, 'capabilities/ericsson-vendored-paths.json'),
    'utf8',
  ))
}

function treeSnapshot(root) {
  if (!fs.existsSync(root)) return []
  const result = []
  const visit = (current, relative = '') => {
    const stat = fs.lstatSync(current)
    if (stat.isSymbolicLink()) {
      result.push([relative, 'link', fs.readlinkSync(current)])
      return
    }
    if (stat.isDirectory()) {
      if (relative) result.push([relative, 'dir'])
      for (const entry of fs.readdirSync(current).sort()) {
        visit(path.join(current, entry), relative ? `${relative}/${entry}` : entry)
      }
      return
    }
    result.push([relative, 'file', fs.readFileSync(current).toString('base64')])
  }
  visit(root)
  return result
}

function sha256(contents) {
  return createHash('sha256').update(contents).digest('hex')
}

function treeHash(target) {
  const records = []
  const visit = (current, relative = '') => {
    const stat = fs.lstatSync(current)
    if (stat.isDirectory()) {
      records.push(`d\0${relative}\0`)
      for (const entry of fs.readdirSync(current).sort()) {
        visit(path.join(current, entry), relative ? `${relative}/${entry}` : entry)
      }
      return
    }
    records.push(`f\0${relative}\0${sha256(fs.readFileSync(current))}\0`)
  }
  visit(target)
  return sha256(records.join(''))
}

function initGitSource(src) {
  execFileSync('git', ['init', '-q'], { cwd: src })
  execFileSync('git', ['config', 'user.email', 'vendor-test@example.invalid'], { cwd: src })
  execFileSync('git', ['config', 'user.name', 'Vendor Test'], { cwd: src })
  execFileSync('git', ['add', '.'], { cwd: src })
  execFileSync('git', ['commit', '-qm', 'fixture'], { cwd: src })
  return execFileSync('git', ['rev-parse', 'HEAD'], { cwd: src, encoding: 'utf8' }).trim()
}

function runVendorCli(src, dst, envOverrides = {}) {
  return execFileSync(process.execPath, [SCRIPT], {
    cwd: dst,
    env: { ...process.env, ERICSSON_CAPABILITIES_DIR: src, ...envOverrides },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}

function seedCurrentSnapshot(dst, { sourceCommit = '1'.repeat(40) } = {}) {
  const manifest = {
    name: 'ericsson', version: '0.2.0',
    skills: ['skills/ericsson/workflow-orchestrator'],
    plugins: ['plugins/ericsson-jira'],
    mcpServers: 'mcp/mcp-servers.yaml',
    mcpServersFile: 'mcp-servers.yaml',
    mcpLocal: ['mcp/outlook-mcp'],
    workflows: ['capabilities/workflows/w.yml'],
    personas: [], env: [], vendoredFrom: sourceCommit,
  }
  write(dst, 'capabilities/ericsson.json', JSON.stringify(manifest, null, 2) + '\n')
  write(dst, 'capabilities/ericsson-vendored-paths.json', JSON.stringify([
    'capabilities/mcp-servers.yaml',
    'capabilities/workflows/w.yml',
    'plugins/ericsson-jira',
    'plugins/outlook-mcp',
    'skills/ericsson/workflow-orchestrator',
  ], null, 2) + '\n')
  write(dst, 'capabilities/mcp-servers.yaml', 'old mcp config\n')
  write(dst, 'capabilities/workflows/w.yml', 'old workflow\n')
  write(dst, 'plugins/ericsson-jira/old.py', 'old jira executable\n')
  write(dst, 'plugins/outlook-mcp/old.py', 'old outlook executable\n')
  write(dst, 'skills/ericsson/workflow-orchestrator/old.md', 'old guidance\n')
}

function transactionArtifacts(dst) {
  return fs.readdirSync(dst).filter(name => name.startsWith('.ericsson-vendor-')).sort()
}

async function waitForPath(target, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (fs.existsSync(target)) return
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  throw new Error(`timed out waiting for ${target}`)
}

async function waitForChild(child) {
  return new Promise((resolve, reject) => {
    child.once('error', reject)
    child.once('exit', (code, signal) => resolve({ code, signal }))
  })
}

test('vendor maps manifest paths into the hermes-agent tree', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  vendor({ sourceDir: src, destRoot: dst, sourceCommit: 'abc1234' })
  assert.ok(fs.existsSync(path.join(dst, 'skills/ericsson/opportunity-visuals/SKILL.md')))
  assert.ok(!fs.existsSync(path.join(dst, 'plugins/workflow')))
  assert.ok(fs.existsSync(path.join(dst, 'plugins/ericsson-jira/plugin.yaml')))
  assert.ok(fs.existsSync(path.join(dst, 'plugins/outlook-mcp/run_server.py')))    // mcpLocal -> plugins/
  const man = JSON.parse(fs.readFileSync(path.join(dst, 'capabilities/ericsson.json'), 'utf8'))
  assert.equal(man.vendoredFrom, 'abc1234')
  assert.deepEqual(man.env.map(e => e.key), ['JIRA_PAT'])
  assert.ok(!fs.existsSync(path.join(dst, 'tests/should_not_copy.py')))            // stripped
  assert.deepEqual(readInventory(dst), [
    'capabilities/mcp-servers.yaml',
    'capabilities/workflow-packages/ericsson',
    'plugins/ericsson-jira',
    'plugins/outlook-mcp',
    'skills/ericsson/opportunity-visuals',
  ])
})

test('vendor preserves historically accepted non-slug legacy plugin paths', () => {
  const legacyPath = 'plugins/legacy.plugin'
  const src = tmpSource({
    skills: [],
    plugins: [legacyPath],
    mcpServers: undefined,
    mcpLocal: [],
    workflowPackages: [],
  })
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))

  assert.deepEqual(managedDestinations({ plugins: [legacyPath] }), [legacyPath])
  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) })

  assert.equal(
    fs.readFileSync(path.join(dst, legacyPath, 'plugin.yaml'), 'utf8'),
    'name: legacy.plugin\nkind: backend\n',
  )
  assert.deepEqual(readInventory(dst), [legacyPath])
})

test('vendor preserves structured standalone metadata and exact descriptor bytes', () => {
  const connector = {
    path: 'plugins/connector-one',
    id: 'connector-one',
    enabled: false,
    lifecycleMigration: {
      id: 'connector-one-backend-to-standalone-v1',
      from: 'auto_seeded_backend',
    },
  }
  const src = tmpSource({
    plugins: ['plugins/workflow', connector],
  })
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const descriptor = 'name: connector-one\nkind: standalone\ndescription: exact bytes\n'
  write(src, 'plugins/connector-one/plugin.yaml', descriptor)
  write(src, 'plugins/connector-one/config.schema.json', '{"exact":true}\n')
  const sourceCommit = '3'.repeat(40)

  vendor({ sourceDir: src, destRoot: dst, sourceCommit })

  const vendored = JSON.parse(fs.readFileSync(
    path.join(dst, 'capabilities/ericsson.json'),
    'utf8',
  ))
  assert.deepEqual(vendored.plugins, ['plugins/workflow', connector])
  assert.equal(vendored.vendoredFrom, sourceCommit)
  assert.equal(
    fs.readFileSync(path.join(dst, 'plugins/connector-one/plugin.yaml'), 'utf8'),
    descriptor,
  )
  assert.deepEqual(
    treeSnapshot(path.join(dst, 'plugins/connector-one')),
    treeSnapshot(path.join(src, 'plugins/connector-one')),
  )
  assert.ok(readInventory(dst).includes('plugins/connector-one'))
})

test('managed destinations accepts structured plugin objects in manifest order', () => {
  const destinations = managedDestinations({
    plugins: [
      'plugins/workflow',
      { path: 'plugins/connector-z', id: 'connector-z', enabled: false },
      { path: 'plugins/connector-a', id: 'connector-a', enabled: false },
    ],
  })

  assert.deepEqual(destinations, ['plugins/connector-a', 'plugins/connector-z'])
})

test('vendor rejects malformed or duplicate lifecycle metadata before publication', () => {
  const cases = [
    [
      'missing migration id',
      [{
        path: 'plugins/connector-one', id: 'connector-one', enabled: false,
        lifecycleMigration: { from: 'auto_seeded_backend' },
      }],
    ],
    [
      'wrong transition source',
      [{
        path: 'plugins/connector-one', id: 'connector-one', enabled: false,
        lifecycleMigration: { id: 'connector-migration-v1', from: 'manual' },
      }],
    ],
    [
      'oversized migration id',
      [{
        path: 'plugins/connector-one', id: 'connector-one', enabled: false,
        lifecycleMigration: { id: 'x'.repeat(65), from: 'auto_seeded_backend' },
      }],
    ],
    [
      'migration id with trailing newline',
      [{
        path: 'plugins/connector-one', id: 'connector-one', enabled: false,
        lifecycleMigration: {
          id: 'connector-migration-v1\n', from: 'auto_seeded_backend',
        },
      }],
    ],
    [
      'duplicate migration id',
      [
        {
          path: 'plugins/connector-one', id: 'connector-one', enabled: false,
          lifecycleMigration: { id: 'connector-migration-v1', from: 'auto_seeded_backend' },
        },
        {
          path: 'plugins/connector-two', id: 'connector-two', enabled: false,
          lifecycleMigration: { id: 'connector-migration-v1', from: 'auto_seeded_backend' },
        },
      ],
    ],
  ]

  for (const [label, plugins] of cases) {
    const src = tmpSource({ plugins })
    const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
    write(dst, 'sentinel.txt', `${label}\n`)
    const before = treeSnapshot(dst)

    assert.throws(
      () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: '4'.repeat(40) }),
      /plugin|lifecycle|migration|metadata/i,
      label,
    )
    assert.deepEqual(treeSnapshot(dst), before, label)
  }
})

test('vendor validates structured plugin descriptor identity before publication', () => {
  for (const [label, descriptor] of [
    ['missing descriptor', undefined],
    ['wrong descriptor id', 'name: another-connector\nkind: standalone\n'],
    ['wrong descriptor kind', 'name: connector-one\nkind: backend\n'],
  ]) {
    const entry = {
      path: 'plugins/connector-one',
      id: 'connector-one',
      enabled: false,
    }
    const src = tmpSource({ plugins: [entry] })
    const descriptorPath = path.join(src, 'plugins/connector-one/plugin.yaml')
    if (descriptor === undefined) fs.rmSync(descriptorPath)
    else fs.writeFileSync(descriptorPath, descriptor)
    const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
    write(dst, 'sentinel.txt', `${label}\n`)
    const before = treeSnapshot(dst)

    assert.throws(
      () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: '5'.repeat(40) }),
      /plugin|descriptor|manifest|kind|name/i,
      label,
    )
    assert.deepEqual(treeSnapshot(dst), before, label)
  }
})

test('managedDestinations returns sorted unique destination paths', () => {
  assert.deepEqual(managedDestinations({
    skills: ['skills/ericsson/z', 'skills/ericsson/a', 'skills/ericsson/z'],
    plugins: ['plugins/z', 'plugins/a', 'plugins/z'],
    mcpLocal: ['mcp/outlook-mcp', 'mcp/outlook-mcp'],
    mcpServers: 'mcp/mcp-servers.yaml',
    workflows: ['workflows/z.yml', 'workflows/z.yml', 'workflows/a.yml'],
    workflowPackages: [{
      path: 'capabilities/workflow-packages/ericsson',
      digestManifest: 'capabilities/workflow-packages/ericsson/digests.json',
    }],
  }), [
    'capabilities/mcp-servers.yaml',
    'capabilities/workflow-packages/ericsson',
    'capabilities/workflows/a.yml',
    'capabilities/workflows/z.yml',
    'plugins/a',
    'plugins/outlook-mcp',
    'plugins/z',
    'skills/ericsson/a',
    'skills/ericsson/z',
  ])
})

test('vendor atomically publishes source-shaped workflows alongside workflow packages', () => {
  const workflows = [
    'workflows/inbox-digest.yml',
    'workflows/jira-to-gitlab.yml',
  ]
  const src = tmpSource({ workflows })
  write(src, workflows[0], 'name: inbox digest\nsource: exact fixture bytes\n')
  write(src, workflows[1], 'name: jira to gitlab\nsource: exact fixture bytes\n')
  write(
    src,
    'workflows/jira-to-gitlab.hermes.yaml',
    'language_compatibility: archon-2026-07\n',
  )
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  seedCurrentSnapshot(dst)
  write(dst, 'capabilities/workflows/unrelated.yml', 'user-owned workflow\n')
  const before = treeSnapshot(dst)
  let injected = false

  assert.throws(() => vendor({
    sourceDir: src,
    destRoot: dst,
    sourceCommit: '6'.repeat(40),
    faultInjector(point, rel) {
      if (!injected
          && point === 'staged-publish'
          && rel === 'capabilities/workflows/jira-to-gitlab.yml') {
        injected = true
        throw new Error('injected workflow publish failure')
      }
    },
  }), /injected workflow publish failure/)
  assert.equal(injected, true)
  assert.deepEqual(treeSnapshot(dst), before)
  assert.deepEqual(transactionArtifacts(dst), [])

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '6'.repeat(40) })

  assert.equal(
    fs.readFileSync(path.join(dst, 'capabilities/workflows/inbox-digest.yml'), 'utf8'),
    'name: inbox digest\nsource: exact fixture bytes\n',
  )
  assert.equal(
    fs.readFileSync(path.join(dst, 'capabilities/workflows/jira-to-gitlab.yml'), 'utf8'),
    'name: jira to gitlab\nsource: exact fixture bytes\n',
  )
  assert.equal(
    fs.readFileSync(
      path.join(dst, 'capabilities/workflows/jira-to-gitlab.hermes.yaml'),
      'utf8',
    ),
    'language_compatibility: archon-2026-07\n',
  )
  assert.ok(!fs.existsSync(path.join(dst, 'capabilities/workflows/w.yml')))
  assert.equal(
    fs.readFileSync(path.join(dst, 'capabilities/workflows/unrelated.yml'), 'utf8'),
    'user-owned workflow\n',
  )
  assert.deepEqual(readInventory(dst), [
    'capabilities/mcp-servers.yaml',
    'capabilities/workflow-packages/ericsson',
    'capabilities/workflows/inbox-digest.yml',
    'capabilities/workflows/jira-to-gitlab.hermes.yaml',
    'capabilities/workflows/jira-to-gitlab.yml',
    'plugins/ericsson-jira',
    'plugins/outlook-mcp',
    'skills/ericsson/opportunity-visuals',
  ])
  const manifest = JSON.parse(fs.readFileSync(
    path.join(dst, 'capabilities/ericsson.json'),
    'utf8',
  ))
  assert.deepEqual(manifest.workflows, workflows)
  assert.deepEqual(manifest.workflowPackages, [{
    path: 'capabilities/workflow-packages/ericsson',
    digestManifest: 'capabilities/workflow-packages/ericsson/digests.json',
  }])
  assert.deepEqual(transactionArtifacts(dst), [])
})

test('vendor preserves only safe unowned manifest compatibility overlays', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const overlaySkill = 'skills/ericsson/compatibility-overlay'
  const staleManagedSkill = 'skills/ericsson/removed-managed-skill'
  const configDefaults = {
    browser: {
      default_profile: '',
      profiles: {
        enrolled: {
          kind: 'enrolled',
          cdp_port: 9333,
          trusted_origins: ['https://*.example.test'],
        },
      },
    },
  }
  write(dst, 'capabilities/ericsson.json', JSON.stringify({
    skills: [staleManagedSkill, overlaySkill],
    plugins: ['plugins/stale-connector'],
    configDefaults,
  }, null, 2) + '\n')
  write(dst, 'capabilities/ericsson-vendored-paths.json', JSON.stringify([
    staleManagedSkill,
  ], null, 2) + '\n')
  write(dst, `${staleManagedSkill}/SKILL.md`, 'stale managed bytes\n')
  write(dst, `${overlaySkill}/SKILL.md`, 'compatibility overlay bytes\n')
  write(dst, 'plugins/stale-connector/plugin.yaml', 'unmanaged plugin bytes\n')

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '7'.repeat(40) })

  const vendored = JSON.parse(fs.readFileSync(
    path.join(dst, 'capabilities/ericsson.json'),
    'utf8',
  ))
  assert.deepEqual(vendored.skills, [
    ...JSON.parse(fs.readFileSync(path.join(src, 'sets/ericsson.json'), 'utf8')).skills,
    overlaySkill,
  ])
  assert.deepEqual(vendored.configDefaults, configDefaults)
  assert.deepEqual(vendored.plugins, ['plugins/workflow', 'plugins/ericsson-jira'])
  assert.equal(
    fs.readFileSync(path.join(dst, `${overlaySkill}/SKILL.md`), 'utf8'),
    'compatibility overlay bytes\n',
  )
  assert.ok(!fs.existsSync(path.join(dst, staleManagedSkill)))
  assert.ok(fs.existsSync(path.join(dst, 'plugins/stale-connector/plugin.yaml')))
  assert.ok(!readInventory(dst).includes(overlaySkill))
})

test('source-owned manifest fields override compatibility overlays', () => {
  const sourceDefaults = { browser: { default_profile: 'source-owned' } }
  const src = tmpSource({ configDefaults: sourceDefaults })
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, 'capabilities/ericsson.json', JSON.stringify({
    configDefaults: { browser: { default_profile: 'stale-overlay' } },
  }))

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '8'.repeat(40) })

  const vendored = JSON.parse(fs.readFileSync(
    path.join(dst, 'capabilities/ericsson.json'),
    'utf8',
  ))
  assert.deepEqual(vendored.configDefaults, sourceDefaults)
})

test('vendor rejects unsafe unmanaged skill overlays before mutation', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'ecoutside-'))
  const overlaySkill = 'skills/ericsson/compatibility-overlay'
  write(outside, 'SKILL.md', 'outside bytes\n')
  write(dst, 'capabilities/ericsson.json', JSON.stringify({
    skills: [overlaySkill],
  }))
  fs.mkdirSync(path.dirname(path.join(dst, overlaySkill)), { recursive: true })
  try {
    fs.symlinkSync(outside, path.join(dst, overlaySkill), 'dir')
  } catch (error) {
    if (error?.code === 'EPERM' || error?.code === 'EACCES') {
      return
    }
    throw error
  }
  write(dst, 'sentinel.txt', 'destination must remain unchanged\n')
  const before = treeSnapshot(dst)

  assert.throws(
    () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: '9'.repeat(40) }),
    /overlay|symbolic link|reparse point|managed destination/i,
  )
  assert.deepEqual(treeSnapshot(dst), before)
})

test('vendor upgrades a legacy snapshot by reconciling destinations proven by its prior manifest', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const previous = {
    skills: ['skills/ericsson/removed-skill'],
    plugins: ['plugins/removed-plugin'],
    mcpLocal: ['mcp/removed-mcp'],
    workflows: ['capabilities/workflows/removed-workflow.yml'],
  }
  write(dst, 'capabilities/ericsson.json', JSON.stringify(previous))
  for (const rel of [
    'skills/ericsson/removed-skill/SKILL.md',
    'plugins/removed-plugin/plugin.yaml',
    'plugins/removed-mcp/run_server.py',
    'capabilities/workflows/removed-workflow.yml',
    'skills/core-skill/SKILL.md',
    'plugins/unrelated/plugin.yaml',
    'capabilities/workflows/unrelated.yml',
  ]) write(dst, rel, 'preserve or remove by contract\n')

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: 'abc1234' })

  for (const rel of [
    'skills/ericsson/removed-skill',
    'plugins/removed-plugin',
    'plugins/removed-mcp',
    'capabilities/workflows/removed-workflow.yml',
  ]) assert.ok(!fs.existsSync(path.join(dst, rel)), `${rel} should be removed`)
  for (const rel of [
    'skills/core-skill/SKILL.md',
    'plugins/unrelated/plugin.yaml',
    'capabilities/workflows/unrelated.yml',
  ]) assert.ok(fs.existsSync(path.join(dst, rel)), `${rel} should be preserved`)
})

test('vendor ignores forged but well-formed ledger entries not proven by the prior manifest', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, 'capabilities/ericsson.json', JSON.stringify({
    skills: ['skills/ericsson/removed-skill'],
  }))
  write(dst, 'capabilities/ericsson-vendored-paths.json', JSON.stringify([
    'skills/ericsson/removed-skill',
    'plugins/unrelated',
    'capabilities/workflows/unrelated.yml',
  ]))
  write(dst, 'skills/ericsson/removed-skill/SKILL.md', 'old\n')
  write(dst, 'plugins/unrelated/plugin.yaml', 'unrelated\n')
  write(dst, 'capabilities/workflows/unrelated.yml', 'unrelated\n')

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: 'abc1234' })

  assert.ok(!fs.existsSync(path.join(dst, 'skills/ericsson/removed-skill')))
  assert.ok(fs.existsSync(path.join(dst, 'plugins/unrelated/plugin.yaml')))
  assert.ok(fs.existsSync(path.join(dst, 'capabilities/workflows/unrelated.yml')))
})

test('vendor rejects malicious ledger paths before deleting any content', () => {
  for (const malicious of [
    'skills/core-skill',
    '../outside-must-survive',
    '/tmp/outside-must-survive',
    'plugins/../../outside-must-survive',
    'C:\\outside-must-survive',
  ]) {
    const src = tmpSource()
    const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
    write(dst, 'capabilities/ericsson.json', JSON.stringify({
      skills: ['skills/ericsson/removed-skill'],
    }))
    write(dst, 'capabilities/ericsson-vendored-paths.json', JSON.stringify([
      'skills/ericsson/removed-skill',
      malicious,
    ]))
    write(dst, 'skills/ericsson/removed-skill/SKILL.md', 'old\n')
    write(dst, 'skills/core-skill/SKILL.md', 'core\n')
    write(dst, 'plugins/unrelated/plugin.yaml', 'unrelated\n')

    assert.throws(
      () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: 'abc1234' }),
      /unsafe managed destination/,
    )
    assert.ok(fs.existsSync(path.join(dst, 'skills/ericsson/removed-skill/SKILL.md')))
    assert.ok(fs.existsSync(path.join(dst, 'skills/core-skill/SKILL.md')))
    assert.ok(fs.existsSync(path.join(dst, 'plugins/unrelated/plugin.yaml')))
  }
})

test('reconcileManagedPaths rejects traversal and absolute inventory paths', () => {
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const outside = path.join(dst, '..', 'outside-must-survive')
  write(path.dirname(dst), path.basename(outside), 'outside\n')

  for (const malicious of [
    '../outside-must-survive',
    '/tmp/outside-must-survive',
    'plugins/../../outside-must-survive',
    'C:\\outside-must-survive',
  ]) {
    assert.throws(
      () => reconcileManagedPaths({ destRoot: dst, previous: [malicious], current: [] }),
      /unsafe managed destination/,
    )
  }
  assert.ok(fs.existsSync(outside))
})

test('vendor rejects unsafe source manifest paths before copying', () => {
  const src = tmpSource()
  const manifest = JSON.parse(fs.readFileSync(path.join(src, 'sets/ericsson.json'), 'utf8'))
  manifest.skills = ['../outside-skill']
  write(src, 'sets/ericsson.json', JSON.stringify(manifest))
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  assert.throws(
    () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: 'abc1234' }),
    /unsafe manifest source path/,
  )
  assert.ok(!fs.existsSync(path.join(dst, 'outside-skill')))
})

test('vendor rejects a source manifest reached through a symlinked ancestor', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'ecoutside-'))
  const manifest = fs.readFileSync(path.join(src, 'sets/ericsson.json'))
  fs.rmSync(path.join(src, 'sets'), { recursive: true })
  write(outside, 'ericsson.json', manifest)
  fs.symlinkSync(outside, path.join(src, 'sets'), process.platform === 'win32' ? 'junction' : 'dir')

  assert.throws(
    () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: 'abc1234' }),
    /symbolic link|reparse point/i,
  )
  assert.ok(!fs.existsSync(path.join(dst, 'capabilities/ericsson.json')))
})

test('vendor rejects a symlinked destination ancestor instead of escaping the destination root', () => {
  const src = tmpSource({ skills: [], workflowPackages: [], mcpLocal: [] })
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'ecoutside-'))
  fs.symlinkSync(outside, path.join(dst, 'plugins'), process.platform === 'win32' ? 'junction' : 'dir')

  assert.throws(
    () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: 'abc1234' }),
    /symbolic link|reparse point/i,
  )
  assert.deepEqual(fs.readdirSync(outside), [])
})

test('vendor rejects a symlink nested inside a managed destination', () => {
  const src = tmpSource()
  write(src, 'plugins/ericsson-jira/nested/payload.txt', 'must not escape\n')
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'ecoutside-'))
  fs.mkdirSync(path.join(dst, 'plugins/ericsson-jira'), { recursive: true })
  fs.symlinkSync(
    outside,
    path.join(dst, 'plugins/ericsson-jira/nested'),
    process.platform === 'win32' ? 'junction' : 'dir',
  )

  assert.throws(
    () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: 'abc1234' }),
    /symbolic link|reparse point/i,
  )
  assert.deepEqual(fs.readdirSync(outside), [])
})

test('vendor rejects a dangling destination symlink before it can create the outside target', () => {
  const src = tmpSource()
  write(src, 'plugins/ericsson-jira/dangling/payload.txt', 'must not escape\n')
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ecoutside-'))
  const outside = path.join(outsideDir, 'must-not-be-created')
  fs.mkdirSync(path.join(dst, 'plugins/ericsson-jira'), { recursive: true })
  fs.symlinkSync(
    outside,
    path.join(dst, 'plugins/ericsson-jira/dangling'),
    process.platform === 'win32' ? 'junction' : 'dir',
  )

  assert.throws(
    () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: 'abc1234' }),
    /symbolic link|reparse point/i,
  )
  assert.ok(!fs.existsSync(outside))
})

test('vendor exactly replaces every retained managed destination', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  seedCurrentSnapshot(dst)
  write(dst, 'skills/core-skill/SKILL.md', 'unrelated core skill\n')
  write(dst, 'plugins/unrelated/plugin.yaml', 'unrelated plugin\n')
  write(dst, 'capabilities/workflows/unrelated.yml', 'unrelated workflow\n')

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) })

  assert.deepEqual(
    treeSnapshot(path.join(dst, 'skills/ericsson/opportunity-visuals')),
    treeSnapshot(path.join(src, 'skills/ericsson/opportunity-visuals')),
  )
  assert.deepEqual(
    treeSnapshot(path.join(dst, 'plugins/ericsson-jira')),
    treeSnapshot(path.join(src, 'plugins/ericsson-jira')),
  )
  assert.deepEqual(
    treeSnapshot(path.join(dst, 'plugins/outlook-mcp')),
    treeSnapshot(path.join(src, 'mcp/outlook-mcp')),
  )
  assert.deepEqual(
    treeSnapshot(path.join(dst, 'capabilities/workflow-packages/ericsson')),
    treeSnapshot(path.join(src, 'capabilities/workflow-packages/ericsson')),
  )
  assert.equal(
    fs.readFileSync(path.join(dst, 'capabilities/mcp-servers.yaml'), 'utf8'),
    fs.readFileSync(path.join(src, 'mcp/mcp-servers.yaml'), 'utf8'),
  )
  assert.ok(fs.existsSync(path.join(dst, 'skills/core-skill/SKILL.md')))
  assert.ok(fs.existsSync(path.join(dst, 'plugins/unrelated/plugin.yaml')))
  assert.ok(fs.existsSync(path.join(dst, 'capabilities/workflows/unrelated.yml')))
  assert.deepEqual(transactionArtifacts(dst), [])
})

test('vendor removes a retired MCP fragment without touching unrelated capability YAML', () => {
  const src = tmpSource({ mcpServers: null })
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  seedCurrentSnapshot(dst)
  // Legacy ledgers predate MCP-fragment ownership; the prior manifest is the
  // compatibility authority for retiring that file.
  write(dst, 'capabilities/ericsson-vendored-paths.json', JSON.stringify(
    readInventory(dst).filter(rel => rel !== 'capabilities/mcp-servers.yaml'),
    null,
    2,
  ) + '\n')
  write(dst, 'capabilities/unrelated.yaml', 'user-owned configuration\n')

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) })

  assert.ok(!fs.existsSync(path.join(dst, 'capabilities/mcp-servers.yaml')))
  assert.equal(
    fs.readFileSync(path.join(dst, 'capabilities/unrelated.yaml'), 'utf8'),
    'user-owned configuration\n',
  )
  assert.ok(!readInventory(dst).includes('capabilities/mcp-servers.yaml'))
  const manifest = JSON.parse(fs.readFileSync(path.join(dst, 'capabilities/ericsson.json'), 'utf8'))
  assert.equal(Object.hasOwn(manifest, 'mcpServersFile'), false)
})

test('vendor renames an MCP fragment as an owned exact-replacement destination', () => {
  const src = tmpSource({ mcpServers: 'mcp/renamed-servers.yml' })
  write(src, 'mcp/renamed-servers.yml', 'mcp_servers:\n  renamed: {}\n')
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  seedCurrentSnapshot(dst)
  write(dst, 'capabilities/unrelated.yml', 'user-owned configuration\n')

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) })

  assert.ok(!fs.existsSync(path.join(dst, 'capabilities/mcp-servers.yaml')))
  assert.equal(
    fs.readFileSync(path.join(dst, 'capabilities/renamed-servers.yml'), 'utf8'),
    fs.readFileSync(path.join(src, 'mcp/renamed-servers.yml'), 'utf8'),
  )
  assert.equal(
    fs.readFileSync(path.join(dst, 'capabilities/unrelated.yml'), 'utf8'),
    'user-owned configuration\n',
  )
  assert.ok(readInventory(dst).includes('capabilities/renamed-servers.yml'))
  assert.ok(!readInventory(dst).includes('capabilities/mcp-servers.yaml'))
  const manifest = JSON.parse(fs.readFileSync(path.join(dst, 'capabilities/ericsson.json'), 'utf8'))
  assert.equal(manifest.mcpServersFile, 'renamed-servers.yml')
})

test('vendor rolls back deterministically on every pre-completion transaction failure', () => {
  for (const point of [
    'stage-copy',
    'live-backup',
    'staged-publish',
    'ledger-publish',
    'manifest-publish',
  ]) {
    const src = tmpSource()
    write(src, 'plugins/ericsson-jira/new.py', `${point} new bytes\n`)
    const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
    seedCurrentSnapshot(dst)
    write(dst, 'unrelated.txt', `${point} unrelated\n`)
    const before = treeSnapshot(dst)
    let injected = false

    assert.throws(() => vendor({
      sourceDir: src,
      destRoot: dst,
      sourceCommit: '2'.repeat(40),
      faultInjector(candidate) {
        if (!injected && candidate === point) {
          injected = true
          throw new Error(`injected ${point}`)
        }
      },
    }), new RegExp(`injected ${point}`))
    assert.equal(injected, true, `${point} fault must be reached`)
    assert.deepEqual(treeSnapshot(dst), before, `${point} must restore the prior snapshot`)
    assert.deepEqual(transactionArtifacts(dst), [], `${point} must leave no transaction debris`)
  }
})

test('vendor retry recovers an interrupted transaction before publishing an exact snapshot', () => {
  const src = tmpSource()
  write(src, 'plugins/ericsson-jira/new.py', 'new executable\n')
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  seedCurrentSnapshot(dst)
  let interrupted = false

  assert.throws(() => vendor({
    sourceDir: src,
    destRoot: dst,
    sourceCommit: '2'.repeat(40),
    faultInjector(point) {
      if (!interrupted && point === 'staged-publish') {
        interrupted = true
        const error = new Error('simulated process interruption')
        error.simulateInterruption = true
        throw error
      }
    },
  }), /simulated process interruption/)
  assert.notDeepEqual(transactionArtifacts(dst), [])

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) })

  assert.deepEqual(
    treeSnapshot(path.join(dst, 'plugins/ericsson-jira')),
    treeSnapshot(path.join(src, 'plugins/ericsson-jira')),
  )
  assert.deepEqual(transactionArtifacts(dst), [])
  const manifest = JSON.parse(fs.readFileSync(path.join(dst, 'capabilities/ericsson.json'), 'utf8'))
  assert.equal(manifest.vendoredFrom, '2'.repeat(40))
})

test('vendor rejects a legacy unauthenticated recovery journal without changing unrelated content', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, 'plugins/unrelated/plugin.yaml', 'user-owned plugin\n')
  write(dst, 'capabilities/unrelated.yml', 'user-owned workflow\n')
  const transactionId = '11111111-1111-4111-8111-111111111111'
  const txnDir = seedOwnedTransaction(dst, transactionId)
  write(dst, '.ericsson-vendor-transaction.json', JSON.stringify({
    version: 1,
    txnDir,
    transactionId,
    manifestHash: 'a'.repeat(64),
    entries: [
      { rel: 'plugins/unrelated', kind: 'content', existed: false, publish: false },
      { rel: 'capabilities/ericsson-vendored-paths.json', kind: 'ledger', existed: false, publish: true },
      { rel: 'capabilities/ericsson.json', kind: 'manifest', existed: false, publish: true },
    ],
  }, null, 2) + '\n')
  const before = treeSnapshot(dst)

  assert.throws(
    () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) }),
    /journal|transaction|plan|authori[sz]ed|inventory/i,
  )
  assert.deepEqual(treeSnapshot(dst), before)
})

test('vendor rejects a current-schema forged plan that exceeds prior manifest and ledger authority', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  seedCurrentSnapshot(dst)
  write(dst, 'plugins/unrelated/plugin.yaml', 'user-owned plugin\n')
  let interrupted = false
  assert.throws(() => vendor({
    sourceDir: src,
    destRoot: dst,
    sourceCommit: '2'.repeat(40),
    faultInjector(point) {
      if (!interrupted && point === 'staged-publish') {
        interrupted = true
        const error = new Error('simulated process interruption')
        error.simulateInterruption = true
        throw error
      }
    },
  }), /simulated process interruption/)

  const journalPath = path.join(dst, '.ericsson-vendor-transaction.json')
  const journal = JSON.parse(fs.readFileSync(journalPath, 'utf8'))
  const txnRoot = path.join(dst, journal.txnDir)
  const planPath = path.join(txnRoot, 'transaction-plan.json')
  const markerPath = path.join(txnRoot, TRANSACTION_MARKER)
  const plan = JSON.parse(fs.readFileSync(planPath, 'utf8'))
  plan.previous = [...plan.previous, 'plugins/unrelated'].sort()
  const forgedEntry = {
    rel: 'plugins/unrelated',
    kind: 'content',
    publish: false,
    existed: true,
    originalHash: treeHash(path.join(dst, 'plugins/unrelated')),
  }
  const metadataIndex = plan.entries.findIndex(entry => entry.kind === 'ledger')
  plan.entries.splice(metadataIndex, 0, forgedEntry)
  const planContents = JSON.stringify(plan, null, 2) + '\n'
  const planHash = sha256(planContents)
  write(txnRoot, 'transaction-plan.json', planContents)
  write(txnRoot, TRANSACTION_MARKER, JSON.stringify({
    schemaVersion: 1,
    transactionId: journal.transactionId,
    planHash,
  }) + '\n')
  journal.planHash = planHash
  journal.entries = plan.entries
  write(dst, '.ericsson-vendor-transaction.json', JSON.stringify(journal, null, 2) + '\n')
  assert.equal(JSON.parse(fs.readFileSync(markerPath, 'utf8')).planHash, planHash)
  assert.equal(sha256(fs.readFileSync(planPath)), planHash)
  assert.deepEqual(
    JSON.parse(fs.readFileSync(journalPath, 'utf8')).entries,
    JSON.parse(fs.readFileSync(planPath, 'utf8')).entries,
  )
  const before = treeSnapshot(dst)

  assert.throws(
    () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) }),
    /transaction plan exceeds prior manifest and ledger authority/,
  )
  assert.deepEqual(treeSnapshot(dst), before)
  assert.ok(!fs.existsSync(path.join(dst, VENDOR_LOCK)))
})

test('destination lock rejects a concurrent vendor and preserves one coherent snapshot', async () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const ready = path.join(os.tmpdir(), `ericsson-vendor-ready-${process.pid}-${Date.now()}`)
  const release = path.join(os.tmpdir(), `ericsson-vendor-release-${process.pid}-${Date.now()}`)
  const moduleUrl = pathToFileURL(SCRIPT).href
  const worker = `
    import fs from 'node:fs';
    import { vendor } from ${JSON.stringify(moduleUrl)};
    vendor({
      sourceDir: ${JSON.stringify(src)},
      destRoot: ${JSON.stringify(dst)},
      sourceCommit: ${JSON.stringify('2'.repeat(40))},
      faultInjector(point) {
        if (point !== 'lock-acquired') return;
        fs.writeFileSync(${JSON.stringify(ready)}, 'ready\\n');
        const deadline = Date.now() + 10000;
        while (!fs.existsSync(${JSON.stringify(release)}) && Date.now() < deadline) {
          Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 20);
        }
      },
    });
  `
  const first = spawn(process.execPath, ['--input-type=module', '-e', worker], {
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let stderr = ''
  first.stderr.setEncoding('utf8')
  first.stderr.on('data', chunk => { stderr += chunk })
  try {
    await waitForPath(ready)
    assert.throws(
      () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: '3'.repeat(40) }),
      /lock|another vendor|in progress|contention/i,
    )
    write(os.tmpdir(), path.basename(release), 'release\n')
    const result = await waitForChild(first)
    assert.equal(result.code, 0, stderr)
    assert.equal(
      JSON.parse(fs.readFileSync(path.join(dst, 'capabilities/ericsson.json'), 'utf8')).vendoredFrom,
      '2'.repeat(40),
    )
    for (const rel of [
      'skills/ericsson/opportunity-visuals/SKILL.md',
      'plugins/ericsson-jira/plugin.yaml',
      'plugins/outlook-mcp/run_server.py',
      'capabilities/workflow-packages/ericsson/workflows/w.yaml',
      'capabilities/mcp-servers.yaml',
      'capabilities/ericsson-vendored-paths.json',
    ]) assert.ok(fs.existsSync(path.join(dst, rel)), rel)
    assert.ok(!fs.existsSync(path.join(dst, VENDOR_LOCK)))
  } finally {
    fs.rmSync(ready, { force: true })
    fs.rmSync(release, { force: true })
    if (first.exitCode === null) first.kill('SIGKILL')
  }
})

test('vendor recovers an authenticated stale lock and preserves lock lookalikes', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, `${VENDOR_LOCK}/${VENDOR_LOCK_MARKER}`, JSON.stringify({
    schemaVersion: 1,
    token: '11111111-1111-4111-8111-111111111111',
    pid: 2147483647,
    hostname: os.hostname(),
    startedAt: new Date(0).toISOString(),
  }) + '\n')
  write(dst, '.ericsson-vendor-lock-user-notes/readme.txt', 'must survive\n')

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) })

  assert.ok(!fs.existsSync(path.join(dst, VENDOR_LOCK)))
  assert.equal(
    fs.readFileSync(path.join(dst, '.ericsson-vendor-lock-user-notes/readme.txt'), 'utf8'),
    'must survive\n',
  )
  assert.ok(fs.existsSync(path.join(dst, 'skills/ericsson/opportunity-visuals/SKILL.md')))
})

test('vendor recovers when a dead stale-lock reclaimer crashed after publishing its claim', async () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, `${VENDOR_LOCK}/${VENDOR_LOCK_MARKER}`, JSON.stringify({
    schemaVersion: 1,
    token: '11111111-1111-4111-8111-111111111111',
    pid: 2147483647,
    hostname: os.hostname(),
    startedAt: new Date(0).toISOString(),
  }) + '\n')
  const moduleUrl = pathToFileURL(SCRIPT).href
  const worker = `
    import { vendor } from ${JSON.stringify(moduleUrl)};
    vendor({
      sourceDir: ${JSON.stringify(src)},
      destRoot: ${JSON.stringify(dst)},
      sourceCommit: ${JSON.stringify('2'.repeat(40))},
      faultInjector(point) {
        if (point === 'stale-lock-claim-created') throw new Error('crash after recovery claim');
      },
    });
  `
  const claimant = spawn(process.execPath, ['--input-type=module', '-e', worker], {
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  const result = await waitForChild(claimant)
  assert.notEqual(result.code, 0)
  const claimPath = path.join(dst, VENDOR_LOCK, 'recovery-claim.json')
  const deadClaim = JSON.parse(fs.readFileSync(claimPath, 'utf8'))
  assert.equal(deadClaim.pid, claimant.pid)
  assert.equal(deadClaim.hostname, os.hostname())

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) })

  assert.ok(!fs.existsSync(path.join(dst, VENDOR_LOCK)))
  assert.ok(fs.existsSync(path.join(dst, 'capabilities/ericsson.json')))
  assert.ok(fs.existsSync(path.join(dst, 'plugins/ericsson-jira/plugin.yaml')))
})

test('vendor does not steal live, foreign, or malformed stale-lock recovery claims', () => {
  const cases = [
    ['live', JSON.stringify({
      schemaVersion: 1,
      staleToken: '11111111-1111-4111-8111-111111111111',
      claimToken: '22222222-2222-4222-8222-222222222222',
      pid: process.pid,
      hostname: os.hostname(),
    }) + '\n'],
    ['foreign', JSON.stringify({
      schemaVersion: 1,
      staleToken: '11111111-1111-4111-8111-111111111111',
      claimToken: '22222222-2222-4222-8222-222222222222',
      pid: 2147483647,
      hostname: 'foreign-host.invalid',
    }) + '\n'],
    ['malformed', '{not json\n'],
  ]
  for (const [label, claim] of cases) {
    const src = tmpSource()
    const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
    write(dst, `${VENDOR_LOCK}/${VENDOR_LOCK_MARKER}`, JSON.stringify({
      schemaVersion: 1,
      token: '11111111-1111-4111-8111-111111111111',
      pid: 2147483647,
      hostname: os.hostname(),
      startedAt: new Date(0).toISOString(),
    }) + '\n')
    write(dst, `${VENDOR_LOCK}/recovery-claim.json`, claim)
    write(dst, 'sentinel.txt', `${label} must survive\n`)
    const before = treeSnapshot(dst)

    assert.throws(
      () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) }),
      /claim|lock|recover|malformed/i,
    )
    assert.deepEqual(treeSnapshot(dst), before)
  }
})

test('cleanup failure after manifest-last completion preserves a coherent snapshot for retry', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  seedCurrentSnapshot(dst)
  let injected = false

  assert.throws(() => vendor({
    sourceDir: src,
    destRoot: dst,
    sourceCommit: '2'.repeat(40),
    faultInjector(point) {
      if (!injected && point === 'cleanup') {
        injected = true
        throw new Error('injected cleanup')
      }
    },
  }), /completed.*cleanup|cleanup.*completed/i)
  assert.equal(injected, true)
  assert.deepEqual(
    treeSnapshot(path.join(dst, 'skills/ericsson/opportunity-visuals')),
    treeSnapshot(path.join(src, 'skills/ericsson/opportunity-visuals')),
  )
  assert.equal(
    JSON.parse(fs.readFileSync(path.join(dst, 'capabilities/ericsson.json'), 'utf8')).vendoredFrom,
    '2'.repeat(40),
  )
  assert.notDeepEqual(transactionArtifacts(dst), [])

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) })
  assert.deepEqual(transactionArtifacts(dst), [])
})

test('vendor retry removes a valid marked orphan transaction', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  seedCurrentSnapshot(dst)
  const orphan = seedOwnedTransaction(dst)
  write(dst, `${orphan}/staged/plugins/ericsson-jira/stale.py`, 'stale executable\n')

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) })

  assert.ok(!fs.existsSync(path.join(dst, orphan)))
  assert.deepEqual(
    treeSnapshot(path.join(dst, 'plugins/ericsson-jira')),
    treeSnapshot(path.join(src, 'plugins/ericsson-jira')),
  )
})

test('vendor preserves transaction-prefix and journal-temp lookalikes', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, '.ericsson-vendor-txn-user-notes/readme.txt', 'user-owned directory\n')
  write(dst, '.ericsson-vendor-txn-user-file', 'user-owned file\n')
  write(dst, '.ericsson-vendor-transaction.json.tmp-user', 'user-owned file\n')

  vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) })

  assert.equal(
    fs.readFileSync(path.join(dst, '.ericsson-vendor-txn-user-notes/readme.txt'), 'utf8'),
    'user-owned directory\n',
  )
  assert.equal(
    fs.readFileSync(path.join(dst, '.ericsson-vendor-txn-user-file'), 'utf8'),
    'user-owned file\n',
  )
  assert.equal(
    fs.readFileSync(path.join(dst, '.ericsson-vendor-transaction.json.tmp-user'), 'utf8'),
    'user-owned file\n',
  )
})

test('vendor fails closed and preserves exact-name unowned transaction directories', () => {
  for (const markerContents of [undefined, 'not json\n', JSON.stringify({
    schemaVersion: 1,
    transactionId: '22222222-2222-4222-8222-222222222222',
  }) + '\n']) {
    const src = tmpSource()
    const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
    const id = '11111111-1111-4111-8111-111111111111'
    const name = transactionName(id)
    write(dst, `${name}/user.txt`, 'must survive\n')
    if (markerContents !== undefined) write(dst, `${name}/${TRANSACTION_MARKER}`, markerContents)
    const before = treeSnapshot(dst)

    assert.throws(
      () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) }),
      /orphan|ownership|marker|transaction/i,
    )
    assert.deepEqual(treeSnapshot(dst), before)
  }
})

test('vendor rejects and preserves an exact-name orphan transaction symlink', {
  skip: process.platform === 'win32' ? 'symlink creation requires elevated Windows privileges' : false,
}, () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'ecoutside-'))
  write(outside, 'user.txt', 'must survive\n')
  const name = transactionName('11111111-1111-4111-8111-111111111111')
  fs.symlinkSync(outside, path.join(dst, name), 'dir')

  assert.throws(
    () => vendor({ sourceDir: src, destRoot: dst, sourceCommit: '2'.repeat(40) }),
    /symbolic link|orphan|transaction/i,
  )
  assert.equal(fs.readFileSync(path.join(outside, 'user.txt'), 'utf8'), 'must survive\n')
  assert.equal(fs.lstatSync(path.join(dst, name)).isSymbolicLink(), true)
})

test('command-line vendoring records the full exact source commit', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const exactCommit = initGitSource(src)

  runVendorCli(src, dst)

  const manifest = JSON.parse(fs.readFileSync(path.join(dst, 'capabilities/ericsson.json'), 'utf8'))
  assert.equal(manifest.vendoredFrom, exactCommit)
  assert.match(manifest.vendoredFrom, /^[0-9a-f]{40}$/)
})

test('command-line vendoring records exact structured-plugin revision and bytes', () => {
  const connector = {
    path: 'plugins/connector-one',
    id: 'connector-one',
    enabled: false,
  }
  const src = tmpSource({ plugins: ['plugins/workflow', connector] })
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const exactCommit = initGitSource(src)
  const committedDescriptor = execFileSync(
    'git', ['show', `${exactCommit}:plugins/connector-one/plugin.yaml`],
    { cwd: src },
  )

  runVendorCli(src, dst)

  const manifest = JSON.parse(fs.readFileSync(
    path.join(dst, 'capabilities/ericsson.json'),
    'utf8',
  ))
  assert.equal(manifest.vendoredFrom, exactCommit)
  assert.deepEqual(manifest.plugins, ['plugins/workflow', connector])
  assert.deepEqual(
    fs.readFileSync(path.join(dst, 'plugins/connector-one/plugin.yaml')),
    committedDescriptor,
  )
})

test('command-line vendoring rejects dirty tracked, staged, and untracked source state without mutation', () => {
  const cases = {
    'dirty tracked': src => write(src, 'skills/ericsson/opportunity-visuals/SKILL.md', 'dirty tracked\n'),
    staged: src => {
      write(src, 'skills/ericsson/opportunity-visuals/SKILL.md', 'dirty staged\n')
      execFileSync('git', ['add', 'skills/ericsson/opportunity-visuals/SKILL.md'], { cwd: src })
    },
    untracked: src => write(src, 'untracked.txt', 'untracked\n'),
  }
  for (const [label, dirty] of Object.entries(cases)) {
    const src = tmpSource()
    const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
    initGitSource(src)
    dirty(src)
    write(dst, 'sentinel.txt', `${label} destination\n`)
    const before = treeSnapshot(dst)

    assert.throws(() => runVendorCli(src, dst), /clean|dirty|provenance|Git/i, label)
    assert.deepEqual(treeSnapshot(dst), before, `${label} must not mutate destination`)
  }
})

test('command-line vendoring rejects a non-Git source without mutation', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, 'sentinel.txt', 'destination\n')
  const before = treeSnapshot(dst)

  assert.throws(() => runVendorCli(src, dst), /Git|provenance|worktree/i)
  assert.deepEqual(treeSnapshot(dst), before)
})

test('command-line vendoring rejects a source subdirectory that is not the worktree root', () => {
  const src = tmpSource()
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ecroot-'))
  const nested = path.join(root, 'nested-capabilities')
  fs.cpSync(src, nested, { recursive: true })
  initGitSource(root)
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, 'sentinel.txt', 'destination\n')
  const before = treeSnapshot(dst)

  assert.throws(() => runVendorCli(nested, dst), /worktree root|provenance/i)
  assert.deepEqual(treeSnapshot(dst), before)
})

test('command-line vendoring fails closed when Git cannot execute', () => {
  const src = tmpSource()
  initGitSource(src)
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, 'sentinel.txt', 'destination\n')
  const before = treeSnapshot(dst)
  const noExecutables = fs.mkdtempSync(path.join(os.tmpdir(), 'empty-path-'))
  const env = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => key.toUpperCase() !== 'PATH'),
  )

  assert.throws(() => execFileSync(process.execPath, [SCRIPT], {
    cwd: dst,
    env: { ...env, PATH: noExecutables, ERICSSON_CAPABILITIES_DIR: src },
    stdio: ['ignore', 'pipe', 'pipe'],
  }), /Git|spawn|ENOENT|provenance/i)
  assert.deepEqual(treeSnapshot(dst), before)
})

test('command-line vendoring rejects dirty submodule state without mutation', () => {
  const submodule = fs.mkdtempSync(path.join(os.tmpdir(), 'ecsubmodule-'))
  write(submodule, 'tracked.txt', 'clean\n')
  initGitSource(submodule)

  const src = tmpSource()
  initGitSource(src)
  execFileSync('git', ['-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', submodule, 'vendor-fixture'], { cwd: src })
  execFileSync('git', ['commit', '-qam', 'add submodule'], { cwd: src })
  write(src, 'vendor-fixture/tracked.txt', 'dirty submodule\n')
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, 'sentinel.txt', 'destination\n')
  const before = treeSnapshot(dst)

  assert.throws(() => runVendorCli(src, dst), /clean|dirty|submodule|provenance/i)
  assert.deepEqual(treeSnapshot(dst), before)
})

test('command-line vendoring rejects ignored untracked bytes inside a copied source path', () => {
  const src = tmpSource()
  write(src, '.gitignore', '*.ignored\n')
  initGitSource(src)
  write(src, 'plugins/ericsson-jira/hidden.ignored', 'not present in HEAD\n')
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  write(dst, 'sentinel.txt', 'destination\n')
  const before = treeSnapshot(dst)

  assert.throws(() => runVendorCli(src, dst), /clean|untracked|ignored|provenance/i)
  assert.deepEqual(treeSnapshot(dst), before)
})

test('command-line vendoring rejects assume-unchanged and skip-worktree index flags', () => {
  for (const [label, flag] of [
    ['assume-unchanged', '--assume-unchanged'],
    ['skip-worktree', '--skip-worktree'],
  ]) {
    const src = tmpSource()
    const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
    initGitSource(src)
    const rel = 'plugins/ericsson-jira/plugin.yaml'
    execFileSync('git', ['update-index', flag, rel], { cwd: src })
    write(src, rel, `${label} bytes hidden from status\n`)
    write(dst, 'sentinel.txt', `${label} destination\n`)
    const before = treeSnapshot(dst)

    assert.throws(
      () => runVendorCli(src, dst),
      /assume-unchanged|skip-worktree|index flag|provenance/i,
      label,
    )
    assert.deepEqual(treeSnapshot(dst), before, `${label} must not mutate destination`)
  }
})

test('command-line vendoring rejects Git replace refs without destination mutation', () => {
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const originalCommit = initGitSource(src)
  const rel = 'plugins/ericsson-jira/plugin.yaml'
  const original = fs.readFileSync(path.join(src, rel), 'utf8')
  write(src, rel, 'replacement commit bytes\n')
  execFileSync('git', ['add', rel], { cwd: src })
  const replacementTree = execFileSync('git', ['write-tree'], { cwd: src, encoding: 'utf8' }).trim()
  const replacementCommit = execFileSync(
    'git', ['commit-tree', replacementTree, '-m', 'replacement'],
    { cwd: src, encoding: 'utf8' },
  ).trim()
  execFileSync('git', ['replace', originalCommit, replacementCommit], { cwd: src })
  execFileSync('git', ['checkout', '--detach', '-q', '-f', originalCommit], { cwd: src })
  assert.equal(
    execFileSync('git', ['status', '--porcelain=v1'], { cwd: src, encoding: 'utf8' }).trim(),
    '',
  )
  assert.equal(
    execFileSync('git', ['--no-replace-objects', 'show', `${originalCommit}:${rel}`], {
      cwd: src,
      encoding: 'utf8',
    }),
    original,
  )
  write(dst, 'sentinel.txt', 'destination\n')
  const before = treeSnapshot(dst)

  assert.throws(() => runVendorCli(src, dst), /replace|provenance|rewrit/i)
  assert.deepEqual(treeSnapshot(dst), before)
})

test('provenance vendoring copies the committed index snapshot after validation', async () => {
  const module = await import('../vendor-ericsson.mjs')
  assert.equal(typeof module.vendorFromCleanGitSource, 'function')
  const src = tmpSource()
  const dst = fs.mkdtempSync(path.join(os.tmpdir(), 'ecdst-'))
  const exactCommit = initGitSource(src)
  const rel = 'plugins/ericsson-jira/plugin.yaml'
  const committed = fs.readFileSync(path.join(src, rel), 'utf8')

  module.vendorFromCleanGitSource({
    sourceDir: src,
    destRoot: dst,
    afterValidation() {
      write(src, rel, 'mutation after provenance validation\n')
    },
  })

  assert.equal(fs.readFileSync(path.join(dst, rel), 'utf8'), committed)
  const manifest = JSON.parse(fs.readFileSync(path.join(dst, 'capabilities/ericsson.json'), 'utf8'))
  assert.equal(manifest.vendoredFrom, exactCommit)
})
