import { expect, test } from '@playwright/test';
import { baseMocks, json, openHome, project, sessionId } from './fixtures/service';

// Spec143BB 基线：ComposerControls「指令与功能」菜单与「发送后滚动」二级菜单的行为合同。
// 权限/工具/MCP 不在本文件范围。迁移后复用同一断言。
test.skip(({ viewport }) => (viewport?.width ?? 0) <= 760, '菜单交互按桌面布局验收');

const trigger = (page: import('@playwright/test').Page) => page.getByRole('button', { name: '指令与功能' });
const menu = (page: import('@playwright/test').Page) => page.getByRole('menu', { name: '指令与功能' });

test('指令菜单：打开聚焦首项，方向键与 Home/End 循环', async ({ page }) => {
  await openHome(page, baseMocks());
  await trigger(page).click();
  await expect(menu(page)).toBeVisible();
  await expect(page.getByRole('menuitem', { name: '云端任务' })).toBeFocused();
  await page.keyboard.press('End');
  await expect(page.getByRole('menuitem', { name: '发送后滚动' })).toBeFocused();
  await page.keyboard.press('Home');
  await expect(page.getByRole('menuitem', { name: '云端任务' })).toBeFocused();
});

test('指令菜单：云端任务项点击一次进入云端面板并关闭菜单', async ({ page }) => {
  await openHome(page, baseMocks());
  await trigger(page).click();
  await page.getByRole('menuitem', { name: '云端任务' }).click();
  await expect(menu(page)).not.toBeVisible();
  await expect(page.getByRole('tab', { name: '云端任务' })).toHaveAttribute('aria-selected', 'true');
});

test('发送后滚动二级菜单：ArrowRight 展开，Escape 逐层关闭', async ({ page }) => {
  await openHome(page, baseMocks());
  const button = trigger(page);
  await button.click();
  const scroll = page.getByRole('menuitem', { name: '发送后滚动' });
  await scroll.focus();
  await page.keyboard.press('ArrowRight');
  const submenu = page.getByRole('menu', { name: '发送后滚动' });
  await expect(submenu).toBeVisible();
  // 键盘打开二级后聚焦当前选中项。
  await expect(page.getByRole('menuitemradio', { name: '保持当前位置' })).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(submenu).not.toBeVisible();
  await expect(scroll).toBeFocused();
  await expect(menu(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(menu(page)).not.toBeVisible();
  await expect(button).toBeFocused();
});

test('发送后滚动：选择后选项生效、菜单关闭、焦点恢复', async ({ page }) => {
  await openHome(page, baseMocks());
  const button = trigger(page);
  await button.click();
  await page.getByRole('menuitem', { name: '发送后滚动' }).hover();
  await expect(page.getByRole('menuitemradio', { name: '回到最新消息' })).toBeVisible();
  await page.getByRole('menuitemradio', { name: '回到最新消息' }).click();
  await expect(menu(page)).not.toBeVisible();
  await expect(button).toBeFocused();
  await button.click();
  await page.getByRole('menuitem', { name: '发送后滚动' }).hover();
  await expect(page.getByRole('menuitemradio', { name: '回到最新消息' })).toHaveAttribute('aria-checked', 'true');
});

test('指令菜单：外部点击关闭', async ({ page }) => {
  await openHome(page, baseMocks());
  await trigger(page).click();
  await expect(menu(page)).toBeVisible();
  await page.mouse.click(400, 300);
  await expect(menu(page)).not.toBeVisible();
});

test('指令菜单：禁用压缩项不执行操作', async ({ page }) => {
  const service = await openHome(page, [...baseMocks()]);
  await trigger(page).click();
  const compact = page.getByRole('menuitem', { name: /压缩上下文/ });
  await expect(compact).toBeDisabled();
  await compact.evaluate(element => (element as HTMLElement).click());
  expect(service.commandPosts(), '禁用项不得产生压缩或其他操作命令').toEqual([]);
  service.expectNoUnexpectedRequests();
});

test('指令菜单：会话切换后关闭已打开菜单', async ({ page }) => {
  const scope = `/api/v1/scopes/${project}`;
  const otherId = `h00${'e'.repeat(28)}`;
  const otherView = {
    session_id: otherId, title: '历史会话 1', model_id: 'A', workspace_root: null, state: 'ready',
    updated_at: '2026-11-04T10:00:00Z', last_run_status: null,
    active_tools: [], permission_mode: 'Default', messages: [], busy: false, can_run: true,
    has_compaction: false, active_run_id: null, latest_run_id: null,
    manual_review_required: false, pending_approval: false, live: null,
    conversation_revision: 1, thinking_level: 'low', checkpoint_id: null,
  };
  const extras = [
    json(`${scope}/sessions/${otherId}/prepare`, otherView),
    json(`${scope}/sessions/${otherId}`, otherView),
    json(`${scope}/sessions/${otherId}/subagents`, []),
    json(`${scope}/sessions/${otherId}/context`, { session_id: otherId, current: { basis: 'unavailable', estimate: null }, latest_request: null }),
  ];
  await openHome(page, [...baseMocks(), ...extras]);
  const firstPrepare = page.waitForResponse(`**/sessions/${sessionId}/prepare`);
  await page.getByRole('button', { name: '验收会话 A' }).click();
  await firstPrepare;
  await trigger(page).click();
  await expect(menu(page)).toBeVisible();

  const otherPrepare = page.waitForResponse(`**/sessions/${otherId}/prepare`);
  await page.getByRole('button', { name: '历史会话 1 A' }).click();
  await otherPrepare;
  await expect(menu(page)).not.toBeVisible();
});

test('指令菜单与二级菜单：窄屏 resize 后定位不溢出', async ({ page }) => {
  await openHome(page, baseMocks());
  await trigger(page).click();
  await page.getByRole('menuitem', { name: '发送后滚动' }).focus();
  await page.keyboard.press('ArrowRight');
  const main = menu(page);
  const submenu = page.getByRole('menu', { name: '发送后滚动' });
  await expect(submenu).toBeVisible();

  await page.setViewportSize({ width: 375, height: 812 });
  for (const current of [main, submenu]) {
    await expect.poll(async () => (await current.boundingBox())!.x).toBeGreaterThanOrEqual(0);
    await expect.poll(async () => {
      const bounds = await current.boundingBox();
      return bounds!.x + bounds!.width;
    }).toBeLessThanOrEqual(375);
    const bounds = await current.boundingBox();
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(812);
  }
});

test('指令菜单 → Skills 空目录：面板获得焦点，Escape 回编辑器', async ({ page }) => {
  // 复现产品回归：Radix Root 关闭后的触发器焦点恢复会覆盖 SkillsMenu 的初始焦点，
  // 焦点掉到触发器上时 Escape 无法回编辑器。先写失败测试确认根因。
  const scope = `/api/v1/scopes/${project}`;
  const skills = json(`${scope}/skills`, { entries: [], default_source: 'agents', diagnostics: [], read_registered: true });
  await openHome(page, [...baseMocks(), skills]);
  await trigger(page).click();
  await page.getByRole('menuitem', { name: /技能/ }).click();
  const panel = page.getByRole('dialog', { name: '技能' });
  await expect(panel).toBeVisible();
  await expect(page.getByText('当前工作区没有可用技能。')).toBeVisible();
  // SkillsMenu 的关闭按钮（autoFocus）应持有焦点。
  await expect(page.getByRole('button', { name: '关闭技能列表' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(panel).not.toBeVisible();
  await expect(page.getByRole('textbox', { name: '消息' })).toBeFocused();
});

test('打开 Skills 后点击消息框，面板关闭且焦点尊重点击目标', async ({ page }) => {
  const scope = `/api/v1/scopes/${project}`;
  const skills = json(`${scope}/skills`, { entries: [{ source_id: 'agents', name: '代码审查', description: '审查改动', command: '/review' }], default_source: 'agents', diagnostics: [], read_registered: true });
  await openHome(page, [...baseMocks(), skills]);
  await trigger(page).click();
  await page.getByRole('menuitem', { name: /技能/ }).click();
  const panel = page.getByRole('dialog', { name: '技能' });
  await expect(panel).toBeVisible();
  await page.getByRole('textbox', { name: '消息' }).click();
  await expect(panel).not.toBeVisible();
  await expect(page.getByRole('textbox', { name: '消息' })).toBeFocused();
});

test('先使用权限面板，再打开 Skills，Escape 回编辑器而非旧触发器', async ({ page }) => {
  const scope = `/api/v1/scopes/${project}`;
  const skills = json(`${scope}/skills`, { entries: [{ source_id: 'agents', name: '代码审查', description: '审查改动', command: '/review' }], default_source: 'agents', diagnostics: [], read_registered: true });
  await openHome(page, [...baseMocks(), skills]);
  await page.getByRole('button', { name: '权限模式：Default' }).click();
  await expect(page.getByRole('dialog', { name: '权限模式' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '权限模式' })).not.toBeVisible();
  await page.getByRole('button', { name: '指令与功能' }).click();
  await page.getByRole('menuitem', { name: /技能/ }).click();
  const panel = page.getByRole('dialog', { name: '技能' });
  await expect(panel).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(panel).not.toBeVisible();
  await expect(page.getByRole('textbox', { name: '消息' })).toBeFocused();
});

test('Skills：挂起加载显示进度，失败后可重试', async ({ page }) => {
  const scope = `/api/v1/scopes/${project}`;
  let calls = 0;
  await openHome(page, baseMocks());
  await page.route('**/skills*', async route => {
    calls++;
    await new Promise(resolve => setTimeout(resolve, 500));
    if (calls <= 2) {
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: { code: 'skills_failed' } }) });
    }
    return route.fulfill({ contentType: 'application/json', json: { entries: [{ source_id: 'agents', name: '代码审查', description: '审查改动', command: '/review' }], default_source: 'agents', diagnostics: [], read_registered: true } });
  });
  await trigger(page).click();
  await page.getByRole('menuitem', { name: /技能/ }).click();
  await expect(page.getByText('正在查找可用技能…')).toBeVisible({ timeout: 2000 });
  const panel = page.getByRole('dialog', { name: '技能' });
  await expect(panel).toBeVisible();
  await expect(page.getByRole('button', { name: '重试加载技能' })).toBeVisible();
  await page.getByRole('button', { name: '重试加载技能' }).click();
  await expect(page.locator('[data-skill-option]').first()).toBeVisible();
  expect(calls).toBe(3);
});
