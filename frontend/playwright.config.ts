import { defineConfig, devices } from '@playwright/test';

// 浏览器交互回归：真实 Chromium + 独立 Vite 测试服务（端口独立，不连日常服务）。
// 失败产物（截图/trace）落在 test-results/，用于追查焦点、关闭与接线回归。
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 0,
  use: {
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: process.env.READING_PROD === '1'
      ? 'npm run preview -- --port 4573 --strictPort'
      : 'npm run dev -- --port 4573 --strictPort',
    url: 'http://127.0.0.1:4573',
    reuseExistingServer: process.env.READING_EXTERNAL_SERVER === '1',
    timeout: 60_000,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { ...devices['Desktop Chrome'], viewport: { width: 375, height: 812 } } },
  ],
});
