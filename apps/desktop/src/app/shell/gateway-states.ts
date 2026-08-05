/**
 * Three things are called "gateway"; this module owns the third one's label.
 *
 * The footer chip already reports the desktop<->backend websocket and the
 * inference gateway. The messaging gateway -- the process that hosts kanban
 * dispatch and cron -- is the one whose absence leaves a board looking healthy
 * while nothing runs, and it is reported from `statusSnapshot.gateway_running`.
 *
 * Its own module on purpose: `gateway-menu-panel.tsx` and
 * `hooks/use-statusbar-items.tsx` import each other, so a helper living in the
 * hook file was reachable from the panel only because `export function` hoists
 * across the cycle. Rewritten as a `const` arrow it would have become a TDZ
 * ReferenceError that blanks the statusbar at runtime with no type error.
 */

export interface GatewayAutomationCopy {
  automationRunning: string
  automationStopped: string
  automationUnknown: string
}

/** Label for the messaging gateway. `undefined` means no status response has
 *  arrived yet, which must not read as "stopped". */
export function gatewayAutomationLabel(
  gatewayRunning: boolean | undefined,
  copy: GatewayAutomationCopy
): string {
  if (gatewayRunning === undefined) {
    return copy.automationUnknown
  }

  return gatewayRunning ? copy.automationRunning : copy.automationStopped
}
