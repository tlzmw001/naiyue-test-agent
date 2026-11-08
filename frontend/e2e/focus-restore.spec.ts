import { expect, test } from '@playwright/test';
import { backgroundCommandsBinding, backgroundCommandsTask, json, openCloudTask, bindingId } from './fixtures/service';

// 云端任务面板的操作区交互当前在桌面布局下验收；窄屏布局属后续补充范围。
test.skip(({ viewport }) => (viewport?.width ?? 0) <= 760, '仅桌面布局');

// 期望行为依据：确认框关闭后焦点应回到打开它的入口按钮（dialog 关闭焦点恢复惯例，
// 与 CloudResults/ProjectPicker 等现有组件一致）；取消/Escape 不发送任何操作命令。
// 「核对并释放环境」入口需要 uncertain 状态任务。

const uncertainExtras = [
  json('/api/client/v1/cloud-tasks', [{ ...backgroundCommandsBinding,
    cloud: { ...backgroundCommandsTask, runner_phase: 'disconnected', last_quiescent_at: null, reclaim_blockers: ['runner_disconnected'] } }]),
  json(`/api/client/v1/cloud-tasks/${bindingId}`, { ...backgroundCommandsBinding,
    cloud: { ...backgroundCommandsTask, runner_phase: 'disconnected', last_quiescent_at: null, reclaim_blockers: ['runner_disconnected'] } }),
];

test('取消停止后台任务确认框后，焦点回到停止按钮且未发送命令', async ({ page }) => {
  const service = await openCloudTask(page);
  const trigger = page.getByRole('button', { name: '停止后台任务' });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: '停止此任务的后台服务？' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '取消' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  expect(service.commandPosts(), '取消不应发送任何操作命令').toEqual([]);
  service.expectNoUnexpectedRequests();
});

test('Escape 关闭停止后台任务确认框后，焦点回到停止按钮', async ({ page }) => {
  const service = await openCloudTask(page);
  const trigger = page.getByRole('button', { name: '停止后台任务' });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: '停止此任务的后台服务？' });
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  expect(service.commandPosts(), 'Escape 关闭不应发送任何操作命令').toEqual([]);
  service.expectNoUnexpectedRequests();
});

test('取消删除任务确认框后，焦点回到删除按钮', async ({ page }) => {
  const service = await openCloudTask(page);
  const trigger = page.getByRole('button', { name: '删除任务' });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: '删除此云端任务？' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '取消' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  expect(service.commandPosts(), '取消不应发送任何操作命令').toEqual([]);
  service.expectNoUnexpectedRequests();
});

test('取消核对并释放环境确认框后，焦点回到该入口', async ({ page }) => {
  const service = await openCloudTask(page, uncertainExtras);
  const trigger = page.getByRole('button', { name: '核对并释放环境' });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: '释放状态未确认的环境？' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '取消' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  expect(service.commandPosts(), '取消不应发送任何操作命令').toEqual([]);
  service.expectNoUnexpectedRequests();
});
