/**
 * Containment for the statusbar: one throwing item used to unmount the whole
 * footer silently (seen in the field on a Windows Server install — every
 * status icon AND the version pill vanished with no visible error). The
 * boundary logs the real crash to the desktop log (renderer console lines are
 * forwarded there) and paints a minimal bar that keeps the app version — the
 * one thing the statusbar promises the user can never lose — on screen.
 */
import { useStore } from '@nanostores/react'
import { Component, type ReactNode } from 'react'

import { $desktopVersion } from '@/store/updates'

export class StatusbarBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: unknown, info: unknown) {
    // Forwarded into desktop.log as a "[renderer console]" line — this is the
    // breadcrumb that names the item that killed the bar.
    console.error('[statusbar] crashed — rendering minimal fallback bar:', error, info)
  }

  render() {
    return this.state.failed ? <StatusbarFallbackBar /> : this.props.children
  }
}

function StatusbarFallbackBar() {
  const version = useStore($desktopVersion)

  return (
    <footer
      className="flex h-5 shrink-0 items-center justify-end gap-2 bg-(--ui-sidebar-surface-background) px-2 text-(--ui-text-tertiary) [-webkit-app-region:no-drag]"
      data-slot="statusbar-fallback"
      title="The statusbar failed to render — see the desktop log. Showing core status only."
    >
      {version?.appVersion ? <span className="text-[0.7rem]">v{version.appVersion}</span> : null}
    </footer>
  )
}
