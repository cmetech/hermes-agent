import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, expect, test, vi } from 'vitest'

import { ConfirmDialog } from '@/components/ui/confirm-dialog'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      common: { cancel: 'Cancel', confirm: 'Confirm', delete: 'Delete', done: 'Done', loading: 'Working' },
      errors: { genericFailure: 'Something failed' }
    }
  })
}))

// ConfirmDialog schedules window.setTimeout(onClose, 600) after a successful
// confirm. The timer had no cleanup, so an unmount inside that window left it
// pending. In CI it came due after the environment was gone. The setState
// path of React then touched `window`:
//
//   ReferenceError: window is not defined
//    at resolveUpdatePriority (react-dom-client.development.js:1308)
//    at dispatchSetState
//    at Timeout.t4 [as _onTimeout] session-actions-menu.tsx:574
//
// The frame at session-actions-menu.tsx:574 is the `onClose` prop of
// DeleteSessionDialog. The owner of the timer is this component.
//
// This test confirms, unmounts inside the 600ms window, and then lets the
// timer come due on the dead tree.
test('the close timer does not fire after unmount', async () => {
  vi.useFakeTimers()
  const onClose = vi.fn()
  const onConfirm = vi.fn()

  render(<ConfirmDialog confirmLabel="Delete" onClose={onClose} onConfirm={onConfirm} open title="Delete session" />)

  fireEvent.click(screen.getByRole('button', { name: 'Delete' }))

  // Not waitFor: it polls on real timers, and the fake timers of this test
  // never let it advance. onConfirm runs synchronously inside the click, and
  // one microtask turn is enough for the await in run() to settle and reach
  // the setTimeout.
  await Promise.resolve()
  await Promise.resolve()
  expect(onConfirm).toHaveBeenCalled()

  // Unmount while the close timer is still pending.
  cleanup()

  // Let the timer come due on the unmounted tree.
  vi.advanceTimersByTime(1000)

  expect(onClose).not.toHaveBeenCalled()
})

function deferred() {
  let resolve!: () => void

  const promise = new Promise<void>(resolvePromise => {
    resolve = resolvePromise
  })

  return { promise, resolve }
}

test('a deferred confirmation resolving after unmount cannot arm a close timer or invoke onClose', async () => {
  vi.useFakeTimers()
  const confirmation = deferred()
  const onClose = vi.fn()

  const setTimeoutSpy = vi.spyOn(window, 'setTimeout')

  const view = render(
    <ConfirmDialog
      confirmLabel="Delete"
      onClose={onClose}
      onConfirm={() => confirmation.promise}
      open
      title="Delete session"
    />
  )

  fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
  expect((screen.getByRole('button', { name: 'Working' }) as HTMLButtonElement).disabled).toBe(true)
  view.unmount()

  await act(async () => confirmation.resolve())
  vi.advanceTimersByTime(1000)

  expect(setTimeoutSpy).not.toHaveBeenCalledWith(expect.any(Function), 600)
  expect(onClose).not.toHaveBeenCalled()
})

test('dismiss-on-confirm does not invoke a stale onClose when its deferred work resolves after unmount', async () => {
  const confirmation = deferred()
  const onClose = vi.fn()

  const view = render(
    <ConfirmDialog
      confirmLabel="Remove"
      dismissOnConfirm
      onClose={onClose}
      onConfirm={() => confirmation.promise}
      open
      title="Remove session"
    />
  )

  fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
  view.unmount()

  await act(async () => confirmation.resolve())

  expect(onClose).not.toHaveBeenCalled()
})

test('the lifecycle guard remains active after StrictMode effect replay', async () => {
  vi.useFakeTimers()
  const confirmation = deferred()
  const onClose = vi.fn()
  render(
    <StrictMode>
      <ConfirmDialog
        confirmLabel="Delete"
        onClose={onClose}
        onConfirm={() => confirmation.promise}
        open
        title="Delete session"
      />
    </StrictMode>
  )

  fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
  await act(async () => confirmation.resolve())

  expect(screen.getByRole('button', { name: 'Done' })).toBeTruthy()
  vi.advanceTimersByTime(600)

  expect(onClose).toHaveBeenCalledTimes(1)
})
