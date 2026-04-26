import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  timeout: 30000,
  expect: {
    timeout: 5000,
  },
  use: {
    baseURL: 'http://localhost:8000',
    headless: true,
  },
  webServer: {
    command: '.venv/bin/python -m uvicorn agentbench.server.app:app --host 127.0.0.1 --port 8000',
    url: 'http://127.0.0.1:8000/health',
    reuseExistingServer: !process.env.CI,
    env: {
      AGENTBENCH_API_KEYS: 'dev-key',
      AGENTBENCH_SECRET_KEY: 'agentbench-browser-test-secret-key-123456',
      PYTHONPATH: process.cwd(),
    },
  },
});
