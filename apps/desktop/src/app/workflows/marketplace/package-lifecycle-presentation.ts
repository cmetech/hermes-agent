import { LifecycleApiError } from '@/api/workflow-marketplace-lifecycle'
import { en } from '@/i18n/en'
import type { Translations } from '@/i18n/types'
import { decodeLifecycleOperation, sameLifecycleValue } from '@/lib/workflow-marketplace-lifecycle-codec'
import { acceptSupervisedOperation } from '@/lib/workflow-marketplace-supervision'
import type { SupervisedRecord } from '@/store/workflow-marketplace-supervisor'
import type { LifecycleOperation } from '@/types/workflow-marketplace-lifecycle'

export interface PackageLifecyclePresentation {
  kind:
    | 'unconfirmed'
    | 'success'
    | 'unchanged'
    | 'cancelled'
    | 'current'
    | 'check_error'
    | 'orphaned'
    | 'update_available'
    | 'review'
  message: string
  canPrepareAgain: boolean
  retryAction?: 'check'
}

export const unconfirmedPackagePresentation: PackageLifecyclePresentation = {
  kind: 'unconfirmed',
  message: en.operations.workflowMarketplaceUnconfirmed,
  canPrepareAgain: false
}

export const conflictingPackagePresentation: PackageLifecyclePresentation = {
  kind: 'unchanged',
  message: en.operations.workflowMarketplaceConflict,
  canPrepareAgain: true
}

export const isPackageAdmissionConflict = (error: unknown) =>
  error instanceof LifecycleApiError &&
  error.status === 409 &&
  (error.code === 'marketplace_operation_conflict' || error.code === 'marketplace_request_conflict')

/** Terminal history is independent of later current-state reconciliation and cached cards. */
export function packageLifecyclePresentation(
  record: SupervisedRecord,
  preparation?: LifecycleOperation,
  copy: Translations['operations'] = en.operations
): PackageLifecyclePresentation {
  const unconfirmed = { ...unconfirmedPackagePresentation, message: copy.workflowMarketplaceUnconfirmed }
  if (record.status !== 'terminal' || !record.operationId) {
    return unconfirmed
  }

  let operation: LifecycleOperation

  try {
    operation = acceptSupervisedOperation(record.operation, record.binding, record)
  } catch {
    return unconfirmed
  }

  if (!operation.outcome || operation.kind.startsWith('trust_')) {
    return unconfirmed
  }

  const outcome = operation.outcome

  switch (outcome.type) {
    case 'outcome_unknown':
      return unconfirmed

    case 'recovery_required':
      return {
        ...unconfirmed,
        message: copy.workflowMarketplaceRecoveryRequired
      }

    case 'cancelled_before_commit':
      return { kind: 'cancelled', message: copy.workflowMarketplaceCancelledBeforeCommit, canPrepareAgain: true }
    case 'committed': {
      const state = outcome.package_state

      if (!state || state.busy || state.recovery !== 'clear') {
        return unconfirmed
      }

      if (operation.kind === 'remove_confirm' && state.state === 'absent') {
        return { kind: 'success', message: copy.workflowMarketplaceRemovalCompleted, canPrepareAgain: false }
      }

      if ((operation.kind === 'install_confirm' || operation.kind === 'update_confirm') && state.installed) {
        return {
          kind: 'success',
          message: `${operation.kind === 'install_confirm' ? copy.workflowMarketplaceInstalledVersionResult(state.installed.version) : copy.workflowMarketplaceUpdatedVersionResult(state.installed.version)} ${copy.workflowMarketplaceInstallationNoTrust}${state.trust?.workflows.some(item => item.state === 'untrusted') ? ` ${copy.workflowMarketplaceUntrustedRunRequirement}` : ''}`,
          canPrepareAgain: false
        }
      }

      return unconfirmed
    }

    case 'known_unchanged': {
      const result = operation.state === 'succeeded' ? operation.result : null

      if (result?.type === 'update_checks' && result.value.checks.length === 1) {
        const check = result.value.checks[0]

        switch (check.status) {
          case 'current':
            return {
              kind: 'current',
              message: copy.workflowMarketplacePackageCurrent(check.installed_version),
              canPrepareAgain: false
            }

          case 'update_available':
            return {
              kind: 'update_available',
              message: copy.workflowMarketplaceUpdateAvailableResult,
              canPrepareAgain: true
            }

          case 'error':
            return {
              kind: 'check_error',
              message: copy.workflowMarketplaceCheckError,
              canPrepareAgain: false,
              retryAction: 'check'
            }

          case 'orphaned':
            return {
              kind: 'orphaned',
              message: copy.workflowMarketplaceOrphaned,
              canPrepareAgain: false
            }
        }
      }

      if (result?.type === 'update_review' && result.value.result === 'unchanged') {
        return {
          kind: 'current',
          message: copy.workflowMarketplacePackageCurrent(result.value.old_version),
          canPrepareAgain: false
        }
      }

      if (result && ['install_review', 'update_review', 'remove_review'].includes(result.type)) {
        return { kind: 'review', message: '', canPrepareAgain: false }
      }

      const verified = outcome.package_state
      const installed = verified && !verified.busy && verified.recovery === 'clear' ? verified.installed : null
      const prepared = decodeLifecycleOperation(preparation)

      const review =
        prepared?.state === 'succeeded' &&
        operation.kind.endsWith('_confirm') &&
        prepared.kind === operation.kind.replace(/_confirm$/, '_prepare') &&
        prepared.profile === operation.profile &&
        prepared.registry_epoch === operation.registry_epoch &&
        sameLifecycleValue(prepared.subject, operation.subject)
          ? prepared.result
          : null

      const previous =
        review?.type === 'update_review'
          ? review.value.old_version
          : review?.type === 'remove_review'
            ? review.value.current_version
            : null

      const version = installed
        ? installed.version === previous
          ? copy.workflowMarketplaceVersionRemainsInstalled(installed.version)
          : copy.workflowMarketplaceCurrentlyInstalled(installed.version)
        : ''

      return {
        kind: 'unchanged',
        message: `${outcome.evidence === 'rollback_verified' ? copy.workflowMarketplaceRolledBack : copy.workflowMarketplaceNoPackageChanges}${version ? ` ${version}` : ''}`,
        canPrepareAgain: true
      }
    }
  }
}
