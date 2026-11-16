import { expect, test } from '@playwright/test';
import { baseMocks, json, openHome, project } from './fixtures/service';

const other = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const otherName = '跨项目验收与较长项目名称';

async function openNavigation(page: import('@playwright/test').Page) {
  if ((page.viewportSize()?.width ?? 1440) <= 760) await page.getByRole('button', { name: '打开会话栏' }).click();
}

test('project context menu opens a new draft and preserves keyboard removal access', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const service = await openHome(page, baseMocks());
  await openNavigation(page);
  const row = page.getByRole('button', { name: '验收会话 A', exact: true });
  await row.click();
  await expect(row).toHaveAttribute('aria-current', 'true');
  await openNavigation(page);
  const origin = page.getByRole('button', { name: '进入验收项目', exact: true });
  await origin.click({ button: 'right' });
  const menu = page.getByRole('menu', { name: '验收项目 的操作' });
  await expect(menu.getByRole('menuitem')).toHaveText(['新建会话', '移除项目']);
  await expect(menu.getByRole('menuitem', { name: '新建会话' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(menu.getByRole('menuitem', { name: '移除项目' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(menu).not.toBeVisible();
  await expect(origin).toBeFocused();
  await page.keyboard.press('Shift+F10');
  await expect(menu).toBeVisible();
  const box = (await menu.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.screenshot({ path: info.outputPath('project-menu.png') });
  await menu.getByRole('menuitem', { name: '新建会话' }).click();
  await expect(menu).not.toBeVisible();
  await expect(page.getByRole('button', { name: '关闭会话栏' })).not.toBeVisible();
  await expect(page.getByRole('textbox', { name: '消息' })).toHaveValue('');
  await openNavigation(page);
  await expect(row).not.toHaveAttribute('aria-current', 'true');
  await origin.click({ button: 'right' });
  await page.getByRole('menuitem', { name: '移除项目' }).click();
  await expect(page.getByRole('dialog', { name: '移除项目' })).toBeVisible();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(origin).toBeVisible();
  expect(service.commandPosts()).toEqual([]);
  expect(service.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

test('project context new session enters the right-clicked project and preserves the original draft', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const scope = `/api/v1/scopes/${other}`;
  const mocks = baseMocks();
  const catalog = { items: [
    { project_id: project, name: '验收项目', source: { path: '/tmp/e2e-acceptance' }, lifecycle: 'active', trusted: true, revision: 1, availability: null },
    { project_id: other, name: otherName, source: { path: '/tmp/other' }, lifecycle: 'active', trusted: true, revision: 1, availability: null },
  ], default_parent_dir: null };
  const service = await openHome(page, [json('/api/v1/projects', catalog), ...mocks,
    { ...mocks.find(mock => mock.path.endsWith(`/navigation/${project}/sessions`))!, path: `/api/v1/navigation/${project}/sessions` },
    json(`/api/v1/navigation/${other}/sessions`, []),
    json(`${scope}/models`, { default_model_id: 'A', model_ids: ['A'] }),
    json(`${scope}/sessions`, []),
    json(`${scope}/background-tasks`, { items: [], next_before: null, active_count: 0, revision: 'empty', terminal_revision: 0 }),
    json(`${scope}/session-controls`, { tools: [], permission_modes: ['Default'], default_permission_mode: 'Default', default_active_tools: [] }),
  ]);
  await openNavigation(page);
  const original = page.getByRole('button', { name: '验收会话 A', exact: true });
  await original.click();
  await expect(original).toHaveAttribute('aria-current', 'true');
  await page.getByRole('textbox', { name: '消息' }).fill('原项目的未发送草稿');
  await openNavigation(page);
  await page.getByRole('button', { name: `进入${otherName}`, exact: true }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: '新建会话' }).click();
  await expect(page.getByRole('button', { name: `项目与位置，当前：${otherName}`, exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '关闭会话栏' })).not.toBeVisible();
  await expect(page.getByRole('textbox', { name: '消息' })).toHaveValue('');
  await page.getByRole('textbox', { name: '消息' }).fill('目标项目草稿');
  await page.screenshot({ path: info.outputPath('target-project-draft.png') });
  await openNavigation(page);
  await page.getByRole('button', { name: '进入验收项目', exact: true }).click();
  await expect(page.getByRole('button', { name: '关闭会话栏' })).not.toBeVisible();
  await expect(page.getByRole('textbox', { name: '消息' })).toHaveValue('原项目的未发送草稿');
  expect(service.commandPosts()).toEqual([]);
  expect(service.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});
