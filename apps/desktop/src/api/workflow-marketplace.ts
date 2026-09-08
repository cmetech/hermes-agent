import type { HermesApiRequest } from '@/global'
import {
  decodeMarketplaceErrorEnvelope,
  decodeMarketplaceOperation,
  decodeWorkflowMarketplaceCapabilities,
  decodeWorkflowMarketplaceInstalledPage,
  decodeWorkflowMarketplaceOperationPage,
  decodeWorkflowMarketplaceSearchPage,
  decodeWorkflowMarketplaceSourceList,
  decodeWorkflowMarketplaceSourceResponse,
  isWorkflowMarketplaceConfirmationToken,
  isWorkflowMarketplaceInstallIdentifier,
  isWorkflowMarketplaceOperationId,
  isWorkflowMarketplacePackageId,
  isWorkflowMarketplacePackagePath,
  isWorkflowMarketplaceSourceName,
  isWorkflowMarketplaceSourceRequestUrl
} from '@/lib/workflow-marketplace-codec'
import type {
  WorkflowMarketplaceCapabilities,
  WorkflowMarketplaceInstalledPage,
  WorkflowMarketplaceOperation,
  WorkflowMarketplaceOperationPage,
  WorkflowMarketplaceSearchPage,
  WorkflowMarketplaceSourceList,
  WorkflowMarketplaceSourceResponse
} from '@/types/hermes'

import { capabilityScoped } from './client'

const ROOT = '/api/plugins/workflow/marketplace'
const INVALID_RESPONSE = 'Hermes returned invalid workflow marketplace data.'
const INVALID_REQUEST = 'Invalid workflow marketplace request.'
const UNSUPPORTED_MESSAGE = 'Workflow Marketplace requires a newer Hermes backend. Please upgrade Hermes and try again.'
const NETWORK_MESSAGE = 'Unable to reach the Hermes workflow marketplace.'

type Decoder<T> = (value: unknown) => T | null

export type WorkflowMarketplaceScope =
  null | string | undefined | { connectionId: null | string; profile: null | string }

type ProfileScope = WorkflowMarketplaceScope

export interface WorkflowMarketplaceSourceCreateInput {
  enabled?: boolean
  name: string
  ref?: null | string
  repositoryUrl: string
}

export interface WorkflowMarketplaceSourceUpdateInput {
  enabled: boolean
  ref: null | string
  repositoryUrl: string
}

export interface WorkflowMarketplacePackageIdentityInput {
  packageId: string
  sourceKey: string
}

export interface WorkflowMarketplaceInstallInput {
  identifier: string
  packagePath?: string
  ref?: string
}

export interface WorkflowMarketplaceSearchOptions {
  limit?: number
  offset?: number
  source?: string
}

export interface WorkflowMarketplaceOperationListOptions {
  limit?: number
  offset?: number
}

export class WorkflowMarketplaceApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(code: string, status: number, message: string) {
    super(message)
    this.name = 'WorkflowMarketplaceApiError'
    this.code = code
    this.status = status
  }
}

export class WorkflowMarketplaceUnsupportedError extends WorkflowMarketplaceApiError {
  constructor() {
    super('marketplace_unsupported', 404, UNSUPPORTED_MESSAGE)
    this.name = 'WorkflowMarketplaceUnsupportedError'
  }
}

export function isWorkflowMarketplaceUnsupportedError(error: unknown): error is WorkflowMarketplaceUnsupportedError {
  return error instanceof WorkflowMarketplaceUnsupportedError
}

function failRequest(): never {
  throw new TypeError(INVALID_REQUEST)
}

function boundedText(value: unknown, maximum: number, allowEmpty = false): value is string {
  return (
    typeof value === 'string' &&
    value.length <= maximum &&
    (allowEmpty || value.length > 0) &&
    value.trim() === value &&
    ![...value].some(character => {
      const codePoint = character.codePointAt(0)

      return codePoint !== undefined && (codePoint < 32 || codePoint === 127)
    })
  )
}

function optionalBoundedText(value: unknown, maximum: number): value is undefined | string {
  return value === undefined || boundedText(value, maximum)
}

function boundedInteger(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum
}

function requestScope(scope?: ProfileScope): Pick<HermesApiRequest, 'connectionId' | 'profile'> {
  if (scope !== null && typeof scope === 'object') {
    let prototype: object | null
    let keys: readonly PropertyKey[]
    let descriptors: PropertyDescriptorMap

    try {
      prototype = Object.getPrototypeOf(scope)
      keys = Reflect.ownKeys(scope)
      descriptors = Object.getOwnPropertyDescriptors(scope)
    } catch {
      failRequest()
    }

    if (
      prototype !== Object.prototype ||
      keys.length !== 2 ||
      keys.some(key => key !== 'connectionId' && key !== 'profile')
    ) {
      failRequest()
    }

    const connectionDescriptor = descriptors.connectionId
    const profileDescriptor = descriptors.profile

    if (
      connectionDescriptor === undefined ||
      profileDescriptor === undefined ||
      !connectionDescriptor.enumerable ||
      !profileDescriptor.enumerable ||
      !Object.hasOwn(connectionDescriptor, 'value') ||
      !Object.hasOwn(profileDescriptor, 'value')
    ) {
      failRequest()
    }

    const connectionId = connectionDescriptor.value
    const profile = profileDescriptor.value

    if (
      (connectionId !== null && !boundedText(connectionId, 256)) ||
      (profile !== null && !boundedText(profile, 256))
    ) {
      failRequest()
    }

    return { connectionId, profile }
  }

  if (typeof scope === 'string' && !boundedText(scope, 256)) {
    failRequest()
  }

  if (scope !== undefined && scope !== null && typeof scope !== 'string') {
    failRequest()
  }

  return capabilityScoped(scope)
}

function routeMissing(body: unknown): boolean {
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    return false
  }

  const entries = Object.entries(body)

  if (entries.length !== 1 || entries[0][0] !== 'detail') {
    return false
  }

  const detail = entries[0][1]

  return typeof detail === 'string' && (detail === 'Not Found' || /^No such API endpoint:/i.test(detail))
}

async function requestMarketplace<T>(
  request: HermesApiRequest,
  decoder: Decoder<T>,
  scope?: ProfileScope,
  capabilityProbe = false
): Promise<T> {
  const scopedRequest = { ...request, ...requestScope(scope) }
  let response

  try {
    response = await window.hermesDesktop.apiStructured<unknown>(scopedRequest)
  } catch {
    throw new WorkflowMarketplaceApiError('marketplace_network_error', 0, NETWORK_MESSAGE)
  }

  if (!response.ok) {
    if (capabilityProbe && response.status === 404 && routeMissing(response.body)) {
      throw new WorkflowMarketplaceUnsupportedError()
    }

    const envelope = decodeMarketplaceErrorEnvelope(response.body)
    throw new WorkflowMarketplaceApiError(
      envelope?.code ?? String(response.status),
      response.status,
      envelope?.message ?? `HTTP ${response.status}`
    )
  }

  const decoded = decoder(response.value)

  if (decoded === null) {
    throw new TypeError(INVALID_RESPONSE)
  }

  return decoded
}

function operation(request: HermesApiRequest, scope?: ProfileScope): Promise<WorkflowMarketplaceOperation> {
  return requestMarketplace(request, decodeMarketplaceOperation, scope)
}

function packageIdentity(input: WorkflowMarketplacePackageIdentityInput): WorkflowMarketplacePackageIdentityInput {
  if (
    typeof input !== 'object' ||
    input === null ||
    Array.isArray(input) ||
    Object.keys(input).length !== 2 ||
    !Object.hasOwn(input, 'packageId') ||
    !Object.hasOwn(input, 'sourceKey') ||
    !isWorkflowMarketplacePackageId(input.packageId) ||
    !boundedText(input.sourceKey, 128)
  ) {
    failRequest()
  }

  return { packageId: input.packageId, sourceKey: input.sourceKey }
}

function sourceName(value: unknown): string {
  if (!isWorkflowMarketplaceSourceName(value)) {
    failRequest()
  }

  return value
}

function workflowName(value: unknown): string {
  if (!boundedText(value, 256)) {
    failRequest()
  }

  return value
}

function confirmationToken(value: unknown): string {
  if (!isWorkflowMarketplaceConfirmationToken(value)) {
    failRequest()
  }

  return value
}

export async function getWorkflowMarketplaceCapabilities(
  scope?: ProfileScope
): Promise<WorkflowMarketplaceCapabilities> {
  return requestMarketplace({ path: `${ROOT}/capabilities` }, decodeWorkflowMarketplaceCapabilities, scope, true)
}

export async function listWorkflowMarketplaceSources(scope?: ProfileScope): Promise<WorkflowMarketplaceSourceList> {
  return requestMarketplace({ path: `${ROOT}/sources` }, decodeWorkflowMarketplaceSourceList, scope)
}

export async function addWorkflowMarketplaceSource(
  input: WorkflowMarketplaceSourceCreateInput,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceSourceResponse> {
  if (
    typeof input !== 'object' ||
    input === null ||
    Array.isArray(input) ||
    Object.keys(input).some(key => key !== 'enabled' && key !== 'name' && key !== 'ref' && key !== 'repositoryUrl') ||
    !Object.hasOwn(input, 'name') ||
    !Object.hasOwn(input, 'repositoryUrl') ||
    !isWorkflowMarketplaceSourceName(input.name) ||
    !isWorkflowMarketplaceSourceRequestUrl(input.repositoryUrl) ||
    (input.enabled !== undefined && typeof input.enabled !== 'boolean') ||
    (input.ref !== undefined && input.ref !== null && !boundedText(input.ref, 1024))
  ) {
    failRequest()
  }

  const body: WorkflowMarketplaceSourceCreateInput = { name: input.name, repositoryUrl: input.repositoryUrl }

  if (input.enabled !== undefined) {
    body.enabled = input.enabled
  }

  if (input.ref !== undefined) {
    body.ref = input.ref
  }

  return requestMarketplace(
    { body, method: 'POST', path: `${ROOT}/sources` },
    decodeWorkflowMarketplaceSourceResponse,
    scope
  )
}

export async function updateWorkflowMarketplaceSource(
  name: string,
  input: WorkflowMarketplaceSourceUpdateInput,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceSourceResponse> {
  const safeName = sourceName(name)

  if (
    typeof input !== 'object' ||
    input === null ||
    Array.isArray(input) ||
    Object.keys(input).length !== 3 ||
    !Object.hasOwn(input, 'enabled') ||
    !Object.hasOwn(input, 'ref') ||
    !Object.hasOwn(input, 'repositoryUrl') ||
    typeof input.enabled !== 'boolean' ||
    (input.ref !== null && !boundedText(input.ref, 1024)) ||
    !isWorkflowMarketplaceSourceRequestUrl(input.repositoryUrl)
  ) {
    failRequest()
  }

  return requestMarketplace(
    {
      body: { enabled: input.enabled, ref: input.ref, repositoryUrl: input.repositoryUrl },
      method: 'PUT',
      path: `${ROOT}/sources/${encodeURIComponent(safeName)}`
    },
    decodeWorkflowMarketplaceSourceResponse,
    scope
  )
}

export async function setWorkflowMarketplaceSourceEnabled(
  name: string,
  enabled: boolean,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceSourceResponse> {
  const safeName = sourceName(name)

  if (typeof enabled !== 'boolean') {
    failRequest()
  }

  return requestMarketplace(
    { body: { enabled }, method: 'POST', path: `${ROOT}/sources/${encodeURIComponent(safeName)}/enabled` },
    decodeWorkflowMarketplaceSourceResponse,
    scope
  )
}

export async function removeWorkflowMarketplaceSource(
  name: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceSourceResponse> {
  const safeName = sourceName(name)

  return requestMarketplace(
    { method: 'DELETE', path: `${ROOT}/sources/${encodeURIComponent(safeName)}` },
    decodeWorkflowMarketplaceSourceResponse,
    scope
  )
}

export async function refreshWorkflowMarketplaceSource(
  name: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  const safeName = sourceName(name)

  return operation({ method: 'POST', path: `${ROOT}/sources/${encodeURIComponent(safeName)}/refresh` }, scope)
}

export async function searchWorkflowPackages(
  query: string,
  scope?: ProfileScope,
  options: WorkflowMarketplaceSearchOptions = {}
): Promise<WorkflowMarketplaceSearchPage> {
  if (
    !boundedText(query, 256, true) ||
    typeof options !== 'object' ||
    options === null ||
    Array.isArray(options) ||
    Object.keys(options).some(key => key !== 'limit' && key !== 'offset' && key !== 'source')
  ) {
    failRequest()
  }

  const offset = options.offset ?? 0
  const limit = options.limit ?? 50

  if (
    !boundedInteger(offset, 0, 199) ||
    !boundedInteger(limit, 1, 100) ||
    offset + limit > 200 ||
    (options.source !== undefined && !isWorkflowMarketplaceSourceName(options.source))
  ) {
    failRequest()
  }

  const parameters = new URLSearchParams()
  parameters.set('q', query)

  if (options.source !== undefined) {
    parameters.set('source', options.source)
  }

  parameters.set('offset', String(offset))
  parameters.set('limit', String(limit))

  return requestMarketplace({ path: `${ROOT}/packages?${parameters}` }, decodeWorkflowMarketplaceSearchPage, scope)
}

export async function inspectWorkflowPackage(
  source: string,
  packageId: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  const safeSource = sourceName(source)

  if (!isWorkflowMarketplacePackageId(packageId)) {
    failRequest()
  }

  return operation(
    { path: `${ROOT}/packages/${encodeURIComponent(safeSource)}/${encodeURIComponent(packageId)}` },
    scope
  )
}

export async function listInstalledWorkflowPackages(scope?: ProfileScope): Promise<WorkflowMarketplaceInstalledPage> {
  return requestMarketplace({ path: `${ROOT}/installed` }, decodeWorkflowMarketplaceInstalledPage, scope)
}

export async function checkWorkflowPackageUpdates(
  input: null | WorkflowMarketplacePackageIdentityInput,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  return operation(
    { body: input === null ? {} : { identity: packageIdentity(input) }, method: 'POST', path: `${ROOT}/updates/check` },
    scope
  )
}

export async function prepareWorkflowPackageInstall(
  input: WorkflowMarketplaceInstallInput,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  if (
    typeof input !== 'object' ||
    input === null ||
    Array.isArray(input) ||
    Object.keys(input).some(key => key !== 'identifier' && key !== 'packagePath' && key !== 'ref') ||
    !Object.hasOwn(input, 'identifier') ||
    !isWorkflowMarketplaceInstallIdentifier(input.identifier) ||
    (input.packagePath !== undefined && !isWorkflowMarketplacePackagePath(input.packagePath)) ||
    !optionalBoundedText(input.ref, 1024)
  ) {
    failRequest()
  }

  const body: WorkflowMarketplaceInstallInput = { identifier: input.identifier }

  if (input.packagePath !== undefined) {
    body.packagePath = input.packagePath
  }

  if (input.ref !== undefined) {
    body.ref = input.ref
  }

  return operation({ body, method: 'POST', path: `${ROOT}/install/prepare` }, scope)
}

function confirm(path: string, value: string, scope?: ProfileScope): Promise<WorkflowMarketplaceOperation> {
  const safeToken = confirmationToken(value)

  return operation({ body: { confirmationToken: safeToken }, method: 'POST', path: `${ROOT}/${path}/confirm` }, scope)
}

export async function confirmWorkflowPackageInstall(
  value: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  return confirm('install', value, scope)
}

export async function prepareWorkflowPackageUpdate(
  input: WorkflowMarketplacePackageIdentityInput,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  return operation({ body: packageIdentity(input), method: 'POST', path: `${ROOT}/update/prepare` }, scope)
}

export async function confirmWorkflowPackageUpdate(
  value: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  return confirm('update', value, scope)
}

export async function prepareWorkflowPackageRemoval(
  input: WorkflowMarketplacePackageIdentityInput,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  return operation({ body: packageIdentity(input), method: 'POST', path: `${ROOT}/remove/prepare` }, scope)
}

export async function confirmWorkflowPackageRemoval(
  value: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  return confirm('remove', value, scope)
}

export async function reviewWorkflowPackageTrust(
  input: WorkflowMarketplacePackageIdentityInput,
  selectedWorkflowName?: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  const body: { identity: WorkflowMarketplacePackageIdentityInput; workflowName?: string } = {
    identity: packageIdentity(input)
  }

  if (selectedWorkflowName !== undefined) {
    body.workflowName = workflowName(selectedWorkflowName)
  }

  return operation({ body, method: 'POST', path: `${ROOT}/trust/review` }, scope)
}

export async function grantWorkflowPackageTrust(
  value: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  const safeToken = confirmationToken(value)

  return operation({ body: { confirmationToken: safeToken }, method: 'POST', path: `${ROOT}/trust/grant` }, scope)
}

export async function revokeWorkflowPackageTrust(
  input: WorkflowMarketplacePackageIdentityInput,
  selectedWorkflowName?: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  const body: { identity: WorkflowMarketplacePackageIdentityInput; workflowName?: string } = {
    identity: packageIdentity(input)
  }

  if (selectedWorkflowName !== undefined) {
    body.workflowName = workflowName(selectedWorkflowName)
  }

  return operation({ body, method: 'POST', path: `${ROOT}/trust/revoke` }, scope)
}

export async function listWorkflowMarketplaceOperations(
  scope?: ProfileScope,
  options: WorkflowMarketplaceOperationListOptions = {}
): Promise<WorkflowMarketplaceOperationPage> {
  if (
    typeof options !== 'object' ||
    options === null ||
    Array.isArray(options) ||
    Object.keys(options).some(key => key !== 'limit' && key !== 'offset')
  ) {
    failRequest()
  }

  const offset = options.offset ?? 0
  const limit = options.limit ?? 50

  if (!boundedInteger(offset, 0, 10_000) || !boundedInteger(limit, 1, 100)) {
    failRequest()
  }

  const parameters = new URLSearchParams()
  parameters.set('offset', String(offset))
  parameters.set('limit', String(limit))

  return requestMarketplace({ path: `${ROOT}/operations?${parameters}` }, decodeWorkflowMarketplaceOperationPage, scope)
}

export async function getWorkflowMarketplaceOperation(
  id: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  if (!isWorkflowMarketplaceOperationId(id)) {
    failRequest()
  }

  const value = await operation({ path: `${ROOT}/operations/${encodeURIComponent(id)}` }, scope)

  if (value.id !== id) {
    throw new TypeError(INVALID_RESPONSE)
  }

  return value
}

export async function cancelWorkflowMarketplaceOperation(
  id: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceOperation> {
  if (!isWorkflowMarketplaceOperationId(id)) {
    failRequest()
  }

  const value = await operation({ method: 'POST', path: `${ROOT}/operations/${encodeURIComponent(id)}/cancel` }, scope)

  if (value.id !== id) {
    throw new TypeError(INVALID_RESPONSE)
  }

  return value
}
