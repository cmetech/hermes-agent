import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'

import { profileScopeKey } from '@/api/client'
import {
  cancelWorkflowMarketplaceOperation,
  checkWorkflowPackageUpdates,
  confirmWorkflowPackageInstall,
  confirmWorkflowPackageRemoval,
  confirmWorkflowPackageUpdate,
  getWorkflowMarketplaceOperation,
  grantWorkflowPackageTrust,
  prepareWorkflowPackageInstall,
  prepareWorkflowPackageRemoval,
  prepareWorkflowPackageUpdate,
  reviewWorkflowPackageTrust
} from '@/hermes'
import type { WorkflowMarketplacePackageIdentityInput, WorkflowMarketplaceScope } from '@/hermes'
import type {
  WorkflowMarketplaceInstalledPackage,
  WorkflowMarketplaceOperation,
  WorkflowMarketplaceOperationKind,
  WorkflowMarketplacePackageDetail,
  WorkflowMarketplacePackageIdentity
} from '@/types/hermes'

import { InstallReviewDialog, type InstallReviewDialogView } from './install-review-dialog'
import { marketplaceKeys } from './query-keys'
import { RemoveReviewDialog, type RemoveReviewDialogView } from './remove-review-dialog'
import { TrustReviewDialog, type TrustReviewDialogView, type TrustSelection } from './trust-review-dialog'

const POLL_INTERVAL_MS = 500

const STALE_REVIEW_CODES = new Set([
  'confirmation_token_invalid',
  'transaction_candidate_changed',
  'transaction_review_changed',
  'trust_review_changed'
])

interface LifecycleTarget {
  candidate: null | WorkflowMarketplacePackageDetail
  detailSourceName: string
  identity: WorkflowMarketplacePackageIdentity
  installed: null | WorkflowMarketplaceInstalledPackage
  origin: HTMLButtonElement | null
  sourceName: string
}

interface Attempt {
  generation: number
  scope: WorkflowMarketplaceScope
  scopeKey: string
  target: LifecycleTarget
}

type ActiveDialog =
  | { attempt: Attempt; dialog: 'install'; view: InstallReviewDialogView }
  | { attempt: Attempt; dialog: 'remove'; view: RemoveReviewDialogView }
  | {
      attempt: Attempt
      availableWorkflowNames: readonly string[]
      dialog: 'trust'
      selection: TrustSelection
      view: TrustReviewDialogView
    }

interface OperationWatch {
  attempt: Attempt
  expectedKind: WorkflowMarketplaceOperationKind
  onTerminal: (operation: WorkflowMarketplaceOperation) => void
  operation: WorkflowMarketplaceOperation
}

interface ConfirmationSecret {
  generation: number
  token: string
}

type ActiveDialogUpdate = ActiveDialog | null | ((current: ActiveDialog | null) => ActiveDialog | null)

function identityInput(identity: WorkflowMarketplacePackageIdentity): WorkflowMarketplacePackageIdentityInput {
  return { packageId: identity.package_id, sourceKey: identity.source_key }
}

function identitiesMatch(left: WorkflowMarketplacePackageIdentity, right: WorkflowMarketplacePackageIdentity): boolean {
  return left.package_id === right.package_id && left.source_key === right.source_key
}

function errorCode(error: unknown): null | string {
  if (typeof error !== 'object' || error === null || !('code' in error)) {
    return null
  }

  return typeof error.code === 'string' ? error.code : null
}

function operationIsActive(operation: WorkflowMarketplaceOperation): boolean {
  return operation.state === 'pending' || operation.state === 'running'
}

function operationFailureKind(operation: WorkflowMarketplaceOperation): 'cancelled' | 'failed' | 'stale' {
  if (operation.state === 'cancelled') {
    return 'cancelled'
  }

  return operation.state === 'failed' && STALE_REVIEW_CODES.has(operation.error.code) ? 'stale' : 'failed'
}

function installedTarget(
  item: WorkflowMarketplaceInstalledPackage,
  origin: HTMLButtonElement,
  detailSourceName = item.source_name
): LifecycleTarget {
  return {
    candidate: null,
    detailSourceName,
    identity: item.identity,
    installed: item,
    origin,
    sourceName: item.source_name
  }
}

function candidateTarget(detail: WorkflowMarketplacePackageDetail, origin: HTMLButtonElement): LifecycleTarget {
  return {
    candidate: detail,
    detailSourceName: detail.source_name,
    identity: detail.identity,
    installed: detail.installed,
    origin,
    sourceName: detail.source_name
  }
}

function waitForPoll(signal: AbortSignal): Promise<boolean> {
  return new Promise(resolve => {
    if (signal.aborted) {
      resolve(false)

      return
    }

    const timeout = window.setTimeout(() => resolve(true), POLL_INTERVAL_MS)
    signal.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timeout)
        resolve(false)
      },
      { once: true }
    )
  })
}

export interface WorkflowPackageLifecycle {
  checkForUpdates: (
    item: WorkflowMarketplaceInstalledPackage,
    origin: HTMLButtonElement,
    detailSourceName?: string
  ) => void
  dialogs: ReactNode
  install: (detail: WorkflowMarketplacePackageDetail, origin: HTMLButtonElement) => void
  remove: (item: WorkflowMarketplaceInstalledPackage, origin: HTMLButtonElement, detailSourceName?: string) => void
  reviewTrust: (item: WorkflowMarketplaceInstalledPackage, origin: HTMLButtonElement, detailSourceName?: string) => void
  update: (item: WorkflowMarketplaceInstalledPackage, origin: HTMLButtonElement, detailSourceName?: string) => void
}

export function useWorkflowPackageLifecycle(
  scope: WorkflowMarketplaceScope,
  focusFallback?: RefObject<HTMLElement | null>,
  contextIdentity?: null | string
): WorkflowPackageLifecycle {
  const queryClient = useQueryClient()
  const scopeKey = profileScopeKey(scope)
  const scopeKeyRef = useRef(scopeKey)
  const mountedRef = useRef(true)
  const generationRef = useRef(0)
  const activeRef = useRef<ActiveDialog | null>(null)
  const operationRef = useRef<OperationWatch | null>(null)
  const pollAbortRef = useRef<AbortController | null>(null)
  const prepareGuardRef = useRef(false)
  const confirmGuardRef = useRef(false)
  const cancelGuardRef = useRef(false)
  const secretRef = useRef<ConfirmationSecret | null>(null)
  const [active, setActiveState] = useState<ActiveDialog | null>(null)

  scopeKeyRef.current = scopeKey
  activeRef.current = active

  const setActive = useCallback((value: ActiveDialogUpdate) => {
    if (typeof value === 'function') {
      setActiveState(current => {
        const next = value(current)
        activeRef.current = next

        return next
      })

      return
    }

    activeRef.current = value
    setActiveState(value)
  }, [])

  const attemptIsCurrent = useCallback(
    (attempt: Attempt) =>
      mountedRef.current && generationRef.current === attempt.generation && scopeKeyRef.current === attempt.scopeKey,
    []
  )

  const stopWatching = useCallback(() => {
    pollAbortRef.current?.abort()
    pollAbortRef.current = null
    operationRef.current = null
  }, [])

  const clearSecret = useCallback(() => {
    secretRef.current = null
  }, [])

  const beginAttempt = useCallback(
    (target: LifecycleTarget): Attempt => {
      generationRef.current += 1
      stopWatching()
      clearSecret()

      return { generation: generationRef.current, scope, scopeKey, target }
    },
    [clearSecret, scope, scopeKey, stopWatching]
  )

  const invalidateOriginTruth = useCallback(
    (attempt: Attempt, includeCatalog: boolean) => {
      if (!attemptIsCurrent(attempt)) {
        return
      }

      const requests = [
        queryClient.invalidateQueries({ queryKey: marketplaceKeys.installed(attempt.scopeKey) }),
        queryClient.invalidateQueries({ queryKey: marketplaceKeys.searchRoot(attempt.scopeKey) }),
        queryClient.invalidateQueries({
          queryKey: marketplaceKeys.detail(
            attempt.scopeKey,
            attempt.target.detailSourceName,
            attempt.target.identity.package_id
          )
        })
      ]

      if (includeCatalog) {
        requests.push(queryClient.invalidateQueries({ queryKey: ['workflow-catalog', attempt.scopeKey] }))
      }

      void Promise.all(requests)
    },
    [attemptIsCurrent, queryClient]
  )

  const publishProgress = useCallback(
    (watch: OperationWatch, operation: WorkflowMarketplaceOperation) => {
      if (!attemptIsCurrent(watch.attempt)) {
        return
      }

      setActive(current => {
        if (!current || current.attempt.generation !== watch.attempt.generation) {
          return current
        }

        const progress = {
          cancellable: true,
          phase: operation.phase,
          progress: operation.progress
        }

        if (current.dialog === 'install') {
          return { ...current, view: { ...progress, kind: 'progress', mode: current.view.mode } }
        }

        if (current.dialog === 'remove') {
          return { ...current, view: { ...progress, kind: 'progress' } }
        }

        return { ...current, view: { ...progress, kind: 'progress' } }
      })
    },
    [attemptIsCurrent, setActive]
  )

  const publishOperationError = useCallback(
    (watch: OperationWatch, kind: 'evicted' | 'status') => {
      if (!attemptIsCurrent(watch.attempt)) {
        return
      }

      pollAbortRef.current?.abort()
      pollAbortRef.current = null
      operationRef.current = watch
      setActive(current => {
        if (!current || current.attempt.generation !== watch.attempt.generation) {
          return current
        }

        if (current.dialog === 'install') {
          return {
            ...current,
            view: {
              kind,
              mode: current.view.mode,
              previousVersion: current.attempt.target.installed?.version,
              recoverable: true
            }
          }
        }

        if (current.dialog === 'remove') {
          return {
            ...current,
            view: {
              currentVersion: current.attempt.target.installed?.version ?? '',
              kind,
              recoverable: true
            }
          }
        }

        return { ...current, view: { kind, recoverable: true } }
      })
    },
    [attemptIsCurrent, setActive]
  )

  const acceptOperation = useCallback(
    (watch: OperationWatch, operation: WorkflowMarketplaceOperation): boolean => {
      if (!attemptIsCurrent(watch.attempt)) {
        return true
      }

      const expectedProfile =
        typeof watch.attempt.scope === 'object' && watch.attempt.scope !== null
          ? (watch.attempt.scope.profile ?? 'default')
          : (watch.attempt.scope ?? 'default')

      if (operation.kind !== watch.expectedKind || operation.profile !== expectedProfile) {
        publishOperationError(watch, 'status')

        return true
      }

      watch.operation = operation
      operationRef.current = watch

      if (operationIsActive(operation)) {
        publishProgress(watch, operation)

        return false
      }

      stopWatching()
      watch.onTerminal(operation)

      return true
    },
    [attemptIsCurrent, publishOperationError, publishProgress, stopWatching]
  )

  const pollOperation = useCallback(
    async (watch: OperationWatch) => {
      const controller = new AbortController()
      pollAbortRef.current?.abort()
      pollAbortRef.current = controller

      while (attemptIsCurrent(watch.attempt) && operationIsActive(watch.operation)) {
        if (!(await waitForPoll(controller.signal)) || !attemptIsCurrent(watch.attempt)) {
          return
        }

        try {
          const operation = await getWorkflowMarketplaceOperation(watch.operation.id, watch.attempt.scope)

          if (controller.signal.aborted || !attemptIsCurrent(watch.attempt)) {
            return
          }

          if (acceptOperation(watch, operation)) {
            return
          }
        } catch (error) {
          if (controller.signal.aborted || !attemptIsCurrent(watch.attempt)) {
            return
          }

          publishOperationError(watch, errorCode(error) === 'marketplace_operation_not_found' ? 'evicted' : 'status')

          return
        }
      }
    },
    [acceptOperation, attemptIsCurrent, publishOperationError]
  )

  const admit = useCallback(
    async (
      attempt: Attempt,
      expectedKind: WorkflowMarketplaceOperationKind,
      request: () => Promise<WorkflowMarketplaceOperation>,
      onTerminal: (operation: WorkflowMarketplaceOperation) => void
    ) => {
      try {
        const operation = await request()

        if (!attemptIsCurrent(attempt)) {
          return
        }

        const watch = { attempt, expectedKind, onTerminal, operation }

        if (!acceptOperation(watch, operation)) {
          void pollOperation(watch)
        }
      } catch (error) {
        if (!attemptIsCurrent(attempt)) {
          return
        }

        const placeholder = operationRef.current

        if (placeholder?.attempt.generation === attempt.generation) {
          publishOperationError(
            placeholder,
            errorCode(error) === 'marketplace_operation_not_found' ? 'evicted' : 'status'
          )

          return
        }

        const thrownKind = STALE_REVIEW_CODES.has(errorCode(error) ?? '') ? 'stale' : 'failed'
        invalidateOriginTruth(attempt, false)

        setActive(current => {
          if (!current || current.attempt.generation !== attempt.generation) {
            return current
          }

          if (current.dialog === 'install') {
            return {
              ...current,
              view: {
                kind: thrownKind,
                mode: current.view.mode,
                previousVersion: attempt.target.installed?.version,
                recoverable: true
              }
            }
          }

          if (current.dialog === 'remove') {
            return {
              ...current,
              view: {
                currentVersion: attempt.target.installed?.version ?? '',
                kind: thrownKind,
                recoverable: true
              }
            }
          }

          return { ...current, view: { kind: thrownKind, recoverable: true } }
        })
      }
    },
    [acceptOperation, attemptIsCurrent, invalidateOriginTruth, pollOperation, publishOperationError, setActive]
  )

  const beginInstall = useCallback(
    (detail: WorkflowMarketplacePackageDetail, origin: HTMLButtonElement) => {
      if (prepareGuardRef.current || detail.install_status !== 'not_installed') {
        return
      }

      prepareGuardRef.current = true
      const attempt = beginAttempt(candidateTarget(detail, origin))
      setActive({
        attempt,
        dialog: 'install',
        view: { cancellable: true, kind: 'progress', mode: 'install', phase: 'queued', progress: 0 }
      })

      const onTerminal = (operation: WorkflowMarketplaceOperation) => {
        if (!attemptIsCurrent(attempt)) {
          return
        }

        if (
          operation.state === 'succeeded' &&
          operation.result.type === 'install_review' &&
          identitiesMatch(operation.result.value.identity, attempt.target.identity)
        ) {
          const { confirmation_token, ...review } = operation.result.value
          secretRef.current = { generation: attempt.generation, token: confirmation_token }
          setActive({ attempt, dialog: 'install', view: { kind: 'review', mode: 'install', review } })

          return
        }

        clearSecret()
        setActive({
          attempt,
          dialog: 'install',
          view: { kind: operationFailureKind(operation), mode: 'install', recoverable: true }
        })
      }

      void admit(
        attempt,
        'install_prepare',
        () => prepareWorkflowPackageInstall({ identifier: detail.identifier }, scope),
        onTerminal
      ).finally(() => {
        prepareGuardRef.current = false
      })
    },
    [admit, attemptIsCurrent, beginAttempt, clearSecret, scope, setActive]
  )

  const beginUpdatePrepare = useCallback(
    (attempt: Attempt) => {
      if (!attemptIsCurrent(attempt)) {
        return
      }

      setActive({
        attempt,
        dialog: 'install',
        view: { cancellable: true, kind: 'progress', mode: 'update', phase: 'queued', progress: 0 }
      })

      const onTerminal = (operation: WorkflowMarketplaceOperation) => {
        if (!attemptIsCurrent(attempt)) {
          return
        }

        if (
          operation.state === 'succeeded' &&
          operation.result.type === 'update_review' &&
          identitiesMatch(operation.result.value.identity, attempt.target.identity)
        ) {
          const { confirmation_token, ...review } = operation.result.value

          if (review.result === 'unchanged' && confirmation_token === null) {
            clearSecret()
            setActive({
              attempt,
              dialog: 'install',
              view: { kind: 'unchanged', mode: 'update', version: review.old_version }
            })

            return
          }

          if (confirmation_token !== null) {
            secretRef.current = { generation: attempt.generation, token: confirmation_token }
            setActive({ attempt, dialog: 'install', view: { kind: 'review', mode: 'update', review } })

            return
          }
        }

        clearSecret()
        invalidateOriginTruth(attempt, false)
        setActive({
          attempt,
          dialog: 'install',
          view: {
            kind: operationFailureKind(operation),
            mode: 'update',
            previousVersion: attempt.target.installed?.version,
            recoverable: true
          }
        })
      }

      void admit(
        attempt,
        'update_prepare',
        () => prepareWorkflowPackageUpdate(identityInput(attempt.target.identity), attempt.scope),
        onTerminal
      ).finally(() => {
        prepareGuardRef.current = false
      })
    },
    [admit, attemptIsCurrent, clearSecret, invalidateOriginTruth, setActive]
  )

  const beginUpdate = useCallback(
    (item: WorkflowMarketplaceInstalledPackage, origin: HTMLButtonElement, detailSourceName?: string) => {
      if (prepareGuardRef.current) {
        return
      }

      prepareGuardRef.current = true
      beginUpdatePrepare(beginAttempt(installedTarget(item, origin, detailSourceName)))
    },
    [beginAttempt, beginUpdatePrepare]
  )

  const beginCheck = useCallback(
    (item: WorkflowMarketplaceInstalledPackage, origin: HTMLButtonElement, detailSourceName?: string) => {
      if (prepareGuardRef.current) {
        return
      }

      prepareGuardRef.current = true
      const attempt = beginAttempt(installedTarget(item, origin, detailSourceName))
      setActive({
        attempt,
        dialog: 'install',
        view: { cancellable: true, kind: 'progress', mode: 'update', phase: 'checking', progress: 0 }
      })

      const onTerminal = (operation: WorkflowMarketplaceOperation) => {
        if (!attemptIsCurrent(attempt)) {
          return
        }

        if (operation.state === 'succeeded' && operation.result.type === 'update_checks') {
          const checks = operation.result.value.checks

          if (checks.length === 1 && identitiesMatch(checks[0].identity, attempt.target.identity)) {
            if (checks[0].status === 'current') {
              prepareGuardRef.current = false
              setActive({
                attempt,
                dialog: 'install',
                view: { kind: 'unchanged', mode: 'update', version: checks[0].installed_version }
              })

              return
            }

            if (checks[0].status === 'update_available') {
              beginUpdatePrepare(attempt)

              return
            }
          }
        }

        prepareGuardRef.current = false
        setActive({
          attempt,
          dialog: 'install',
          view: {
            kind: operationFailureKind(operation),
            mode: 'update',
            previousVersion: item.version,
            recoverable: true
          }
        })
      }

      void admit(
        attempt,
        'update_check',
        () => checkWorkflowPackageUpdates(identityInput(item.identity), attempt.scope),
        onTerminal
      ).finally(() => {
        if (activeRef.current?.view.kind !== 'progress') {
          prepareGuardRef.current = false
        }
      })
    },
    [admit, attemptIsCurrent, beginAttempt, beginUpdatePrepare, setActive]
  )

  const beginRemove = useCallback(
    (item: WorkflowMarketplaceInstalledPackage, origin: HTMLButtonElement, detailSourceName?: string) => {
      if (prepareGuardRef.current) {
        return
      }

      prepareGuardRef.current = true
      const attempt = beginAttempt(installedTarget(item, origin, detailSourceName))
      setActive({
        attempt,
        dialog: 'remove',
        view: { cancellable: true, kind: 'progress', phase: 'queued', progress: 0 }
      })

      const onTerminal = (operation: WorkflowMarketplaceOperation) => {
        if (!attemptIsCurrent(attempt)) {
          return
        }

        if (
          operation.state === 'succeeded' &&
          operation.result.type === 'remove_review' &&
          identitiesMatch(operation.result.value.identity, item.identity)
        ) {
          const { confirmation_token, ...review } = operation.result.value
          secretRef.current = { generation: attempt.generation, token: confirmation_token }
          setActive({ attempt, dialog: 'remove', view: { kind: 'review', review } })

          return
        }

        clearSecret()
        invalidateOriginTruth(attempt, false)
        setActive({
          attempt,
          dialog: 'remove',
          view: { currentVersion: item.version, kind: operationFailureKind(operation), recoverable: true }
        })
      }

      void admit(
        attempt,
        'remove_prepare',
        () => prepareWorkflowPackageRemoval(identityInput(item.identity), attempt.scope),
        onTerminal
      ).finally(() => {
        prepareGuardRef.current = false
      })
    },
    [admit, attemptIsCurrent, beginAttempt, clearSecret, invalidateOriginTruth, setActive]
  )

  const beginTrust = useCallback(
    (
      item: WorkflowMarketplaceInstalledPackage,
      origin: HTMLButtonElement,
      detailSourceName?: string,
      selection: TrustSelection = { kind: 'all' },
      availableWorkflowNames: readonly string[] = []
    ) => {
      if (prepareGuardRef.current) {
        return
      }

      prepareGuardRef.current = true
      const attempt = beginAttempt(installedTarget(item, origin, detailSourceName))
      setActive({
        attempt,
        availableWorkflowNames,
        dialog: 'trust',
        selection,
        view: { cancellable: true, kind: 'progress', phase: 'queued', progress: 0 }
      })

      const onTerminal = (operation: WorkflowMarketplaceOperation) => {
        if (!attemptIsCurrent(attempt)) {
          return
        }

        if (
          operation.state === 'succeeded' &&
          operation.result.type === 'trust_review' &&
          identitiesMatch(operation.result.value.identity, item.identity)
        ) {
          const { confirmation_token, ...review } = operation.result.value

          const nextWorkflowNames =
            selection.kind === 'all' ? review.workflows.map(workflow => workflow.workflow_name) : availableWorkflowNames

          secretRef.current = { generation: attempt.generation, token: confirmation_token }
          setActive({
            attempt,
            availableWorkflowNames: nextWorkflowNames,
            dialog: 'trust',
            selection,
            view: { kind: 'review', review }
          })

          return
        }

        clearSecret()
        setActive({
          attempt,
          availableWorkflowNames,
          dialog: 'trust',
          selection,
          view: { kind: operationFailureKind(operation), recoverable: true }
        })
      }

      void admit(
        attempt,
        'trust_prepare',
        () =>
          reviewWorkflowPackageTrust(
            identityInput(item.identity),
            selection.kind === 'one' ? selection.workflowName : undefined,
            attempt.scope
          ),
        onTerminal
      ).finally(() => {
        prepareGuardRef.current = false
      })
    },
    [admit, attemptIsCurrent, beginAttempt, clearSecret, setActive]
  )

  const confirmInstallOrUpdate = useCallback(async () => {
    const current = activeRef.current
    const secret = secretRef.current

    if (
      !current ||
      current.dialog !== 'install' ||
      current.view.kind !== 'review' ||
      !secret ||
      secret.generation !== current.attempt.generation ||
      confirmGuardRef.current
    ) {
      return
    }

    confirmGuardRef.current = true
    clearSecret()
    const { attempt } = current
    const mode = current.view.mode
    setActive({
      attempt,
      dialog: 'install',
      view: { cancellable: true, kind: 'progress', mode, phase: 'queued', progress: 0 }
    })

    const onTerminal = (operation: WorkflowMarketplaceOperation) => {
      if (!attemptIsCurrent(attempt)) {
        return
      }

      const expectedResult = mode === 'install' ? 'installed_package' : 'updated_package'

      if (
        operation.state === 'succeeded' &&
        operation.result.type === expectedResult &&
        identitiesMatch(operation.result.value.identity, attempt.target.identity)
      ) {
        attempt.target.installed = operation.result.value
        invalidateOriginTruth(attempt, true)
        setActive({
          attempt,
          dialog: 'install',
          view: { kind: 'succeeded', mode, trustRequired: true, version: operation.result.value.version }
        })

        return
      }

      invalidateOriginTruth(attempt, false)
      setActive({
        attempt,
        dialog: 'install',
        view: {
          kind: operationFailureKind(operation),
          mode,
          previousVersion: attempt.target.installed?.version,
          recoverable: true
        }
      })
    }

    try {
      await admit(
        attempt,
        mode === 'install' ? 'install_confirm' : 'update_confirm',
        () =>
          mode === 'install'
            ? confirmWorkflowPackageInstall(secret.token, attempt.scope)
            : confirmWorkflowPackageUpdate(secret.token, attempt.scope),
        onTerminal
      )
    } finally {
      confirmGuardRef.current = false
    }
  }, [admit, attemptIsCurrent, clearSecret, invalidateOriginTruth, setActive])

  const confirmRemove = useCallback(async () => {
    const current = activeRef.current
    const secret = secretRef.current

    if (
      !current ||
      current.dialog !== 'remove' ||
      current.view.kind !== 'review' ||
      !secret ||
      secret.generation !== current.attempt.generation ||
      confirmGuardRef.current
    ) {
      return
    }

    confirmGuardRef.current = true
    clearSecret()
    const { attempt } = current
    setActive({
      attempt,
      dialog: 'remove',
      view: { cancellable: true, kind: 'progress', phase: 'queued', progress: 0 }
    })

    const onTerminal = (operation: WorkflowMarketplaceOperation) => {
      if (!attemptIsCurrent(attempt)) {
        return
      }

      if (
        operation.state === 'succeeded' &&
        operation.result.type === 'removed_package' &&
        identitiesMatch(operation.result.value.identity, attempt.target.identity)
      ) {
        invalidateOriginTruth(attempt, true)
        setActive({ attempt, dialog: 'remove', view: { kind: 'succeeded' } })

        return
      }

      invalidateOriginTruth(attempt, false)
      setActive({
        attempt,
        dialog: 'remove',
        view: {
          currentVersion: attempt.target.installed?.version ?? '',
          kind: operationFailureKind(operation),
          recoverable: true
        }
      })
    }

    try {
      await admit(
        attempt,
        'remove_confirm',
        () => confirmWorkflowPackageRemoval(secret.token, attempt.scope),
        onTerminal
      )
    } finally {
      confirmGuardRef.current = false
    }
  }, [admit, attemptIsCurrent, clearSecret, invalidateOriginTruth, setActive])

  const confirmTrust = useCallback(async () => {
    const current = activeRef.current
    const secret = secretRef.current

    if (
      !current ||
      current.dialog !== 'trust' ||
      current.view.kind !== 'review' ||
      !secret ||
      secret.generation !== current.attempt.generation ||
      confirmGuardRef.current
    ) {
      return
    }

    confirmGuardRef.current = true
    clearSecret()
    const { attempt, availableWorkflowNames, selection } = current
    const reviewedWorkflowNames = current.view.review.workflows.map(workflow => workflow.workflow_name).sort()
    setActive({
      attempt,
      availableWorkflowNames,
      dialog: 'trust',
      selection,
      view: { cancellable: true, kind: 'progress', phase: 'queued', progress: 0 }
    })

    const onTerminal = (operation: WorkflowMarketplaceOperation) => {
      if (!attemptIsCurrent(attempt)) {
        return
      }

      const grantedStates =
        operation.state === 'succeeded' && operation.result.type === 'trust_grant'
          ? operation.result.value.workflows
          : []

      const grantedWorkflowNames = grantedStates.map(workflow => workflow.workflow_name).sort()

      if (
        operation.state === 'succeeded' &&
        operation.result.type === 'trust_grant' &&
        grantedStates.every(workflow => workflow.state === 'trusted') &&
        grantedWorkflowNames.length === reviewedWorkflowNames.length &&
        grantedWorkflowNames.every((name, index) => name === reviewedWorkflowNames[index])
      ) {
        invalidateOriginTruth(attempt, true)
        setActive({ attempt, availableWorkflowNames, dialog: 'trust', selection, view: { kind: 'succeeded' } })

        return
      }

      invalidateOriginTruth(attempt, false)
      setActive({
        attempt,
        availableWorkflowNames,
        dialog: 'trust',
        selection,
        view: { kind: operationFailureKind(operation), recoverable: true }
      })
    }

    try {
      await admit(attempt, 'trust_confirm', () => grantWorkflowPackageTrust(secret.token, attempt.scope), onTerminal)
    } finally {
      confirmGuardRef.current = false
    }
  }, [admit, attemptIsCurrent, clearSecret, invalidateOriginTruth, setActive])

  const cancel = useCallback(() => {
    const watch = operationRef.current

    if (!watch || !attemptIsCurrent(watch.attempt) || cancelGuardRef.current) {
      return
    }

    cancelGuardRef.current = true
    pollAbortRef.current?.abort()
    const operationId = watch.operation.id
    void cancelWorkflowMarketplaceOperation(operationId, watch.attempt.scope)
      .then(operation => {
        if (!attemptIsCurrent(watch.attempt)) {
          return
        }

        watch.operation = operation

        if (!acceptOperation(watch, operation)) {
          void pollOperation(watch)
        }
      })
      .catch(error => {
        if (attemptIsCurrent(watch.attempt)) {
          publishOperationError(watch, errorCode(error) === 'marketplace_operation_not_found' ? 'evicted' : 'status')
        }
      })
      .finally(() => {
        cancelGuardRef.current = false
      })
  }, [acceptOperation, attemptIsCurrent, pollOperation, publishOperationError])

  const retryStatus = useCallback(() => {
    const watch = operationRef.current

    if (!watch || !attemptIsCurrent(watch.attempt) || prepareGuardRef.current) {
      return
    }

    prepareGuardRef.current = true
    void getWorkflowMarketplaceOperation(watch.operation.id, watch.attempt.scope)
      .then(operation => {
        if (!attemptIsCurrent(watch.attempt)) {
          return
        }

        if (!acceptOperation(watch, operation)) {
          void pollOperation(watch)
        }
      })
      .catch(error => {
        if (attemptIsCurrent(watch.attempt)) {
          publishOperationError(watch, errorCode(error) === 'marketplace_operation_not_found' ? 'evicted' : 'status')
        }
      })
      .finally(() => {
        prepareGuardRef.current = false
      })
  }, [acceptOperation, attemptIsCurrent, pollOperation, publishOperationError])

  const close = useCallback(() => {
    const current = activeRef.current
    generationRef.current += 1
    stopWatching()
    clearSecret()
    setActive(null)

    const origin = current?.attempt.target.origin

    if (origin) {
      requestAnimationFrame(() => {
        if (origin.isConnected) {
          origin.focus()
        } else {
          focusFallback?.current?.focus()
        }
      })
    }
  }, [clearSecret, focusFallback, setActive, stopWatching])

  const prepareAgain = useCallback(() => {
    const current = activeRef.current

    if (!current) {
      return
    }

    const item = current.attempt.target.installed
    const candidate = current.attempt.target.candidate
    const origin = current.attempt.target.origin

    if (!origin) {
      return
    }

    if (current.dialog === 'install' && current.view.mode === 'install' && candidate) {
      beginInstall(candidate, origin)
    } else if (current.dialog === 'install' && item) {
      beginUpdate(item, origin, current.attempt.target.detailSourceName)
    } else if (current.dialog === 'remove' && item) {
      beginRemove(item, origin, current.attempt.target.detailSourceName)
    } else if (current.dialog === 'trust' && item) {
      beginTrust(
        item,
        origin,
        current.attempt.target.detailSourceName,
        current.selection,
        current.availableWorkflowNames
      )
    }
  }, [beginInstall, beginRemove, beginTrust, beginUpdate])

  const changeTrustSelection = useCallback(
    (selection: TrustSelection) => {
      const current = activeRef.current

      if (
        !current ||
        current.dialog !== 'trust' ||
        !current.attempt.target.installed ||
        !current.attempt.target.origin
      ) {
        return
      }

      beginTrust(
        current.attempt.target.installed,
        current.attempt.target.origin,
        current.attempt.target.detailSourceName,
        selection,
        current.availableWorkflowNames
      )
    },
    [beginTrust]
  )

  const reviewTrustAfterMutation = useCallback(() => {
    const current = activeRef.current

    if (
      !current ||
      current.dialog !== 'install' ||
      current.view.kind !== 'succeeded' ||
      !current.attempt.target.installed ||
      !current.attempt.target.origin
    ) {
      return
    }

    beginTrust(current.attempt.target.installed, current.attempt.target.origin, current.attempt.target.detailSourceName)
  }, [beginTrust])

  // eslint-disable-next-line no-restricted-syntax -- invalidates ephemeral operation authority at a backend scope boundary
  useEffect(() => {
    generationRef.current += 1
    stopWatching()
    clearSecret()
    setActive(null)
  }, [clearSecret, contextIdentity, scopeKey, setActive, stopWatching])

  // eslint-disable-next-line no-restricted-syntax -- mount generation prevents late async completion from publishing
  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      generationRef.current += 1
      stopWatching()
      clearSecret()
    }
  }, [clearSecret, stopWatching])

  const dialogs =
    active?.attempt.scopeKey === scopeKey ? (
      active.dialog === 'install' ? (
        <InstallReviewDialog
          onCancelOperation={cancel}
          onClose={close}
          onConfirm={confirmInstallOrUpdate}
          onPrepareAgain={prepareAgain}
          onRetryStatus={retryStatus}
          onReviewTrust={reviewTrustAfterMutation}
          open
          view={active.view}
        />
      ) : active.dialog === 'remove' ? (
        <RemoveReviewDialog
          onCancelOperation={cancel}
          onClose={close}
          onConfirm={confirmRemove}
          onPrepareAgain={prepareAgain}
          onRetryStatus={retryStatus}
          open
          sourceName={active.attempt.target.sourceName}
          view={active.view}
        />
      ) : (
        <TrustReviewDialog
          availableWorkflowNames={active.availableWorkflowNames}
          onCancelOperation={cancel}
          onClose={close}
          onGrant={confirmTrust}
          onPrepareAgain={prepareAgain}
          onRetryStatus={retryStatus}
          onSelectionChange={changeTrustSelection}
          open
          selection={active.selection}
          view={active.view}
        />
      )
    ) : null

  return {
    checkForUpdates: beginCheck,
    dialogs,
    install: beginInstall,
    remove: beginRemove,
    reviewTrust: beginTrust,
    update: beginUpdate
  }
}
