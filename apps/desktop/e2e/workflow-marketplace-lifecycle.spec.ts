import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { marketplaceFixturePython } from './marketplace-fixture-paths'
import { allowErrorBanners, expect, test } from './test'

const ROOT = path.resolve(import.meta.dirname, '../../..')

test.describe('Workflow Marketplace real backend lifecycle', () => {
  test.describe.configure({ mode: 'serial', timeout: 180_000 })
  let fixture: MockBackendFixture
  const previous = new Map<string, string | undefined>()

  test.beforeAll(async () => {
    for (const key of ['TMPDIR', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0']) {
      previous.set(key, process.env[key])
    }
    process.env.TMPDIR = fs.realpathSync(os.tmpdir())
    fixture = await setupMockBackend({
      prepareHermesHome(home) {
        const repository = path.join(path.dirname(home), 'marketplace-publisher')
        const python = marketplaceFixturePython(ROOT)
        execFileSync(
          python,
          [
            '-c',
            String.raw`
import sys
from pathlib import Path
from tests.plugins.workflow.test_marketplace_service import _write_package, _write_index
repository = Path(sys.argv[1])
repository.mkdir()
_write_package(repository, "laptop-support", version="1.0.0")
_write_index(repository)
`,
            repository
          ],
          { cwd: ROOT }
        )
        for (const args of [
          ['init', '--initial-branch=main'],
          ['config', 'user.email', 'marketplace@example.test'],
          ['config', 'user.name', 'Marketplace E2E'],
          ['add', '.'],
          ['commit', '-m', 'publish fixture']
        ])
          execFileSync('git', args, { cwd: repository })
        const source = 'https://fixtures.example/workflows.git'
        process.env.GIT_CONFIG_COUNT = '1'
        process.env.GIT_CONFIG_KEY_0 = `url.${pathToFileURL(fs.realpathSync(repository)).href}.insteadOf`
        process.env.GIT_CONFIG_VALUE_0 = source
        execFileSync(
          python,
          [
            '-c',
            String.raw`
import sys
from pathlib import Path
from plugins.workflow.marketplace.models import WorkflowMarketplaceSource
from plugins.workflow.marketplace.service import WorkflowMarketplaceService
service = WorkflowMarketplaceService(Path(sys.argv[1]), profile="default")
service.add_source(WorkflowMarketplaceSource(name="company", repositoryUrl=sys.argv[2], ref="main"))
assert service.refresh_source("company").state == "fresh"
`,
            fs.realpathSync(home),
            source
          ],
          { cwd: ROOT }
        )
      }
    })
    await waitForAppReady(fixture, 120_000)
    await fixture.app.evaluate(({ ipcMain }) => {
      const handlers = (
        ipcMain as unknown as { _invokeHandlers: Map<string, (...args: unknown[]) => Promise<unknown>> }
      )._invokeHandlers
      const original = handlers.get('hermes:api:structured')!
      const observed: unknown[] = []
      ;(globalThis as unknown as { marketplaceEvidence: unknown[] }).marketplaceEvidence = observed
      ipcMain.removeHandler('hermes:api:structured')
      ipcMain.handle('hermes:api:structured', async (event, request) => {
        const response = (await original(event, request)) as {
          ok: boolean
          status?: number
          body?: { detail?: { code?: string } }
          value?: { id?: string; kind?: string; state?: string; error?: { code?: string } }
        }
        if (request.path.includes('/lifecycle/v2/'))
          observed.push({
            path: request.path,
            requestId: request.body?.request_id,
            id: response.value?.id,
            kind: response.value?.kind,
            ok: response.ok,
            status: response.status,
            state: response.value?.state,
            code: response.value?.error?.code ?? response.body?.detail?.code
          })
        const fault = (
          globalThis as unknown as {
            lifecycleFault?: {
              dropConfirm: boolean
              failState: boolean
              lookup: Promise<void>
              failedReads: number
              requestId?: string
              operationId?: string
            }
          }
        ).lifecycleFault
        if (fault && request.path.includes('/lifecycle/v2/')) {
          if (fault.dropConfirm && request.path.endsWith('/install/confirm') && response.ok) {
            fault.dropConfirm = false
            fault.requestId = request.body.request_id
            fault.operationId = response.value?.id
            throw new Error('deliberately lost confirmed admission response')
          }
          if (request.path.includes('/admissions/') || request.path.endsWith('/install/confirm')) await fault.lookup
          if (fault.failState && request.path.endsWith('/state')) {
            fault.failedReads += 1
            return { ok: false, status: 503, body: { detail: { code: 'marketplace_internal_error' } } }
          }
        }
        const hold = (globalThis as unknown as { inspectionHold?: { id: string | null; promise: Promise<void> } })
          .inspectionHold
        if (hold && !hold.id && response.value?.kind === 'inspect' && response.value.id) {
          hold.id = response.value.id
          await hold.promise
        }
        return response
      })
    })
  })

  test.afterAll(async () => {
    await fixture?.cleanup()
    for (const [key, value] of previous) {
      if (value === undefined) delete process.env[key]
      else process.env[key] = value
    }
  })

  test.afterEach(async ({}, testInfo) => {
    await fixture.app.evaluate(() => {
      const controls = globalThis as unknown as {
        inspectionHold?: { release: () => void }
        lifecycleFault?: { release: () => void }
      }
      controls.inspectionHold?.release()
      controls.lifecycleFault?.release()
    })
    const evidence = await fixture.app.evaluate(
      () => (globalThis as unknown as { marketplaceEvidence: unknown[] }).marketplaceEvidence
    )
    await testInfo.attach('marketplace-safe-evidence', {
      body: JSON.stringify(evidence, null, 2),
      contentType: 'application/json'
    })
  })

  test('installs verified bytes only after explicit confirmation', async () => {
    const page = fixture.page
    await page.getByRole('button', { name: 'Workflows', exact: true }).first().click()
    await page.getByRole('tab', { name: 'Marketplace', exact: true }).click()
    await page.getByRole('option', { name: /Laptop Support/ }).click({ timeout: 60_000 })
    await page.getByRole('button', { name: 'Install package', exact: true }).click({ timeout: 60_000 })
    const dialog = page.locator('[data-marketplace-lifecycle-dialog][data-state="open"]')
    await expect
      .poll(
        () =>
          fixture.app.evaluate(() =>
            (
              globalThis as unknown as { marketplaceEvidence: Array<{ state: string; code?: string }> }
            ).marketplaceEvidence.filter(item => item.state === 'failed' || item.state === 'succeeded')
          ),
        { timeout: 10_000 }
      )
      .toContainEqual(expect.objectContaining({ state: 'succeeded' }))
    await expect(dialog.getByRole('button', { name: 'Confirm install', exact: true })).toBeVisible({ timeout: 30_000 })
    const destination = path.join(fixture.sandbox.hermesHome, 'workflows/marketplace/company/laptop-support')
    expect(fs.existsSync(destination)).toBe(false)
    await dialog.getByRole('button', { name: 'Confirm install', exact: true }).click()
    await expect(dialog.getByText('Installed — trust required to run')).toBeVisible()
    expect(JSON.parse(fs.readFileSync(path.join(destination, 'workflow-package.json'), 'utf8')).version).toBe('1.0.0')
    await expect(dialog.getByRole('button', { name: 'Review trust', exact: true })).toBeEnabled()
    await dialog.getByRole('button', { name: 'Review trust', exact: true }).click()
    await expect(dialog.getByRole('button', { name: 'Grant trust', exact: true })).toBeEnabled()
    // Hold an actual exact inspection response at the native boundary. Backend
    // overlap itself is event-controlled in the temporary-Git API test.
    await fixture.app.evaluate(() => {
      let release!: () => void
      const promise = new Promise<void>(resolve => {
        release = resolve
      })
      ;(
        globalThis as unknown as { inspectionHold: { id: string | null; promise: Promise<void>; release: () => void } }
      ).inspectionHold = { id: null, promise, release }
    })
    await dialog.getByRole('button', { name: 'Grant trust', exact: true }).click()
    await expect(dialog.getByText('Trust granted for all reviewed workflows.')).toBeVisible()
    await dialog.getByRole('button', { name: 'Close', exact: true }).first().click()
    await page.getByRole('tab', { name: 'Installed', exact: true }).click()
    const installed = page.getByRole('article', { name: 'company/laptop-support installed package' })
    await expect
      .poll(() =>
        fixture.app.evaluate(
          () => (globalThis as unknown as { inspectionHold: { id: string | null } }).inspectionHold.id
        )
      )
      .toEqual(expect.any(String))
    await expect(installed.getByRole('button', { name: 'Remove package', exact: true })).toBeEnabled()
    await installed.getByRole('button', { name: 'Remove package', exact: true }).click()
    await expect
      .poll(
        () =>
          fixture.app.evaluate(() =>
            (
              globalThis as unknown as { marketplaceEvidence: Array<{ kind: string; state: string; code?: string }> }
            ).marketplaceEvidence.filter(
              item => item.kind === 'remove_prepare' && (item.state === 'succeeded' || item.state === 'failed')
            )
          ),
        { timeout: 15_000 }
      )
      .toContainEqual(expect.objectContaining({ state: 'succeeded' }))
    await expect(dialog.getByRole('button', { name: 'Remove package', exact: true })).toBeEnabled()
    await dialog.getByRole('button', { name: 'Remove package', exact: true }).click()
    await expect
      .poll(() =>
        fixture.app.evaluate(() => {
          const evidence = (globalThis as unknown as { marketplaceEvidence: Array<{ path: string; id?: string }> })
            .marketplaceEvidence
          return evidence.find(item => item.path.endsWith('/remove/confirm'))?.id
        })
      )
      .toEqual(expect.any(String))
    // Exactly one explicit confirmation was sent; correlate terminal history by
    // its returned operation identity, never by a latest-kind heuristic.
    const id = await fixture.app.evaluate(() => {
      const matches = (
        globalThis as unknown as { marketplaceEvidence: Array<{ path: string; id?: string }> }
      ).marketplaceEvidence.filter(item => item.path.endsWith('/remove/confirm'))
      if (matches.length !== 1) throw new Error('Expected one removal admission')
      return matches[0].id!
    })
    await expect
      .poll(() =>
        fixture.app.evaluate(
          (_electron, id) =>
            (
              globalThis as unknown as { marketplaceEvidence: Array<{ id?: string; state?: string }> }
            ).marketplaceEvidence.some(item => item.id === id && item.state === 'succeeded'),
          id
        )
      )
      .toBe(true)
    await expect(installed).toHaveCount(0)
    expect(fs.existsSync(destination)).toBe(false)
    await fixture.app.evaluate(() => {
      ;(globalThis as unknown as { inspectionHold: { release: () => void } }).inspectionHold.release()
    })
    await expect(installed).toHaveCount(0)
  })

  test('recovers a lost confirmation across close and tab return without bypassing failed state reads', async () => {
    allowErrorBanners()
    const page = fixture.page
    await page.getByRole('tab', { name: 'Marketplace', exact: true }).click()
    await page.getByRole('option', { name: /Laptop Support/ }).click()
    await page.getByRole('button', { name: 'Install package', exact: true }).click()
    const dialog = page.locator('[data-marketplace-lifecycle-dialog][data-state="open"]')
    await expect(dialog.getByRole('button', { name: 'Confirm install', exact: true })).toBeEnabled()
    await fixture.app.evaluate(() => {
      let release!: () => void
      const lookup = new Promise<void>(resolve => {
        release = resolve
      })
      ;(globalThis as unknown as { lifecycleFault: unknown }).lifecycleFault = {
        dropConfirm: true,
        failState: true,
        lookup,
        release,
        failedReads: 0
      }
    })
    await dialog.getByRole('button', { name: 'Confirm install', exact: true }).click()
    await expect(dialog.getByText(/State could not be confirmed/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Retry status', exact: true }).click()
    await dialog.getByRole('button', { name: 'Close', exact: true }).first().click()
    await expect(dialog).toHaveCount(0)
    await expect
      .poll(() => page.evaluate(() => document.activeElement !== document.body && document.activeElement !== null))
      .toBe(true)
    await page.getByRole('tab', { name: 'Installed', exact: true }).click()
    await page.getByRole('tab', { name: 'Marketplace', exact: true }).click()
    await fixture.app.evaluate(() => {
      ;(globalThis as unknown as { lifecycleFault: { release: () => void } }).lifecycleFault.release()
    })
    const destination = path.join(fixture.sandbox.hermesHome, 'workflows/marketplace/company/laptop-support')
    await expect.poll(() => fs.existsSync(path.join(destination, 'workflow-package.json'))).toBe(true)
    await expect
      .poll(() =>
        fixture.app.evaluate(
          () => (globalThis as unknown as { lifecycleFault: { failedReads: number } }).lifecycleFault.failedReads
        )
      )
      .toBeGreaterThan(0)
    await page.getByRole('option', { name: /Laptop Support/ }).click()
    await expect(page.getByText(/Last observed/).first()).toBeVisible()
    await expect(
      page
        .locator('button:enabled')
        .filter({ hasText: /^(Install package|Update package|Review trust|Remove package)$/ })
    ).toHaveCount(0)
    const evidence = await fixture.app.evaluate(() => {
      const state = globalThis as unknown as {
        marketplaceEvidence: Array<{ path: string; id?: string; requestId?: string }>
        lifecycleFault: { requestId: string; operationId: string }
      }
      return {
        requestId: state.lifecycleFault.requestId,
        operationId: state.lifecycleFault.operationId,
        admissions: state.marketplaceEvidence
          .filter(item => item.path.endsWith('/install/confirm') && item.requestId === state.lifecycleFault.requestId)
          .map(item => item.id)
      }
    })
    // Retry status replays the exact original request. Both responses identify
    // one backend admission; the retry is not a new confirmation intent.
    expect(evidence.admissions).toEqual([evidence.operationId, evidence.operationId])
    expect(evidence.requestId).toEqual(expect.any(String))
    await fixture.app.evaluate(() => {
      ;(globalThis as unknown as { lifecycleFault: { failState: boolean } }).lifecycleFault.failState = false
    })
    await page.getByRole('button', { name: 'Refresh state', exact: true }).click()
    await page.getByRole('tab', { name: 'Installed', exact: true }).click()
    const installed = page.getByRole('article', { name: 'company/laptop-support installed package' })
    await expect(installed.getByRole('button', { name: 'Review trust', exact: true })).toBeEnabled()
    expect(JSON.parse(fs.readFileSync(path.join(destination, 'workflow-package.json'), 'utf8')).version).toBe('1.0.0')
    const diagnostic = page
      .getByRole('row')
      .filter({ has: page.getByRole('cell', { name: 'diagnostic', exact: true }) })
    await expect(diagnostic.getByRole('cell', { name: 'untrusted', exact: true })).toBeVisible()
    await expect(diagnostic.getByRole('button', { name: 'Run', exact: true })).toBeDisabled()
  })
})
