import type {
  WorkflowMarketplaceAssessment,
  WorkflowMarketplaceCapabilities,
  WorkflowMarketplaceCapability,
  WorkflowMarketplaceCatalogPackage,
  WorkflowMarketplaceCompatibilityChanges,
  WorkflowMarketplaceCompatibilityIdentity,
  WorkflowMarketplaceDiagnostic,
  WorkflowMarketplaceErrorEnvelope,
  WorkflowMarketplaceExternalRequirements,
  WorkflowMarketplaceFileChange,
  WorkflowMarketplaceInstalledPackage,
  WorkflowMarketplaceInstalledPage,
  WorkflowMarketplaceInstallReview,
  WorkflowMarketplaceOperation,
  WorkflowMarketplaceOperationError,
  WorkflowMarketplaceOperationKind,
  WorkflowMarketplaceOperationPage,
  WorkflowMarketplaceOperationResult,
  WorkflowMarketplacePackageDetail,
  WorkflowMarketplacePackageIdentity,
  WorkflowMarketplacePackageResource,
  WorkflowMarketplaceRemoveReview,
  WorkflowMarketplaceRequirementChanges,
  WorkflowMarketplaceResourceType,
  WorkflowMarketplaceRiskChanges,
  WorkflowMarketplaceRiskIdentity,
  WorkflowMarketplaceSearchPage,
  WorkflowMarketplaceSource,
  WorkflowMarketplaceSourceList,
  WorkflowMarketplaceSourceRecord,
  WorkflowMarketplaceSourceRefresh,
  WorkflowMarketplaceSourceResponse,
  WorkflowMarketplaceStringSetChange,
  WorkflowMarketplaceTrustReview,
  WorkflowMarketplaceTrustReviewItem,
  WorkflowMarketplaceTrustState,
  WorkflowMarketplaceUpdateCheck,
  WorkflowMarketplaceUpdateReview
} from '@/types/hermes'

import { workflowMarketplaceCanonicalIdentity, workflowMarketplaceCasefold } from './workflow-marketplace-casefold'

const SHA256 = /^[0-9a-f]{64}$/
const COMMIT = /^[0-9a-f]{40}$/

const SEMVER =
  /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/

const SOURCE_NAME = /^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$/
const PACKAGE_ID = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/
const TAG = /^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$/
const IDENTIFIER = /^[a-z][a-z0-9_]{0,127}$/
const OPERATION_ID = /^wmop_[0-9a-f]{12}_[0-9a-f]{32}$/
const CONFIRMATION_TOKEN = /^[A-Za-z0-9_-]{32,4096}$/
const ISO_UTC = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$/
const FILE_DECODE_LIMIT = 8

const CREDENTIAL_PARAMETER_WORDS = new Set([
  'auth',
  'authorization',
  'credential',
  'credentials',
  'key',
  'password',
  'secret',
  'signature',
  'token'
])

const CREDENTIAL_COMPOUND_QUALIFIERS = [
  'access',
  'api',
  'auth',
  'authorization',
  'aws',
  'azure',
  'client',
  'deploy',
  'github',
  'gitlab',
  'google',
  'oauth',
  'private',
  'secret',
  'security'
] as const

const CREDENTIAL_COMPOUND_SUFFIXES = [
  'credential',
  'credentials',
  'key',
  'password',
  'secret',
  'signature',
  'token'
] as const

const SENSITIVE_REPOSITORY_SEGMENTS = new Set([
  '.cache',
  '.quarantine',
  '.staging',
  'cache',
  'caches',
  'quarantine',
  'staging',
  'temp',
  'tmp'
])

type Decoder<T> = (value: unknown) => T | null

function containsControl(value: string): boolean {
  return [...value].some(character => {
    const codePoint = character.codePointAt(0)

    return codePoint !== undefined && (codePoint < 32 || codePoint === 127)
  })
}

function exactRecord(value: unknown, keys: readonly string[]): Map<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }

  const entries = Object.entries(value)

  if (entries.length !== keys.length) {
    return null
  }

  const expected = new Set(keys)

  if (entries.some(([key]) => !expected.has(key))) {
    return null
  }

  return new Map(entries)
}

function text(value: unknown, minimum: number, maximum: number): string | null {
  return typeof value === 'string' && value.length >= minimum && value.length <= maximum && !value.includes('\0')
    ? value
    : null
}

function cleanText(value: unknown, minimum: number, maximum: number): string | null {
  const decoded = text(value, minimum, maximum)

  return decoded !== null && decoded.trim() === decoded ? decoded : null
}

function optionalText(value: unknown, maximum: number): null | string | undefined {
  if (value === null) {
    return null
  }

  const decoded = cleanText(value, 1, maximum)

  return decoded === null ? undefined : decoded
}

function integer(value: unknown, minimum: number, maximum: number): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : null
}

function decodeArray<T>(value: unknown, maximum: number, decoder: Decoder<T>, minimum = 0): T[] | null {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    return null
  }

  const result: T[] = []

  for (const item of value) {
    const decoded = decoder(item)

    if (decoded === null) {
      return null
    }

    result.push(decoded)
  }

  return result
}

function unique(values: readonly string[]): boolean {
  return new Set(values).size === values.length
}

function logicalIdentity(value: string): string {
  return workflowMarketplaceCanonicalIdentity(value)
}

function logicallyUnique(values: readonly string[]): boolean {
  return unique(values.map(logicalIdentity))
}

function sortedUnique(values: readonly string[]): boolean {
  return unique(values) && values.every((value, index) => index === 0 || values[index - 1] <= value)
}

function stringArray(
  value: unknown,
  maximum: number,
  itemMaximum: number,
  options: { minimum?: number; sorted?: boolean; unique?: boolean; validate?: (value: string) => boolean } = {}
): string[] | null {
  const values = decodeArray(value, maximum, item => cleanText(item, 1, itemMaximum), options.minimum ?? 0)

  if (values === null) {
    return null
  }

  if (options.unique && !unique(values)) {
    return null
  }

  if (options.sorted && !sortedUnique(values)) {
    return null
  }

  if (options.validate && values.some(item => !options.validate?.(item))) {
    return null
  }

  return values
}

function canonicalPath(value: unknown): string | null {
  const decoded = cleanText(value, 1, 1024)

  if (
    decoded === null ||
    containsControl(decoded) ||
    decoded.startsWith('/') ||
    decoded.endsWith('/') ||
    decoded.includes('\\')
  ) {
    return null
  }

  const parts = decoded.split('/')

  if (
    parts.some(
      part => !part || part === '.' || part === '..' || /^[A-Za-z]:/.test(part) || part.toLowerCase() === '.git'
    )
  ) {
    return null
  }

  return decoded.normalize('NFC') === decoded ? decoded : null
}

function canonicalTimestamp(value: unknown): string | null {
  if (typeof value !== 'string' || value.length < 20 || value.length > 64) {
    return null
  }

  const match = ISO_UTC.exec(value)

  if (match === null) {
    return null
  }

  if (match[7] !== undefined && (match[7].length !== 6 || match[7] === '000000')) {
    return null
  }

  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const hour = Number(match[4])
  const minute = Number(match[5])
  const second = Number(match[6])
  const date = new Date(Date.UTC(year, month - 1, day, hour, minute, second))

  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day ||
    date.getUTCHours() !== hour ||
    date.getUTCMinutes() !== minute ||
    date.getUTCSeconds() !== second
  ) {
    return null
  }

  return value
}

function repeatedlyDecode(value: string): string[] | null {
  let decoded = value
  const layers = [value]

  try {
    for (let index = 0; index < FILE_DECODE_LIMIT; index += 1) {
      const next = decodeURIComponent(decoded)

      if (next === decoded) {
        return next.includes('%') ? null : layers
      }

      decoded = next
      layers.push(next)
    }
  } catch {
    return null
  }

  return null
}

const ENCODED_URL_DELIMITER = /%(?:2f|3a|3f|23|40|5c)/i
const REDACTED_PLACEHOLDER = /^(?:\[?redacted(?:_path)?\]?)(?:\.git)?$/i
const CREDENTIAL_PARAMETER_BOUNDARY = /[?#&;]([A-Za-z0-9_.~+%-]+)=/g

function hasDotSegment(value: string): boolean {
  return /(?:^|[/:?#&;=])\.{1,2}(?=$|[/:?#&;=])/.test(value)
}

function parameterNameWords(name: string): Set<string> {
  let decoded = name

  for (let index = 0; index < 3; index += 1) {
    let next: string

    try {
      next = decodeURIComponent(decoded.replaceAll('+', ' '))
    } catch {
      return new Set(['credential'])
    }

    if (next === decoded) {
      break
    }

    decoded = next
  }

  const separated = decoded.replace(/(?<=[a-z0-9])(?=[A-Z])/g, ' ').replace(/(?<=[A-Z])(?=[A-Z][a-z])/g, ' ')

  return new Set(workflowMarketplaceCasefold(separated).match(/[a-z0-9]+/g) ?? [])
}

function isCredentialQualifierSequence(value: string): boolean {
  if (!value) {
    return false
  }

  const reachable = new Set([0])

  for (let start = 0; start < value.length; start += 1) {
    if (!reachable.has(start)) {
      continue
    }

    for (const qualifier of CREDENTIAL_COMPOUND_QUALIFIERS) {
      if (value.startsWith(qualifier, start)) {
        reachable.add(start + qualifier.length)
      }
    }
  }

  return reachable.has(value.length)
}

function parameterNameContainsCredentials(name: string): boolean {
  if ([...parameterNameWords(name)].some(word => CREDENTIAL_PARAMETER_WORDS.has(word))) {
    return true
  }

  const compactName = workflowMarketplaceCasefold(name).replace(/[^a-z0-9]+/g, '')

  return CREDENTIAL_COMPOUND_SUFFIXES.some(
    suffix => compactName.endsWith(suffix) && isCredentialQualifierSequence(compactName.slice(0, -suffix.length))
  )
}

function hasCredentialParameter(value: string): boolean {
  return [...value.matchAll(CREDENTIAL_PARAMETER_BOUNDARY)].some(match => parameterNameContainsCredentials(match[1]))
}

function hasRedactedPlaceholder(value: string): boolean {
  const queryIndex = value.indexOf('?')
  const fragmentIndex = value.indexOf('#')

  const identityEnd = [queryIndex, fragmentIndex]
    .filter(index => index >= 0)
    .reduce((left, right) => Math.min(left, right), value.length)

  const identityParts = value.slice(0, identityEnd).split(/[/:]/)
  const fragmentParts = fragmentIndex < 0 ? [] : value.slice(fragmentIndex + 1).split('/')

  return [...identityParts, ...fragmentParts].some(part => REDACTED_PLACEHOLDER.test(part))
}

function hasCredentialAuthority(value: string): boolean {
  return (
    /(?:https?|ssh):\/\/[^/?#\s:@]+:[^/?#\s@]+@/i.test(value) ||
    /(?:^|[^A-Za-z0-9._-])[A-Za-z0-9._-]+:[^@\s/]+@[A-Za-z0-9.-]+/.test(value)
  )
}

function repositoryLayers(candidate: string): string[] | null {
  const layers = repeatedlyDecode(candidate)

  if (
    layers === null ||
    layers.some(
      layer =>
        containsControl(layer) ||
        layer.includes('\\') ||
        ENCODED_URL_DELIMITER.test(layer) ||
        hasDotSegment(layer) ||
        hasCredentialParameter(layer) ||
        hasCredentialAuthority(layer)
    )
  ) {
    return null
  }

  return layers
}

type RepositoryUrlMode = 'request' | 'response'

function safeFileRepositoryUrl(candidate: string, parsed: URL, mode: RepositoryUrlMode): string | null {
  if (
    parsed.search ||
    parsed.username ||
    parsed.password ||
    (parsed.hostname && parsed.hostname.toLowerCase() !== 'localhost') ||
    !parsed.pathname.startsWith('/')
  ) {
    return null
  }

  const normalized = parsed.pathname.replaceAll('\\', '/')

  const segments = normalized.split('/').filter(Boolean).map(logicalIdentity)

  if (
    segments.length === 0 ||
    normalized.startsWith('//') ||
    segments.some(segment => segment === '.' || segment === '..') ||
    /^[A-Za-z]:/.test(normalized.slice(1)) ||
    (mode === 'response' &&
      (parsed.hash.length > 0 ||
        segments.some(segment => SENSITIVE_REPOSITORY_SEGMENTS.has(segment)) ||
        segments.some(
          (segment, index) =>
            (segment === 'marketplace' && segments[index + 1] === 'workflows') ||
            (segment === 'workflows' && segments[index + 1] === 'marketplace') ||
            (segment === 'var' && segments[index + 1] === 'folders')
        )))
  ) {
    return null
  }

  return candidate
}

function safeRepositoryUrl(value: unknown, mode: RepositoryUrlMode): string | null {
  const candidate = cleanText(value, 1, 4096)

  if (candidate === null || containsControl(candidate) || candidate.includes('\\')) {
    return null
  }

  if (candidate === 'file:///REDACTED') {
    return mode === 'response' ? candidate : null
  }

  if (hasRedactedPlaceholder(candidate)) {
    return null
  }

  const layers = repositoryLayers(candidate)

  if (layers === null) {
    return null
  }

  const fullyDecoded = layers.at(-1)

  if (fullyDecoded === undefined) {
    return null
  }

  if (/^file:/i.test(fullyDecoded) && !/^file:\//i.test(fullyDecoded)) {
    return null
  }

  if (candidate.startsWith('git@')) {
    if (!isSafeScpRepositoryUrl(candidate) || !isSafeScpRepositoryUrl(fullyDecoded)) {
      return null
    }

    return candidate
  }

  let parsed: URL

  try {
    parsed = new URL(fullyDecoded)
  } catch {
    return null
  }

  if (parsed.password || (parsed.protocol === 'https:' && parsed.username)) {
    return null
  }

  if (parsed.protocol === 'https:') {
    return parsed.hostname && parsed.pathname ? candidate : null
  }

  if (parsed.protocol === 'ssh:') {
    return parsed.hostname && parsed.pathname && !parsed.search ? candidate : null
  }

  if (parsed.protocol !== 'file:') {
    return null
  }

  return safeFileRepositoryUrl(candidate, parsed, mode)
}

function isSafeScpRepositoryUrl(value: string): boolean {
  const primary = value.split('#', 1)[0]
  const prefix = 'git@'
  const separator = primary.indexOf(':', prefix.length)

  if (!primary.startsWith(prefix) || separator < 0 || primary.includes('?') || /\s/.test(value)) {
    return false
  }

  const host = primary.slice(prefix.length, separator)
  const repositoryPath = primary.slice(separator + 1)

  return /^[^@\s/:?#]+$/.test(host) && repositoryPath.length > 0
}

export function isWorkflowMarketplaceRepositoryUrl(value: unknown): value is string {
  return safeRepositoryUrl(value, 'response') !== null
}

function isSafeRepositoryShorthand(value: unknown): value is string {
  const decoded = cleanText(value, 1, 4096)

  if (decoded === null || containsControl(decoded) || decoded.includes('\\')) {
    return false
  }

  const layers = repositoryLayers(decoded)

  if (layers === null || layers.some(layer => layer.includes('@')) || layers.length !== 1) {
    return false
  }

  const boundary = [decoded.indexOf('?'), decoded.indexOf('#')]
    .filter(index => index >= 0)
    .reduce((left, right) => Math.min(left, right), decoded.length)

  const identity = decoded.slice(0, boundary)

  if (identity.includes('://') || identity.includes('@') || identity.includes(':')) {
    return false
  }

  const parts = identity.split('/')

  return (
    parts.length >= 2 &&
    parts.every(
      part =>
        part.length > 0 &&
        part.length <= 256 &&
        part !== '.' &&
        part !== '..' &&
        !containsControl(part) &&
        !hasRedactedPlaceholder(part)
    )
  )
}

export function isWorkflowMarketplaceSourceRequestUrl(value: unknown): value is string {
  return safeRepositoryUrl(value, 'request') !== null || isSafeRepositoryShorthand(value)
}

export function isWorkflowMarketplaceSourceName(value: unknown): value is string {
  const decoded = cleanText(value, 1, 64)

  return decoded !== null && SOURCE_NAME.test(decoded)
}

export function isWorkflowMarketplacePackageId(value: unknown): value is string {
  const decoded = cleanText(value, 1, 64)

  return decoded !== null && PACKAGE_ID.test(decoded)
}

export function isWorkflowMarketplacePackagePath(value: unknown): value is string {
  return canonicalPath(value) !== null
}

export function isWorkflowMarketplaceInstallIdentifier(value: unknown): value is string {
  const decoded = cleanText(value, 1, 4096)

  if (decoded === null || containsControl(decoded)) {
    return false
  }

  if (isSafeRepositoryShorthand(decoded)) {
    return true
  }

  return safeRepositoryUrl(decoded, 'request') !== null
}

export function isWorkflowMarketplaceConfirmationToken(value: unknown): value is string {
  return token(value) !== null
}

export function isWorkflowMarketplaceOperationId(value: unknown): value is string {
  const decoded = cleanText(value, 1, 128)

  return decoded !== null && OPERATION_ID.test(decoded)
}

function decodeIdentity(value: unknown): WorkflowMarketplacePackageIdentity | null {
  const record = exactRecord(value, ['package_id', 'source_key'])

  if (record === null) {
    return null
  }

  const packageId = cleanText(record.get('package_id'), 1, 64)
  const sourceKey = cleanText(record.get('source_key'), 1, 128)

  if (packageId === null || !PACKAGE_ID.test(packageId) || sourceKey === null) {
    return null
  }

  return { package_id: packageId, source_key: sourceKey }
}

function decodeRequirements(value: unknown): WorkflowMarketplaceExternalRequirements | null {
  const record = exactRecord(value, ['providers', 'runtimes', 'secrets', 'services', 'tools'])

  if (record === null) {
    return null
  }

  const providers = stringArray(record.get('providers'), 128, 128, { unique: true })
  const runtimes = stringArray(record.get('runtimes'), 128, 128, { unique: true })
  const secrets = stringArray(record.get('secrets'), 128, 128, { unique: true })
  const services = stringArray(record.get('services'), 128, 128, { unique: true })
  const tools = stringArray(record.get('tools'), 128, 128, { unique: true })

  if (providers === null || runtimes === null || secrets === null || services === null || tools === null) {
    return null
  }

  return { providers, runtimes, secrets, services, tools }
}

function decodeDiagnostic(value: unknown): WorkflowMarketplaceDiagnostic | null {
  const record = exactRecord(value, ['code', 'message', 'severity'])

  if (record === null) {
    return null
  }

  const code = cleanText(record.get('code'), 1, 128)
  const message = cleanText(record.get('message'), 1, 4096)
  const severity = record.get('severity')

  if (code === null || message === null || (severity !== 'advisory' && severity !== 'blocker')) {
    return null
  }

  return { code, message, severity }
}

function decodeCapability(value: unknown): WorkflowMarketplaceCapability | null {
  return value === 'installed'
    ? value
    : value === 'operations'
      ? value
      : value === 'search'
        ? value
        : value === 'sources'
          ? value
          : value === 'transactions'
            ? value
            : value === 'trust'
              ? value
              : value === 'updates'
                ? value
                : null
}

function decodeSource(value: unknown): WorkflowMarketplaceSource | null {
  const record = exactRecord(value, ['enabled', 'name', 'ref', 'repository_url'])

  if (record === null) {
    return null
  }

  const enabled = record.get('enabled')
  const name = cleanText(record.get('name'), 1, 64)
  const ref = optionalText(record.get('ref'), 1024)
  const repositoryUrl = safeRepositoryUrl(record.get('repository_url'), 'response')

  if (
    typeof enabled !== 'boolean' ||
    name === null ||
    !SOURCE_NAME.test(name) ||
    ref === undefined ||
    repositoryUrl === null
  ) {
    return null
  }

  return { enabled, name, ref, repository_url: repositoryUrl }
}

const DIAGNOSTIC_CREDENTIAL_ASSIGNMENT =
  /\b(?:access[_-]?token|refresh[_-]?token|token|api[_-]?key|auth(?:orization)?|password|credentials?|client[_-]?secret|confirmation[_-]?token)\b\s*[=:]/i

const DIAGNOSTIC_LOCAL_PATH =
  /(?:^|\s)(?:\/(?:private|tmp|users|home|var\/folders|var\/tmp|var\/cache)\/|[A-Za-z]:\\|\\\\[^\\\s]+\\|[^\s]*(?:\.staging|\.quarantine)(?:\/|\\))/i

function safeDiagnosticText(value: unknown): string | null {
  const decoded = cleanText(value, 1, 4096)

  return decoded !== null &&
    !containsControl(decoded) &&
    !DIAGNOSTIC_CREDENTIAL_ASSIGNMENT.test(decoded) &&
    !DIAGNOSTIC_LOCAL_PATH.test(decoded) &&
    !hasCredentialAuthority(decoded) &&
    !hasCredentialParameter(decoded)
    ? decoded
    : null
}

function decodeSourceRecord(value: unknown): WorkflowMarketplaceSourceRecord | null {
  const record = exactRecord(value, [
    'attempted_at',
    'diagnostic_code',
    'enabled',
    'message',
    'name',
    'ref',
    'refresh_state',
    'repository_url',
    'resolved_commit',
    'verified_at',
    'verified_package_count'
  ])

  if (record === null) {
    return null
  }

  const source = decodeSource({
    enabled: record.get('enabled'),
    name: record.get('name'),
    ref: record.get('ref'),
    repository_url: record.get('repository_url')
  })

  const rawAttemptedAt = record.get('attempted_at')
  const attemptedAt = rawAttemptedAt === null ? null : canonicalTimestamp(rawAttemptedAt)
  const rawDiagnosticCode = record.get('diagnostic_code')
  const diagnosticCode = rawDiagnosticCode === null ? null : cleanText(rawDiagnosticCode, 1, 128)
  const rawMessage = record.get('message')
  const message = rawMessage === null ? null : safeDiagnosticText(rawMessage)
  const refreshState = record.get('refresh_state')
  const rawResolvedCommit = record.get('resolved_commit')
  const resolvedCommit = rawResolvedCommit === null ? null : cleanText(rawResolvedCommit, 40, 40)
  const rawVerifiedAt = record.get('verified_at')
  const verifiedAt = rawVerifiedAt === null ? null : canonicalTimestamp(rawVerifiedAt)
  const verifiedPackageCount = integer(record.get('verified_package_count'), 0, 4096)

  const validRefreshState =
    refreshState === null ||
    refreshState === 'authentication-failed' ||
    refreshState === 'fresh' ||
    refreshState === 'incompatible' ||
    refreshState === 'malformed' ||
    refreshState === 'stale' ||
    refreshState === 'unavailable'

  const hasVerifiedCache = resolvedCommit !== null || verifiedAt !== null

  if (
    source === null ||
    (attemptedAt === null && rawAttemptedAt !== null) ||
    (diagnosticCode === null && rawDiagnosticCode !== null) ||
    (diagnosticCode !== null && !IDENTIFIER.test(diagnosticCode)) ||
    (message === null && rawMessage !== null) ||
    !validRefreshState ||
    (resolvedCommit === null && rawResolvedCommit !== null) ||
    (resolvedCommit !== null && !COMMIT.test(resolvedCommit)) ||
    (verifiedAt === null && rawVerifiedAt !== null) ||
    verifiedPackageCount === null ||
    (resolvedCommit === null) !== (verifiedAt === null) ||
    (!hasVerifiedCache && verifiedPackageCount !== 0) ||
    (refreshState === null &&
      (attemptedAt !== null || diagnosticCode !== null || message !== null || hasVerifiedCache)) ||
    (refreshState !== null && attemptedAt === null) ||
    (refreshState === 'fresh' && (!hasVerifiedCache || diagnosticCode !== null || message !== null)) ||
    (refreshState === 'stale' && (!hasVerifiedCache || diagnosticCode === null || message === null)) ||
    (refreshState !== null &&
      refreshState !== 'fresh' &&
      refreshState !== 'stale' &&
      (hasVerifiedCache || verifiedPackageCount !== 0 || diagnosticCode === null || message === null))
  ) {
    return null
  }

  return {
    ...source,
    attempted_at: attemptedAt,
    diagnostic_code: diagnosticCode,
    message,
    refresh_state: refreshState,
    resolved_commit: resolvedCommit,
    verified_at: verifiedAt,
    verified_package_count: verifiedPackageCount
  }
}

export function decodeWorkflowMarketplaceCapabilities(value: unknown): WorkflowMarketplaceCapabilities | null {
  const record = exactRecord(value, ['capabilities', 'profile', 'schema_version'])

  if (record === null || record.get('schema_version') !== 1) {
    return null
  }

  const capabilities = decodeArray(record.get('capabilities'), 7, decodeCapability, 7)
  const profile = cleanText(record.get('profile'), 1, 256)
  const required = new Set(['installed', 'operations', 'search', 'sources', 'transactions', 'trust', 'updates'])

  if (
    capabilities === null ||
    !unique(capabilities) ||
    profile === null ||
    capabilities.some(item => !required.has(item)) ||
    capabilities.length !== required.size
  ) {
    return null
  }

  return { capabilities, profile, schema_version: 1 }
}

export function decodeWorkflowMarketplaceSourceList(value: unknown): WorkflowMarketplaceSourceList | null {
  const record = exactRecord(value, ['profile', 'sources'])

  if (record === null) {
    return null
  }

  const profile = cleanText(record.get('profile'), 1, 256)
  const sources = decodeArray(record.get('sources'), 128, decodeSourceRecord)

  if (profile === null || sources === null || !unique(sources.map(source => source.name))) {
    return null
  }

  return { profile, sources }
}

export function decodeWorkflowMarketplaceSourceResponse(value: unknown): WorkflowMarketplaceSourceResponse | null {
  const record = exactRecord(value, ['profile', 'source', 'status'])

  if (record === null) {
    return null
  }

  const profile = cleanText(record.get('profile'), 1, 256)
  const source = decodeSource(record.get('source'))
  const status = record.get('status')

  if (
    profile === null ||
    source === null ||
    (status !== 'created' &&
      status !== 'disabled' &&
      status !== 'enabled' &&
      status !== 'removed' &&
      status !== 'updated')
  ) {
    return null
  }

  return { profile, source, status }
}

function decodeCatalogPackage(value: unknown): WorkflowMarketplaceCatalogPackage | null {
  const record = exactRecord(value, [
    'configured_ref',
    'contract_version',
    'description',
    'display_name',
    'id',
    'identifier',
    'license',
    'package_digest',
    'package_path',
    'publisher',
    'repository_url',
    'resolved_commit',
    'source_name',
    'state',
    'tags',
    'verified_at',
    'version'
  ])

  if (record === null || record.get('contract_version') !== 1) {
    return null
  }

  const configuredRef = optionalText(record.get('configured_ref'), 1024)
  const description = cleanText(record.get('description'), 1, 4096)
  const displayName = cleanText(record.get('display_name'), 1, 256)
  const id = cleanText(record.get('id'), 1, 64)
  const identifier = cleanText(record.get('identifier'), 3, 129)
  const license = cleanText(record.get('license'), 1, 256)
  const packageDigest = cleanText(record.get('package_digest'), 64, 64)
  const packagePath = canonicalPath(record.get('package_path'))
  const publisher = cleanText(record.get('publisher'), 1, 256)
  const repositoryUrl = safeRepositoryUrl(record.get('repository_url'), 'response')
  const resolvedCommit = cleanText(record.get('resolved_commit'), 40, 40)
  const sourceName = cleanText(record.get('source_name'), 1, 64)
  const state = record.get('state')
  const tags = stringArray(record.get('tags'), 64, 64, { minimum: 1, unique: true, validate: item => TAG.test(item) })
  const verifiedAt = canonicalTimestamp(record.get('verified_at'))
  const version = cleanText(record.get('version'), 1, 128)

  if (
    configuredRef === undefined ||
    description === null ||
    displayName === null ||
    id === null ||
    !PACKAGE_ID.test(id) ||
    identifier === null ||
    license === null ||
    packageDigest === null ||
    !SHA256.test(packageDigest) ||
    packagePath === null ||
    publisher === null ||
    repositoryUrl === null ||
    resolvedCommit === null ||
    !COMMIT.test(resolvedCommit) ||
    sourceName === null ||
    !SOURCE_NAME.test(sourceName) ||
    identifier !== `${sourceName}/${id}` ||
    (state !== 'fresh' && state !== 'stale') ||
    tags === null ||
    verifiedAt === null ||
    version === null ||
    !SEMVER.test(version)
  ) {
    return null
  }

  return {
    configured_ref: configuredRef,
    contract_version: 1,
    description,
    display_name: displayName,
    id,
    identifier,
    license,
    package_digest: packageDigest,
    package_path: packagePath,
    publisher,
    repository_url: repositoryUrl,
    resolved_commit: resolvedCommit,
    source_name: sourceName,
    state,
    tags,
    verified_at: verifiedAt,
    version
  }
}

export function decodeWorkflowMarketplaceSearchPage(value: unknown): WorkflowMarketplaceSearchPage | null {
  const record = exactRecord(value, ['items', 'limit', 'next_offset', 'offset', 'profile', 'query', 'source'])

  if (record === null) {
    return null
  }

  const items = decodeArray(record.get('items'), 100, decodeCatalogPackage)
  const limit = integer(record.get('limit'), 1, 100)
  const rawNextOffset = record.get('next_offset')
  const nextOffset = rawNextOffset === null ? null : integer(rawNextOffset, 1, 199)
  const offset = integer(record.get('offset'), 0, 199)
  const profile = cleanText(record.get('profile'), 1, 256)
  const rawQuery = record.get('query')
  const query = typeof rawQuery === 'string' && rawQuery.length <= 256 ? rawQuery : null
  const source = optionalText(record.get('source'), 64)

  if (
    items === null ||
    limit === null ||
    (nextOffset === null && rawNextOffset !== null) ||
    offset === null ||
    profile === null ||
    query === null ||
    source === undefined ||
    (source !== null && !SOURCE_NAME.test(source)) ||
    !unique(items.map(item => item.identifier)) ||
    (nextOffset !== null && nextOffset <= offset)
  ) {
    return null
  }

  return { items, limit, next_offset: nextOffset, offset, profile, query, source }
}

function decodeInstalledPackage(value: unknown): WorkflowMarketplaceInstalledPackage | null {
  const record = exactRecord(value, [
    'actor',
    'configured_ref',
    'contract_version',
    'distribution_digest',
    'identity',
    'installed_at',
    'orphaned_source',
    'package_path',
    'repository_url',
    'resolved_commit',
    'source_name',
    'version',
    'workflow_paths'
  ])

  if (record === null || record.get('contract_version') !== 1) {
    return null
  }

  const actor = cleanText(record.get('actor'), 1, 256)
  const configuredRef = optionalText(record.get('configured_ref'), 1024)
  const distributionDigest = cleanText(record.get('distribution_digest'), 64, 64)
  const identity = decodeIdentity(record.get('identity'))
  const installedAt = canonicalTimestamp(record.get('installed_at'))
  const orphanedSource = record.get('orphaned_source')
  const packagePath = canonicalPath(record.get('package_path'))
  const repositoryUrl = safeRepositoryUrl(record.get('repository_url'), 'response')
  const resolvedCommit = cleanText(record.get('resolved_commit'), 40, 40)
  const sourceName = cleanText(record.get('source_name'), 1, 64)
  const version = cleanText(record.get('version'), 1, 128)
  const workflowPaths = decodeArray(record.get('workflow_paths'), 512, canonicalPath)

  if (
    actor === null ||
    configuredRef === undefined ||
    distributionDigest === null ||
    !SHA256.test(distributionDigest) ||
    identity === null ||
    installedAt === null ||
    typeof orphanedSource !== 'boolean' ||
    packagePath === null ||
    repositoryUrl === null ||
    resolvedCommit === null ||
    !COMMIT.test(resolvedCommit) ||
    sourceName === null ||
    !SOURCE_NAME.test(sourceName) ||
    identity.source_key !== sourceName ||
    version === null ||
    !SEMVER.test(version) ||
    workflowPaths === null ||
    !logicallyUnique(workflowPaths)
  ) {
    return null
  }

  return {
    actor,
    configured_ref: configuredRef,
    contract_version: 1,
    distribution_digest: distributionDigest,
    identity,
    installed_at: installedAt,
    orphaned_source: orphanedSource,
    package_path: packagePath,
    repository_url: repositoryUrl,
    resolved_commit: resolvedCommit,
    source_name: sourceName,
    version,
    workflow_paths: workflowPaths
  }
}

export function decodeWorkflowMarketplaceInstalledPage(value: unknown): WorkflowMarketplaceInstalledPage | null {
  const record = exactRecord(value, ['packages', 'profile'])

  if (record === null) {
    return null
  }

  const packages = decodeArray(record.get('packages'), 512, decodeInstalledPackage)
  const profile = cleanText(record.get('profile'), 1, 256)

  if (
    packages === null ||
    profile === null ||
    !unique(packages.map(item => `${item.identity.source_key}\0${item.identity.package_id}`))
  ) {
    return null
  }

  return { packages, profile }
}

function decodeAssessment(value: unknown): WorkflowMarketplaceAssessment | null {
  const record = exactRecord(value, [
    'advisories',
    'blockers',
    'external_requirements',
    'package_digest',
    'package_resources',
    'review_digest',
    'workflow_names'
  ])

  if (record === null) {
    return null
  }

  const advisories = decodeArray(record.get('advisories'), 512, decodeDiagnostic)
  const blockers = decodeArray(record.get('blockers'), 512, decodeDiagnostic)
  const externalRequirements = decodeRequirements(record.get('external_requirements'))
  const packageDigest = cleanText(record.get('package_digest'), 64, 64)
  const packageResources = decodeArray(record.get('package_resources'), 512, canonicalPath)
  const reviewDigest = cleanText(record.get('review_digest'), 64, 64)
  const workflowNames = stringArray(record.get('workflow_names'), 512, 256, { minimum: 1, unique: true })

  if (
    advisories === null ||
    advisories.some(item => item.severity !== 'advisory') ||
    blockers === null ||
    blockers.some(item => item.severity !== 'blocker') ||
    externalRequirements === null ||
    packageDigest === null ||
    !SHA256.test(packageDigest) ||
    packageResources === null ||
    !sortedUnique(packageResources) ||
    !logicallyUnique(packageResources) ||
    reviewDigest === null ||
    !SHA256.test(reviewDigest) ||
    workflowNames === null
  ) {
    return null
  }

  return {
    advisories,
    blockers,
    external_requirements: externalRequirements,
    package_digest: packageDigest,
    package_resources: packageResources,
    review_digest: reviewDigest,
    workflow_names: workflowNames
  }
}

function sortedStrings(value: unknown, maximum = 512, itemMaximum = 256): string[] | null {
  return stringArray(value, maximum, itemMaximum, { sorted: true, unique: true })
}

function sortedPaths(value: unknown, maximum = 512): string[] | null {
  const paths = decodeArray(value, maximum, canonicalPath)

  return paths !== null && sortedUnique(paths) && logicallyUnique(paths) ? paths : null
}

function workflowResourcePaths(review: WorkflowMarketplaceTrustReviewItem): string[] {
  return [
    review.definition_path,
    ...(review.companion_path === null ? [] : [review.companion_path]),
    ...review.command_resources,
    ...review.script_resources,
    ...review.mcp_resources,
    ...review.mcp_resource_files
  ]
}

function reviewsMatchAssessment(
  assessment: WorkflowMarketplaceAssessment,
  reviews: readonly WorkflowMarketplaceTrustReviewItem[],
  packageDigest: string
): boolean {
  const reviewNames = reviews.map(review => review.workflow_name)
  const reviewNameSet = new Set(reviewNames)

  if (
    !logicallyUnique(assessment.workflow_names) ||
    !logicallyUnique(reviewNames) ||
    assessment.workflow_names.length !== reviewNames.length ||
    assessment.workflow_names.some(name => !reviewNameSet.has(name)) ||
    reviews.some(review => review.package_digest !== packageDigest)
  ) {
    return false
  }

  const packageResources = new Set(assessment.package_resources)

  return reviews.every(review => workflowResourcePaths(review).every(path => packageResources.has(path)))
}

function decodeTrustReviewItem(value: unknown): WorkflowMarketplaceTrustReviewItem | null {
  const record = exactRecord(value, [
    'approval_nodes',
    'command_nodes',
    'command_resources',
    'companion_path',
    'compatibility',
    'definition_path',
    'external_requirements',
    'local_mcp_servers',
    'mcp_resource_files',
    'mcp_resources',
    'outward_action_nodes',
    'package_digest',
    'package_resource_set',
    'providers',
    'remote_mcp_servers',
    'requested_skills',
    'requested_tools',
    'required_secrets',
    'risk_digest',
    'script_resources',
    'shell_or_script_nodes',
    'trust_state',
    'workflow_name'
  ])

  if (record === null || record.get('package_resource_set') !== 'package') {
    return null
  }

  const approvalNodes = sortedStrings(record.get('approval_nodes'))
  const commandNodes = sortedStrings(record.get('command_nodes'))
  const commandResources = sortedPaths(record.get('command_resources'))
  const companionPath = record.get('companion_path') === null ? null : canonicalPath(record.get('companion_path'))
  const compatibility = decodeArray(record.get('compatibility'), 512, decodeDiagnostic)
  const definitionPath = canonicalPath(record.get('definition_path'))
  const externalRequirements = decodeRequirements(record.get('external_requirements'))
  const localMcpServers = sortedStrings(record.get('local_mcp_servers'))
  const mcpResourceFiles = sortedPaths(record.get('mcp_resource_files'))
  const mcpResources = sortedPaths(record.get('mcp_resources'))
  const outwardActionNodes = sortedStrings(record.get('outward_action_nodes'))
  const packageDigest = cleanText(record.get('package_digest'), 64, 64)
  const providers = sortedStrings(record.get('providers'))
  const remoteMcpServers = sortedStrings(record.get('remote_mcp_servers'))
  const requestedSkills = sortedStrings(record.get('requested_skills'))
  const requestedTools = sortedStrings(record.get('requested_tools'))
  const requiredSecrets = sortedStrings(record.get('required_secrets'))
  const riskDigest = cleanText(record.get('risk_digest'), 64, 64)
  const scriptResources = sortedPaths(record.get('script_resources'))
  const shellOrScriptNodes = sortedStrings(record.get('shell_or_script_nodes'))
  const trustState = record.get('trust_state')
  const workflowName = cleanText(record.get('workflow_name'), 1, 256)

  if (
    approvalNodes === null ||
    commandNodes === null ||
    commandResources === null ||
    (companionPath === null && record.get('companion_path') !== null) ||
    compatibility === null ||
    definitionPath === null ||
    externalRequirements === null ||
    localMcpServers === null ||
    mcpResourceFiles === null ||
    mcpResources === null ||
    outwardActionNodes === null ||
    packageDigest === null ||
    !SHA256.test(packageDigest) ||
    providers === null ||
    remoteMcpServers === null ||
    requestedSkills === null ||
    requestedTools === null ||
    requiredSecrets === null ||
    riskDigest === null ||
    !SHA256.test(riskDigest) ||
    scriptResources === null ||
    shellOrScriptNodes === null ||
    (trustState !== 'trusted' && trustState !== 'untrusted') ||
    workflowName === null
  ) {
    return null
  }

  return {
    approval_nodes: approvalNodes,
    command_nodes: commandNodes,
    command_resources: commandResources,
    companion_path: companionPath,
    compatibility,
    definition_path: definitionPath,
    external_requirements: externalRequirements,
    local_mcp_servers: localMcpServers,
    mcp_resource_files: mcpResourceFiles,
    mcp_resources: mcpResources,
    outward_action_nodes: outwardActionNodes,
    package_digest: packageDigest,
    package_resource_set: 'package',
    providers,
    remote_mcp_servers: remoteMcpServers,
    requested_skills: requestedSkills,
    requested_tools: requestedTools,
    required_secrets: requiredSecrets,
    risk_digest: riskDigest,
    script_resources: scriptResources,
    shell_or_script_nodes: shellOrScriptNodes,
    trust_state: trustState,
    workflow_name: workflowName
  }
}

function decodeResource(value: unknown): WorkflowMarketplacePackageResource | null {
  const record = exactRecord(value, ['path', 'types'])

  if (record === null) {
    return null
  }

  const path = canonicalPath(record.get('path'))
  const types = decodeArray(record.get('types'), 7, decodeResourceType, 1)

  if (path === null || types === null || !sortedUnique(types)) {
    return null
  }

  return { path, types }
}

function decodeResourceType(value: unknown): WorkflowMarketplaceResourceType | null {
  return value === 'command'
    ? value
    : value === 'mcp'
      ? value
      : value === 'mcp_resource'
        ? value
        : value === 'other'
          ? value
          : value === 'script'
            ? value
            : value === 'workflow_companion'
              ? value
              : value === 'workflow_definition'
                ? value
                : null
}

export function decodeMarketplacePackageDetail(value: unknown): WorkflowMarketplacePackageDetail | null {
  const record = exactRecord(value, [
    'advisories',
    'blockers',
    'configured_ref',
    'contract_version',
    'description',
    'display_name',
    'external_requirements',
    'id',
    'identifier',
    'identity',
    'install_status',
    'installed',
    'license',
    'package_digest',
    'package_path',
    'publisher',
    'repository_url',
    'resolved_commit',
    'resources',
    'source_name',
    'source_state',
    'tags',
    'update_status',
    'verified',
    'verified_at',
    'version',
    'workflows'
  ])

  if (
    record === null ||
    record.get('contract_version') !== 1 ||
    record.get('source_state') !== 'fresh' ||
    record.get('verified') !== true
  ) {
    return null
  }

  const advisories = decodeArray(record.get('advisories'), 512, decodeDiagnostic)
  const blockers = decodeArray(record.get('blockers'), 512, decodeDiagnostic)
  const configuredRef = optionalText(record.get('configured_ref'), 1024)
  const description = cleanText(record.get('description'), 1, 4096)
  const displayName = cleanText(record.get('display_name'), 1, 256)
  const externalRequirements = decodeRequirements(record.get('external_requirements'))
  const id = cleanText(record.get('id'), 1, 64)
  const identifier = cleanText(record.get('identifier'), 3, 129)
  const installStatus = record.get('install_status')
  const installed = record.get('installed') === null ? null : decodeInstalledPackage(record.get('installed'))
  const license = cleanText(record.get('license'), 1, 256)
  const packageDigest = cleanText(record.get('package_digest'), 64, 64)
  const packagePath = canonicalPath(record.get('package_path'))
  const publisher = cleanText(record.get('publisher'), 1, 256)
  const repositoryUrl = safeRepositoryUrl(record.get('repository_url'), 'response')
  const resolvedCommit = cleanText(record.get('resolved_commit'), 40, 40)
  const resources = decodeArray(record.get('resources'), 512, decodeResource)
  const sourceName = cleanText(record.get('source_name'), 1, 64)

  const tags = stringArray(record.get('tags'), 64, 64, {
    minimum: 1,
    sorted: true,
    unique: true,
    validate: item => TAG.test(item)
  })

  const updateStatus = record.get('update_status')
  const verifiedAt = canonicalTimestamp(record.get('verified_at'))
  const version = cleanText(record.get('version'), 1, 128)
  const workflows = decodeArray(record.get('workflows'), 512, decodeTrustReviewItem, 1)

  if (
    advisories === null ||
    advisories.some(item => item.severity !== 'advisory') ||
    blockers === null ||
    blockers.some(item => item.severity !== 'blocker') ||
    configuredRef === undefined ||
    description === null ||
    displayName === null ||
    externalRequirements === null ||
    id === null ||
    !PACKAGE_ID.test(id) ||
    identifier === null ||
    (installStatus !== 'installed' && installStatus !== 'not_installed') ||
    (installed === null && record.get('installed') !== null) ||
    license === null ||
    packageDigest === null ||
    !SHA256.test(packageDigest) ||
    packagePath === null ||
    publisher === null ||
    repositoryUrl === null ||
    resolvedCommit === null ||
    !COMMIT.test(resolvedCommit) ||
    resources === null ||
    !sortedUnique(resources.map(item => item.path)) ||
    !logicallyUnique(resources.map(item => item.path)) ||
    sourceName === null ||
    !SOURCE_NAME.test(sourceName) ||
    identifier !== `${sourceName}/${id}` ||
    tags === null ||
    (updateStatus !== 'current' && updateStatus !== 'not_applicable' && updateStatus !== 'update_available') ||
    verifiedAt === null ||
    version === null ||
    !SEMVER.test(version) ||
    workflows === null ||
    !sortedUnique(workflows.map(item => item.workflow_name)) ||
    !logicallyUnique(workflows.map(item => item.workflow_name)) ||
    workflows.some(item => item.package_digest !== packageDigest)
  ) {
    return null
  }

  const identity = decodeIdentity(record.get('identity'))

  if (identity === null || identity.source_key !== sourceName || identity.package_id !== id) {
    return null
  }

  if (installStatus === 'not_installed') {
    if (installed !== null || updateStatus !== 'not_applicable') {
      return null
    }
  } else if (
    installed === null ||
    installed.identity.source_key !== identity.source_key ||
    installed.identity.package_id !== identity.package_id ||
    updateStatus === 'not_applicable'
  ) {
    return null
  }

  const roles = new Map(resources.map(resource => [resource.path, new Set(resource.types)]))

  for (const workflow of workflows) {
    if (!roles.get(workflow.definition_path)?.has('workflow_definition')) {
      return null
    }

    if (workflow.companion_path !== null && !roles.get(workflow.companion_path)?.has('workflow_companion')) {
      return null
    }

    if (workflow.command_resources.some(path => !roles.get(path)?.has('command'))) {
      return null
    }

    if (workflow.script_resources.some(path => !roles.get(path)?.has('script'))) {
      return null
    }

    if (workflow.mcp_resources.some(path => !roles.get(path)?.has('mcp'))) {
      return null
    }

    if (workflow.mcp_resource_files.some(path => !roles.get(path)?.has('mcp_resource'))) {
      return null
    }
  }

  return {
    advisories,
    blockers,
    configured_ref: configuredRef,
    contract_version: 1,
    description,
    display_name: displayName,
    external_requirements: externalRequirements,
    id,
    identifier,
    identity,
    install_status: installStatus,
    installed,
    license,
    package_digest: packageDigest,
    package_path: packagePath,
    publisher,
    repository_url: repositoryUrl,
    resolved_commit: resolvedCommit,
    resources,
    source_name: sourceName,
    source_state: 'fresh',
    tags,
    update_status: updateStatus,
    verified: true,
    verified_at: verifiedAt,
    version,
    workflows
  }
}

function nullableDigest(value: unknown): null | string | undefined {
  if (value === null) {
    return null
  }

  const decoded = cleanText(value, 64, 64)

  return decoded !== null && SHA256.test(decoded) ? decoded : undefined
}

function decodeFileChange(value: unknown): WorkflowMarketplaceFileChange | null {
  const record = exactRecord(value, ['candidate_digest', 'kind', 'old_digest', 'old_path', 'path'])

  if (record === null) {
    return null
  }

  const candidateDigest = nullableDigest(record.get('candidate_digest'))
  const kind = record.get('kind')
  const oldDigest = nullableDigest(record.get('old_digest'))
  const oldPath = record.get('old_path') === null ? null : canonicalPath(record.get('old_path'))
  const path = canonicalPath(record.get('path'))

  if (
    candidateDigest === undefined ||
    oldDigest === undefined ||
    (oldPath === null && record.get('old_path') !== null) ||
    path === null ||
    (kind !== 'added' && kind !== 'modified' && kind !== 'removed' && kind !== 'renamed')
  ) {
    return null
  }

  if (kind === 'added' && (oldPath !== null || oldDigest !== null || candidateDigest === null)) {
    return null
  }

  if (kind === 'removed' && (oldPath !== null || oldDigest === null || candidateDigest !== null)) {
    return null
  }

  if (kind === 'modified' && (oldPath !== null || oldDigest === null || candidateDigest === null)) {
    return null
  }

  if (
    kind === 'renamed' &&
    (oldPath === null || oldDigest === null || candidateDigest === null || oldDigest !== candidateDigest)
  ) {
    return null
  }

  return { candidate_digest: candidateDigest, kind, old_digest: oldDigest, old_path: oldPath, path }
}

function fileChangesAreCoherent(changes: readonly WorkflowMarketplaceFileChange[]): boolean {
  const endpoints = changes.flatMap(change =>
    change.old_path === null ? [change.path] : [change.old_path, change.path]
  )

  return logicallyUnique(endpoints)
}

function decodeStringSetChange(value: unknown): WorkflowMarketplaceStringSetChange | null {
  const record = exactRecord(value, ['added', 'removed'])

  if (record === null) {
    return null
  }

  const added = sortedStrings(record.get('added'))
  const removed = sortedStrings(record.get('removed'))

  if (added === null || removed === null) {
    return null
  }

  return { added, removed }
}

function decodeRiskIdentity(value: unknown): WorkflowMarketplaceRiskIdentity | null {
  const record = exactRecord(value, ['package_digest', 'risk_digest', 'workflow_name'])

  if (record === null) {
    return null
  }

  const packageDigest = cleanText(record.get('package_digest'), 64, 64)
  const riskDigest = cleanText(record.get('risk_digest'), 64, 64)
  const workflowName = cleanText(record.get('workflow_name'), 1, 256)

  if (
    packageDigest === null ||
    !SHA256.test(packageDigest) ||
    riskDigest === null ||
    !SHA256.test(riskDigest) ||
    workflowName === null
  ) {
    return null
  }

  return { package_digest: packageDigest, risk_digest: riskDigest, workflow_name: workflowName }
}

function riskIdentityKey(value: WorkflowMarketplaceRiskIdentity): string {
  return `${value.workflow_name}\0${value.package_digest}\0${value.risk_digest}`
}

function decodeRiskChanges(value: unknown): WorkflowMarketplaceRiskChanges | null {
  const record = exactRecord(value, ['added', 'removed'])

  if (record === null) {
    return null
  }

  const added = decodeArray(record.get('added'), 512, decodeRiskIdentity)
  const removed = decodeArray(record.get('removed'), 512, decodeRiskIdentity)

  if (
    added === null ||
    removed === null ||
    !sortedUnique(added.map(riskIdentityKey)) ||
    !sortedUnique(removed.map(riskIdentityKey))
  ) {
    return null
  }

  return { added, removed }
}

function decodeCompatibilityIdentity(value: unknown): WorkflowMarketplaceCompatibilityIdentity | null {
  const record = exactRecord(value, ['code', 'severity', 'workflow_name'])

  if (record === null) {
    return null
  }

  const code = cleanText(record.get('code'), 1, 128)
  const severity = record.get('severity')
  const workflowName = cleanText(record.get('workflow_name'), 1, 256)

  if (code === null || (severity !== 'advisory' && severity !== 'blocker') || workflowName === null) {
    return null
  }

  return { code, severity, workflow_name: workflowName }
}

function compatibilityIdentityKey(value: WorkflowMarketplaceCompatibilityIdentity): string {
  return `${value.workflow_name}\0${value.code}\0${value.severity}`
}

function decodeCompatibilityChanges(value: unknown): WorkflowMarketplaceCompatibilityChanges | null {
  const record = exactRecord(value, ['added', 'removed'])

  if (record === null) {
    return null
  }

  const added = decodeArray(record.get('added'), 512, decodeCompatibilityIdentity)
  const removed = decodeArray(record.get('removed'), 512, decodeCompatibilityIdentity)

  if (
    added === null ||
    removed === null ||
    !sortedUnique(added.map(compatibilityIdentityKey)) ||
    !sortedUnique(removed.map(compatibilityIdentityKey))
  ) {
    return null
  }

  return { added, removed }
}

function decodeRequirementChanges(value: unknown): WorkflowMarketplaceRequirementChanges | null {
  const record = exactRecord(value, ['providers', 'runtimes', 'secrets', 'services', 'tools'])

  if (record === null) {
    return null
  }

  const providers = decodeStringSetChange(record.get('providers'))
  const runtimes = decodeStringSetChange(record.get('runtimes'))
  const secrets = decodeStringSetChange(record.get('secrets'))
  const services = decodeStringSetChange(record.get('services'))
  const tools = decodeStringSetChange(record.get('tools'))

  if (providers === null || runtimes === null || secrets === null || services === null || tools === null) {
    return null
  }

  return { providers, runtimes, secrets, services, tools }
}

function token(value: unknown): string | null {
  const decoded = cleanText(value, 32, 4096)

  return decoded !== null && CONFIRMATION_TOKEN.test(decoded) ? decoded : null
}

export function decodeMarketplaceInstallReview(value: unknown): WorkflowMarketplaceInstallReview | null {
  const record = exactRecord(value, [
    'assessment',
    'candidate_digest',
    'candidate_version',
    'confirmation_token',
    'configured_ref',
    'file_changes',
    'identity',
    'operation',
    'package_path',
    'repository_url',
    'resolved_commit',
    'result',
    'review_digest',
    'source_name',
    'workflow_reviews'
  ])

  if (record === null || record.get('operation') !== 'install' || record.get('result') !== 'review_required') {
    return null
  }

  const assessment = decodeAssessment(record.get('assessment'))
  const candidateDigest = cleanText(record.get('candidate_digest'), 64, 64)
  const candidateVersion = cleanText(record.get('candidate_version'), 1, 128)
  const confirmationToken = token(record.get('confirmation_token'))
  const configuredRef = optionalText(record.get('configured_ref'), 1024)
  const fileChanges = decodeArray(record.get('file_changes'), 1024, decodeFileChange)
  const identity = decodeIdentity(record.get('identity'))
  const packagePath = canonicalPath(record.get('package_path'))
  const repositoryUrl = safeRepositoryUrl(record.get('repository_url'), 'response')
  const resolvedCommit = cleanText(record.get('resolved_commit'), 40, 40)
  const reviewDigest = cleanText(record.get('review_digest'), 64, 64)
  const sourceName = cleanText(record.get('source_name'), 1, 64)
  const workflowReviews = decodeArray(record.get('workflow_reviews'), 512, decodeTrustReviewItem)

  if (
    assessment === null ||
    candidateDigest === null ||
    !SHA256.test(candidateDigest) ||
    candidateVersion === null ||
    !SEMVER.test(candidateVersion) ||
    confirmationToken === null ||
    configuredRef === undefined ||
    fileChanges === null ||
    identity === null ||
    packagePath === null ||
    repositoryUrl === null ||
    resolvedCommit === null ||
    !COMMIT.test(resolvedCommit) ||
    reviewDigest === null ||
    !SHA256.test(reviewDigest) ||
    sourceName === null ||
    !SOURCE_NAME.test(sourceName) ||
    identity.source_key !== sourceName ||
    workflowReviews === null ||
    assessment.package_digest !== candidateDigest ||
    assessment.review_digest !== reviewDigest ||
    !reviewsMatchAssessment(assessment, workflowReviews, candidateDigest) ||
    !fileChangesAreCoherent(fileChanges)
  ) {
    return null
  }

  return {
    assessment,
    candidate_digest: candidateDigest,
    candidate_version: candidateVersion,
    confirmation_token: confirmationToken,
    configured_ref: configuredRef,
    file_changes: fileChanges,
    identity,
    operation: 'install',
    package_path: packagePath,
    repository_url: repositoryUrl,
    resolved_commit: resolvedCommit,
    result: 'review_required',
    review_digest: reviewDigest,
    source_name: sourceName,
    workflow_reviews: workflowReviews
  }
}

export function decodeMarketplaceUpdateReview(value: unknown): WorkflowMarketplaceUpdateReview | null {
  const record = exactRecord(value, [
    'assessment',
    'candidate_commit',
    'candidate_digest',
    'candidate_version',
    'compatibility_changes',
    'confirmation_token',
    'configured_ref',
    'file_changes',
    'identity',
    'old_commit',
    'old_digest',
    'old_version',
    'operation',
    'repository_url',
    'requirement_changes',
    'result',
    'review_digest',
    'risk_changes',
    'source_name',
    'workflow_changes',
    'workflow_reviews'
  ])

  if (record === null || record.get('operation') !== 'update') {
    return null
  }

  const assessment = decodeAssessment(record.get('assessment'))
  const candidateCommit = cleanText(record.get('candidate_commit'), 40, 40)
  const candidateDigest = cleanText(record.get('candidate_digest'), 64, 64)
  const candidateVersion = cleanText(record.get('candidate_version'), 1, 128)
  const compatibilityChanges = decodeCompatibilityChanges(record.get('compatibility_changes'))
  const rawToken = record.get('confirmation_token')
  const confirmationToken = rawToken === null ? null : token(rawToken)
  const configuredRef = optionalText(record.get('configured_ref'), 1024)
  const fileChanges = decodeArray(record.get('file_changes'), 1024, decodeFileChange)
  const identity = decodeIdentity(record.get('identity'))
  const oldCommit = cleanText(record.get('old_commit'), 40, 40)
  const oldDigest = cleanText(record.get('old_digest'), 64, 64)
  const oldVersion = cleanText(record.get('old_version'), 1, 128)
  const repositoryUrl = safeRepositoryUrl(record.get('repository_url'), 'response')
  const requirementChanges = decodeRequirementChanges(record.get('requirement_changes'))
  const result = record.get('result')
  const reviewDigest = cleanText(record.get('review_digest'), 64, 64)
  const riskChanges = decodeRiskChanges(record.get('risk_changes'))
  const sourceName = cleanText(record.get('source_name'), 1, 64)
  const workflowChanges = decodeStringSetChange(record.get('workflow_changes'))
  const workflowReviews = decodeArray(record.get('workflow_reviews'), 512, decodeTrustReviewItem)

  if (
    assessment === null ||
    candidateCommit === null ||
    !COMMIT.test(candidateCommit) ||
    candidateDigest === null ||
    !SHA256.test(candidateDigest) ||
    candidateVersion === null ||
    !SEMVER.test(candidateVersion) ||
    compatibilityChanges === null ||
    (confirmationToken === null && rawToken !== null) ||
    configuredRef === undefined ||
    fileChanges === null ||
    identity === null ||
    oldCommit === null ||
    !COMMIT.test(oldCommit) ||
    oldDigest === null ||
    !SHA256.test(oldDigest) ||
    oldVersion === null ||
    !SEMVER.test(oldVersion) ||
    repositoryUrl === null ||
    requirementChanges === null ||
    (result !== 'review_required' && result !== 'unchanged' && result !== 'update_available') ||
    reviewDigest === null ||
    !SHA256.test(reviewDigest) ||
    riskChanges === null ||
    sourceName === null ||
    !SOURCE_NAME.test(sourceName) ||
    identity.source_key !== sourceName ||
    workflowChanges === null ||
    workflowReviews === null ||
    (result === 'unchanged' ? confirmationToken !== null : confirmationToken === null) ||
    assessment.package_digest !== candidateDigest ||
    assessment.review_digest !== reviewDigest ||
    !reviewsMatchAssessment(assessment, workflowReviews, candidateDigest) ||
    !fileChangesAreCoherent(fileChanges) ||
    (result === 'unchanged' && (oldDigest !== candidateDigest || fileChanges.length !== 0))
  ) {
    return null
  }

  return {
    assessment,
    candidate_commit: candidateCommit,
    candidate_digest: candidateDigest,
    candidate_version: candidateVersion,
    compatibility_changes: compatibilityChanges,
    confirmation_token: confirmationToken,
    configured_ref: configuredRef,
    file_changes: fileChanges,
    identity,
    old_commit: oldCommit,
    old_digest: oldDigest,
    old_version: oldVersion,
    operation: 'update',
    repository_url: repositoryUrl,
    requirement_changes: requirementChanges,
    result,
    review_digest: reviewDigest,
    risk_changes: riskChanges,
    source_name: sourceName,
    workflow_changes: workflowChanges,
    workflow_reviews: workflowReviews
  }
}

export function decodeMarketplaceRemoveReview(value: unknown): WorkflowMarketplaceRemoveReview | null {
  const record = exactRecord(value, [
    'confirmation_token',
    'current_commit',
    'current_version',
    'distribution_digest',
    'identity',
    'operation',
    'result',
    'review_digest',
    'workflow_names'
  ])

  if (record === null || record.get('operation') !== 'remove' || record.get('result') !== 'review_required') {
    return null
  }

  const confirmationToken = token(record.get('confirmation_token'))
  const currentCommit = cleanText(record.get('current_commit'), 40, 40)
  const currentVersion = cleanText(record.get('current_version'), 1, 128)
  const distributionDigest = cleanText(record.get('distribution_digest'), 64, 64)
  const identity = decodeIdentity(record.get('identity'))
  const reviewDigest = cleanText(record.get('review_digest'), 64, 64)
  const workflowNames = stringArray(record.get('workflow_names'), 512, 256, { unique: true })

  if (
    confirmationToken === null ||
    currentCommit === null ||
    !COMMIT.test(currentCommit) ||
    currentVersion === null ||
    !SEMVER.test(currentVersion) ||
    distributionDigest === null ||
    !SHA256.test(distributionDigest) ||
    identity === null ||
    reviewDigest === null ||
    !SHA256.test(reviewDigest) ||
    workflowNames === null
  ) {
    return null
  }

  return {
    confirmation_token: confirmationToken,
    current_commit: currentCommit,
    current_version: currentVersion,
    distribution_digest: distributionDigest,
    identity,
    operation: 'remove',
    result: 'review_required',
    review_digest: reviewDigest,
    workflow_names: workflowNames
  }
}

export function decodeMarketplaceTrustReview(value: unknown): WorkflowMarketplaceTrustReview | null {
  const record = exactRecord(value, [
    'confirmation_token',
    'distribution_digest',
    'identity',
    'package_resources',
    'resolved_commit',
    'review_digest',
    'source_name',
    'version',
    'workflows'
  ])

  if (record === null) {
    return null
  }

  const confirmationToken = token(record.get('confirmation_token'))
  const distributionDigest = cleanText(record.get('distribution_digest'), 64, 64)
  const identity = decodeIdentity(record.get('identity'))
  const packageResources = sortedPaths(record.get('package_resources'))
  const resolvedCommit = cleanText(record.get('resolved_commit'), 40, 40)
  const reviewDigest = cleanText(record.get('review_digest'), 64, 64)
  const sourceName = cleanText(record.get('source_name'), 1, 64)
  const version = cleanText(record.get('version'), 1, 128)
  const workflows = decodeArray(record.get('workflows'), 512, decodeTrustReviewItem, 1)
  const packageResourceSet = packageResources === null ? null : new Set(packageResources)

  if (
    confirmationToken === null ||
    distributionDigest === null ||
    !SHA256.test(distributionDigest) ||
    identity === null ||
    packageResources === null ||
    resolvedCommit === null ||
    !COMMIT.test(resolvedCommit) ||
    reviewDigest === null ||
    !SHA256.test(reviewDigest) ||
    sourceName === null ||
    !SOURCE_NAME.test(sourceName) ||
    identity.source_key !== sourceName ||
    version === null ||
    !SEMVER.test(version) ||
    workflows === null ||
    packageResourceSet === null ||
    !logicallyUnique(workflows.map(item => item.workflow_name)) ||
    workflows.some(item => item.package_digest !== distributionDigest) ||
    workflows.some(item => workflowResourcePaths(item).some(path => !packageResourceSet.has(path)))
  ) {
    return null
  }

  return {
    confirmation_token: confirmationToken,
    distribution_digest: distributionDigest,
    identity,
    package_resources: packageResources,
    resolved_commit: resolvedCommit,
    review_digest: reviewDigest,
    source_name: sourceName,
    version,
    workflows
  }
}

function decodeUpdateCheck(value: unknown): WorkflowMarketplaceUpdateCheck | null {
  const record = exactRecord(value, [
    'candidate_version',
    'diagnostic_code',
    'identity',
    'installed_version',
    'message',
    'status'
  ])

  if (record === null) {
    return null
  }

  const rawCandidateVersion = record.get('candidate_version')
  const candidateVersion = rawCandidateVersion === null ? null : cleanText(rawCandidateVersion, 1, 128)
  const rawDiagnosticCode = record.get('diagnostic_code')
  const diagnosticCode = rawDiagnosticCode === null ? null : cleanText(rawDiagnosticCode, 1, 128)
  const identity = decodeIdentity(record.get('identity'))
  const installedVersion = cleanText(record.get('installed_version'), 1, 128)
  const rawMessage = record.get('message')
  const message = rawMessage === null ? null : cleanText(rawMessage, 1, 4096)
  const status = record.get('status')

  if (
    (candidateVersion === null && rawCandidateVersion !== null) ||
    (candidateVersion !== null && !SEMVER.test(candidateVersion)) ||
    (diagnosticCode === null && rawDiagnosticCode !== null) ||
    (diagnosticCode !== null && !IDENTIFIER.test(diagnosticCode)) ||
    identity === null ||
    installedVersion === null ||
    !SEMVER.test(installedVersion) ||
    (message === null && rawMessage !== null) ||
    (status !== 'current' && status !== 'error' && status !== 'orphaned' && status !== 'update_available') ||
    (status === 'error' ? diagnosticCode === null || message === null : diagnosticCode !== null || message !== null)
  ) {
    return null
  }

  return {
    candidate_version: candidateVersion,
    diagnostic_code: diagnosticCode,
    identity,
    installed_version: installedVersion,
    message,
    status
  }
}

function decodeSourceRefresh(value: unknown): WorkflowMarketplaceSourceRefresh | null {
  const record = exactRecord(value, [
    'diagnostic_code',
    'message',
    'package_count',
    'repository_url',
    'resolved_commit',
    'source_name',
    'state',
    'verified_at'
  ])

  if (record === null) {
    return null
  }

  const rawDiagnosticCode = record.get('diagnostic_code')
  const diagnosticCode = rawDiagnosticCode === null ? null : cleanText(rawDiagnosticCode, 1, 128)
  const rawMessage = record.get('message')
  const message = rawMessage === null ? null : cleanText(rawMessage, 1, 4096)
  const packageCount = integer(record.get('package_count'), 0, 4096)
  const repositoryUrl = safeRepositoryUrl(record.get('repository_url'), 'response')
  const rawResolvedCommit = record.get('resolved_commit')
  const resolvedCommit = rawResolvedCommit === null ? null : cleanText(rawResolvedCommit, 40, 40)
  const sourceName = cleanText(record.get('source_name'), 1, 64)
  const state = record.get('state')

  const states = new Set([
    'authentication-failed',
    'cancelled',
    'disabled',
    'fresh',
    'incompatible',
    'malformed',
    'stale',
    'unavailable'
  ])

  const rawVerifiedAt = record.get('verified_at')
  const verifiedAt = rawVerifiedAt === null ? null : canonicalTimestamp(rawVerifiedAt)

  if (
    (diagnosticCode === null && rawDiagnosticCode !== null) ||
    (diagnosticCode !== null && !IDENTIFIER.test(diagnosticCode)) ||
    (message === null && rawMessage !== null) ||
    packageCount === null ||
    repositoryUrl === null ||
    (resolvedCommit === null && rawResolvedCommit !== null) ||
    (resolvedCommit !== null && !COMMIT.test(resolvedCommit)) ||
    sourceName === null ||
    !SOURCE_NAME.test(sourceName) ||
    typeof state !== 'string' ||
    !states.has(state) ||
    (verifiedAt === null && rawVerifiedAt !== null)
  ) {
    return null
  }

  return {
    diagnostic_code: diagnosticCode,
    message,
    package_count: packageCount,
    repository_url: repositoryUrl,
    resolved_commit: resolvedCommit,
    source_name: sourceName,
    state:
      state === 'authentication-failed'
        ? 'authentication-failed'
        : state === 'cancelled'
          ? 'cancelled'
          : state === 'disabled'
            ? 'disabled'
            : state === 'fresh'
              ? 'fresh'
              : state === 'incompatible'
                ? 'incompatible'
                : state === 'malformed'
                  ? 'malformed'
                  : state === 'stale'
                    ? 'stale'
                    : 'unavailable',
    verified_at: verifiedAt
  }
}

function decodeTrustState(value: unknown): WorkflowMarketplaceTrustState | null {
  const record = exactRecord(value, ['state', 'workflow_name'])

  if (record === null) {
    return null
  }

  const state = record.get('state')
  const workflowName = cleanText(record.get('workflow_name'), 1, 256)

  if ((state !== 'trusted' && state !== 'untrusted') || workflowName === null) {
    return null
  }

  return { state, workflow_name: workflowName }
}

interface WorkflowMarketplaceSucceededCommon {
  readonly created_at: string
  readonly finished_at: string
  readonly id: string
  readonly profile: string
  readonly schema_version: 1
  readonly started_at: string
  readonly updated_at: string
}

interface WorkflowMarketplaceOperationContract {
  readonly runningPhases: readonly string[]
  readonly succeeded: (
    common: WorkflowMarketplaceSucceededCommon,
    result: WorkflowMarketplaceOperationResult
  ) => object | null
}

function succeededShape(common: WorkflowMarketplaceSucceededCommon): WorkflowMarketplaceSucceededCommon & {
  error: null
  phase: 'completed'
  progress: 100
  state: 'succeeded'
} {
  return {
    ...common,
    error: null,
    phase: 'completed',
    progress: 100,
    state: 'succeeded'
  }
}

const OPERATION_CONTRACTS: ReadonlyMap<WorkflowMarketplaceOperationKind, WorkflowMarketplaceOperationContract> =
  new Map([
    [
      'refresh',
      {
        runningPhases: ['running', 'fetching', 'verifying'],
        succeeded(common, result) {
          return result.type === 'source_refresh' ? { ...succeededShape(common), kind: 'refresh', result } : null
        }
      }
    ],
    [
      'package_detail',
      {
        runningPhases: ['running', 'fetching'],
        succeeded(common, result) {
          return result.type === 'package_detail' ? { ...succeededShape(common), kind: 'package_detail', result } : null
        }
      }
    ],
    [
      'update_check',
      {
        runningPhases: ['running', 'fetching'],
        succeeded(common, result) {
          return result.type === 'update_checks' ? { ...succeededShape(common), kind: 'update_check', result } : null
        }
      }
    ],
    [
      'install_prepare',
      {
        runningPhases: ['running', 'fetching', 'reviewing'],
        succeeded(common, result) {
          return result.type === 'install_review'
            ? { ...succeededShape(common), kind: 'install_prepare', result }
            : null
        }
      }
    ],
    [
      'install_confirm',
      {
        runningPhases: ['running', 'committing'],
        succeeded(common, result) {
          return result.type === 'installed_package'
            ? { ...succeededShape(common), kind: 'install_confirm', result }
            : null
        }
      }
    ],
    [
      'update_prepare',
      {
        runningPhases: ['running', 'fetching', 'reviewing'],
        succeeded(common, result) {
          return result.type === 'update_review' ? { ...succeededShape(common), kind: 'update_prepare', result } : null
        }
      }
    ],
    [
      'update_confirm',
      {
        runningPhases: ['running', 'committing'],
        succeeded(common, result) {
          return result.type === 'updated_package'
            ? { ...succeededShape(common), kind: 'update_confirm', result }
            : null
        }
      }
    ],
    [
      'remove_prepare',
      {
        runningPhases: ['running', 'reviewing'],
        succeeded(common, result) {
          return result.type === 'remove_review' ? { ...succeededShape(common), kind: 'remove_prepare', result } : null
        }
      }
    ],
    [
      'remove_confirm',
      {
        runningPhases: ['running', 'committing'],
        succeeded(common, result) {
          return result.type === 'removed_package'
            ? { ...succeededShape(common), kind: 'remove_confirm', result }
            : null
        }
      }
    ],
    [
      'trust_prepare',
      {
        runningPhases: ['running', 'reviewing'],
        succeeded(common, result) {
          return result.type === 'trust_review' ? { ...succeededShape(common), kind: 'trust_prepare', result } : null
        }
      }
    ],
    [
      'trust_confirm',
      {
        runningPhases: ['running', 'committing'],
        succeeded(common, result) {
          return result.type === 'trust_grant' ? { ...succeededShape(common), kind: 'trust_confirm', result } : null
        }
      }
    ],
    [
      'trust_revoke',
      {
        runningPhases: ['running', 'committing'],
        succeeded(common, result) {
          return result.type === 'trust_revoke' ? { ...succeededShape(common), kind: 'trust_revoke', result } : null
        }
      }
    ]
  ])

function decodeOperationKind(value: unknown): WorkflowMarketplaceOperationKind | null {
  if (typeof value !== 'string') {
    return null
  }

  for (const kind of OPERATION_CONTRACTS.keys()) {
    if (value === kind) {
      return kind
    }
  }

  return null
}

function decodeOperationResult(value: unknown): WorkflowMarketplaceOperationResult | null {
  const record = exactRecord(value, ['type', 'value'])

  if (record === null) {
    return null
  }

  const type = record.get('type')
  const resultValue = record.get('value')
  let result: WorkflowMarketplaceOperationResult | null = null

  if (type === 'install_review') {
    const decoded = decodeMarketplaceInstallReview(resultValue)

    if (decoded !== null) {
      result = { type, value: decoded }
    }
  } else if (type === 'installed_package' || type === 'removed_package' || type === 'updated_package') {
    const decoded = decodeInstalledPackage(resultValue)

    if (decoded !== null) {
      result =
        type === 'installed_package'
          ? { type, value: decoded }
          : type === 'removed_package'
            ? { type, value: decoded }
            : { type, value: decoded }
    }
  } else if (type === 'package_detail') {
    const decoded = decodeMarketplacePackageDetail(resultValue)

    if (decoded !== null) {
      result = { type, value: decoded }
    }
  } else if (type === 'remove_review') {
    const decoded = decodeMarketplaceRemoveReview(resultValue)

    if (decoded !== null) {
      result = { type, value: decoded }
    }
  } else if (type === 'source_refresh') {
    const decoded = decodeSourceRefresh(resultValue)

    if (decoded !== null) {
      result = { type, value: decoded }
    }
  } else if (type === 'trust_grant') {
    const resultRecord = exactRecord(resultValue, ['workflows'])

    if (resultRecord !== null) {
      const workflows = decodeArray(resultRecord.get('workflows'), 512, decodeTrustState)

      if (workflows !== null && unique(workflows.map(item => item.workflow_name))) {
        result = { type, value: { workflows } }
      }
    }
  } else if (type === 'trust_review') {
    const decoded = decodeMarketplaceTrustReview(resultValue)

    if (decoded !== null) {
      result = { type, value: decoded }
    }
  } else if (type === 'trust_revoke') {
    const resultRecord = exactRecord(resultValue, ['revoked'])

    if (resultRecord !== null) {
      const revoked = integer(resultRecord.get('revoked'), 0, 512)

      if (revoked !== null) {
        result = { type, value: { revoked } }
      }
    }
  } else if (type === 'update_checks') {
    const resultRecord = exactRecord(resultValue, ['checks'])

    if (resultRecord !== null) {
      const checks = decodeArray(resultRecord.get('checks'), 512, decodeUpdateCheck)

      if (checks !== null && unique(checks.map(item => `${item.identity.source_key}\0${item.identity.package_id}`))) {
        result = { type, value: { checks } }
      }
    }
  } else if (type === 'update_review') {
    const decoded = decodeMarketplaceUpdateReview(resultValue)

    if (decoded !== null) {
      result = { type, value: decoded }
    }
  }

  if (result === null) {
    return null
  }

  try {
    if (new TextEncoder().encode(JSON.stringify(result)).byteLength > 2 * 1024 * 1024) {
      return null
    }
  } catch {
    return null
  }

  return result
}

function decodeOperationError(value: unknown): WorkflowMarketplaceOperationError | null {
  const record = exactRecord(value, ['code', 'message'])

  if (record === null || record.get('message') !== 'Workflow marketplace operation failed.') {
    return null
  }

  const code = cleanText(record.get('code'), 1, 128)

  if (code === null || !IDENTIFIER.test(code)) {
    return null
  }

  return { code, message: 'Workflow marketplace operation failed.' }
}

export function decodeMarketplaceOperation(value: unknown): WorkflowMarketplaceOperation | null {
  const record = exactRecord(value, [
    'created_at',
    'error',
    'finished_at',
    'id',
    'kind',
    'phase',
    'profile',
    'progress',
    'result',
    'schema_version',
    'source_name',
    'started_at',
    'state',
    'updated_at'
  ])

  if (record === null || record.get('schema_version') !== 1) {
    return null
  }

  const createdAt = canonicalTimestamp(record.get('created_at'))
  const rawError = record.get('error')
  const error = rawError === null ? null : decodeOperationError(rawError)
  const rawFinishedAt = record.get('finished_at')
  const finishedAt = rawFinishedAt === null ? null : canonicalTimestamp(rawFinishedAt)
  const id = cleanText(record.get('id'), 1, 128)
  const kind = decodeOperationKind(record.get('kind'))
  const phase = cleanText(record.get('phase'), 1, 64)
  const profile = cleanText(record.get('profile'), 1, 256)
  const progress = integer(record.get('progress'), 0, 100)
  const rawResult = record.get('result')
  const result = rawResult === null ? null : decodeOperationResult(rawResult)
  const rawStartedAt = record.get('started_at')
  const startedAt = rawStartedAt === null ? null : canonicalTimestamp(rawStartedAt)
  const state = record.get('state')
  const rawSourceName = record.get('source_name')
  const sourceName = rawSourceName === null ? null : cleanText(rawSourceName, 1, 64)
  const updatedAt = canonicalTimestamp(record.get('updated_at'))

  if (
    createdAt === null ||
    (error === null && rawError !== null) ||
    (finishedAt === null && rawFinishedAt !== null) ||
    id === null ||
    !OPERATION_ID.test(id) ||
    kind === null ||
    (sourceName === null && rawSourceName !== null) ||
    (sourceName !== null && !SOURCE_NAME.test(sourceName)) ||
    (kind === 'refresh') !== (sourceName !== null) ||
    phase === null ||
    !IDENTIFIER.test(phase) ||
    profile === null ||
    progress === null ||
    (result === null && rawResult !== null) ||
    (startedAt === null && rawStartedAt !== null) ||
    (state !== 'cancelled' &&
      state !== 'failed' &&
      state !== 'pending' &&
      state !== 'running' &&
      state !== 'succeeded') ||
    updatedAt === null
  ) {
    return null
  }

  const operationContract = OPERATION_CONTRACTS.get(kind)

  if (operationContract === undefined) {
    return null
  }

  const common = {
    created_at: createdAt,
    id,
    kind,
    profile,
    schema_version: 1 as const,
    source_name: sourceName,
    updated_at: updatedAt
  }

  if (state === 'pending') {
    if (
      phase !== 'queued' ||
      startedAt !== null ||
      finishedAt !== null ||
      result !== null ||
      error !== null ||
      progress !== 0
    ) {
      return null
    }

    return {
      ...common,
      error: null,
      finished_at: null,
      phase: 'queued',
      progress: 0,
      result: null,
      started_at: null,
      state: 'pending'
    } as WorkflowMarketplaceOperation
  }

  if (state === 'running') {
    if (
      startedAt === null ||
      finishedAt !== null ||
      result !== null ||
      error !== null ||
      progress >= 100 ||
      !operationContract.runningPhases.includes(phase)
    ) {
      return null
    }

    return {
      ...common,
      error: null,
      finished_at: null,
      phase,
      progress,
      result: null,
      started_at: startedAt,
      state: 'running'
    } as WorkflowMarketplaceOperation
  }

  if (state === 'succeeded') {
    if (
      phase !== 'completed' ||
      startedAt === null ||
      finishedAt === null ||
      result === null ||
      error !== null ||
      progress !== 100
    ) {
      return null
    }

    const succeeded = operationContract.succeeded(
      {
        created_at: createdAt,
        finished_at: finishedAt,
        id,
        profile,
        schema_version: 1,
        started_at: startedAt,
        updated_at: updatedAt
      },
      result
    )

    return succeeded === null ? null : ({ ...succeeded, source_name: sourceName } as WorkflowMarketplaceOperation)
  }

  if (state === 'failed') {
    if (
      phase !== 'failed' ||
      startedAt === null ||
      finishedAt === null ||
      result !== null ||
      error === null ||
      progress >= 100
    ) {
      return null
    }

    return {
      ...common,
      error,
      finished_at: finishedAt,
      phase: 'failed',
      progress,
      result: null,
      started_at: startedAt,
      state: 'failed'
    } as WorkflowMarketplaceOperation
  }

  if (phase !== 'cancelled' || finishedAt === null || result !== null || error !== null || progress >= 100) {
    return null
  }

  return {
    ...common,
    error: null,
    finished_at: finishedAt,
    phase: 'cancelled',
    progress,
    result: null,
    started_at: startedAt,
    state: 'cancelled'
  } as WorkflowMarketplaceOperation
}

export function decodeWorkflowMarketplaceOperationPage(value: unknown): WorkflowMarketplaceOperationPage | null {
  const record = exactRecord(value, ['limit', 'offset', 'operations', 'profile'])

  if (record === null) {
    return null
  }

  const limit = integer(record.get('limit'), 1, 100)
  const offset = integer(record.get('offset'), 0, 10_000)
  const operations = decodeArray(record.get('operations'), 100, decodeMarketplaceOperation)
  const profile = cleanText(record.get('profile'), 1, 256)

  if (
    limit === null ||
    offset === null ||
    operations === null ||
    profile === null ||
    !unique(operations.map(item => item.id))
  ) {
    return null
  }

  return { limit, offset, operations, profile }
}

export function decodeMarketplaceErrorEnvelope(value: unknown): WorkflowMarketplaceErrorEnvelope | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }

  const entries = Object.entries(value)

  if (entries.length !== 1 || entries[0][0] !== 'detail') {
    return null
  }

  const detail = entries[0][1]

  if (typeof detail !== 'object' || detail === null || Array.isArray(detail)) {
    return null
  }

  const detailEntries = Object.entries(detail)

  if (detailEntries.length < 1 || detailEntries.length > 2) {
    return null
  }

  const detailKeys = new Set(detailEntries.map(([key]) => key))

  if (!detailKeys.has('code') || [...detailKeys].some(key => key !== 'code' && key !== 'message')) {
    return null
  }

  const detailMap = new Map(detailEntries)
  const code = cleanText(detailMap.get('code'), 1, 128)
  const rawMessage = detailMap.get('message')

  if (
    code === null ||
    !IDENTIFIER.test(code) ||
    (rawMessage !== undefined && (typeof rawMessage !== 'string' || rawMessage.length < 1 || rawMessage.length > 4096))
  ) {
    return null
  }

  return { code, message: 'Workflow marketplace request failed.' }
}
