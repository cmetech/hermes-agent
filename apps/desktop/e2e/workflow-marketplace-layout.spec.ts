import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import type { Locator } from '@playwright/test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const DESKTOP_ROOT = path.resolve(import.meta.dirname, '..')
const REPO_ROOT = path.resolve(DESKTOP_ROOT, '..', '..')
const LONG_PACKAGE_DISPLAY_NAME = 'OperationalAutomationPackageIdentifier'.repeat(3)
const LONG_PACKAGE_PUBLISHER = 'CorporateAutomationEngineeringDivision'.repeat(3)
const EXPECTED_DIAGNOSTIC_DISCLOSURES = 6

function prepareSource(hermesHome: string): void {
  const repository = path.join(path.dirname(hermesHome), 'marketplace-source')
  const python = path.join(REPO_ROOT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

  const generate = String.raw`
import json
import sys
from pathlib import Path

from tests.plugins.workflow.test_marketplace_service import _publish, _write_index, _write_package

repository = Path(sys.argv[1])
repository.mkdir(parents=True)
_write_package(repository, "laptop-support", version="1.2.3")
package_root = repository / "packages" / "laptop-support"
manifest_path = package_root / "workflow-package.json"
manifest = json.loads(manifest_path.read_bytes())
manifest["displayName"] = ${JSON.stringify(LONG_PACKAGE_DISPLAY_NAME)}
manifest["publisher"] = ${JSON.stringify(LONG_PACKAGE_PUBLISHER)}
manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
_publish(package_root)
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

async function setZoomPercent(fixture: MockBackendFixture, percent: 100 | 200): Promise<void> {
  await fixture.page.evaluate(value => {
    const desktop = window as unknown as {
      hermesDesktop: { zoom: { setPercent: (nextPercent: number) => void } }
    }

    desktop.hermesDesktop.zoom.setPercent(value)
  }, percent)
}

async function openMarketplace(fixture: MockBackendFixture, locale: 'ar' | 'en') {
  const page = fixture.page
  const marketplaceTab = page.getByRole('tab', {
    name: locale === 'ar' ? 'سوق الحزم' : 'Marketplace',
    exact: true
  })

  if (!(await marketplaceTab.isVisible())) {
    await page.getByRole('button', { name: 'Workflows', exact: true }).first().click()
  }

  await marketplaceTab.click()

  const back = page.getByRole('button', {
    name: locale === 'ar' ? 'العودة إلى الحزم' : 'Back to packages',
    exact: true
  })

  if (await back.isVisible()) {
    await back.click()
  }

  const packageOption = page.getByRole('option', { name: new RegExp(LONG_PACKAGE_DISPLAY_NAME) })
  await expect(packageOption).toBeVisible({ timeout: 60_000 })

  return packageOption
}

async function assertContainedReadableText(locator: Locator) {
  const metrics = await locator.evaluate(element => {
    const bounds = element.getBoundingClientRect()
    const container = element.closest('[role="option"]') ?? element.parentElement
    const containerBounds = container?.getBoundingClientRect()

    return {
      clientWidth: element.clientWidth,
      contained:
        bounds.left >= -1 &&
        bounds.right <= window.innerWidth + 1 &&
        (!containerBounds || (bounds.left >= containerBounds.left - 1 && bounds.right <= containerBounds.right + 1)),
      scrollWidth: element.scrollWidth,
      visible: bounds.width > 0 && bounds.height > 0
    }
  })

  expect(metrics).toMatchObject({ contained: true, visible: true })
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1)
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

  test('keeps V2 package actions reachable at 320 CSS px, 200% zoom and reduced motion', async () => {
    const page = fixture.page
    await page.emulateMedia({ reducedMotion: 'reduce' })

    await page.getByRole('button', { name: 'Workflows', exact: true }).first().click()
    await expect(page.getByRole('heading', { name: 'Workflows', exact: true })).toBeVisible()
    await page.getByRole('tab', { name: 'Marketplace', exact: true }).click()

    const packageOption = page.getByRole('option', { name: new RegExp(LONG_PACKAGE_DISPLAY_NAME) })
    await expect(packageOption).toBeVisible({ timeout: 60_000 })
    const refresh = page.getByRole('button', { name: 'Refresh', exact: true })
    await refresh.click()
    await expect(refresh).toHaveAttribute('aria-busy', 'true')
    await expect(refresh).toHaveAttribute('aria-busy', 'false', { timeout: 60_000 })
    await expect(packageOption).toBeVisible({ timeout: 60_000 })
    await packageOption.click()
    const detail = page.getByRole('region', { name: new RegExp(`${LONG_PACKAGE_DISPLAY_NAME} package details`) })
    await expect(detail).toBeVisible({ timeout: 60_000 })

    await page.evaluate(() => {
      const desktop = window as unknown as {
        hermesDesktop: { zoom: { setPercent: (percent: number) => void } }
      }

      desktop.hermesDesktop.zoom.setPercent(200)
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
    ).toEqual({ direction: 'ltr', noHorizontalOverflow: true, reducedMotion: true })

    const back = page.getByRole('button', { name: 'Back to packages', exact: true })
    const install = page.getByRole('button', { name: 'Install package', exact: true })
    const repository = detail.getByRole('link').first()
    const refreshState = page.getByRole('button', { name: 'Refresh state', exact: true })
    const retry = page.getByRole('button', { name: 'Retry', exact: true })
    const technicalDetails = detail.getByText('Technical details', { exact: true })
    await expect(technicalDetails).toHaveCount(EXPECTED_DIAGNOSTIC_DISCLOSURES)
    const detailFocusOrder = [
      back,
      repository,
      ...Array.from({ length: EXPECTED_DIAGNOSTIC_DISCLOSURES }, (_, index) => technicalDetails.nth(index)),
      install,
      refreshState,
      retry
    ]

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
    for (let index = EXPECTED_DIAGNOSTIC_DISCLOSURES - 1; index >= 0; index--) {
      await page.keyboard.press('Shift+Tab')
      await expect(technicalDetails.nth(index)).toBeFocused()
    }
    await page.keyboard.press('Shift+Tab')
    await expect(repository).toBeFocused()
    for (let index = 0; index < EXPECTED_DIAGNOSTIC_DISCLOSURES; index++) {
      await page.keyboard.press('Tab')
      await expect(technicalDetails.nth(index)).toBeFocused()
    }
    await page.keyboard.press('Tab')
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
    const confirm = review.getByRole('button', { name: 'Confirm install', exact: true })
    const reviewRepository = review.getByRole('link').first()
    const iconClose = review.locator('button[data-slot="dialog-close-button"]')
    const reviewTechnicalDetails = review.getByText('Technical details', { exact: true })
    await expect(reviewTechnicalDetails).toHaveCount(EXPECTED_DIAGNOSTIC_DISCLOSURES)
    await expect(confirm).toBeEnabled({ timeout: 60_000 })
    await expect(close).toBeFocused()

    for (const action of [
      confirm,
      iconClose,
      reviewRepository,
      ...Array.from({ length: EXPECTED_DIAGNOSTIC_DISCLOSURES }, (_, index) => reviewTechnicalDetails.nth(index)),
      close
    ]) {
      await page.keyboard.press('Tab')
      await expect(action).toBeFocused()
      await expect(action).toBeInViewport()
    }

    for (let index = EXPECTED_DIAGNOSTIC_DISCLOSURES - 1; index >= 0; index--) {
      await page.keyboard.press('Shift+Tab')
      await expect(reviewTechnicalDetails.nth(index)).toBeFocused()
    }
    await page.keyboard.press('Shift+Tab')
    await expect(reviewRepository).toBeFocused()
    await page.keyboard.press('Shift+Tab')
    await expect(iconClose).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(review).toBeHidden()
    await expect(detail).toBeVisible()
    await expect(back).toBeVisible()
    await expect(install).toBeFocused()
  })

  test('reflows complete long list and detail metadata across width, zoom, locale, direction, and theme', async () => {
    const locales = ['en', 'ar'] as const

    for (const locale of locales) {
      const localeFixture =
        locale === 'en'
          ? fixture
          : await setupMockBackend({
              extraDisplayConfig: '  language: ar',
              prepareHermesHome: prepareSource
            })

      try {
        if (locale === 'ar') {
          await waitForAppReady(localeFixture, 120_000)
        }

        const page = localeFixture.page
        await openMarketplace(localeFixture, locale)

        for (const colorScheme of ['light', 'dark'] as const) {
          await page.evaluate(mode => {
            localStorage.setItem('hermes-desktop-mode-v1', mode)
            window.dispatchEvent(new StorageEvent('storage', { key: 'hermes-desktop-mode-v1', newValue: mode }))
          }, colorScheme)
          await expect.poll(() => page.evaluate(() => document.documentElement.dataset.hermesMode)).toBe(colorScheme)
          await expect
            .poll(() => page.evaluate(() => document.documentElement.classList.contains('dark')))
            .toBe(colorScheme === 'dark')

          for (const zoom of [100, 200] as const) {
            await setZoomPercent(localeFixture, zoom)

            for (const width of [320, 768, 1440]) {
              await setContentSize(localeFixture, width * (zoom / 100), 900)
              await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(width)

              const back = page.getByRole('button', {
                name: locale === 'ar' ? 'العودة إلى الحزم' : 'Back to packages',
                exact: true
              })

              if (await back.isVisible()) {
                await back.click()
              }

              const packageOption = page.getByRole('option', { name: new RegExp(LONG_PACKAGE_DISPLAY_NAME) })
              await expect(packageOption).toBeVisible()
              await assertContainedReadableText(packageOption.getByText(LONG_PACKAGE_DISPLAY_NAME, { exact: true }))
              await assertContainedReadableText(packageOption.getByText(LONG_PACKAGE_PUBLISHER, { exact: true }))
              await expect(packageOption).toHaveAccessibleName(
                `${LONG_PACKAGE_DISPLAY_NAME} company/laptop-support ${LONG_PACKAGE_PUBLISHER}`
              )

              await packageOption.click()
              const detail = page.getByRole('region', { name: new RegExp(LONG_PACKAGE_DISPLAY_NAME) })
              await expect(detail).toBeVisible({ timeout: 60_000 })
              await assertContainedReadableText(detail.getByRole('heading', { name: LONG_PACKAGE_DISPLAY_NAME }))
              await assertContainedReadableText(detail.getByText(LONG_PACKAGE_PUBLISHER, { exact: true }))
              await expect(
                detail.getByText(locale === 'ar' ? 'التفاصيل الفنية' : 'Technical details', { exact: true })
              ).toHaveCount(EXPECTED_DIAGNOSTIC_DISCLOSURES)

              const install = page.getByRole('button', {
                name: locale === 'ar' ? 'تثبيت الحزمة' : 'Install package',
                exact: true
              })
              await install.scrollIntoViewIfNeeded()
              await expect(install).toBeVisible()
              await expect(install).toBeInViewport()

              expect(
                await page.evaluate(() => ({
                  direction: getComputedStyle(document.documentElement).direction,
                  language: document.documentElement.lang,
                  noHorizontalOverflow:
                    document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1 &&
                    document.body.scrollWidth <= window.innerWidth + 1,
                  renderedMode: document.documentElement.dataset.hermesMode
                }))
              ).toEqual({
                direction: locale === 'ar' ? 'rtl' : 'ltr',
                language: locale,
                noHorizontalOverflow: true,
                renderedMode: colorScheme
              })
            }
          }
        }
      } finally {
        if (locale === 'ar') {
          await localeFixture.cleanup()
        }
      }
    }
  })
})
