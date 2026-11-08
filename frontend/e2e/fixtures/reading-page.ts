import { expect, type Page } from '@playwright/test';
import { SCOPE } from './conversation-server';

export async function installConversationApi(page: Page, port: number) {
  await page.addInitScript(({ port: servicePort }) => {
    const original = window.fetch;
    window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.startsWith('/api/')) return original(`http://127.0.0.1:${servicePort}${url}`, init);
      return original(input, init);
    }) as typeof fetch;
  }, { port });
}

export async function openConversation(page: Page, options: { port: number; title?: string }) {
  const title = options.title ?? '阅读基线会话 A';
  await installConversationApi(page, options.port);
  await page.goto(`http://127.0.0.1:4573/#access_token=${'e2e0'.padEnd(48, '0')}&scope_id=${SCOPE}&instance_id=${'0'.repeat(32)}`);
  const narrow = (page.viewportSize()?.width ?? 1280) <= 760;
  if (narrow) await page.getByRole('button', { name: '打开会话栏' }).click();
  const prepare = page.waitForResponse(response => response.url().includes('/prepare') && response.ok());
  const session = page.getByRole('button', { name: title, exact: true });
  await session.click();
  await expect(session).toHaveAttribute('aria-current', 'true');
  await prepare;
  if (narrow) {
    const close = page.getByRole('button', { name: '关闭会话栏' });
    if (await close.isVisible()) await close.click();
  }
}

export async function selectTheme(page: Page, theme: 'light' | 'dark') {
  const label = theme === 'light' ? '浅色' : '深色';
  await page.getByRole('button', { name: /^主题：/ }).click();
  await page.getByRole('menuitemradio', { name: label, exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.getByRole('button', { name: `主题：${label}`, exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('naiyue.theme'))).toBe(theme);
  const colors = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return { background: style.backgroundColor, text: style.color };
  });
  expect(colors).toEqual(theme === 'light'
    ? { background: 'rgb(255, 255, 255)', text: 'rgb(24, 24, 24)' }
    : { background: 'rgb(27, 29, 32)', text: 'rgb(232, 233, 236)' });
}
