import type { HermesApiRequest } from '@/global'
import {
  decodeLifecycleAdmission,
  decodeLifecycleCapabilities,
  decodeLifecycleEvicted,
  decodeLifecycleOperation,
  decodeLifecycleOperationPage,
  decodeLifecyclePackageState,
  decodeLifecycleReviewToken,
  decodeLifecycleSelection,
  decodeLifecycleStartBody,
  decodeLifecycleSubject,
  isLifecycleProfile,
  sameLifecycleIdentity,
  sameLifecycleValue
} from '@/lib/workflow-marketplace-lifecycle-codec'
import type * as wire from '@/types/workflow-marketplace-lifecycle'
import { lifecycleRules } from '@/types/workflow-marketplace-lifecycle'

const ROOT = '/api/plugins/workflow/marketplace/lifecycle/v2'
const REQUEST_ID = /^wmreq_([0-9a-f]{32})_[0-9]{13}_[0-9a-f]{32}$/
const OPERATION_ID = /^wmop_[0-9a-f]{12}_[0-9a-f]{32}$/
const EPOCH = /^[0-9a-f]{32}$/

export interface LifecycleScope {
  connectionId: string | null
  profile: string
  registryEpoch?: string
}

export interface LifecycleCorrelation {
  requestId: string
  kind: wire.LifecycleOperation['kind']
  subject: wire.LifecycleOperation['subject']
  selection: wire.LifecycleOperation['selection']
}

export interface LifecycleStart extends LifecycleCorrelation {
  body: wire._EmptyBody | wire._InstallBody | wire._IdentityBody | wire._CheckBody | wire._TrustBody | wire._ConfirmBody
}

export class LifecycleApiError extends Error {
  readonly code: string
  readonly status: number
  constructor(code: string, status: number) {
    super('Workflow marketplace lifecycle request could not be completed.')
    this.name = 'LifecycleApiError'
    this.code = code
    this.status = status
  }
}

function invalid(): never {
  throw new TypeError('Hermes returned invalid workflow lifecycle data.')
}

function invalidRequest(): never {
  throw new TypeError('Invalid workflow lifecycle request.')
}

function record(value: unknown, keys: readonly string[], optional: readonly string[] = []): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Object.getPrototypeOf(value) !== Object.prototype) {
    invalidRequest()
  }

  const descriptors = Object.getOwnPropertyDescriptors(value)

  if (
    Reflect.ownKeys(value).some(key => typeof key !== 'string' || !keys.includes(key)) ||
    keys.some(key => !Object.hasOwn(descriptors, key) && !optional.includes(key))
  ) {
    invalidRequest()
  }

  const result: Record<string, unknown> = {}

  for (const [key, descriptor] of Object.entries(descriptors)) {
    if (!descriptor.enumerable || !Object.hasOwn(descriptor, 'value')) {
      invalidRequest()
    }

    result[key] = descriptor.value
  }

  return result
}

function text(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    value.length <= 256 &&
    value.trim() === value &&
    !value.includes('\0')
  )
}

function captureScope(value: unknown, epoch = true): LifecycleScope {
  const fields = record(value, ['connectionId', 'profile', 'registryEpoch'], ['registryEpoch'])

  if (
    (fields.connectionId !== null && !text(fields.connectionId)) ||
    !isLifecycleProfile(fields.profile) ||
    (epoch && (typeof fields.registryEpoch !== 'string' || !EPOCH.test(fields.registryEpoch)))
  ) {
    invalidRequest()
  }

  return {
    connectionId: fields.connectionId as string | null,
    profile: fields.profile,
    ...(fields.registryEpoch === undefined ? {} : { registryEpoch: fields.registryEpoch as string })
  }
}

function captureCorrelation(value: unknown, scope: LifecycleScope): LifecycleCorrelation {
  const fields = record(value, ['requestId', 'kind', 'subject', 'selection'])

  if (
    typeof fields.requestId !== 'string' ||
    REQUEST_ID.exec(fields.requestId)?.[1] !== scope.registryEpoch ||
    typeof fields.kind !== 'string' ||
    !Object.hasOwn(lifecycleRules.subjects, fields.kind)
  ) {
    invalidRequest()
  }

  const kind = fields.kind as wire.LifecycleOperation['kind']
  const subject = decodeLifecycleSubject(fields.subject)
  const selection = fields.selection === null ? null : decodeLifecycleSelection(fields.selection)

  if (
    !subject ||
    !(lifecycleRules.subjects[kind] as readonly string[]).includes(subject.type) ||
    (fields.selection !== null && !selection) ||
    kind.startsWith('trust_') !== (selection !== null)
  ) {
    invalidRequest()
  }

  return { requestId: fields.requestId, kind, subject, selection }
}

async function request<T>(
  requestValue: HermesApiRequest,
  scope: LifecycleScope,
  decoder: (raw: unknown) => T | null,
  capabilities = false
): Promise<T> {
  let response

  try {
    response = await window.hermesDesktop.apiStructured<unknown>({
      ...requestValue,
      connectionId: scope.connectionId,
      profile: scope.profile
    })
  } catch {
    throw new LifecycleApiError('marketplace_network_error', 0)
  }

  if (!response.ok) {
    const body = response.body

    if (
      capabilities &&
      response.status === 404 &&
      typeof body === 'object' &&
      body !== null &&
      Object.keys(body).length === 1 &&
      'detail' in body &&
      typeof body.detail === 'string' &&
      (body.detail === 'Not Found' || /^No such API endpoint:/i.test(body.detail))
    ) {
      throw new LifecycleApiError('marketplace_lifecycle_unsupported', 404)
    }

    // The backend's bounded code is the only reflected error material.
    let code = String(response.status)

    if (
      body &&
      typeof body === 'object' &&
      'detail' in body &&
      body.detail &&
      typeof body.detail === 'object' &&
      'code' in body.detail &&
      typeof body.detail.code === 'string' &&
      /^[a-z][a-z0-9_]{0,127}$/.test(body.detail.code)
    ) {
      code = body.detail.code
    }

    throw new LifecycleApiError(code, response.status)
  }

  const value = decoder(response.value)

  if (value === null) {
    invalid()
  }

  return value
}

function correlate(
  value: wire.LifecycleOperation | wire.AdmissionEvicted,
  scope: LifecycleScope,
  expected?: LifecycleCorrelation
): void {
  if (value.registry_epoch !== scope.registryEpoch || value.profile !== scope.profile) {
    invalid()
  }

  if (
    expected &&
    (value.request_id !== expected.requestId ||
      value.kind !== expected.kind ||
      !sameLifecycleValue(value.subject, expected.subject) ||
      !sameLifecycleValue(value.selection, expected.selection))
  ) {
    invalid()
  }
}

export async function getLifecycleCapabilities(scopeInput: LifecycleScope): Promise<wire.LifecycleCapabilities> {
  const scope = captureScope(scopeInput, false)
  const value = await request({ path: `${ROOT}/capabilities` }, scope, decodeLifecycleCapabilities, true)

  if (value.profile !== scope.profile) {
    invalid()
  }

  return value
}

/** Server clock observation and elapsed local milliseconds; never a matching heuristic. */
export function createLifecycleRequestId(rawCapabilities: unknown, receivedAtMs: number, nowMs = Date.now()): string {
  const capabilities = decodeLifecycleCapabilities(rawCapabilities)

  if (!capabilities || !Number.isSafeInteger(receivedAtMs) || !Number.isSafeInteger(nowMs) || nowMs < receivedAtMs) {
    invalidRequest()
  }

  const issued = Date.parse(capabilities.server_time) + nowMs - receivedAtMs

  if (!Number.isSafeInteger(issued) || !/^\d{13}$/.test(String(issued))) {
    invalidRequest()
  }

  const random = crypto.getRandomValues(new Uint8Array(16))

  return `wmreq_${capabilities.registry_epoch}_${issued}_${Array.from(random, byte => byte.toString(16).padStart(2, '0')).join('')}`
}

async function exactOperation(
  id: string,
  scopeInput: LifecycleScope,
  expectedInput: unknown,
  cancel: boolean
): Promise<wire.LifecycleOperation> {
  const scope = captureScope(scopeInput)

  if (!OPERATION_ID.test(id)) {
    invalidRequest()
  }

  const expected = expectedInput === undefined ? undefined : captureCorrelation(expectedInput, scope)

  const value = await request(
    { path: `${ROOT}/operations/${id}${cancel ? '/cancel' : ''}`, ...(cancel ? { method: 'POST' } : {}) },
    scope,
    decodeLifecycleOperation
  )

  if (value.id !== id) {
    invalid()
  }

  correlate(value, scope, expected)

  return value
}

export function getLifecycleOperation(
  id: string,
  scope: LifecycleScope,
  expected?: unknown
): Promise<wire.LifecycleOperation> {
  return exactOperation(id, scope, expected, false)
}

export function cancelLifecycleOperation(
  id: string,
  scope: LifecycleScope,
  expected?: unknown
): Promise<wire.LifecycleOperation> {
  return exactOperation(id, scope, expected, true)
}

export async function listLifecycleOperations(
  scopeInput: LifecycleScope,
  options: { cursor?: string; limit?: number } = {}
): Promise<wire.LifecycleOperationPage> {
  const scope = captureScope(scopeInput)
  const fields = record(options, ['cursor', 'limit'], ['cursor', 'limit'])

  if (fields.cursor !== undefined && (typeof fields.cursor !== 'string' || !EPOCH.test(fields.cursor))) {
    invalidRequest()
  }

  if (
    fields.limit !== undefined &&
    (typeof fields.limit !== 'number' || !Number.isInteger(fields.limit) || fields.limit < 1 || fields.limit > 100)
  ) {
    invalidRequest()
  }

  const query = new URLSearchParams()

  if (fields.cursor !== undefined) {
    query.set('cursor', fields.cursor as string)
  }

  if (fields.limit !== undefined) {
    query.set('limit', String(fields.limit))
  }

  const value = await request(
    { path: `${ROOT}/operations${query.size ? `?${query}` : ''}` },
    scope,
    decodeLifecycleOperationPage
  )

  for (const operation of value.items) {
    correlate(operation, scope)
  }

  return value
}

export async function lookupLifecycleAdmission(
  requestId: string,
  scopeInput: LifecycleScope,
  expectedInput?: unknown
): Promise<wire.AdmissionFound | wire.AdmissionEvicted> {
  const scope = captureScope(scopeInput)

  if (REQUEST_ID.exec(requestId)?.[1] !== scope.registryEpoch) {
    invalidRequest()
  }

  const expected = expectedInput === undefined ? undefined : captureCorrelation(expectedInput, scope)
  const value = await request({ path: `${ROOT}/admissions/${requestId}` }, scope, decodeLifecycleAdmission)
  const operation = value.state === 'found' ? value.operation : value

  if (operation.request_id !== requestId) {
    invalid()
  }

  correlate(operation, scope, expected)

  return value
}

export async function getLifecyclePackageState(
  identityInput: wire.PackageIdentity,
  scopeInput: LifecycleScope
): Promise<wire.PackageState> {
  const scope = captureScope(scopeInput)
  const subject = decodeLifecycleSubject({ type: 'package', identity: identityInput })

  if (!subject || subject.type !== 'package') {
    invalidRequest()
  }

  const identity = subject.identity

  const value = await request(
    { path: `${ROOT}/packages/${identity.source_key}/${identity.package_id}/state` },
    scope,
    decodeLifecyclePackageState
  )

  if (value.profile !== scope.profile || !sameLifecycleIdentity(value.identity, identity)) {
    invalid()
  }

  return value
}

export async function startLifecycleOperation(
  input: unknown,
  scopeInput: LifecycleScope
): Promise<wire.LifecycleOperation | wire.AdmissionEvicted> {
  const scope = captureScope(scopeInput)
  const fields = record(input, ['requestId', 'kind', 'subject', 'selection', 'body'])

  const expected = captureCorrelation(
    { requestId: fields.requestId, kind: fields.kind, subject: fields.subject, selection: fields.selection },
    scope
  )

  const body = decodeLifecycleStartBody(expected.kind, fields.body)

  if (body === null) {
    invalidRequest()
  }

  const payload = body as Record<string, unknown>

  if (
    'identity' in payload &&
    (payload.identity === null
      ? expected.subject.type !== 'all_packages'
      : expected.subject.type !== 'package' || !sameLifecycleValue(payload.identity, expected.subject.identity))
  ) {
    invalidRequest()
  }

  if (
    'workflow_name' in payload &&
    !sameLifecycleValue(
      expected.selection,
      payload.workflow_name === null ? { type: 'all' } : { type: 'one', workflow_name: payload.workflow_name }
    )
  ) {
    invalidRequest()
  }

  if (
    'subject' in payload &&
    (!sameLifecycleValue(expected.subject, payload.subject) ||
      !sameLifecycleValue(expected.selection, payload.selection))
  ) {
    invalidRequest()
  }

  if (
    expected.kind === 'install_prepare' &&
    expected.subject.type === 'package' &&
    payload.identifier !== `${expected.subject.identity.source_key}/${expected.subject.identity.package_id}`
  ) {
    invalidRequest()
  }

  const paths: Partial<Record<wire.LifecycleOperation['kind'], string>> = {
    update_check: '/updates/check',
    install_prepare: '/install/prepare',
    install_confirm: '/install/confirm',
    update_prepare: '/update/prepare',
    update_confirm: '/update/confirm',
    remove_prepare: '/remove/prepare',
    remove_confirm: '/remove/confirm',
    trust_prepare: '/trust/review',
    trust_confirm: '/trust/grant',
    trust_revoke: '/trust/revoke'
  }

  const path =
    expected.subject.type === 'source'
      ? `/sources/${expected.subject.source_name}/refresh`
      : expected.kind === 'inspect' && expected.subject.type === 'package'
        ? `/packages/${expected.subject.identity.source_key}/${expected.subject.identity.package_id}`
        : paths[expected.kind]

  if (!path) {
    invalidRequest()
  }

  const value = await request(
    { path: ROOT + path, method: 'POST', timeoutMs: 15000, body: { request_id: expected.requestId, body } },
    scope,
    raw => decodeLifecycleOperation(raw) ?? decodeLifecycleEvicted(raw)
  )

  correlate(value, scope, expected)

  return value
}

/** Explicit caller-owned secret. Never call from shared observation/query code. */
export async function getLifecycleReviewToken(
  preparation: unknown,
  scopeInput: LifecycleScope
): Promise<wire.ReviewTokenResponse> {
  const scope = captureScope(scopeInput)
  const prepared = decodeLifecycleOperation(preparation)

  if (
    !prepared ||
    prepared.state !== 'succeeded' ||
    !prepared.kind.endsWith('_prepare') ||
    !prepared.result ||
    !('confirmation_available' in prepared.result.value) ||
    !prepared.result.value.confirmation_available
  ) {
    invalidRequest()
  }

  correlate(prepared, scope)
  const review = prepared.result.value

  const value = await request(
    {
      path: `${ROOT}/operations/${prepared.id}/review-token`,
      method: 'POST',
      body: { review_digest: review.review_digest, subject: prepared.subject, selection: prepared.selection }
    },
    scope,
    decodeLifecycleReviewToken
  )

  if (
    value.operation_id !== prepared.id ||
    value.request_id !== prepared.request_id ||
    !sameLifecycleValue(value.subject, prepared.subject) ||
    !sameLifecycleValue(value.selection, prepared.selection) ||
    value.review_digest !== review.review_digest ||
    value.expires_at !== review.expires_at
  ) {
    invalid()
  }

  return value
}
