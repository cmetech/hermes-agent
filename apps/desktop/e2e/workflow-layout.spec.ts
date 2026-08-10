import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { expect, test } from './test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'

const DESKTOP_ROOT = path.resolve(import.meta.dirname, '..')
const REPO_ROOT = path.resolve(DESKTOP_ROOT, '..', '..')
const RUN_COUNT = 300
const WINDOW_HEIGHT = 800

const SEED_WORKFLOW_RUNS = String.raw`
import sys
from pathlib import Path

import yaml

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore

home = Path(sys.argv[1])
run_count = int(sys.argv[2])
fixture_root = home / "e2e-workflow-layout"
definition = fixture_root / "layout-fixture.yaml"
fixture_root.mkdir(parents=True, exist_ok=True)
definition.write_text(
    yaml.safe_dump(
        {
            "name": "responsive-layout-fixture",
            "description": "Real-browser workflow layout fixture",
            "nodes": [{"id": "finish", "bash": "true"}],
        },
        sort_keys=False,
    ),
    encoding="utf-8",
)
package = load_workflow(definition)
store = RunStore(
    home,
    max_nonterminal_runs=run_count + 10,
    max_start_requests_per_minute=run_count + 10,
)

for index in range(run_count):
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="desktop",
            idempotency_key=f"layout-{index}",
            concurrency_key=f"layout-{index}",
            concurrency_policy="allow",
        ),
        immutable_snapshot=prepared,
    )
    claim = store.claim_node(
        admitted.run_id,
        "finish",
        "layout-seeder",
        executor_id="layout-seeder",
    )
    if claim is None:
        raise RuntimeError(f"failed to claim seeded run {admitted.run_id}")
    store.complete_node(
        claim,
        status="failed",
        error_code="fixture_failure",
        error_message="Responsive fixture failure",
    )
`

function findHermesPython(): string {
  const executable = process.platform === 'win32' ? path.join('Scripts', 'python.exe') : path.join('bin', 'python')
  const candidates = [
    process.env.HERMES_DESKTOP_PYTHON,
    path.join(REPO_ROOT, '.venv', executable),
    path.join(REPO_ROOT, 'venv', executable),
    path.resolve(REPO_ROOT, '..', '..', '.venv', executable),
    path.resolve(REPO_ROOT, '..', '..', 'venv', executable)
  ]

  const python = candidates.find((candidate): candidate is string => Boolean(candidate && fs.existsSync(candidate)))

  if (!python) {
    throw new Error('Workflow layout E2E requires the repository Python environment.')
  }

  return python
}

function seedWorkflowRuns(python: string, hermesHome: string): void {
  const result = spawnSync(python, ['-c', SEED_WORKFLOW_RUNS, hermesHome, String(RUN_COUNT)], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    timeout: 120_000
  })

  if (result.status !== 0) {
    throw new Error(`Workflow layout seed failed:\n${result.stderr || result.stdout}`)
  }
}

async function resizeWindow(fixture: MockBackendFixture, width: number): Promise<void> {
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
    { height: WINDOW_HEIGHT, width }
  )

  await expect.poll(() => fixture.page.evaluate(() => window.innerWidth)).toBe(width)
}

async function expandEveryLane(fixture: MockBackendFixture): Promise<void> {
  for (const lane of ['Queued', 'Active', 'Needs attention', 'Completed']) {
    const expand = fixture.page.getByRole('button', { name: `Expand ${lane}`, exact: true })

    if (await expand.isVisible().catch(() => false)) {
      await expand.click()
    }
  }
}

async function pageHasNoHorizontalOverflow(fixture: MockBackendFixture): Promise<boolean> {
  return fixture.page.evaluate(() => {
    const root = document.documentElement

    return root.scrollWidth <= root.clientWidth + 1 && document.body.scrollWidth <= window.innerWidth + 1
  })
}

async function layoutMetrics(fixture: MockBackendFixture) {
  return fixture.page.evaluate(() => {
    const root = document.documentElement
    const strip = document.querySelector<HTMLElement>('[data-layout="collapsible-lanes"]')
    const lane = document.querySelector<HTMLElement>('[data-column="stopped"] [data-lane-scroll]')
    const runView = document.querySelector<HTMLElement>('[data-workflow-run-view]')
    const attention = document.querySelector<HTMLElement>('[aria-label="Workflow attention"]')
    const attentionList = attention?.querySelector<HTMLElement>('[data-workflow-attention-list]')

    if (!strip || !lane || !runView || !attention || !attentionList) {
      throw new Error('Workflow layout elements are unavailable')
    }

    strip.scrollLeft = 64
    lane.scrollTop = 64
    attentionList.scrollTop = 64
    const scrollingElement = document.scrollingElement as HTMLElement
    scrollingElement.scrollTop = 64

    const result = {
      attentionBounded: attention.clientHeight <= runView.clientHeight * 0.4 + 1,
      attentionCanScroll: attentionList.scrollHeight > attentionList.clientHeight && attentionList.scrollTop > 0,
      attentionOverflowY: getComputedStyle(attentionList).overflowY,
      documentCanScrollVertically: scrollingElement.scrollTop > 0,
      documentHorizontalOverflow: root.scrollWidth - root.clientWidth,
      laneCanScroll: lane.scrollHeight > lane.clientHeight && lane.scrollTop > 0,
      laneOverflowY: getComputedStyle(lane).overflowY,
      stripCanScroll: strip.scrollWidth > strip.clientWidth && strip.scrollLeft > 0,
      stripOverflowX: getComputedStyle(strip).overflowX,
      viewportWidth: window.innerWidth
    }

    scrollingElement.scrollTop = 0

    return result
  })
}

test.describe('workflow responsive scroll ownership', () => {
  test.describe.configure({ mode: 'serial', timeout: 180_000 })

  let fixture: MockBackendFixture
  let priorPython: string | undefined

  test.beforeAll(async () => {
    const python = findHermesPython()
    priorPython = process.env.HERMES_DESKTOP_PYTHON
    process.env.HERMES_DESKTOP_PYTHON = python
    fixture = await setupMockBackend({
      prepareHermesHome: hermesHome => seedWorkflowRuns(python, hermesHome)
    })
    await waitForAppReady(fixture, 120_000)
  })

  test.afterAll(async () => {
    await fixture?.cleanup()

    if (priorPython === undefined) {
      delete process.env.HERMES_DESKTOP_PYTHON
    } else {
      process.env.HERMES_DESKTOP_PYTHON = priorPython
    }
  })

  test('contains responsive overflow and keeps cleanup reachable at 320, 768, and 1440px', async () => {
    const page = fixture.page
    await page.getByRole('button', { name: 'Workflows', exact: true }).first().click()
    await expect(page.getByRole('heading', { name: 'Workflows', exact: true })).toBeVisible()
    await page.getByRole('tab', { name: 'Active board', exact: true }).click()

    const failedLane = page.locator('[data-column="stopped"]')
    await expect(failedLane).toHaveAttribute('aria-label', 'Failed / stopped, 100')

    for (const count of [200, RUN_COUNT]) {
      await failedLane.getByRole('button', { name: 'Load more', exact: true }).click()
      await expect(failedLane).toHaveAttribute('aria-label', `Failed / stopped, ${count}`)
    }

    for (const width of [320, 768, 1440]) {
      await resizeWindow(fixture, width)
      await page.getByRole('tab', { name: 'Active board', exact: true }).click()
      await expect(failedLane).toHaveAttribute('aria-label', `Failed / stopped, ${RUN_COUNT}`)
      await expandEveryLane(fixture)

      expect(await layoutMetrics(fixture)).toEqual({
        attentionBounded: true,
        attentionCanScroll: true,
        attentionOverflowY: 'auto',
        documentCanScrollVertically: false,
        documentHorizontalOverflow: 0,
        laneCanScroll: true,
        laneOverflowY: 'auto',
        stripCanScroll: true,
        stripOverflowX: 'auto',
        viewportWidth: width
      })

      for (const view of ['History', 'Archive']) {
        await page.getByRole('tab', { name: view, exact: true }).click()
        const cleanup = page.getByRole('region', { name: 'Cleanup' })
        const inspect = cleanup.getByRole('button', { name: 'Inspect cleanup impact', exact: true })

        await expect(cleanup).toBeVisible()
        await expect(inspect).toBeInViewport()
        await inspect.focus()
        await expect(inspect).toBeFocused()
        expect(await pageHasNoHorizontalOverflow(fixture)).toBe(true)
      }
    }
  })
})
