import type { TestProjectConfiguration } from 'vitest/config';
import { defineConfig } from 'vitest/config'

const reactUi: TestProjectConfiguration = {
  extends: './vite.config.ts',
  test: {
    name: 'ui',
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    globals: true,
    // The first test in each file pays jsdom env init + full module transform,
    // which can exceed vitest's 5000ms default under CI/load. 15s gives the
    // cold start headroom without masking genuinely hung tests.
    testTimeout: 15_000
  }
}

// Files under electron/ that use the node:test runner (`import test from
// 'node:test'`) rather than vitest. Vitest's glob collects them but cannot
// execute them, so each reported "No test suite found" and failed the electron
// project — while their assertions never ran at all. They are run instead by
// the `check:test:electron-node` script (tsx --test). Keep this list in sync
// with any file whose header says "Run with: npx tsx --test".
const NODE_TEST_FILES = [
  'electron/brand-scope.test.ts',
  'electron/release-update.test.ts',
  'electron/structured-api-channel.test.ts',
  'electron/structured-api-response.test.ts'
]

const electronNative: TestProjectConfiguration = {
  test: {
    name: 'electron',
    environment: 'node',
    include: ['electron/**/*.test.ts', 'scripts/**.test.{ts,mjs}'],
    exclude: ['**/node_modules/**', '**/dist/**', ...NODE_TEST_FILES]
  }
}

export default defineConfig({
  test: {
    projects: [reactUi, electronNative]
  }
})
