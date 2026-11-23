// Playwright against the running dev stack (`make dev && make seed`), in its
// own container on the compose network (`make test-e2e-ui`).
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  timeout: 60_000,
  use: {
    baseURL: process.env.CORTEX_E2E_BASE_URL ?? 'http://frontend:5001',
    // Repo standard (PROD:frontend/arbitr/playwright.config.ts): data-test-id.
    testIdAttribute: 'data-test-id',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure'
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
})
