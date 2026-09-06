import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const DESKTOP_ROOT = path.resolve(import.meta.dirname, '..')
const REPO_ROOT = path.resolve(DESKTOP_ROOT, '..', '..')

function prepareSource(hermesHome: string): void {
  const repository = path.join(path.dirname(hermesHome), 'marketplace-source')
  const python = path.join(REPO_ROOT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

  const generate = String.raw`
import sys
from pathlib import Path

from tests.plugins.workflow.test_marketplace_service import _write_index, _write_package

repository = Path(sys.argv[1])
repository.mkdir(parents=True)
_write_package(repository, "laptop-support", version="1.2.3")
_write_index(repository)
`

  execFileSync(python, ['-c', generate, repository], { cwd: REPO_ROOT })
  execFileSync('git', ['init', '--initial-branch=main'], { cwd: repository })
  execFileSync('git', ['config', 'user.email', 'marketplace-e2e@example.test'], { cwd: repository })
  execFileSync('git', ['config', 'user.name', 'Marketplace E2E'], { cwd: repository })
  execFileSync('git', ['add', '.'], { cwd: repository })
  execFileSync('git', ['commit', '-m', 'fixture'], { cwd: repository })

  const sourceUrl = 'https://fixtures.example/workflows.git'
  process.env.GIT_CONFIG_COUNT = '1'
  process.env.GIT_CONFIG_KEY_0 = `url.${pathToFileURL(fs.realpathSync(repository)).href}.insteadOf`
  process.env.GIT_CONFIG_VALUE_0 = sourceUrl

  const seed = String.raw`
import sys
from pathlib import Path

from plugins.workflow.marketplace.models import WorkflowMarketplaceSource
from plugins.workflow.marketplace.service import WorkflowMarketplaceService

service = WorkflowMarketplaceService(Path(sys.argv[1]), profile="default")
service.add_source(
    WorkflowMarketplaceSource.model_validate(
        {"name": "company", "repositoryUrl": sys.argv[2], "ref": "main", "enabled": True}
    )
)
result = service.refresh_source("company")
if result.state != "fresh":
    raise RuntimeError(result.message or "marketplace fixture refresh failed")
`

  execFileSync(python, ['-c', seed, fs.realpathSync(hermesHome), sourceUrl], { cwd: REPO_ROOT })
}

async function setContentSize(fixture: MockBackendFixture, width: number, height: number): Promise<void> {
  await fixture.app.evaluate(
    ({ BrowserWindow }, size) => {
      const window = BrowserWindow.getAllWindows()[0]

      if (!window) {
        throw new Error('Hermes window is unavailable')
      }

      window.unmaximize()
      window.setMinimumSize(1, 1)
      window.setContentSize(size.width, size.height, false)
    },
    { height, width }
  )
}

test.describe('Workflow Marketplace responsive browser behavior', () => {
  test.describe.configure({ mode: 'serial', timeout: 180_000 })

  let fixture: MockBackendFixture
  let priorTmpDir: string | undefined
  const priorGitEnvironment = new Map<string, string | undefined>()

  test.beforeAll(async () => {
    for (const name of ['GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0']) {
      priorGitEnvironment.set(name, process.env[name])
    }

    priorTmpDir = process.env.TMPDIR
    process.env.TMPDIR = fs.realpathSync(os.tmpdir())
    fixture = await setupMockBackend({
      prepareHermesHome: prepareSource
    })
    await waitForAppReady(fixture, 120_000)
  })

  test.afterAll(async () => {
    await fixture?.cleanup()

    if (priorTmpDir === undefined) {
      delete process.env.TMPDIR
    } else {
      process.env.TMPDIR = priorTmpDir
    }

    for (const [name, value] of priorGitEnvironment) {
      if (value === undefined) {
        delete process.env[name]
      } else {
        process.env[name] = value
      }
    }
  })

  test('keeps V2 package actions reachable at 320 CSS px, 200% zoom, RTL and reduced motion', async () => {
    const page = fixture.page
    await page.emulateMedia({ reducedMotion: 'reduce' })

    await page.getByRole('button', { name: 'Workflows', exact: true }).first().click()
    await expect(page.getByRole('heading', { name: 'Workflows', exact: true })).toBeVisible()
    await page.getByRole('tab', { name: 'Marketplace', exact: true }).click()

    const packageOption = page.getByRole('option', { name: /Laptop Support/ })
    await expect(packageOption).toBeVisible({ timeout: 60_000 })
    const refresh = page.getByRole('button', { name: 'Refresh', exact: true })
    await refresh.click()
    await expect(refresh).toHaveAttribute('aria-busy', 'true')
    await expect(refresh).toHaveAttribute('aria-busy', 'false', { timeout: 60_000 })
    await expect(packageOption).toBeVisible({ timeout: 60_000 })
    await packageOption.click()
    const detail = page.getByRole('region', { name: 'Laptop Support package details' })
    await expect(detail).toBeVisible({ timeout: 60_000 })

    await page.evaluate(() => {
      const desktop = window as unknown as {
        hermesDesktop: { zoom: { setPercent: (percent: number) => void } }
      }

      desktop.hermesDesktop.zoom.setPercent(200)
      document.documentElement.dir = 'rtl'
    })
    await setContentSize(fixture, 640, 900)
    await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(320)

    expect(
      await page.evaluate(() => ({
        direction: getComputedStyle(document.documentElement).direction,
        noHorizontalOverflow:
          document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1 &&
          document.body.scrollWidth <= window.innerWidth + 1,
        reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches
      }))
    ).toEqual({ direction: 'rtl', noHorizontalOverflow: true, reducedMotion: true })

    const back = page.getByRole('button', { name: 'Back to packages', exact: true })
    const install = page.getByRole('button', { name: 'Install package', exact: true })
    const repository = detail.getByRole('link').first()
    const refreshState = page.getByRole('button', { name: 'Refresh state', exact: true })
    const retry = page.getByRole('button', { name: 'Retry', exact: true })
    const detailFocusOrder = [back, repository, install, refreshState, retry]

    for (
      let attempt = 0;
      attempt < 40 && !(await back.evaluate(element => element === document.activeElement));
      attempt++
    ) {
      await page.keyboard.press('Tab')
    }

    for (const [index, action] of detailFocusOrder.entries()) {
      if (index > 0) {
        await page.keyboard.press('Tab')
      }

      await expect(action).toBeFocused()
      await expect(action).toBeInViewport()
    }

    await page.keyboard.press('Shift+Tab')
    await expect(refreshState).toBeFocused()
    await page.keyboard.press('Shift+Tab')
    await expect(install).toBeFocused()
    await page.keyboard.press('Enter')

    const review = page.locator('[data-marketplace-lifecycle-dialog][data-state="open"]')
    await expect(review).toBeVisible({ timeout: 60_000 })
    expect(
      await review.evaluate(element => {
        const style = getComputedStyle(element)
        const bounds = element.getBoundingClientRect()
        const zeroDuration = (value: string) => value.split(',').every(part => Number.parseFloat(part) === 0)

        return {
          animationDisabled: style.animationName === 'none' || zeroDuration(style.animationDuration),
          contained:
            bounds.left >= -1 && bounds.right <= window.innerWidth + 1 && bounds.width <= window.innerWidth + 1,
          noHorizontalOverflow:
            document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1 &&
            document.body.scrollWidth <= window.innerWidth + 1,
          transitionDisabled: style.transitionProperty === 'none' || zeroDuration(style.transitionDuration)
        }
      })
    ).toEqual({
      animationDisabled: true,
      contained: true,
      noHorizontalOverflow: true,
      transitionDisabled: true
    })
    const close = review.locator('button[data-slot="button"]').filter({ hasText: 'Close' })
    const retryPreparation = review.getByRole('button', { name: 'Prepare again', exact: true })
    const iconClose = review.locator('button[data-slot="dialog-close-button"]')
    await expect(retryPreparation).toBeVisible({ timeout: 60_000 })
    await expect(close).toBeFocused()

    for (const action of [retryPreparation, iconClose, close]) {
      await page.keyboard.press('Tab')
      await expect(action).toBeFocused()
      await expect(action).toBeInViewport()
    }

    await page.keyboard.press('Shift+Tab')
    await expect(iconClose).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(review).toBeHidden()
    await expect(detail).toBeVisible()
    await expect(back).toBeVisible()
    await expect(install).toBeFocused()
  })
})
