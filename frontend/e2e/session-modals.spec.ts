import { expect, test } from '@playwright/test';
import type { Route } from '@playwright/test';
import { baseMocks, openHome, sessionId, project } from './fixtures/service';

// Spec143BA 阶段 2：SessionMenu 确认态迁原生 dialog 后的业务与交互保护。
// 业务断言沿用原回调语义：重命名/删除各一次调用、失败保留输入与重试、busy 期间不可关闭。
// 写操作响应经 fixtures 拦截体系按方法声明，不叠加 page.route 双重处理。
test.skip(({ viewport }) => (viewport?.width ?? 0) <= 760, '会话操作菜单交互按桌面布局验收');

const sessionPath = `/api/v1/scopes/${project}/sessions/${sessionId}`;
const presentationPath = `${sessionPath}/presentation`; // updateSession 实际路径（api.ts PATCH presentation）
const savedRow = { session_id: sessionId, title: '更名后的会话', model_id: 'A', workspace_root: null,
  state: 'ready', updated_at: '2026-11-04T10:00:00Z', last_run_status: null };

async function openRename(page: import('@playwright/test').Page, extras: Parameters<typeof openHome>[1] = []) {
  const service = await openHome(page, [...baseMocks(), ...extras]);
  await page.getByRole('button', { name: '验收会话 A' }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: '重命名' }).click();
  const dialog = page.getByRole('dialog', { name: '重命名会话' });
  await expect(dialog).toBeVisible();
  return { service, dialog };
}

test('重命名提交调用原更新入口，成功后关闭且一次操作只调用一次', async ({ page }) => {
  const { service } = await openRename(page, [
    { path: presentationPath, method: 'PATCH', reply: route => route.fulfill({ contentType: 'application/json', json: savedRow }) },
  ]);
  const dialog = page.getByRole('dialog', { name: '重命名会话' });
  await dialog.getByRole('textbox', { name: '会话名称' }).fill('更名后的会话');
  await dialog.getByRole('button', { name: '保存名称' }).click();
  await expect(dialog).not.toBeVisible();
  const patches = service.postRequests.filter(item => item.startsWith('PATCH'));
  expect(patches).toHaveLength(1);
  expect(patches[0]).toContain(sessionPath);
});

test('重命名失败保留错误提示与输入，可重试', async ({ page }) => {
  let calls = 0;
  const { dialog } = await openRename(page, [
    { path: presentationPath, method: 'PATCH', reply: route => {
      calls++;
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: { code: 'rename_failed' } }) });
    } },
  ]);
  const name = dialog.getByRole('textbox', { name: '会话名称' });
  await name.fill('重试中的名称');
  await dialog.getByRole('button', { name: '保存名称' }).click();
  await expect(dialog.getByText('操作未确认，请核对提示后重试。')).toBeVisible();
  expect(await name.inputValue()).toBe('重试中的名称');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '保存名称' }).click();
  expect(calls).toBe(2);
  await expect(dialog.getByText('操作未确认，请核对提示后重试。')).toBeVisible();
});

test('Escape 关闭重命名模态且焦点回到会话行', async ({ page }) => {
  await openHome(page, baseMocks());
  const row = page.getByRole('button', { name: '验收会话 A' });
  await row.click({ button: 'right' });
  await page.getByRole('menuitem', { name: '重命名' }).click();
  const dialog = page.getByRole('dialog', { name: '重命名会话' });
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(row).toBeFocused();
});

test('删除确认沿用原删除入口且一次操作只调用一次', async ({ page }) => {
  const service = await openHome(page, [...baseMocks(), {
    path: sessionPath, method: 'DELETE',
    reply: route => route.fulfill({ contentType: 'application/json', json: { session_id: sessionId } }),
  }]);
  await page.getByRole('button', { name: '验收会话 A' }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: '删除' }).click();
  const dialog = page.getByRole('dialog', { name: '删除会话' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '删除会话' }).click();
  await expect(dialog).not.toBeVisible();
  expect(service.postRequests.filter(item => item.startsWith('DELETE'))).toHaveLength(1);
});

test('提交挂起时确认框保持打开且 Escape 不关闭', async ({ page }) => {
  const pending = { path: presentationPath, method: 'PATCH', reply: () => new Promise<void>(() => undefined) };
  const { dialog } = await openRename(page, [pending]);
  await dialog.getByRole('textbox', { name: '会话名称' }).fill('提交中的名称');
  await dialog.getByRole('button', { name: '保存名称' }).click();
  await expect(dialog.getByRole('button', { name: '保存名称' })).toBeDisabled({ timeout: 15_000 });
  await page.keyboard.press('Escape');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button', { name: '取消' })).toBeDisabled();
});
