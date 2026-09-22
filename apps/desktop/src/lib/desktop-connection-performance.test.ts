import { afterEach, describe, expect, it } from 'vitest'

import {
  _resetDesktopConnectionPerformanceForTests,
  beginDesktopConnectionMeasure
} from './desktop-connection-performance'

describe('desktop connection performance marks', () => {
  afterEach(() => {
    _resetDesktopConnectionPerformanceForTests()
  })

  it('records identifier-free start, terminal, and duration entries once', () => {
    const calls: string[] = []

    const performance = {
      clearMarks: (name: string) => calls.push(`clear:${name}`),
      mark: (name: string) => calls.push(`mark:${name}`),
      measure: (name: string, start: string, end: string) => calls.push(`measure:${name}:${start}:${end}`)
    }

    const finish = beginDesktopConnectionMeasure('source.activation', performance)

    finish('ready')
    finish('failed')

    expect(calls).toEqual([
      'mark:hermes.desktop.source.activation.1.start',
      'mark:hermes.desktop.source.activation.1.ready',
      'measure:hermes.desktop.source.activation.ready:hermes.desktop.source.activation.1.start:hermes.desktop.source.activation.1.ready',
      'clear:hermes.desktop.source.activation.1.start',
      'clear:hermes.desktop.source.activation.1.ready'
    ])
    expect(calls.join(' ')).not.toMatch(/profile|connectionId|https?:|token|error=/)
  })

  it('degrades to a no-op when the performance timeline is unavailable', () => {
    expect(() => beginDesktopConnectionMeasure('connection.initial', null)('failed')).not.toThrow()
  })
})
