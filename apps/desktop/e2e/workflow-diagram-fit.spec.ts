import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { allowErrorBanners, expect, test } from './test'

const WINDOW_SIZE = { height: 800, width: 620 }
const WORKFLOW_NAME = 'diagram-fit-windows-scale'

function seedWorkflow(hermesHome: string): void {
  const workflows = path.join(hermesHome, 'workflows')

  fs.mkdirSync(workflows, { recursive: true })
  fs.writeFileSync(
    path.join(workflows, `${WORKFLOW_NAME}.yaml`),
    [
      `name: ${WORKFLOW_NAME}`,
      'description: Two-node workflow used to verify responsive diagram fitting.',
      'nodes:',
      '  - id: inspect-request',
      '    bash: "printf inspect"',
      '  - id: write-summary',
      '    bash: "printf summary"',
      '    depends_on: [inspect-request]',
      ''
    ].join('\n'),
    'utf8'
  )
  fs.writeFileSync(
    path.join(workflows, `${WORKFLOW_NAME}.hermes.yaml`),
    'language_compatibility: archon-2026-07\n',
    'utf8'
  )
}

async function resizeWindow(fixture: MockBackendFixture): Promise<void> {
  await fixture.app.evaluate(({ BrowserWindow }, size) => {
    const window = BrowserWindow.getAllWindows()[0]

    if (!window) {
      throw new Error('Hermes window is unavailable')
    }

    window.unmaximize()
    window.setMinimumSize(1, 1)
    window.setContentSize(size.width, size.height, false)
  }, WINDOW_SIZE)

  await expect.poll(() => fixture.page.evaluate(() => window.innerWidth)).toBe(WINDOW_SIZE.width)
}

test.use({ trace: 'off' })

test.describe('workflow diagram responsive fit', () => {
  test.describe.configure({ mode: 'serial', timeout: 180_000 })

  let fixture: MockBackendFixture

  test.beforeAll(async () => {
    fixture = await setupMockBackend({ prepareHermesHome: seedWorkflow })
    await waitForAppReady(fixture, 120_000)
  })

  test('fills and centres the initial Diagram tab below the sm breakpoint', async () => {
    test.setTimeout(180_000)
    allowErrorBanners()
    const page = fixture.page

    try {
      await page.evaluate(() => {
        window.location.hash = '/workflows'
      })
      await expect(page.getByRole('heading', { name: 'Workflows', exact: true })).toBeVisible()
      await resizeWindow(fixture)

      const row = page.getByRole('row').filter({ hasText: WORKFLOW_NAME })

      await expect(row).toBeVisible()
      await row.getByRole('button', { name: 'View', exact: true }).click()

      const dialog = page.getByRole('dialog', { name: `View ${WORKFLOW_NAME}` })
      const diagram = dialog.locator('[data-workflow-view-scroll] svg.flowchart')

      await expect(diagram).toBeVisible()

      const metrics = await dialog.evaluate(node => {
        const svg = node.querySelector<SVGSVGElement>('[data-workflow-view-scroll] svg.flowchart')
        const drawing = svg?.querySelector<SVGGElement>('g')
        const canvas = svg?.parentElement

        if (!svg || !drawing || !canvas) {
          throw new Error('Workflow diagram geometry is unavailable')
        }

        const dialogRect = node.getBoundingClientRect()
        const canvasRect = canvas.getBoundingClientRect()
        const svgRect = svg.getBoundingClientRect()
        const drawingRect = drawing.getBoundingClientRect()

        return {
          canvasHeight: canvasRect.height,
          canvasWidth: canvasRect.width,
          dialogWidth: dialogRect.width,
          drawingCenterX: drawingRect.left + drawingRect.width / 2,
          drawingCenterY: drawingRect.top + drawingRect.height / 2,
          drawingWidth: drawingRect.width,
          svgCenterX: svgRect.left + svgRect.width / 2,
          svgCenterY: svgRect.top + svgRect.height / 2,
          svgHeight: svgRect.height,
          svgWidth: svgRect.width,
          viewportWidth: window.innerWidth
        }
      })

      const geometry = JSON.stringify(metrics)

      expect(metrics.dialogWidth / metrics.viewportWidth, geometry).toBeGreaterThan(0.88)
      expect(metrics.svgWidth / metrics.canvasWidth, geometry).toBeGreaterThan(0.93)
      expect(metrics.svgHeight / metrics.canvasHeight, geometry).toBeGreaterThan(0.93)
      expect(metrics.drawingWidth / metrics.svgWidth, geometry).toBeGreaterThan(0.7)
      expect(Math.abs(metrics.drawingCenterX - metrics.svgCenterX), geometry).toBeLessThan(4)
      expect(Math.abs(metrics.drawingCenterY - metrics.svgCenterY), geometry).toBeLessThan(4)
    } finally {
      await fixture.cleanup()
      await new Promise(resolve => setTimeout(resolve, 50))
    }
  })
})
