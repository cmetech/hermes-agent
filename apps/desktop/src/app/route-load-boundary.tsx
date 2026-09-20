import { type ReactNode, Suspense, useEffect } from 'react'

import { PageLoader } from '@/components/page-loader'
import { useI18n } from '@/i18n'
import { noteDesktopRouteSettled, noteDesktopRouteVisible } from '@/lib/desktop-performance'

export type RouteLoadBoundaryVariant = 'overlay' | 'tile' | 'workspace'

export interface RouteLoadBoundaryProps {
  children: ReactNode
  onSettled?: (route: string) => void
  onVisible?: (route: string) => void
  route: string
  variant: RouteLoadBoundaryVariant
}

const VARIANT_CLASS: Record<RouteLoadBoundaryVariant, string> = {
  overlay: 'route-load-overlay fixed inset-0 z-50 min-h-64 w-full bg-black/22 backdrop-blur-[0.125rem]',
  tile: 'route-load-tile min-h-0 h-full',
  workspace: 'route-load-workspace min-h-0 flex-1'
}

function RouteLoading({ onVisible, route, variant }: Omit<RouteLoadBoundaryProps, 'children' | 'onSettled'>) {
  const { t } = useI18n()

  useEffect(() => {
    ;(onVisible ?? noteDesktopRouteVisible)(route)
  }, [onVisible, route])

  return <PageLoader className={VARIANT_CLASS[variant]} label={t.common.loading} />
}

function RouteSettled({
  onSettled,
  onVisible,
  route
}: Pick<RouteLoadBoundaryProps, 'onSettled' | 'onVisible' | 'route'>) {
  useEffect(() => {
    ;(onVisible ?? noteDesktopRouteVisible)(route)
    ;(onSettled ?? noteDesktopRouteSettled)(route)
  }, [onSettled, onVisible, route])

  return null
}

export function RouteLoadBoundary({ children, onSettled, onVisible, route, variant }: RouteLoadBoundaryProps) {
  return (
    <Suspense fallback={<RouteLoading onVisible={onVisible} route={route} variant={variant} />}>
      <RouteSettled onSettled={onSettled} onVisible={onVisible} route={route} />
      {children}
    </Suspense>
  )
}
