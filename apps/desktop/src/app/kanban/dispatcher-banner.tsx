import { useQuery } from '@tanstack/react-query'

import { getApiRequestProfile, getStatus } from '@/hermes'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { PAGE_INSET_X } from '../layout-constants'

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
 *
 * Cadence matches the board below it (20s, paused when the tab is hidden) plus
 * a refetch on window focus. On a cold launch the desktop backend autostarts
 * the gateway, so the very first response can legitimately say `false`; on a
 * 60s cycle the banner then claimed nothing would be picked up for a full
 * minute after the gateway was already live. Crying wolf costs exactly the
 * signal this exists to provide.
 */
export function DispatcherBanner() {
  const { t } = useI18n()
  const profile = getApiRequestProfile() ?? 'default'

  const status = useQuery({
    queryFn: () => getStatus(),
    queryKey: ['dispatcher-presence', profile],
    refetchInterval: () => (document.visibilityState === 'visible' ? 20_000 : false),
    refetchOnWindowFocus: true
  })

  if (status.data?.gateway_running !== false) {
    return null
  }

  return (
    // The board content below is inset with PAGE_INSET_X; without the same
    // inset the banner sat flush against the pane edge.
    <div className={cn('pt-6', PAGE_INSET_X)}>
      <div
        className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400"
        role="status"
      >
        {t.operations.dispatcherOffline}
      </div>
    </div>
  )
}
