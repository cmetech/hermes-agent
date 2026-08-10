// Vendor the ericsson-capabilities set into this repo (base). Manifest-driven:
// reads sets/ericsson.json and copies exactly what it lists. See CLAUDE.md brand row.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { pathToFileURL } from 'node:url'

const INVENTORY_FILE = 'capabilities/ericsson-vendored-paths.json'
const MANIFEST_FILE = 'capabilities/ericsson.json'
const JOURNAL_FILE = '.ericsson-vendor-transaction.json'
const TRANSACTION_PREFIX = '.ericsson-vendor-txn-'
const TRANSACTION_MARKER = '.ericsson-vendor-owned.json'
const TRANSACTION_PLAN = 'transaction-plan.json'
const TRANSACTION_SCHEMA_VERSION = 1
const TRANSACTION_NAME = /^\.ericsson-vendor-txn-([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i
const LOCK_DIR = '.ericsson-vendor-lock'
const LOCK_MARKER = 'owner.json'
const LOCK_RECOVERY_CLAIM = 'recovery-claim.json'
const LOCK_SCHEMA_VERSION = 1
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const PLUGIN_PATH = /^plugins\/[a-z0-9][a-z0-9_-]*$/
const PLUGIN_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/
const LIFECYCLE_MIGRATION_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/
const PLUGIN_OBJECT_FIELDS = new Set(['path', 'id', 'enabled', 'lifecycleMigration'])

function assertStrictRelativePath(rel, label) {
  if (typeof rel !== 'string' || rel.length === 0
    || rel.includes('\\') || /^[A-Za-z]:/.test(rel)
    || path.posix.isAbsolute(rel) || path.posix.normalize(rel) !== rel
    || rel === '.' || rel.startsWith('../') || rel.includes('/../')) {
    throw new Error(`unsafe ${label}: ${String(rel)}`)
  }
  return rel
}

function assertManifestSourcePath(rel, kind) {
  assertStrictRelativePath(rel, 'manifest source path')
  const patterns = {
    skill: /^skills\/ericsson\/[^/]+$/,
    plugin: /^plugins\/[^/]+$/,
    mcpLocal: /^mcp\/[^/]+$/,
    workflow: /^workflows\/[^/]+$/,
    workflowPackage: /^capabilities\/workflow-packages\/[A-Za-z0-9][A-Za-z0-9._-]*$/,
    mcpServers: /^mcp\/[^/]+\.ya?ml$/i,
  }
  if (!patterns[kind]?.test(rel)) {
    throw new Error(`unsafe manifest source path: ${rel}`)
  }
  return rel
}

function assertManagedDestination(rel) {
  assertStrictRelativePath(rel, 'managed destination')
  const allowed = /^skills\/ericsson\/[^/]+$/.test(rel)
    || /^plugins\/[^/]+$/.test(rel)
    || /^capabilities\/workflow-packages\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(rel)
    || /^capabilities\/workflows\/[^/]+$/.test(rel)
    || /^capabilities\/[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml$/i.test(rel)
  if (!allowed) throw new Error(`unsafe managed destination: ${rel}`)
  return rel
}

function mcpDestinationFromManifest(manifest, { includeLegacyFile = true } = {}) {
  let destination
  if (manifest.mcpServers) {
    const source = assertManifestSourcePath(manifest.mcpServers, 'mcpServers')
    destination = `capabilities/${path.posix.basename(source)}`
    assertManagedDestination(destination)
  }
  if (includeLegacyFile && manifest.mcpServersFile) {
    assertStrictRelativePath(manifest.mcpServersFile, 'vendored MCP fragment')
    if (path.posix.basename(manifest.mcpServersFile) !== manifest.mcpServersFile
      || !/^[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml$/i.test(manifest.mcpServersFile)) {
      throw new Error(`unsafe vendored MCP fragment: ${manifest.mcpServersFile}`)
    }
    const legacyDestination = assertManagedDestination(`capabilities/${manifest.mcpServersFile}`)
    if (destination && destination !== legacyDestination) {
      throw new Error('vendored MCP fragment fields disagree')
    }
    destination = legacyDestination
  }
  return destination
}

function normalizePluginEntries(manifest) {
  const rawEntries = manifest.plugins ?? []
  if (!Array.isArray(rawEntries)) throw new Error('plugin metadata must be a list')

  const normalized = []
  const ids = new Set()
  const legacyPaths = new Set()
  const structuredPaths = new Set()
  const migrationIds = new Set()
  for (const raw of rawEntries) {
    if (typeof raw === 'string') {
      const rel = assertManifestSourcePath(raw, 'plugin')
      if (!PLUGIN_PATH.test(rel)) throw new Error(`unsafe manifest source path: ${rel}`)
      if (structuredPaths.has(rel)) throw new Error(`duplicate plugin metadata path: ${rel}`)
      const id = path.posix.basename(rel)
      normalized.push({ path: rel, id, enabled: true, structured: false })
      legacyPaths.add(rel)
      ids.add(id)
      continue
    }

    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error('plugin metadata entries must be paths or objects')
    }
    const keys = Object.keys(raw)
    if (!['path', 'id', 'enabled'].every(key => keys.includes(key))
      || keys.some(key => !PLUGIN_OBJECT_FIELDS.has(key))) {
      throw new Error('structured plugin metadata has invalid fields')
    }
    const rel = assertManifestSourcePath(raw.path, 'plugin')
    if (!PLUGIN_PATH.test(rel)) throw new Error(`unsafe manifest source path: ${rel}`)
    if (typeof raw.id !== 'string' || !PLUGIN_ID.test(raw.id)) {
      throw new Error('structured plugin metadata id must be a bounded slug')
    }
    if (typeof raw.enabled !== 'boolean') {
      throw new Error('structured plugin metadata enabled must be boolean')
    }
    if (legacyPaths.has(rel) || structuredPaths.has(rel) || ids.has(raw.id)) {
      throw new Error('duplicate structured plugin metadata path or id')
    }

    let lifecycleMigration
    if (Object.hasOwn(raw, 'lifecycleMigration')) {
      const migration = raw.lifecycleMigration
      if (raw.enabled || !migration || typeof migration !== 'object'
        || Array.isArray(migration)
        || Object.keys(migration).sort().join(',') !== 'from,id'
        || migration.from !== 'auto_seeded_backend'
        || typeof migration.id !== 'string'
        || !LIFECYCLE_MIGRATION_ID.test(migration.id)
        || migrationIds.has(migration.id)) {
        throw new Error('invalid or duplicate plugin lifecycle migration metadata')
      }
      migrationIds.add(migration.id)
      lifecycleMigration = migration
    }

    structuredPaths.add(rel)
    ids.add(raw.id)
    normalized.push({
      path: rel,
      id: raw.id,
      enabled: raw.enabled,
      structured: true,
      lifecycleMigration,
    })
  }
  return normalized
}

function unquoteYamlScalar(raw) {
  const value = raw.trim()
  if (value.startsWith("'") && value.endsWith("'")) {
    return value.slice(1, -1).replaceAll("''", "'")
  }
  if (value.startsWith('"') && value.endsWith('"')) {
    try {
      return JSON.parse(value)
    } catch {
      throw new Error('plugin descriptor contains an invalid quoted scalar')
    }
  }
  return value.split(/\s+#/, 1)[0].trim()
}

function readPluginDescriptor(sourceDir, entry) {
  const descriptorRel = path.posix.join(entry.path, 'plugin.yaml')
  assertNoSymlinkComponents(sourceDir, descriptorRel, 'plugin descriptor')
  const descriptorPath = path.join(sourceDir, descriptorRel)
  const stat = lstatIfPresent(descriptorPath)
  if (!stat || stat.isSymbolicLink() || !stat.isFile()) {
    throw new Error(`plugin descriptor is not a regular file: ${descriptorRel}`)
  }
  const fields = new Map()
  for (const line of fs.readFileSync(descriptorPath, 'utf8').split(/\r?\n/)) {
    if (!line || /^\s/.test(line) || /^\s*#/.test(line)) continue
    const match = /^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$/.exec(line)
    if (match) fields.set(match[1], unquoteYamlScalar(match[2]))
  }
  const expectedKind = entry.enabled ? 'backend' : 'standalone'
  if (fields.get('name') !== entry.id || fields.get('kind') !== expectedKind) {
    throw new Error(
      `plugin descriptor name/kind does not match manifest metadata: ${entry.path}`,
    )
  }
}

function sourceDestinationPairs(manifest) {
  const plugins = normalizePluginEntries(manifest)
  const pairs = [
    ...(manifest.skills || []).map(rel => [assertManifestSourcePath(rel, 'skill'), rel]),
    ...plugins
      .filter(entry => entry.path !== 'plugins/workflow')
      .map(entry => [entry.path, entry.path]),
    ...(manifest.mcpLocal || []).map(rel => [
      assertManifestSourcePath(rel, 'mcpLocal'),
      path.posix.join('plugins', path.posix.basename(rel)),
    ]),
    ...(manifest.workflowPackages || []).map(entry => {
      if (!entry || Object.keys(entry).sort().join(',') !== 'digestManifest,path') {
        throw new Error('workflowPackages entries require digestManifest and path')
      }
      const source = assertManifestSourcePath(entry.path, 'workflowPackage')
      assertStrictRelativePath(entry.digestManifest, 'workflow digest manifest')
      if (entry.digestManifest !== `${source}/digests.json`) {
        throw new Error('workflow digest manifest must be inside its package root')
      }
      return [source, source]
    }),
  ]
  const sourcesByDestination = new Map()
  for (const [source, destination] of pairs) {
    assertManagedDestination(destination)
    const priorSource = sourcesByDestination.get(destination)
    if (priorSource && priorSource !== source) {
      throw new Error(`multiple manifest sources map to managed destination: ${destination}`)
    }
    sourcesByDestination.set(destination, source)
  }
  return [...sourcesByDestination].map(([destination, source]) => [source, destination])
}

// Return the exact repo-relative destinations owned by a source or vendored
// Ericsson manifest. Vendored workflow paths are accepted for backward
// compatibility; all other fields retain their source-manifest shape.
export function managedDestinations(manifest, { includeLegacyMcpFile = true } = {}) {
  const destinations = []
  for (const rel of manifest.skills || []) {
    assertManagedDestination(rel)
    destinations.push(rel)
  }
  for (const entry of normalizePluginEntries(manifest)) {
    if (entry.path === 'plugins/workflow') continue
    assertManagedDestination(entry.path)
    destinations.push(entry.path)
  }
  for (const rel of manifest.mcpLocal || []) {
    assertManifestSourcePath(rel, 'mcpLocal')
    destinations.push(path.posix.join('plugins', path.posix.basename(rel)))
  }
  for (const rel of manifest.workflows || []) {
    assertStrictRelativePath(rel, 'manifest source path')
    if (!/^workflows\/[^/]+$/.test(rel) && !/^capabilities\/workflows\/[^/]+$/.test(rel)) {
      throw new Error(`unsafe manifest source path: ${rel}`)
    }
    destinations.push(path.posix.join('capabilities/workflows', path.posix.basename(rel)))
  }
  for (const entry of manifest.workflowPackages || []) {
    if (!entry || Object.keys(entry).sort().join(',') !== 'digestManifest,path') {
      throw new Error('workflowPackages entries require digestManifest and path')
    }
    const destination = assertManifestSourcePath(entry.path, 'workflowPackage')
    assertStrictRelativePath(entry.digestManifest, 'workflow digest manifest')
    if (entry.digestManifest !== `${destination}/digests.json`) {
      throw new Error('workflow digest manifest must be inside its package root')
    }
    destinations.push(destination)
  }
  const mcpDestination = mcpDestinationFromManifest(manifest, {
    includeLegacyFile: includeLegacyMcpFile,
  })
  if (mcpDestination) destinations.push(mcpDestination)
  return [...new Set(destinations.map(assertManagedDestination))].sort()
}

function isWithin(root, candidate) {
  const relative = path.relative(root, candidate)
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative))
}

function lstatIfPresent(target) {
  try {
    return fs.lstatSync(target)
  } catch (error) {
    if (error?.code === 'ENOENT') return undefined
    throw error
  }
}

function assertNoSymlinkComponents(root, rel, label) {
  const rootReal = fs.realpathSync.native(root)
  let current = root
  for (const component of rel.split('/')) {
    current = path.join(current, component)
    const stat = lstatIfPresent(current)
    if (!stat) continue
    if (stat.isSymbolicLink()) {
      throw new Error(`${label} contains a symbolic link or reparse point: ${rel}`)
    }
    const currentReal = fs.realpathSync.native(current)
    if (!isWithin(rootReal, currentReal)) throw new Error(`${label} escapes root: ${rel}`)
  }
}

function assertSafeSourceTree(sourceDir, rel) {
  assertNoSymlinkComponents(sourceDir, rel, 'manifest source path')
  const target = path.join(sourceDir, rel)
  if (!fs.existsSync(target)) throw new Error(`manifest lists missing path: ${rel}`)
  const visit = current => {
    const stat = fs.lstatSync(current)
    if (stat.isSymbolicLink()) {
      throw new Error(`manifest source path contains a symbolic link or reparse point: ${rel}`)
    }
    if (stat.isDirectory()) {
      for (const entry of fs.readdirSync(current)) visit(path.join(current, entry))
    }
  }
  visit(target)
}

function assertSafeDestination(destRoot, rel) {
  assertManagedDestination(rel)
  assertNoSymlinkComponents(destRoot, rel, 'managed destination')
}

function assertSafeExistingTree(destRoot, rel) {
  assertNoSymlinkComponents(destRoot, rel, 'managed destination')
  const target = path.join(destRoot, rel)
  const initial = lstatIfPresent(target)
  if (!initial) return
  const visit = current => {
    const stat = fs.lstatSync(current)
    if (stat.isSymbolicLink()) {
      throw new Error(`managed destination contains a symbolic link or reparse point: ${rel}`)
    }
    if (stat.isDirectory()) {
      for (const entry of fs.readdirSync(current)) visit(path.join(current, entry))
    }
  }
  visit(target)
}

function assertTransactionDestination(rel, kind = 'content') {
  if (kind === 'manifest' && rel === MANIFEST_FILE) return rel
  if (kind === 'ledger' && rel === INVENTORY_FILE) return rel
  if (kind === 'mcp' && /^capabilities\/[^/]+\.ya?ml$/.test(rel)) {
    return assertStrictRelativePath(rel, 'transaction destination')
  }
  return assertManagedDestination(rel)
}

function sha256(contents) {
  return createHash('sha256').update(contents).digest('hex')
}

function fileHashIfPresent(file) {
  const stat = lstatIfPresent(file)
  if (!stat || !stat.isFile()) return undefined
  return sha256(fs.readFileSync(file))
}

function treeHashIfPresent(target) {
  const initial = lstatIfPresent(target)
  if (!initial) return undefined
  const records = []
  const visit = (current, relative = '') => {
    const stat = fs.lstatSync(current)
    if (stat.isSymbolicLink()) throw new Error(`cannot hash symbolic link or reparse point: ${current}`)
    if (stat.isDirectory()) {
      records.push(`d\0${relative}\0`)
      for (const entry of fs.readdirSync(current).sort()) {
        visit(path.join(current, entry), relative ? `${relative}/${entry}` : entry)
      }
      return
    }
    if (!stat.isFile()) throw new Error(`cannot hash non-file vendor content: ${current}`)
    records.push(`f\0${relative}\0${sha256(fs.readFileSync(current))}\0`)
  }
  visit(target)
  return sha256(records.join(''))
}

function atomicWriteNew(file, contents) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const temporary = `${file}.tmp-${process.pid}-${randomUUID()}`
  try {
    fs.writeFileSync(temporary, contents, { flag: 'wx' })
    fs.renameSync(temporary, file)
  } finally {
    fs.rmSync(temporary, { force: true })
  }
}

function atomicReplace(file, contents) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const temporary = `${file}.tmp-${process.pid}-${randomUUID()}`
  try {
    fs.writeFileSync(temporary, contents, { flag: 'wx' })
    fs.renameSync(temporary, file)
  } finally {
    fs.rmSync(temporary, { force: true })
  }
}

function readStrictJsonFile(file, label) {
  const stat = lstatIfPresent(file)
  if (!stat || stat.isSymbolicLink() || !stat.isFile()) {
    throw new Error(`${label} is not a regular file`)
  }
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch (error) {
    throw new Error(`${label} is malformed`, { cause: error })
  }
}

function isProcessAlive(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    if (error?.code === 'ESRCH') return false
    return true
  }
}

function validateLockOwner(raw) {
  if (!raw || raw.schemaVersion !== LOCK_SCHEMA_VERSION
    || !UUID.test(raw.token || '')
    || !Number.isSafeInteger(raw.pid) || raw.pid <= 0
    || typeof raw.hostname !== 'string' || raw.hostname.length === 0
    || typeof raw.startedAt !== 'string' || !Number.isFinite(Date.parse(raw.startedAt))
    || Object.keys(raw).sort().join(',') !== 'hostname,pid,schemaVersion,startedAt,token') {
    throw new Error('Ericsson vendor lock has an invalid ownership marker')
  }
  return raw
}

function readLockOwner(lockRoot) {
  return validateLockOwner(readStrictJsonFile(
    path.join(lockRoot, LOCK_MARKER),
    'Ericsson vendor lock ownership marker',
  ))
}

function validateRecoveryClaim(raw, observed) {
  if (!raw || raw.schemaVersion !== LOCK_SCHEMA_VERSION
    || raw.staleToken !== observed.token
    || !UUID.test(raw.claimToken || '')
    || !Number.isSafeInteger(raw.pid) || raw.pid <= 0
    || typeof raw.hostname !== 'string' || raw.hostname.length === 0
    || Object.keys(raw).sort().join(',')
      !== 'claimToken,hostname,pid,schemaVersion,staleToken') {
    throw new Error('Ericsson vendor stale-lock recovery claim is invalid')
  }
  return raw
}

function readRecoveryClaim(claimPath, observed) {
  return validateRecoveryClaim(readStrictJsonFile(
    claimPath,
    'Ericsson vendor stale-lock recovery claim',
  ), observed)
}

function writeRecoveryClaim(claimPath, observed, claimToken) {
  fs.writeFileSync(claimPath, JSON.stringify({
    schemaVersion: LOCK_SCHEMA_VERSION,
    staleToken: observed.token,
    claimToken,
    pid: process.pid,
    hostname: os.hostname(),
  }) + '\n', { flag: 'wx', mode: 0o600 })
}

function reclaimAbandonedRecoveryClaim(lockRoot, claimPath, observed, claimToken) {
  const abandoned = readRecoveryClaim(claimPath, observed)
  if (abandoned.hostname !== os.hostname() || isProcessAlive(abandoned.pid)) {
    throw new Error(
      `another Ericsson vendor is recovering the stale lock (pid ${abandoned.pid} on ${abandoned.hostname})`,
    )
  }
  const reclaimPath = path.join(lockRoot, `recovery-reclaim-${abandoned.claimToken}.json`)
  fs.writeFileSync(reclaimPath, JSON.stringify({
    schemaVersion: LOCK_SCHEMA_VERSION,
    abandonedClaimToken: abandoned.claimToken,
    claimToken,
    pid: process.pid,
    hostname: os.hostname(),
  }) + '\n', { flag: 'wx', mode: 0o600 })
  const currentOwner = readLockOwner(lockRoot)
  const currentClaim = readRecoveryClaim(claimPath, observed)
  if (currentOwner.token !== observed.token
    || JSON.stringify(currentClaim) !== JSON.stringify(abandoned)) {
    throw new Error('Ericsson vendor stale-lock recovery ownership changed during reclaim')
  }
  const abandonedPath = path.join(
    lockRoot,
    `recovery-claim-abandoned-${abandoned.claimToken}.json`,
  )
  if (lstatIfPresent(abandonedPath)) {
    throw new Error('Ericsson vendor abandoned-claim quarantine already exists')
  }
  fs.renameSync(claimPath, abandonedPath)
  writeRecoveryClaim(claimPath, observed, claimToken)
}

function recoverStaleLock(destRoot, observed, faultInjector) {
  const lockRoot = path.join(destRoot, LOCK_DIR)
  if (observed.hostname !== os.hostname() || isProcessAlive(observed.pid)) {
    throw new Error(
      `another Ericsson vendor is in progress (pid ${observed.pid} on ${observed.hostname})`,
    )
  }
  const claimToken = randomUUID()
  const claimPath = path.join(lockRoot, LOCK_RECOVERY_CLAIM)
  try {
    writeRecoveryClaim(claimPath, observed, claimToken)
  } catch (error) {
    if (error?.code === 'EEXIST') {
      try {
        reclaimAbandonedRecoveryClaim(lockRoot, claimPath, observed, claimToken)
      } catch (claimError) {
        if (claimError?.code === 'EEXIST') {
          throw new Error('another Ericsson vendor is reclaiming a stale recovery claim', {
            cause: claimError,
          })
        }
        throw claimError
      }
    } else {
      throw error
    }
  }
  faultInjector('stale-lock-claim-created', LOCK_DIR)
  const current = readLockOwner(lockRoot)
  if (current.token !== observed.token) {
    throw new Error('Ericsson vendor lock ownership changed during stale recovery')
  }
  const quarantine = path.join(destRoot, `${LOCK_DIR}-stale-${observed.token}`)
  if (lstatIfPresent(quarantine)) {
    throw new Error('Ericsson vendor stale-lock quarantine already exists')
  }
  fs.renameSync(lockRoot, quarantine)
  fs.rmSync(quarantine, { recursive: true, force: true })
}

// Lock policy: contention fails immediately. A same-host lock is recovered
// only when its strictly validated owner PID no longer exists. Foreign-host,
// malformed, symlinked, and unmarked locks fail closed and are never removed.
function acquireVendorLock(destRoot, faultInjector) {
  const lockRoot = path.join(destRoot, LOCK_DIR)
  assertNoSymlinkComponents(destRoot, LOCK_DIR, 'Ericsson vendor lock')
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const token = randomUUID()
    try {
      fs.mkdirSync(lockRoot, { mode: 0o700 })
      try {
        atomicWriteNew(path.join(lockRoot, LOCK_MARKER), JSON.stringify({
          schemaVersion: LOCK_SCHEMA_VERSION,
          token,
          pid: process.pid,
          hostname: os.hostname(),
          startedAt: new Date().toISOString(),
        }) + '\n')
      } catch (error) {
        fs.rmSync(lockRoot, { recursive: true, force: true })
        throw error
      }
      return {
        token,
        release() {
          const stat = lstatIfPresent(lockRoot)
          if (!stat) return
          if (stat.isSymbolicLink() || !stat.isDirectory()) {
            throw new Error('Ericsson vendor lock changed type before release')
          }
          const owner = readLockOwner(lockRoot)
          if (owner.token !== token || owner.pid !== process.pid || owner.hostname !== os.hostname()) {
            throw new Error('Ericsson vendor lock ownership changed before release')
          }
          const releaseRoot = path.join(destRoot, `${LOCK_DIR}-release-${token}`)
          fs.renameSync(lockRoot, releaseRoot)
          fs.rmSync(releaseRoot, { recursive: true, force: true })
        },
      }
    } catch (error) {
      if (error?.code !== 'EEXIST') throw error
      const stat = lstatIfPresent(lockRoot)
      if (!stat || stat.isSymbolicLink() || !stat.isDirectory()) {
        throw new Error('Ericsson vendor lock is not an owned directory', { cause: error })
      }
      const observed = readLockOwner(lockRoot)
      recoverStaleLock(destRoot, observed, faultInjector)
    }
  }
  throw new Error('could not acquire Ericsson vendor lock after stale-lock recovery')
}

function readJsonIfPresent(file) {
  if (!fs.existsSync(file)) return undefined
  return JSON.parse(fs.readFileSync(file, 'utf8'))
}

function managedInventoryFromMetadata(previousManifest, ledger) {
  const provenByManifest = previousManifest ? managedDestinations(previousManifest) : []
  if (ledger === undefined) return provenByManifest
  if (!Array.isArray(ledger)) throw new Error('invalid Ericsson managed-path inventory')
  const ledgerSet = new Set(ledger.map(assertManagedDestination))
  const priorMcpDestination = previousManifest
    ? mcpDestinationFromManifest(previousManifest)
    : undefined
  // A ledger is an index, not deletion authority. Requiring its entries to
  // agree with the prior vendored manifest prevents a forged ledger from
  // deleting unrelated plugins or workflows. MCP ownership was added after
  // the first ledger format, so a prior manifest remains compatibility
  // authority for that one exact YAML destination.
  return provenByManifest.filter(rel => ledgerSet.has(rel) || rel === priorMcpDestination)
}

function previousManagedInventory(destRoot) {
  const capabilitiesDir = path.join(destRoot, 'capabilities')
  const previousManifest = readJsonIfPresent(path.join(capabilitiesDir, 'ericsson.json'))
  const ledger = readJsonIfPresent(path.join(destRoot, INVENTORY_FILE))
  return managedInventoryFromMetadata(previousManifest, ledger)
}

export function reconcileManagedPaths({ destRoot, previous, current }) {
  const previousPaths = [...new Set(previous.map(assertManagedDestination))].sort()
  const keep = new Set(current.map(assertManagedDestination))
  for (const rel of previousPaths) assertSafeDestination(destRoot, rel)
  for (const rel of previousPaths) {
    if (!keep.has(rel)) fs.rmSync(path.join(destRoot, rel), { recursive: true, force: true })
  }
}

function copyRec(src, dst, destRoot, sourceRoot) {
  const destRelative = path.relative(destRoot, dst).split(path.sep).join('/')
  assertStrictRelativePath(destRelative, 'copy destination')
  assertNoSymlinkComponents(destRoot, destRelative, 'copy destination')
  fs.mkdirSync(path.dirname(dst), { recursive: true })
  const st = fs.lstatSync(src)
  if (st.isSymbolicLink()) throw new Error(`source copy contains a symbolic link or reparse point: ${src}`)
  if (sourceRoot && !isWithin(fs.realpathSync.native(sourceRoot), fs.realpathSync.native(src))) {
    throw new Error(`source copy escapes root: ${src}`)
  }
  if (st.isDirectory()) {
    fs.mkdirSync(dst, { recursive: true })
    for (const e of fs.readdirSync(src)) {
      if (['__pycache__', '.venv', '.pytest_cache', '.git'].includes(e)) continue
      copyRec(path.join(src, e), path.join(dst, e), destRoot, sourceRoot)
    }
  } else {
    fs.copyFileSync(src, dst)
  }
}

function transactionIdentity(name) {
  const match = TRANSACTION_NAME.exec(name)
  return match ? match[1] : undefined
}

function validateOwnedTransaction(destRoot, name) {
  const transactionId = transactionIdentity(name)
  if (!transactionId) throw new Error(`invalid Ericsson vendor transaction directory: ${name}`)
  const txnRoot = path.join(destRoot, name)
  const stat = lstatIfPresent(txnRoot)
  if (!stat || stat.isSymbolicLink() || !stat.isDirectory()) {
    throw new Error(`orphan transaction is not an owned directory: ${name}`)
  }
  const markerPath = path.join(txnRoot, TRANSACTION_MARKER)
  const markerStat = lstatIfPresent(markerPath)
  if (!markerStat || markerStat.isSymbolicLink() || !markerStat.isFile()) {
    throw new Error(`orphan transaction has no valid ownership marker: ${name}`)
  }
  let marker
  try {
    marker = JSON.parse(fs.readFileSync(markerPath, 'utf8'))
  } catch (error) {
    throw new Error(`orphan transaction has a malformed ownership marker: ${name}`, { cause: error })
  }
  const markerKeys = Object.keys(marker).sort().join(',')
  const validKeys = markerKeys === 'schemaVersion,transactionId'
    || markerKeys === 'planHash,schemaVersion,transactionId'
  if (marker.schemaVersion !== TRANSACTION_SCHEMA_VERSION
    || marker.transactionId !== transactionId
    || !validKeys
    || (marker.planHash !== undefined && !/^[0-9a-f]{64}$/.test(marker.planHash))) {
    throw new Error(`orphan transaction ownership marker does not match: ${name}`)
  }
  return { transactionId, txnRoot, marker }
}

function normalizeTransactionEntries(entries) {
  if (!Array.isArray(entries)) throw new Error('invalid Ericsson vendor transaction entries')
  const seen = new Set()
  return entries.map(entry => {
    if (!entry || typeof entry.existed !== 'boolean' || typeof entry.publish !== 'boolean'
      || !['content', 'mcp', 'ledger', 'manifest'].includes(entry.kind)
      || (entry.originalHash !== undefined && !/^[0-9a-f]{64}$/.test(entry.originalHash))
      || (entry.stagedHash !== undefined && !/^[0-9a-f]{64}$/.test(entry.stagedHash))) {
      throw new Error('invalid Ericsson vendor transaction journal entry')
    }
    const expectedKeys = [
      'existed', 'kind', 'publish', 'rel',
      ...(entry.originalHash !== undefined ? ['originalHash'] : []),
      ...(entry.stagedHash !== undefined ? ['stagedHash'] : []),
    ].sort().join(',')
    if (Object.keys(entry).sort().join(',') !== expectedKeys) {
      throw new Error('invalid Ericsson vendor transaction journal entry fields')
    }
    assertTransactionDestination(entry.rel, entry.kind)
    if (seen.has(entry.rel)) throw new Error(`duplicate transaction destination: ${entry.rel}`)
    seen.add(entry.rel)
    if (entry.existed !== (entry.originalHash !== undefined)) {
      throw new Error(`transaction original hash disagrees with existence: ${entry.rel}`)
    }
    if (entry.publish !== (entry.stagedHash !== undefined)) {
      throw new Error(`transaction staged hash disagrees with publish intent: ${entry.rel}`)
    }
    return {
      rel: entry.rel,
      existed: entry.existed,
      publish: entry.publish,
      kind: entry.kind,
      ...(entry.originalHash ? { originalHash: entry.originalHash } : {}),
      ...(entry.stagedHash ? { stagedHash: entry.stagedHash } : {}),
    }
  })
}

function expectedTransactionShape(previous, current, mcpDestination) {
  const contentKinds = new Map()
  for (const rel of [...new Set([...previous, ...current])].sort()) contentKinds.set(rel, 'content')
  if (mcpDestination) contentKinds.set(mcpDestination, 'mcp')
  const currentSet = new Set(current)
  return [
    ...[...contentKinds].map(([rel, kind]) => ({
      rel,
      kind,
      publish: currentSet.has(rel),
    })),
    { rel: INVENTORY_FILE, kind: 'ledger', publish: true },
    { rel: MANIFEST_FILE, kind: 'manifest', publish: true },
  ]
}

function priorMetadataForRecovery(destRoot, txnRoot, entry) {
  if (!entry.existed) return undefined
  const backup = path.join(txnRoot, 'backup', entry.rel)
  const live = path.join(destRoot, entry.rel)
  const candidate = lstatIfPresent(backup) ? backup : live
  if (!lstatIfPresent(candidate)) {
    throw new Error(`transaction cannot prove prior metadata: ${entry.rel}`)
  }
  if (treeHashIfPresent(candidate) !== entry.originalHash) {
    throw new Error(`transaction prior metadata hash mismatch: ${entry.rel}`)
  }
  return readStrictJsonFile(candidate, `transaction prior metadata ${entry.rel}`)
}

function validateTransactionFileState(destRoot, txnRoot, entry) {
  const live = path.join(destRoot, entry.rel)
  const staged = path.join(txnRoot, 'staged', entry.rel)
  const backup = path.join(txnRoot, 'backup', entry.rel)
  const liveHash = treeHashIfPresent(live)
  const stagedHash = treeHashIfPresent(staged)
  const backupHash = treeHashIfPresent(backup)

  if (!entry.existed && backupHash !== undefined) {
    throw new Error(`transaction has an unauthorized backup: ${entry.rel}`)
  }
  if (entry.existed && backupHash !== undefined && backupHash !== entry.originalHash) {
    throw new Error(`transaction backup hash mismatch: ${entry.rel}`)
  }
  if (stagedHash !== undefined && stagedHash !== entry.stagedHash) {
    throw new Error(`transaction staged hash mismatch: ${entry.rel}`)
  }
  if (entry.publish && stagedHash === undefined && liveHash !== entry.stagedHash) {
    throw new Error(`transaction published hash mismatch: ${entry.rel}`)
  }
  if (!entry.publish && stagedHash !== undefined) {
    throw new Error(`transaction unexpectedly staged a removal: ${entry.rel}`)
  }
  if (entry.existed && backupHash === undefined) {
    if (liveHash !== entry.originalHash || (entry.publish && stagedHash !== entry.stagedHash)) {
      throw new Error(`transaction original state mismatch: ${entry.rel}`)
    }
  }
  if (entry.existed && backupHash !== undefined) {
    if (entry.publish && stagedHash !== undefined && liveHash !== undefined) {
      throw new Error(`transaction has both staged and live content: ${entry.rel}`)
    }
    if (!entry.publish && liveHash !== undefined) {
      throw new Error(`transaction removal still has live content: ${entry.rel}`)
    }
  }
  if (!entry.existed && entry.publish) {
    if (stagedHash !== undefined && liveHash !== undefined) {
      throw new Error(`transaction new destination already exists: ${entry.rel}`)
    }
    if (stagedHash === undefined && liveHash !== entry.stagedHash) {
      throw new Error(`transaction new destination state mismatch: ${entry.rel}`)
    }
  }
  if (!entry.existed && !entry.publish && liveHash !== undefined) {
    throw new Error(`transaction cannot delete an unowned destination: ${entry.rel}`)
  }
}

function validateJournal(raw, destRoot, currentExpected, expectedMcpDestination) {
  if (!raw || raw.version !== 1 || typeof raw.txnDir !== 'string'
    || typeof raw.transactionId !== 'string'
    || transactionIdentity(raw.txnDir) !== raw.transactionId
    || !Array.isArray(raw.entries)
    || !/^[0-9a-f]{64}$/.test(raw.manifestHash || '')
    || !/^[0-9a-f]{64}$/.test(raw.planHash || '')) {
    throw new Error('invalid Ericsson vendor transaction journal')
  }
  if (Object.keys(raw).sort().join(',')
    !== 'entries,manifestHash,planHash,transactionId,txnDir,version') {
    throw new Error('invalid Ericsson vendor transaction journal fields')
  }
  const entries = normalizeTransactionEntries(raw.entries)
  const seen = new Set(entries.map(entry => entry.rel))
  if (!seen.has(MANIFEST_FILE) || !seen.has(INVENTORY_FILE)) {
    throw new Error('transaction journal omits required metadata')
  }
  const { txnRoot, marker } = validateOwnedTransaction(destRoot, raw.txnDir)
  if (marker.planHash !== raw.planHash) {
    throw new Error('transaction journal is not authenticated by its ownership marker')
  }
  if (!isWithin(fs.realpathSync.native(destRoot), fs.realpathSync.native(txnRoot))) {
    throw new Error('transaction directory escapes destination root')
  }
  const planPath = path.join(txnRoot, TRANSACTION_PLAN)
  const planContents = fs.readFileSync(planPath)
  if (sha256(planContents) !== raw.planHash) throw new Error('transaction plan hash mismatch')
  const plan = readStrictJsonFile(planPath, 'Ericsson vendor transaction plan')
  if (!plan || plan.version !== 1 || plan.transactionId !== raw.transactionId
    || plan.manifestHash !== raw.manifestHash
    || !Array.isArray(plan.previous) || !Array.isArray(plan.current)
    || (plan.mcpDestination !== null && typeof plan.mcpDestination !== 'string')) {
    throw new Error('invalid Ericsson vendor transaction plan')
  }
  if (Object.keys(plan).sort().join(',')
    !== 'current,entries,manifestHash,mcpDestination,previous,transactionId,version') {
    throw new Error('invalid Ericsson vendor transaction plan fields')
  }
  const planPrevious = [...new Set(plan.previous.map(assertManagedDestination))].sort()
  const planCurrent = [...new Set(plan.current.map(assertManagedDestination))].sort()
  const expectedCurrent = [...new Set(currentExpected.map(assertManagedDestination))].sort()
  const planMcp = plan.mcpDestination === null
    ? undefined
    : assertManagedDestination(plan.mcpDestination)
  if (JSON.stringify(plan.previous) !== JSON.stringify(planPrevious)
    || JSON.stringify(plan.current) !== JSON.stringify(planCurrent)) {
    throw new Error('transaction inventories are not canonical')
  }
  if (JSON.stringify(planCurrent) !== JSON.stringify(expectedCurrent)
    || planMcp !== expectedMcpDestination) {
    throw new Error('transaction plan does not match the current source inventory')
  }
  const planEntries = normalizeTransactionEntries(plan.entries)
  if (JSON.stringify(planEntries) !== JSON.stringify(entries)) {
    throw new Error('transaction journal entries do not match the authenticated plan')
  }
  const manifestEntry = planEntries.find(entry => entry.kind === 'manifest')
  const ledgerEntry = planEntries.find(entry => entry.kind === 'ledger')
  const priorManifest = priorMetadataForRecovery(destRoot, txnRoot, manifestEntry)
  const priorLedger = priorMetadataForRecovery(destRoot, txnRoot, ledgerEntry)
  const trustedPrevious = managedInventoryFromMetadata(priorManifest, priorLedger)
  if (JSON.stringify(planPrevious) !== JSON.stringify(trustedPrevious)) {
    throw new Error('transaction plan exceeds prior manifest and ledger authority')
  }
  const expectedShape = expectedTransactionShape(planPrevious, planCurrent, planMcp)
  const actualShape = planEntries.map(({ rel, kind, publish }) => ({ rel, kind, publish }))
  if (JSON.stringify(actualShape) !== JSON.stringify(expectedShape)) {
    throw new Error('transaction plan entries do not equal the authorized inventories')
  }
  for (const entry of planEntries) validateTransactionFileState(destRoot, txnRoot, entry)
  return { ...raw, entries: planEntries, txnRoot }
}

function removeLivePath(destRoot, entry) {
  assertNoSymlinkComponents(destRoot, entry.rel, 'transaction destination')
  const target = path.join(destRoot, entry.rel)
  fs.rmSync(target, { recursive: true, force: true })
  if (entry.rel.startsWith('capabilities/workflow-packages/')) {
    const packageParent = path.dirname(target)
    if (lstatIfPresent(packageParent)?.isDirectory()
      && fs.readdirSync(packageParent).length === 0) {
      fs.rmdirSync(packageParent)
    }
  }
}

function cleanupTransaction(destRoot, journal) {
  fs.rmSync(path.join(destRoot, JOURNAL_FILE), { force: true })
  fs.rmSync(journal.txnRoot, { recursive: true, force: true })
}

function cleanupOrphanTransactions(destRoot, referencedTxnDir) {
  for (const name of fs.readdirSync(destRoot)) {
    if (!transactionIdentity(name) || name === referencedTxnDir) continue
    const { txnRoot } = validateOwnedTransaction(destRoot, name)
    fs.rmSync(txnRoot, { recursive: true, force: true })
  }
}

function rollbackTransaction(destRoot, journal) {
  const backupRoot = path.join(journal.txnRoot, 'backup')
  for (const entry of [...journal.entries].reverse()) {
    const live = path.join(destRoot, entry.rel)
    const backup = path.join(backupRoot, entry.rel)
    const backupStat = lstatIfPresent(backup)
    if (entry.existed) {
      if (backupStat) {
        removeLivePath(destRoot, entry)
        assertNoSymlinkComponents(journal.txnRoot, `backup/${entry.rel}`, 'transaction backup')
        fs.mkdirSync(path.dirname(live), { recursive: true })
        fs.renameSync(backup, live)
      } else if (!lstatIfPresent(live)) {
        throw new Error(`cannot recover missing original destination: ${entry.rel}`)
      }
    } else {
      removeLivePath(destRoot, entry)
    }
  }
  cleanupTransaction(destRoot, journal)
}

function recoverPendingTransaction(destRoot, current, mcpDestination) {
  assertNoSymlinkComponents(destRoot, JOURNAL_FILE, 'transaction journal')
  const raw = readJsonIfPresent(path.join(destRoot, JOURNAL_FILE))
  if (!raw) {
    cleanupOrphanTransactions(destRoot)
    return
  }
  const journal = validateJournal(raw, destRoot, current, mcpDestination)
  const readyMarker = path.join(journal.txnRoot, 'manifest-ready.json')
  const completed = lstatIfPresent(readyMarker)
    && fileHashIfPresent(path.join(destRoot, MANIFEST_FILE)) === journal.manifestHash
  if (completed) {
    cleanupTransaction(destRoot, journal)
    cleanupOrphanTransactions(destRoot)
    return
  }
  rollbackTransaction(destRoot, journal)
  cleanupOrphanTransactions(destRoot)
}

function stageSnapshot({
  sourceDir, destRoot, manifest, copyList, previous, current, sourceCommit, faultInjector,
}) {
  const transactionId = randomUUID()
  const txnRoot = path.join(destRoot, `${TRANSACTION_PREFIX}${transactionId}`)
  fs.mkdirSync(txnRoot, { mode: 0o700 })
  try {
    atomicWriteNew(path.join(txnRoot, TRANSACTION_MARKER), JSON.stringify({
      schemaVersion: TRANSACTION_SCHEMA_VERSION,
      transactionId,
    }) + '\n')
    const stagedRoot = path.join(txnRoot, 'staged')
    fs.mkdirSync(stagedRoot, { recursive: true })
    for (const [sourceRel, destinationRel] of copyList) {
      faultInjector('stage-copy', destinationRel)
      copyRec(
        path.join(sourceDir, sourceRel),
        path.join(stagedRoot, destinationRel),
        stagedRoot,
        sourceDir,
      )
    }
    const mcpDestination = mcpDestinationFromManifest(manifest, { includeLegacyFile: false })
    if (mcpDestination) {
      faultInjector('stage-copy', mcpDestination)
      copyRec(
        path.join(sourceDir, manifest.mcpServers),
        path.join(stagedRoot, mcpDestination),
        stagedRoot,
        sourceDir,
      )
    }
    const vendored = {
      ...manifest,
      vendoredFrom: sourceCommit,
      workflowPackages: manifest.workflowPackages || [],
    }
    if (mcpDestination) vendored.mcpServersFile = path.posix.basename(mcpDestination)
    else delete vendored.mcpServersFile
    const ledgerContents = JSON.stringify(current, null, 2) + '\n'
    const manifestContents = JSON.stringify(vendored, null, 2) + '\n'
    atomicWriteNew(path.join(stagedRoot, INVENTORY_FILE), ledgerContents)
    atomicWriteNew(path.join(stagedRoot, MANIFEST_FILE), manifestContents)
    const manifestHash = sha256(manifestContents)
    const entries = expectedTransactionShape(previous, current, mcpDestination).map(shape => {
      const entry = transactionEntry(destRoot, shape.rel, shape.kind, shape.publish)
      const originalHash = treeHashIfPresent(path.join(destRoot, shape.rel))
      const stagedHash = treeHashIfPresent(path.join(stagedRoot, shape.rel))
      if (entry.existed !== (originalHash !== undefined)) {
        throw new Error(`destination changed while staging: ${shape.rel}`)
      }
      if (shape.publish !== (stagedHash !== undefined)) {
        throw new Error(`staged transaction is incomplete: ${shape.rel}`)
      }
      return {
        ...entry,
        ...(originalHash ? { originalHash } : {}),
        ...(stagedHash ? { stagedHash } : {}),
      }
    })
    const planContents = JSON.stringify({
      version: 1,
      transactionId,
      manifestHash,
      previous,
      current,
      mcpDestination: mcpDestination || null,
      entries,
    }, null, 2) + '\n'
    const planHash = sha256(planContents)
    atomicWriteNew(path.join(txnRoot, TRANSACTION_PLAN), planContents)
    atomicReplace(path.join(txnRoot, TRANSACTION_MARKER), JSON.stringify({
      schemaVersion: TRANSACTION_SCHEMA_VERSION,
      transactionId,
      planHash,
    }) + '\n')
    return {
      txnRoot,
      stagedRoot,
      mcpDestination,
      manifestHash,
      planHash,
      entries,
    }
  } catch (error) {
    fs.rmSync(txnRoot, { recursive: true, force: true })
    throw error
  }
}

function transactionEntry(destRoot, rel, kind, publish) {
  assertTransactionDestination(rel, kind)
  assertNoSymlinkComponents(destRoot, rel, 'transaction destination')
  if (kind === 'content' || kind === 'mcp') assertSafeExistingTree(destRoot, rel)
  return { rel, kind, publish, existed: Boolean(lstatIfPresent(path.join(destRoot, rel))) }
}

function publishTransaction({ destRoot, staged, current, faultInjector }) {
  const entries = staged.entries
  const journalData = {
    version: 1,
    txnDir: path.basename(staged.txnRoot),
    transactionId: transactionIdentity(path.basename(staged.txnRoot)),
    manifestHash: staged.manifestHash,
    planHash: staged.planHash,
    entries,
  }
  const stagedJournal = path.join(staged.txnRoot, 'journal-ready.json')
  atomicWriteNew(stagedJournal, JSON.stringify(journalData, null, 2) + '\n')
  fs.renameSync(stagedJournal, path.join(destRoot, JOURNAL_FILE))
  const journal = validateJournal(journalData, destRoot, current, staged.mcpDestination)
  const backupRoot = path.join(staged.txnRoot, 'backup')
  let completed = false

  const backup = entry => {
    if (!entry.existed) return
    faultInjector('live-backup', entry.rel)
    assertNoSymlinkComponents(destRoot, entry.rel, 'transaction destination')
    if (entry.kind === 'content' || entry.kind === 'mcp') assertSafeExistingTree(destRoot, entry.rel)
    const backupPath = path.join(backupRoot, entry.rel)
    fs.mkdirSync(path.dirname(backupPath), { recursive: true })
    fs.renameSync(path.join(destRoot, entry.rel), backupPath)
  }
  const publish = (entry, point) => {
    if (!entry.publish) return
    faultInjector(point, entry.rel)
    assertNoSymlinkComponents(destRoot, entry.rel, 'transaction destination')
    const stagedPath = path.join(staged.stagedRoot, entry.rel)
    if (!lstatIfPresent(stagedPath)) throw new Error(`staged destination missing: ${entry.rel}`)
    fs.mkdirSync(path.dirname(path.join(destRoot, entry.rel)), { recursive: true })
    fs.renameSync(stagedPath, path.join(destRoot, entry.rel))
  }

  try {
    for (const entry of entries.filter(entry => !['ledger', 'manifest'].includes(entry.kind))) {
      backup(entry)
      publish(entry, 'staged-publish')
    }
    const ledger = entries.find(entry => entry.kind === 'ledger')
    backup(ledger)
    publish(ledger, 'ledger-publish')
    atomicWriteNew(
      path.join(staged.txnRoot, 'manifest-ready.json'),
      JSON.stringify({ manifestHash: staged.manifestHash }) + '\n',
    )
    const manifest = entries.find(entry => entry.kind === 'manifest')
    backup(manifest)
    publish(manifest, 'manifest-publish')
    completed = true
    faultInjector('cleanup', journal.txnDir)
    cleanupTransaction(destRoot, journal)
  } catch (error) {
    if (error?.simulateInterruption) throw error
    if (completed) {
      throw new Error(`vendor completed but transaction cleanup failed: ${error.message}`, { cause: error })
    }
    try {
      rollbackTransaction(destRoot, journal)
    } catch (rollbackError) {
      throw new Error(
        `vendor failed (${error.message}) and rollback requires recovery (${rollbackError.message})`,
        { cause: error },
      )
    }
    throw error
  }
}

// mcpLocal dirs land under plugins/<basename>; complete workflow package roots stay intact.
export function vendor({ sourceDir, destRoot, sourceCommit, faultInjector = () => {} }) {
  const lock = acquireVendorLock(destRoot, faultInjector)
  try {
    faultInjector('lock-acquired', LOCK_DIR)
    assertNoSymlinkComponents(sourceDir, 'sets/ericsson.json', 'source manifest')
    const manifest = JSON.parse(fs.readFileSync(path.join(sourceDir, 'sets/ericsson.json'), 'utf8'))
    const pluginEntries = normalizePluginEntries(manifest)
    const copyList = sourceDestinationPairs(manifest)
    if (manifest.mcpServers) assertManifestSourcePath(manifest.mcpServers, 'mcpServers')

    // Validate every read/write path before the first reconciliation or copy.
    for (const [rel, destRel] of copyList) {
      assertSafeSourceTree(sourceDir, rel)
      assertSafeDestination(destRoot, destRel)
    }
    for (const entry of pluginEntries) {
      if (entry.path !== 'plugins/workflow') readPluginDescriptor(sourceDir, entry)
    }
    if (manifest.mcpServers) assertSafeSourceTree(sourceDir, manifest.mcpServers)
    const mcpDestination = mcpDestinationFromManifest(manifest, { includeLegacyFile: false })
    for (const rel of [
      'capabilities/ericsson.json',
      INVENTORY_FILE,
      ...(mcpDestination ? [mcpDestination] : []),
    ]) assertNoSymlinkComponents(destRoot, rel, 'vendor output')
    const current = managedDestinations(manifest, { includeLegacyMcpFile: false })
    recoverPendingTransaction(destRoot, current, mcpDestination)
    const previous = previousManagedInventory(destRoot)
    const staged = stageSnapshot({
      sourceDir,
      destRoot,
      manifest,
      copyList,
      previous,
      current,
      sourceCommit,
      faultInjector,
    })
    publishTransaction({ destRoot, staged, current, faultInjector })
  } finally {
    lock.release()
  }
}

function comparableRealPath(target) {
  const resolved = fs.realpathSync.native(target)
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved
}

export function resolveCleanSourceCommit(sourceDir) {
  try {
    const sourceRoot = comparableRealPath(sourceDir)
    const gitEnvironment = { ...process.env, GIT_NO_REPLACE_OBJECTS: '1' }
    delete gitEnvironment.GIT_REPLACE_REF_BASE
    const gitRaw = args => execFileSync(
      'git',
      ['-C', sourceRoot, ...args],
      { env: gitEnvironment, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
    )
    const git = args => gitRaw(args).trim()
    const worktree = git(['rev-parse', '--show-toplevel'])
    if (comparableRealPath(worktree) !== sourceRoot) {
      throw new Error(`source directory is not the Git worktree root: ${sourceDir}`)
    }
    const replaceRefs = git(['for-each-ref', '--format=%(refname)', 'refs/replace'])
    if (replaceRefs) throw new Error(`source repository contains Git replace refs:\n${replaceRefs}`)
    if (git(['rev-parse', '--is-shallow-repository']) === 'true') {
      throw new Error('source repository is shallow; exact provenance requires complete history')
    }
    const rawGraftsPath = git(['rev-parse', '--git-path', 'info/grafts'])
    const graftsPath = path.isAbsolute(rawGraftsPath)
      ? rawGraftsPath
      : path.resolve(sourceRoot, rawGraftsPath)
    if (lstatIfPresent(graftsPath)?.isFile() && fs.readFileSync(graftsPath, 'utf8').trim()) {
      throw new Error('source repository contains legacy Git graft rewriting')
    }
    const sourceCommit = git(['rev-parse', '--verify', 'HEAD^{commit}'])
    if (!/^[0-9a-f]{40}$/i.test(sourceCommit)) {
      throw new Error('Git did not resolve a full 40-character HEAD commit')
    }
    const status = git([
      'status',
      '--porcelain=v1',
      '--untracked-files=all',
      '--ignore-submodules=none',
    ])
    if (status) throw new Error(`source worktree is not clean:\n${status}`)
    const flaggedIndexEntries = gitRaw(['ls-files', '-v', '-z'])
      .split('\0')
      .filter(Boolean)
      .filter(record => record[0] === 'S' || /^[a-z]$/.test(record[0]))
      .map(record => record.slice(2))
    if (flaggedIndexEntries.length) {
      throw new Error(
        'source index contains assume-unchanged or skip-worktree flags:\n'
        + flaggedIndexEntries.join('\n'),
      )
    }
    assertNoSymlinkComponents(sourceRoot, 'sets/ericsson.json', 'source manifest')
    const manifest = JSON.parse(fs.readFileSync(path.join(sourceRoot, 'sets/ericsson.json'), 'utf8'))
    const copiedSourcePaths = [
      'sets/ericsson.json',
      ...sourceDestinationPairs(manifest).map(([sourceRel]) => sourceRel),
      ...(manifest.mcpServers
        ? [assertManifestSourcePath(manifest.mcpServers, 'mcpServers')]
        : []),
    ]
    const ignored = git([
      'ls-files',
      '--others',
      '--ignored',
      '--exclude-standard',
      '--',
      ...copiedSourcePaths,
    ]).split('\n').filter(Boolean).filter(rel => {
      const components = rel.split('/')
      return !components.some(component => (
        ['__pycache__', '.venv', '.pytest_cache', '.git'].includes(component)
      ))
    })
    if (ignored.length) {
      throw new Error(`copied source paths contain ignored untracked files:\n${ignored.join('\n')}`)
    }
    return sourceCommit
  } catch (error) {
    throw new Error(`Git provenance check failed: ${error.message}`, { cause: error })
  }
}

function committedIndexSnapshot(sourceDir, sourceCommit) {
  const container = fs.mkdtempSync(path.join(os.tmpdir(), 'ericsson-capabilities-index-'))
  const snapshotRoot = path.join(container, 'snapshot')
  const indexFile = path.join(container, 'index')
  fs.mkdirSync(snapshotRoot)
  const env = { ...process.env, GIT_INDEX_FILE: indexFile, GIT_NO_REPLACE_OBJECTS: '1' }
  try {
    execFileSync(
      'git',
      ['-C', sourceDir, 'read-tree', sourceCommit],
      { env, stdio: ['ignore', 'pipe', 'pipe'] },
    )
    execFileSync(
      'git',
      [
        '-C', sourceDir, 'checkout-index', '--all', '--force',
        `--prefix=${snapshotRoot.split(path.sep).join('/')}/`,
      ],
      { env, stdio: ['ignore', 'pipe', 'pipe'] },
    )
    return {
      sourceDir: snapshotRoot,
      cleanup() {
        fs.rmSync(container, { recursive: true, force: true })
      },
    }
  } catch (error) {
    fs.rmSync(container, { recursive: true, force: true })
    throw new Error(`Git committed-index snapshot failed: ${error.message}`, { cause: error })
  }
}

export function vendorFromCleanGitSource({
  sourceDir,
  destRoot,
  afterValidation = () => {},
  faultInjector = () => {},
}) {
  const sourceCommit = resolveCleanSourceCommit(sourceDir)
  afterValidation()
  const snapshot = committedIndexSnapshot(comparableRealPath(sourceDir), sourceCommit)
  try {
    vendor({ sourceDir: snapshot.sourceDir, destRoot, sourceCommit, faultInjector })
  } finally {
    snapshot.cleanup()
  }
  return sourceCommit
}

function main() {
  const sourceDir = process.env.ERICSSON_CAPABILITIES_DIR
    || path.resolve(process.cwd(), '..', 'ericsson-capabilities')
  if (!fs.existsSync(path.join(sourceDir, 'sets/ericsson.json')))
    throw new Error(`ericsson-capabilities not found at ${sourceDir} (set ERICSSON_CAPABILITIES_DIR)`)
  const sourceCommit = vendorFromCleanGitSource({ sourceDir, destRoot: process.cwd() })
  console.log(`vendored ericsson-capabilities @ ${sourceCommit} into ${process.cwd()}`)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main()
