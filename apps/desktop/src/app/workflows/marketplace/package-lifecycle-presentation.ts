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
  message: 'State could not be confirmed. The operation may have completed.',
  canPrepareAgain: false
}

/** Terminal history is independent of later current-state reconciliation and cached cards. */
export function packageLifecyclePresentation(
  record: SupervisedRecord,
  preparation?: LifecycleOperation
): PackageLifecyclePresentation {
  if (record.status !== 'terminal' || !record.operationId) {
    return unconfirmedPackagePresentation
  }

  let operation: LifecycleOperation

  try {
    operation = acceptSupervisedOperation(record.operation, record.binding, record)
  } catch {
    return unconfirmedPackagePresentation
  }

  if (!operation.outcome || operation.kind.startsWith('trust_')) {
    return unconfirmedPackagePresentation
  }

  const outcome = operation.outcome

  switch (outcome.type) {
    case 'outcome_unknown':
      return unconfirmedPackagePresentation

    case 'recovery_required':
      return {
        ...unconfirmedPackagePresentation,
        message: 'State could not be confirmed. Package recovery is required.'
      }

    case 'cancelled_before_commit':
      return { kind: 'cancelled', message: 'Cancelled before changes were committed.', canPrepareAgain: true }
    case 'committed': {
      const state = outcome.package_state

      if (!state || state.busy || state.recovery !== 'clear') {
        return unconfirmedPackagePresentation
      }

      if (operation.kind === 'remove_confirm' && state.state === 'absent') {
        return { kind: 'success', message: 'Package removal completed.', canPrepareAgain: false }
      }

      if ((operation.kind === 'install_confirm' || operation.kind === 'update_confirm') && state.installed) {
        return {
          kind: 'success',
          message: `${operation.kind === 'install_confirm' ? 'Installed version' : 'Updated to version'} ${state.installed.version}. Installation does not grant trust.${state.trust?.workflows.some(item => item.state === 'untrusted') ? ' Trust required to run untrusted workflows.' : ''}`,
          canPrepareAgain: false
        }
      }

      return unconfirmedPackagePresentation
    }

    case 'known_unchanged': {
      const result = operation.state === 'succeeded' ? operation.result : null

      if (result?.type === 'update_checks' && result.value.checks.length === 1) {
        const check = result.value.checks[0]

        switch (check.status) {
          case 'current':
            return {
              kind: 'current',
              message: `Version ${check.installed_version} is current. No update was installed.`,
              canPrepareAgain: false
            }

          case 'update_available':
            return { kind: 'update_available', message: 'An update is available.', canPrepareAgain: true }

          case 'error':
            return {
              kind: 'check_error',
              message: 'Could not check for updates.',
              canPrepareAgain: false,
              retryAction: 'check'
            }

          case 'orphaned':
            return {
              kind: 'orphaned',
              message: "The installed package's source is unavailable.",
              canPrepareAgain: false
            }
        }
      }

      if (result?.type === 'update_review' && result.value.result === 'unchanged') {
        return {
          kind: 'current',
          message: `Version ${result.value.old_version} is current. No update was installed.`,
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
          ? `Version ${installed.version} remains installed.`
          : `Currently installed: ${installed.version}.`
        : ''

      return {
        kind: 'unchanged',
        message: `${outcome.evidence === 'rollback_verified' ? 'Changes were rolled back.' : 'This attempt made no package changes.'}${version ? ` ${version}` : ''}`,
        canPrepareAgain: true
      }
    }
  }
}
