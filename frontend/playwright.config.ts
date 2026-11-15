import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { API_URL, OFFLINE_URL, FRONTEND_URL } from './tests/servers';

const python = fileURLToPath(new URL(process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python', import.meta.url));

export default defineConfig({
  testDir: './tests/browser',
  timeout: 90000,
  expect: { timeout: 30000 },
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: FRONTEND_URL,
    channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: [{
    command: `"${python}" -m backend.main --port 8100`,
    cwd: fileURLToPath(new URL('..', import.meta.url)),
    url: `${API_URL}/api/health`,
    reuseExistingServer: !process.env.CI,
  }, {
    command: `"${python}" -m backend.main --offline --port 8101`,
    cwd: fileURLToPath(new URL('..', import.meta.url)),
    url: `${OFFLINE_URL}/api/health`,
    reuseExistingServer: !process.env.CI,
  }, {
    command: 'npm run dev -- --port 5273 --strictPort',
    url: FRONTEND_URL,
    env: { ATLAS_API_URL: API_URL },
    reuseExistingServer: !process.env.CI,
  }],
});
