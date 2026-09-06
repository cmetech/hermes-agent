import { type RefObject, useLayoutEffect, useRef } from 'react'

export const LIFECYCLE_DIALOG_ATTRIBUTE = 'data-marketplace-lifecycle-dialog'

function usable(element: HTMLElement | null): element is HTMLElement {
  return Boolean(element?.isConnected && !(element instanceof HTMLButtonElement && element.disabled))
}

/** A lifecycle portal owns Escape before React can bubble it to the Marketplace route. */
export function claimLifecycleEscape(
  event: { preventDefault: () => void; stopPropagation: () => void },
  dismissible: boolean,
  close: () => void
) {
  event.preventDefault()
  event.stopPropagation()

  if (dismissible) {
    close()
  }
}

export function marketplaceEscapeIsOwned(event: { defaultPrevented: boolean; target: EventTarget | null }) {
  if (event.defaultPrevented) {
    return true
  }

  const target = event.target

  return target instanceof Element && target.closest(`[${LIFECYCLE_DIALOG_ATTRIBUTE}][data-state="open"]`) !== null
}

/** Refocus only when a dialog-owned focused node became unsafe; external navigation keeps ownership. */
export function useLifecycleDialogFocus(
  open: boolean,
  transition: unknown,
  primaryRef: RefObject<HTMLButtonElement | null>,
  headingRef: RefObject<HTMLElement | null>
) {
  const lastDialogFocus = useRef<HTMLElement | null>(null)

  useLayoutEffect(() => {
    if (!open) {
      lastDialogFocus.current = null

      return
    }

    const previous = lastDialogFocus.current

    if (!previous || usable(previous)) {
      return
    }

    const active = document.activeElement

    if (active && active !== document.body && active.isConnected && active !== previous) {
      return
    }

    const target = usable(primaryRef.current) ? primaryRef.current : headingRef.current

    if (usable(target)) {
      target.focus()
      lastDialogFocus.current = target
    }
  }, [headingRef, open, primaryRef, transition])

  return (event: { target: EventTarget | null }) => {
    if (event.target instanceof HTMLElement) {
      lastDialogFocus.current = event.target
    }
  }
}
