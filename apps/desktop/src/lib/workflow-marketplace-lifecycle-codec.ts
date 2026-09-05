import * as wire from '@/types/workflow-marketplace-lifecycle'

import {
  isWorkflowMarketplaceInstallIdentifier,
  isWorkflowMarketplaceRepositoryUrl,
  isWorkflowMarketplaceSourceRequestUrl
} from './workflow-marketplace-codec'

type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
interface Schema {
  $ref?: string
  additionalProperties?: boolean
  anyOf?: readonly Schema[]
  const?: Json
  default?: Json
  enum?: readonly Json[]
  items?: Schema
  maximum?: number
  maxItems?: number
  maxLength?: number
  minimum?: number
  minItems?: number
  minLength?: number
  oneOf?: readonly Schema[]
  pattern?: string
  properties?: Record<string, Schema>
  required?: readonly string[]
  type?: string
}

const schemas: Record<string, Schema> = wire.lifecycleSchemas
const RESULT_BYTES = 2 * 1024 * 1024
const RESPONSE_BYTES = 16 * 1024 * 1024 + 64 * 1024
const OPERATION_ID = /^wmop_[0-9a-f]{12}_[0-9a-f]{32}$/
const REQUEST_ID = /^wmreq_([0-9a-f]{32})_[0-9]{13}_[0-9a-f]{32}$/
const EPOCH = /^[0-9a-f]{32}$/
const SOURCE_KEY = /^[a-z0-9](?:[a-z0-9_-]{0,126}[a-z0-9])?$/
const PACKAGE_ID = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/

const PYTHON_SPACE =
  // Python str.strip includes these controls and excludes U+FEFF.
  // eslint-disable-next-line no-control-regex
  /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]$/u

function requireValid(condition: unknown): asserts condition {
  if (!condition) {
    throw new Error('Invalid lifecycle projection.')
  }
}

function clean(value: string): boolean {
  return !PYTHON_SPACE.test(value) && !value.includes('\0')
}

export function isLifecycleProfile(value: unknown): value is string {
  return typeof value === 'string' && [...value].length >= 1 && [...value].length <= 256 && clean(value)
}

function canonicalPath(value: string): boolean {
  return (
    value.length > 0 &&
    value.normalize('NFC') === value &&
    !value.includes('\\') &&
    !value.includes('\0') &&
    value
      .split('/')
      .every(
        part =>
          part !== '' && part !== '.' && part !== '..' && part.toLowerCase() !== '.git' && !/^[A-Za-z]:/.test(part)
      )
  )
}

function repositoryIdentity(value: unknown): boolean {
  if (typeof value !== 'string' || !clean(value)) {
    return false
  }

  // V2 reuses the backend Git identity domain, which includes local file URLs,
  // shorthand and HTTP. Reuse the credential checks in the request validator;
  // HTTP uses the same authority/path rules as HTTPS. Preserve the exact value.
  const validationValue = value.startsWith('http://') ? 'https://' + value.slice(7) : value

  return isWorkflowMarketplaceRepositoryUrl(value) || isWorkflowMarketplaceSourceRequestUrl(validationValue)
}

/** Python canonical UTC preserves exactly six fractional digits when nonzero. */
export function isLifecycleTimestamp(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z$/.test(value)) {
    return false
  }

  if (value.startsWith('0000-') || value.endsWith('.000000Z')) {
    return false
  }

  const milliseconds = Date.parse(value)

  if (!Number.isFinite(milliseconds)) {
    return false
  }

  return new Date(milliseconds).toISOString().slice(0, 19) === value.slice(0, 19)
}

function instant(value: string): string {
  return value.length === 20 ? value.slice(0, -1) + '.000000Z' : value
}

function unique(values: readonly string[]): boolean {
  return new Set(values).size === values.length
}

function compareUnicode(a: string, b: string): number {
  const left = [...a],
    right = [...b]

  for (let index = 0; index < Math.min(left.length, right.length); index++) {
    const delta = left[index].codePointAt(0)! - right[index].codePointAt(0)!

    if (delta) {
      return delta
    }
  }

  return left.length - right.length
}

function sorted(values: readonly string[]): boolean {
  return unique(values) && values.every((value, index) => index === 0 || compareUnicode(values[index - 1], value) < 0)
}

export function sameLifecycleIdentity(a: wire.PackageIdentity, b: wire.PackageIdentity): boolean {
  return a.source_key === b.source_key && a.package_id === b.package_id
}

export function sameLifecycleValue(a: unknown, b: unknown): boolean {
  if (a === b) {
    return true
  }

  if (typeof a !== 'object' || a === null || typeof b !== 'object' || b === null) {
    return false
  }

  if (Array.isArray(a) !== Array.isArray(b)) {
    return false
  }

  const left = Object.keys(a),
    right = Object.keys(b)

  return (
    left.length === right.length &&
    left.every(
      key =>
        Object.hasOwn(b, key) &&
        sameLifecycleValue((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key])
    )
  )
}

/** Inspect descriptors before copying: untrusted getters/prototypes are never invoked. */
function copyJson(value: unknown, depth = 0, budget = { bytes: 0 }): Json {
  requireValid(depth <= 64)

  if (value === null || typeof value === 'boolean') {
    return value
  }

  if (typeof value === 'number') {
    requireValid(Number.isFinite(value))

    return value
  }

  if (typeof value === 'string') {
    budget.bytes += new TextEncoder().encode(value).length
    requireValid(budget.bytes <= RESPONSE_BYTES)

    return value
  }

  requireValid(typeof value === 'object')
  const prototype = Object.getPrototypeOf(value)
  requireValid(
    prototype === Object.prototype || prototype === null || (Array.isArray(value) && prototype === Array.prototype)
  )
  const descriptors = Object.getOwnPropertyDescriptors(value)
  requireValid(Reflect.ownKeys(value).every(key => typeof key === 'string'))

  if (Array.isArray(value)) {
    requireValid(value.length <= 65536 && Object.keys(descriptors).length === value.length + 1)

    return Array.from({ length: value.length }, (_, index) => {
      const descriptor = descriptors[String(index)]
      requireValid(descriptor && descriptor.enumerable && Object.hasOwn(descriptor, 'value'))

      return copyJson(descriptor.value, depth + 1, budget)
    })
  }

  const output: { [key: string]: Json } = {}

  for (const [key, descriptor] of Object.entries(descriptors)) {
    requireValid(descriptor.enumerable && Object.hasOwn(descriptor, 'value'))
    Object.defineProperty(output, key, {
      value: copyJson(descriptor.value, depth + 1, budget),
      enumerable: true,
      writable: true,
      configurable: true
    })
  }

  return output
}

function validate(schema: Schema, value: Json): Json {
  if (schema.$ref) {
    const name = schema.$ref.split('/').at(-1)!
    const decoded = validate(schemas[name], value)
    relationships(name, decoded)

    return decoded
  }

  const union = schema.anyOf ?? schema.oneOf

  if (union) {
    if (value !== null && typeof value === 'object' && !Array.isArray(value) && typeof value.type === 'string') {
      const selected = union.find(
        candidate => candidate.$ref && schemas[candidate.$ref.split('/').at(-1)!].properties?.type?.const === value.type
      )

      if (selected) {
        return validate(selected, value)
      }
    }

    for (const candidate of union) {
      try {
        return validate(candidate, value)
      } catch {
        /* Try the next closed union member. */
      }
    }

    throw new Error('Invalid lifecycle union.')
  }

  if (Object.hasOwn(schema, 'const')) {
    requireValid(value === schema.const)
  }

  if (schema.enum) {
    requireValid(schema.enum.includes(value))
  }

  if (schema.type === 'null') {
    requireValid(value === null)
  }

  if (schema.type === 'boolean') {
    requireValid(typeof value === 'boolean')
  }

  if (schema.type === 'integer' || schema.type === 'number') {
    requireValid(typeof value === 'number' && Number.isFinite(value))
    requireValid(schema.type !== 'integer' || Number.isSafeInteger(value))
    requireValid(schema.minimum === undefined || value >= schema.minimum)
    requireValid(schema.maximum === undefined || value <= schema.maximum)
  }

  if (schema.type === 'string') {
    requireValid(typeof value === 'string')
    const length = [...value].length
    requireValid(schema.minLength === undefined || length >= schema.minLength)
    requireValid(schema.maxLength === undefined || length <= schema.maxLength)
    // Python's ASCII patterns use a strict end; JS $ otherwise admits a final newline.
    requireValid(!schema.pattern || new RegExp(schema.pattern.replace(/\$$/, '$(?![\\s\\S])'), 'u').test(value))
  }

  if (schema.type === 'array') {
    requireValid(Array.isArray(value))
    requireValid(schema.minItems === undefined || value.length >= schema.minItems)
    requireValid(schema.maxItems === undefined || value.length <= schema.maxItems)

    return value.map(item => validate(schema.items!, item))
  }

  if (schema.type === 'object') {
    requireValid(value !== null && typeof value === 'object' && !Array.isArray(value))
    const properties = schema.properties!
    requireValid(Object.keys(value).every(key => Object.hasOwn(properties, key)))
    const output: { [key: string]: Json } = {}

    for (const [key, field] of Object.entries(properties)) {
      if (Object.hasOwn(value, key)) {
        output[key] = validate(field, value[key])
      } else {
        requireValid(!schema.required?.includes(key) && Object.hasOwn(field, 'default'))
        output[key] = copyJson(field.default)
      }
    }

    return output
  }

  return value
}

function inventory(items: readonly wire.WorkflowInventoryItem[]): void {
  requireValid(unique(items.map(item => item.workflow_name)) && unique(items.map(item => item.definition_path)))
}

function packageState(state: wire.PackageState): void {
  requireValid(clean(state.profile) && isLifecycleTimestamp(state.observed_at))

  if (state.state === 'installed') {
    requireValid(state.installed && state.trust && state.recovery === 'clear')
    requireValid(
      sameLifecycleIdentity(state.identity, state.installed.identity) &&
        sameLifecycleIdentity(state.identity, state.trust.identity)
    )
    requireValid(state.trust.distribution_digest === state.installed.distribution_digest)
    requireValid(
      sameLifecycleValue(
        [...state.installed.workflow_paths].sort(),
        state.trust.workflows.map(item => item.definition_path).sort()
      )
    )
  } else {
    requireValid(state.installed === null && state.trust === null)

    if (state.state === 'absent') {
      requireValid(state.recovery === 'clear')
    }
  }
}

function operation(value: wire.LifecycleOperation): void {
  requireValid(clean(value.profile) && REQUEST_ID.exec(value.request_id)?.[1] === value.registry_epoch)
  requireValid((wire.lifecycleRules.subjects[value.kind] as readonly string[]).includes(value.subject.type))
  requireValid(value.kind.startsWith('trust_') === (value.selection !== null))

  for (const time of [value.created_at, value.updated_at, value.started_at, value.finished_at]) {
    requireValid(time === null || isLifecycleTimestamp(time))
  }

  const created = instant(value.created_at),
    updated = instant(value.updated_at)

  const started = value.started_at && instant(value.started_at),
    finished = value.finished_at && instant(value.finished_at)

  requireValid(updated >= created && (!started || (started >= created && updated >= started)))
  requireValid(!finished || (finished >= created && (!started || finished >= started) && updated >= finished))

  if (value.state === 'pending' || value.state === 'running') {
    requireValid(!finished && value.result === null && value.error === null && value.outcome === null)

    if (value.state === 'pending') {
      requireValid(value.phase === 'queued' && value.progress === 0 && !started)
    } else {
      requireValid(
        started &&
          value.progress < 100 &&
          (wire.lifecycleRules.phases[value.kind] as readonly string[]).includes(value.phase)
      )
    }

    return
  }

  requireValid(finished && value.outcome)

  if (value.state === 'succeeded') {
    requireValid(
      started && value.phase === 'completed' && value.progress === 100 && value.result && value.error === null
    )
  } else if (value.state === 'failed') {
    requireValid(value.phase === 'failed' && value.progress < 100 && value.result === null && value.error)
  } else {
    requireValid(
      value.phase === 'cancelled' &&
        value.progress < 100 &&
        value.result === null &&
        value.error === null &&
        value.outcome.type === 'cancelled_before_commit'
    )
  }

  const outcome = value.outcome
  const proof = 'package_state' in outcome ? outcome.package_state : null

  if (proof) {
    requireValid(
      value.subject.type === 'package' &&
        sameLifecycleIdentity(proof.identity, value.subject.identity) &&
        proof.profile === value.profile
    )
  }

  if (outcome.type === 'known_unchanged' && outcome.evidence === 'rollback_verified') {
    requireValid(proof && proof.state !== 'unconfirmed' && proof.recovery === 'clear')
  }

  if (value.state === 'failed') {
    requireValid(outcome.type !== 'committed' && outcome.type !== 'cancelled_before_commit')

    const reason =
      value.error?.code === 'transaction_rollback_failed'
        ? 'rollback_failed'
        : value.error?.code === 'transaction_recovery_ambiguous'
          ? 'recovery_ambiguous'
          : null

    if (reason) {
      requireValid(outcome.type === 'recovery_required' && outcome.reason === reason)
    }

    return
  }

  if (value.state === 'cancelled') {
    return
  }

  const result = value.result!
  requireValid(result.type === wire.lifecycleRules.results[value.kind])

  if (value.subject.type === 'source') {
    requireValid(result.type === 'source_refresh' && result.value.source_name === value.subject.source_name)
  } else if (value.subject.type === 'all_packages') {
    requireValid(result.type === 'update_checks')
    requireValid(unique(result.value.checks.map(item => `${item.identity.source_key}/${item.identity.package_id}`)))
  } else if (value.subject.type === 'direct_install') {
    requireValid(result.type === 'install_review' && result.value.identity.source_key === value.subject.source_key)
  } else {
    if (result.type === 'update_checks') {
      requireValid(
        result.value.checks.length === 1 &&
          sameLifecycleIdentity(result.value.checks[0].identity, value.subject.identity)
      )
    } else {
      requireValid('identity' in result.value && sameLifecycleIdentity(result.value.identity, value.subject.identity))
    }
  }

  if (result.type === 'trust_review') {
    const reviewed = result.value.workflows.map(item => [item.workflow_name, item.definition_path])

    const expected = result.value.package_workflows
      .filter(
        item =>
          value.selection?.type === 'all' ||
          item.workflow_name === (value.selection?.type === 'one' ? value.selection.workflow_name : null)
      )
      .map(item => [item.workflow_name, item.definition_path])

    requireValid(
      sameLifecycleValue(reviewed.sort(), expected.sort()) && (value.selection?.type !== 'one' || expected.length === 1)
    )
  }

  if (result.type === 'trust_grant' || result.type === 'trust_revoke') {
    requireValid(sameLifecycleValue(result.value.selection, value.selection))
  }

  if (value.kind === 'refresh') {
    requireValid(result.type === 'source_refresh' && proof === null)
    requireValid(result.value.state === 'fresh' ? outcome.type === 'committed' : outcome.type === 'known_unchanged')
  } else if (value.kind.endsWith('_prepare') || value.kind === 'inspect' || value.kind === 'update_check') {
    requireValid(outcome.type === 'known_unchanged' && outcome.evidence === 'read_only' && proof === null)
  } else {
    requireValid(outcome.type === 'committed' && proof)

    if (result.type === 'removed_package') {
      requireValid(proof.state === 'absent')
    } else {
      requireValid(proof.state === 'installed' && proof.installed)

      if (result.type === 'installed_package' || result.type === 'updated_package') {
        requireValid(sameLifecycleValue(result.value, proof.installed))
      } else {
        requireValid((result.type === 'trust_grant' || result.type === 'trust_revoke') && proof.trust)
        requireValid(
          sameLifecycleIdentity(result.value.identity, proof.trust.identity) &&
            result.value.distribution_digest === proof.trust.distribution_digest &&
            sameLifecycleValue(result.value.workflows, proof.trust.workflows)
        )
      }
    }
  }
}

function relationships(name: string, raw: Json): void {
  const object = raw as Record<string, Json>

  if (name.endsWith('Result')) {
    requireValid(new TextEncoder().encode(JSON.stringify(raw)).length <= RESULT_BYTES)
  }

  if (['PackageIdentity', 'InstalledPackageIdentity'].includes(name)) {
    const value = raw as wire.PackageIdentity
    requireValid(SOURCE_KEY.test(value.source_key) && PACKAGE_ID.test(value.package_id))
  }

  if (['OneTrustSelection', 'WorkflowInventoryItem', 'TrustWorkflowState'].includes(name)) {
    requireValid(clean(object.workflow_name as string))
  }

  if (['WorkflowInventoryItem', 'TrustWorkflowState'].includes(name)) {
    requireValid(canonicalPath(object.definition_path as string))
  }

  if (
    ['InstallReviewProjection', 'UpdateReviewProjection', 'RemoveReviewProjection', 'TrustReviewProjection'].includes(
      name
    )
  ) {
    requireValid(object.confirmation_available === (object.expires_at !== null))
    requireValid(object.expires_at === null || isLifecycleTimestamp(object.expires_at))
  }

  if (
    [
      'InstalledPackage',
      'PackageInspection',
      'InstallReviewProjection',
      'UpdateReviewProjection',
      'TrustReviewProjection'
    ].includes(name)
  ) {
    const identity = object.identity as wire.PackageIdentity
    requireValid(object.source_name === identity.source_key)
  }

  if (
    [
      'InstalledPackage',
      'PackageInspection',
      'InstallReviewProjection',
      'UpdateReviewProjection',
      'SourceRefreshValue'
    ].includes(name)
  ) {
    requireValid(repositoryIdentity(object.repository_url))
  }

  if (['InstalledPackage', 'PackageInspection', 'InstallReviewProjection', 'UpdateReviewProjection'].includes(name)) {
    requireValid(object.configured_ref === null || clean(object.configured_ref as string))
  }

  if (['InstalledPackage', 'PackageInspection', 'InstallReviewProjection'].includes(name)) {
    requireValid(canonicalPath(object.package_path as string))
  }

  if (name === 'ExternalRequirements') {
    for (const values of Object.values(object)) {
      requireValid(unique(values as string[]) && (values as string[]).every(clean))
    }
  }

  if (name === 'StringSetChange') {
    requireValid(sorted(object.added as string[]) && sorted(object.removed as string[]))
  }

  if (name === 'WorkflowRiskChanges' || name === 'WorkflowCompatibilityChanges') {
    for (const items of [object.added, object.removed] as Record<string, string>[][]) {
      const fields =
        name === 'WorkflowRiskChanges'
          ? ['workflow_name', 'package_digest', 'risk_digest']
          : ['workflow_name', 'code', 'severity']

      const keys = items.map(item => fields.map(field => item[field]))
      requireValid(unique(keys.map(key => JSON.stringify(key))))
      requireValid(
        keys.every(
          (key, index) =>
            index === 0 ||
            key.some(
              (part, position) =>
                key.slice(0, position).every((v, p) => v === keys[index - 1][p]) &&
                compareUnicode(keys[index - 1][position], part) < 0
            )
        )
      )
    }
  }

  if (name === 'FileDigestChange') {
    const value = raw as wire.FileDigestChange
    requireValid(canonicalPath(value.path) && (value.old_path === null || canonicalPath(value.old_path)))

    if (value.kind === 'added') {
      requireValid(value.old_path === null && value.old_digest === null && value.candidate_digest !== null)
    }

    if (value.kind === 'removed') {
      requireValid(value.old_path === null && value.old_digest !== null && value.candidate_digest === null)
    }

    if (value.kind === 'modified') {
      requireValid(value.old_path === null && value.old_digest !== null && value.candidate_digest !== null)
    }

    if (value.kind === 'renamed') {
      requireValid(value.old_path !== null && value.old_digest !== null && value.old_digest === value.candidate_digest)
    }
  }

  if (name === 'WorkflowTrustReviewItem') {
    const value = raw as wire.WorkflowTrustReviewItem
    requireValid(
      canonicalPath(value.definition_path) && (value.companion_path === null || canonicalPath(value.companion_path))
    )

    for (const paths of [
      value.command_resources,
      value.script_resources,
      value.mcp_resources,
      value.mcp_resource_files
    ]) {
      requireValid(sorted(paths) && paths.every(canonicalPath))
    }
  }

  if (name === 'PackageReviewAssessment') {
    const value = raw as wire.PackageReviewAssessment
    requireValid(
      unique(value.workflow_names) &&
        value.workflow_names.every(clean) &&
        sorted(value.package_resources) &&
        value.package_resources.every(canonicalPath)
    )
  }

  if (name === 'InstalledPackage') {
    const value = raw as wire.InstalledPackage
    requireValid(
      isLifecycleTimestamp(value.installed_at) && clean(value.actor) && value.workflow_paths.every(canonicalPath)
    )
    requireValid(
      unique(
        value.workflow_paths.map(path =>
          [...path]
            .map(character => wire.lifecycleCaseFold[character as keyof typeof wire.lifecycleCaseFold] ?? character)
            .join('')
        )
      )
    )
  }

  if (name === 'PackageInspectionResource') {
    requireValid(canonicalPath(object.path as string) && sorted(object.types as string[]))
  }

  if (name === 'PackageInspection') {
    inspection(raw as wire.PackageInspection)
  }

  if (name === 'UpdateCheck') {
    const value = raw as wire.UpdateCheck
    requireValid(
      value.status === 'error'
        ? value.diagnostic_code !== null && value.message !== null
        : value.diagnostic_code === null && value.message === null
    )
  }

  if (name === 'UpdateReviewProjection' && object.result === 'unchanged') {
    requireValid(object.confirmation_available === false && object.expires_at === null)
  }

  if (name === 'TrustReviewProjection') {
    const value = raw as wire.TrustReviewProjection
    requireValid(sorted(value.package_resources) && value.package_resources.every(canonicalPath))
    inventory(value.package_workflows)
    inventory(value.workflows)
    requireValid(
      value.workflows.every(item =>
        value.package_workflows.some(
          known => known.workflow_name === item.workflow_name && known.definition_path === item.definition_path
        )
      )
    )
  }

  if (name === 'TrustSnapshot' || name === 'TrustGrantValue' || name === 'TrustRevokeValue') {
    inventory(object.workflows as wire.TrustWorkflowState[])
  }

  if (name === 'TrustGrantValue' || name === 'TrustRevokeValue') {
    const value = raw as wire.TrustGrantValue

    if (value.selection.type === 'one') {
      const selected = value.workflows.find(
        item => item.workflow_name === (value.selection as wire.OneTrustSelection).workflow_name
      )

      requireValid(selected && (name !== 'TrustGrantValue' || selected.state === 'trusted'))
    } else if (name === 'TrustGrantValue') {
      requireValid(value.workflows.every(item => item.state === 'trusted'))
    }
  }

  if (name === 'SourceRefreshValue') {
    requireValid(object.verified_at === null || isLifecycleTimestamp(object.verified_at))
  }

  if (name === 'PackageState') {
    packageState(raw as wire.PackageState)
  }

  if (name === 'LifecycleOperation') {
    operation(raw as wire.LifecycleOperation)
  }
}

function inspection(value: wire.PackageInspection): void {
  requireValid(isLifecycleTimestamp(value.verified_at))
  requireValid([value.display_name, value.description, value.license, value.publisher].every(clean))
  requireValid(
    sorted(value.tags) &&
      sorted(value.workflows.map(item => item.workflow_name)) &&
      sorted(value.resources.map(item => item.path))
  )

  for (const diagnostics of [value.blockers, value.advisories]) {
    const keys = diagnostics.map(item => [item.code, item.message, item.severity])
    requireValid(unique(keys.map(key => JSON.stringify(key))))
    requireValid(
      keys.every(
        (key, index) =>
          index === 0 ||
          key.some(
            (part, position) =>
              key.slice(0, position).every((v, p) => v === keys[index - 1][p]) &&
              compareUnicode(keys[index - 1][position], part) < 0
          )
      )
    )
  }

  requireValid(value.identifier === `${value.source_name}/${value.id}` && value.identity.package_id === value.id)

  if (value.install_status === 'not_installed') {
    requireValid(value.installed === null && value.update_status === 'not_applicable')
  } else {
    requireValid(
      value.installed &&
        sameLifecycleIdentity(value.identity, value.installed.identity) &&
        value.update_status !== 'not_applicable'
    )
  }

  for (const workflow of value.workflows) {
    const references: [string | null, string][] = [
      [workflow.definition_path, 'workflow_definition'],
      [workflow.companion_path, 'workflow_companion']
    ]

    for (const [paths, role] of [
      [workflow.command_resources, 'command'],
      [workflow.script_resources, 'script'],
      [workflow.mcp_resources, 'mcp'],
      [workflow.mcp_resource_files, 'mcp_resource']
    ] as const) {
      for (const path of paths) {
        references.push([path, role])
      }
    }

    requireValid(
      references.every(
        ([path, role]) =>
          path === null ||
          value.resources.some(resource => resource.path === path && (resource.types as string[]).includes(role))
      )
    )
  }
}

function decode<T>(name: string, value: unknown): T | null {
  try {
    const fresh = copyJson(value)
    requireValid(new TextEncoder().encode(JSON.stringify(fresh)).length <= RESPONSE_BYTES)

    return validate({ $ref: `#/$defs/${name}` }, fresh) as T
  } catch {
    return null
  }
}

export function decodeLifecycleOperation(value: unknown): wire.LifecycleOperation | null {
  return decode('LifecycleOperation', value)
}

export function decodeLifecyclePackageState(value: unknown): wire.PackageState | null {
  return decode('PackageState', value)
}

export function decodeLifecycleSubject(value: unknown): wire.LifecycleOperation['subject'] | null {
  for (const name of ['SourceSubject', 'PackageSubject', 'AllPackagesSubject', 'DirectInstallSubject']) {
    const subject = decode<wire.LifecycleOperation['subject']>(name, value)

    if (subject) {
      return subject
    }
  }

  return null
}

export function decodeLifecycleSelection(value: unknown): wire.AllTrustSelection | wire.OneTrustSelection | null {
  return decode('AllTrustSelection', value) ?? decode('OneTrustSelection', value)
}

export function decodeLifecycleCapabilities(value: unknown): wire.LifecycleCapabilities | null {
  const decoded = decode<wire.LifecycleCapabilities>('_Capabilities', value)

  return decoded && clean(decoded.profile) && isLifecycleTimestamp(decoded.server_time) && unique(decoded.capabilities)
    ? decoded
    : null
}

export function decodeLifecycleOperationPage(value: unknown): wire.LifecycleOperationPage | null {
  const decoded = decode<wire.LifecycleOperationPage>('LifecycleOperationPage', value)

  if (
    !decoded ||
    decoded.items.length > 100 ||
    !unique(decoded.items.map(item => item.id)) ||
    decoded.complete !== (decoded.next_cursor === null)
  ) {
    return null
  }

  if (decoded.next_cursor !== null && !EPOCH.test(decoded.next_cursor)) {
    return null
  }

  if (
    decoded.items.some(
      item => item.registry_epoch !== decoded.items[0].registry_epoch || item.profile !== decoded.items[0].profile
    )
  ) {
    return null
  }

  return decoded
}

export function decodeLifecycleEvicted(value: unknown): wire.AdmissionEvicted | null {
  const decoded = decode<wire.AdmissionEvicted>('AdmissionEvicted', value)

  if (
    !decoded ||
    !OPERATION_ID.test(decoded.operation_id) ||
    !EPOCH.test(decoded.registry_epoch) ||
    REQUEST_ID.exec(decoded.request_id)?.[1] !== decoded.registry_epoch ||
    !clean(decoded.profile) ||
    !Object.hasOwn(wire.lifecycleRules.subjects, decoded.kind)
  ) {
    return null
  }

  const kind = decoded.kind as wire.LifecycleOperation['kind']

  return (wire.lifecycleRules.subjects[kind] as readonly string[]).includes(decoded.subject.type) &&
    kind.startsWith('trust_') === (decoded.selection !== null)
    ? decoded
    : null
}

export function decodeLifecycleAdmission(value: unknown): wire.AdmissionFound | wire.AdmissionEvicted | null {
  return decode<wire.AdmissionFound>('AdmissionFound', value) ?? decodeLifecycleEvicted(value)
}

/** Explicit token retrieval only; callers must keep this value outside shared state. */
export function decodeLifecycleReviewToken(value: unknown): wire.ReviewTokenResponse | null {
  const decoded = decode<wire.ReviewTokenResponse>('ReviewTokenResponse', value)

  return decoded &&
    OPERATION_ID.test(decoded.operation_id) &&
    REQUEST_ID.test(decoded.request_id) &&
    /^[a-zA-Z0-9_-]{32,4096}$/.test(decoded.confirmation_token) &&
    /^[0-9a-f]{64}$/.test(decoded.review_digest) &&
    isLifecycleTimestamp(decoded.expires_at)
    ? decoded
    : null
}

export function decodeLifecycleStartBody(
  kind: wire.LifecycleOperation['kind'],
  value: unknown
):
  | wire._EmptyBody
  | wire._InstallBody
  | wire._IdentityBody
  | wire._CheckBody
  | wire._TrustBody
  | wire._ConfirmBody
  | null {
  const model = kind.endsWith('_confirm')
    ? '_ConfirmBody'
    : kind.startsWith('trust_')
      ? '_TrustBody'
      : kind === 'install_prepare'
        ? '_InstallBody'
        : kind === 'update_check'
          ? '_CheckBody'
          : kind === 'refresh' || kind === 'inspect'
            ? '_EmptyBody'
            : '_IdentityBody'

  const decoded = decode<Record<string, Json>>(model, value)

  if (!decoded) {
    return null
  }

  if (
    model === '_InstallBody' &&
    (!isWorkflowMarketplaceInstallIdentifier(decoded.identifier) ||
      (decoded.ref !== null && (typeof decoded.ref !== 'string' || decoded.ref.length > 1024 || !clean(decoded.ref))) ||
      (decoded.package_path !== null &&
        (typeof decoded.package_path !== 'string' ||
          decoded.package_path.length > 1024 ||
          !canonicalPath(decoded.package_path))))
  ) {
    return null
  }

  if (
    model === '_TrustBody' &&
    decoded.workflow_name !== null &&
    !decodeLifecycleSelection({ type: 'one', workflow_name: decoded.workflow_name })
  ) {
    return null
  }

  return decoded
}
