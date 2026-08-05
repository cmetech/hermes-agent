import { useQuery } from '@tanstack/react-query'

import { getApiRequestProfile, getStatus } from '@/hermes'
import { useI18n } from '@/i18n'

/**
 * Say when the board is inert.
 *
 * The dispatcher that sweeps Triage, promotes todo -> ready and spawns workers
 * lives only inside the gateway process. With no gateway running, every card
 * sits exactly where it was created and the board looks identical to a healthy
 * idle one -- the failure this banner exists to make visible.
 *
 * Renders only on an explicit `false`: while the first request is in flight
 * `gateway_running` is undefined, and flashing a warning on every mount would
 * teach users to ignore it.
 */
export function DispatcherBanner() {
  const { t } = useI18n()
  const profile = getApiRequestProfile() ?? 'default'

  const status = useQuery({
    queryFn: () => getStatus(),
    queryKey: ['dispatcher-presence', profile],
    refetchInterval: () => (document.visibilityState === 'visible' ? 60_000 : false)
  })

  if (status.data?.gateway_running !== false) {
    return null
  }

  return (
    <div
      className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400"
      role="status"
    >
      {t.operations.dispatcherOffline}
    </div>
  )
}
