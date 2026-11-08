import { expect, test } from '@playwright/test';

// Spec143BD：Base UI 公开 API 对照试点（测试专用组合页，不进入产品）。
// 断言与 Radix 基线（theme-menu / command-menu / controlled-sub-pilot）一致，仅定位器随组件差异调整。
test.skip(({ viewport }) => (viewport?.width ?? 0) <= 760, '组合场景按桌面验收');

const init = async (page: import('@playwright/test').Page) => {
  await page.goto('http://127.0.0.1:4573/e2e-harness/index.html');
  await page.evaluate(() => {
    const state = window as unknown as { __pilot?: Record<string, unknown>; __skillMode?: string };
    state.__skillMode = 'normal';
    state.__pilot = { selects: 0, themeSelects: 0, scrollSelects: 0, reasons: [], ...(state.__pilot ?? {}) };
  });
};

test('Base 主题菜单：键盘打开、选择回调一次、Escape 恢复触发器', async ({ page }) => {
  await init(page);
  const button = page.getByRole('button', { name: /主题：/ });
  await button.focus();
  await page.keyboard.press('ArrowDown');
  const menu = page.getByRole('menu', { name: '主题选择' });
  await expect(menu).toBeVisible();
  await page.getByRole('menuitemradio', { name: '深色', exact: true }).click();
  await expect(menu).not.toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __pilot: { themeSelects: number } }).__pilot.themeSelects)).toBe(1);

  await button.focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitemradio', { name: '深色', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(menu).not.toBeVisible();
  await expect(button).toBeFocused();
  const reasons = await page.evaluate(() => (window as unknown as { __pilot: { reasons: string[] } }).__pilot.reasons);
  expect(reasons.some(reason => reason.endsWith('escape-key'))).toBe(true);
});

test('Base 指令菜单子菜单：ArrowRight 展开，Escape 逐层关闭并恢复焦点', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: '指令与功能' }).click();
  const main = page.getByRole('menu', { name: '指令与功能' });
  await expect(main).toBeVisible();
  const subTrigger = page.getByRole('menuitem', { name: '发送后滚动' });
  await subTrigger.focus();
  await page.keyboard.press('ArrowRight');
  const submenu = page.getByRole('menu', { name: '发送后滚动' });
  await expect(submenu).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(submenu).not.toBeVisible();
  await expect(main).toBeVisible();
  await expect(subTrigger).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(main).not.toBeVisible();
  await expect(page.getByRole('button', { name: '指令与功能' })).toBeFocused();
});

test('Base 指令菜单子菜单：hover 展开，子项选择一次回调并整树关闭', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: '指令与功能' }).click();
  await page.getByRole('menuitem', { name: '发送后滚动' }).hover();
  const submenu = page.getByRole('menu', { name: '发送后滚动' });
  await expect(submenu).toBeVisible();
  await page.getByRole('menuitemradio', { name: '回到最新消息' }).click();
  expect(await page.evaluate(() => (window as unknown as { __pilot: { scrollSelects: number } }).__pilot.scrollSelects)).toBe(1);
  await expect(submenu).not.toBeVisible();
  await expect(page.getByRole('menu', { name: '指令与功能' })).not.toBeVisible();
  await expect(page.getByRole('button', { name: '指令与功能' })).toBeFocused();
});

test('Base 指令菜单 → Skills：交接焦点、键盘操作与 Escape 回编辑器', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: '指令与功能' }).click();
  await page.getByRole('menuitem', { name: /技能/ }).click();
  const skills = page.getByRole('dialog', { name: '技能' });
  await expect(skills).toBeVisible();
  await expect(page.getByRole('menu', { name: '指令与功能' })).not.toBeVisible();
  // SkillsMenu 自身 autoFocus 关闭按钮，面板获得并保持焦点。
  await expect(page.getByRole('button', { name: '关闭技能列表' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('[data-skill-option]').first()).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(skills).not.toBeVisible();
  await expect(page.getByRole('button', { name: '编辑器占位' })).toBeFocused();
});

test('Base Skills 目录：空列表与失败重试状态', async ({ page }) => {
  await init(page);
  await page.evaluate(() => { (window as unknown as { __skillMode?: string }).__skillMode = 'empty'; });
  await page.getByRole('button', { name: '指令与功能' }).click();
  await page.getByRole('menuitem', { name: /技能/ }).click();
  await expect(page.getByText('当前工作区没有可用技能。')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '技能' })).not.toBeVisible();

  await page.evaluate(() => { (window as unknown as { __skillMode?: string }).__skillMode = 'error'; });
  await page.getByRole('button', { name: '指令与功能' }).click();
  await page.getByRole('menuitem', { name: /技能/ }).click();
  await expect(page.getByRole('button', { name: '重试加载技能' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '技能' })).not.toBeVisible();
});

test('Base dialog 内嵌菜单：Escape 只关子层，再关菜单，不误关 dialog', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: '打开Base弹窗' }).click();
  const dialog = page.getByRole('dialog', { name: 'Base组合弹窗' });
  await expect(dialog).toBeVisible();
  await page.getByRole('button', { name: 'Base弹窗内菜单' }).click();
  const menu = page.getByRole('menu', { name: 'Base弹窗内菜单' });
  await expect(menu).toBeVisible();

  await page.getByRole('menuitem', { name: 'Base弹窗内子菜单' }).focus();
  await page.keyboard.press('ArrowRight');
  const submenu = page.getByRole('menu', { name: 'Base弹窗内子菜单' });
  await expect(submenu).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(submenu).not.toBeVisible();
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(menu).not.toBeVisible();
  await expect(dialog).toBeVisible();
});

test('Base 菜单：375px resize 后定位不溢出', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: '指令与功能' }).click();
  const main = page.getByRole('menu', { name: '指令与功能' });
  await expect(main).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  for (const current of [main]) {
    await expect.poll(async () => (await current.boundingBox())!.x).toBeGreaterThanOrEqual(0);
    await expect.poll(async () => {
      const bounds = await current.boundingBox();
      return bounds!.x + bounds!.width;
    }).toBeLessThanOrEqual(375);
    const bounds = await current.boundingBox();
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(812);
  }
});

test('Base 禁用项：压缩上下文不执行任何回调', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: '指令与功能' }).click();
  const compact = page.getByRole('menuitem', { name: /压缩上下文/ });
  await expect(compact).toBeDisabled();
  await compact.evaluate(element => (element as HTMLElement).click());
  const calls = await page.evaluate(() => (window as unknown as { __pilot: { compactCalls: number } }).__pilot.compactCalls ?? 0);
  expect(calls).toBe(0);
});

test('Base 会话切换：打开菜单在切换后关闭', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: '指令与功能' }).click();
  const main = page.getByRole('menu', { name: '指令与功能' });
  await expect(main).toBeVisible();
  await page.getByRole('button', { name: '切换会话' }).click();
  await expect(main).not.toBeVisible();
});

test('Base 主题菜单：外部点击尊重用户点击目标，不强制回触发器', async ({ page }) => {
  await init(page);
  const button = page.getByRole('button', { name: /主题：/ });
  await button.click();
  const menu = page.getByRole('menu', { name: '主题选择' });
  await expect(menu).toBeVisible();
  await page.mouse.click(700, 400);
  await expect(menu).not.toBeVisible();
  await expect(button).not.toBeFocused();
});

test('Base Skills：挂起加载与失败重试', async ({ page }) => {
  await init(page);
  await page.evaluate(() => { (window as unknown as { __skillMode?: string }).__skillMode = 'pending'; });
  await page.getByRole('button', { name: '指令与功能' }).click();
  await page.getByRole('menuitem', { name: /技能/ }).click();
  await expect(page.getByText('正在查找可用技能…')).toBeVisible();

  await page.evaluate(() => { (window as unknown as { __skillMode?: string }).__skillMode = 'error'; });
  await page.getByRole('button', { name: '关闭技能列表' }).click();
  await page.getByRole('button', { name: '指令与功能' }).click();
  await page.getByRole('menuitem', { name: /技能/ }).click();
  await expect(page.getByRole('button', { name: '重试加载技能' })).toBeVisible();
  await page.getByRole('button', { name: '重试加载技能' }).click();
  await expect(page.getByRole('button', { name: '重试加载技能' })).toBeVisible();
  await page.evaluate(() => { (window as unknown as { __skillMode?: string }).__skillMode = 'normal'; });
  await page.getByRole('button', { name: '重试加载技能' }).click();
  await expect(page.locator('[data-skill-option]').first()).toBeVisible();
});

test('Base 窄屏：父子菜单在 375px 下均不溢出', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: '指令与功能' }).click();
  const main = page.getByRole('menu', { name: '指令与功能' });
  await expect(main).toBeVisible();
  await page.getByRole('menuitem', { name: '发送后滚动' }).focus();
  await page.keyboard.press('ArrowRight');
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

test('Base dialog 内嵌：选择后整树关闭、焦点回触发器、dialog 保持打开', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: '打开Base弹窗' }).click();
  const dialog = page.getByRole('dialog', { name: 'Base组合弹窗' });
  await expect(dialog).toBeVisible();
  await page.getByRole('button', { name: 'Base弹窗内菜单' }).click();
  await page.getByRole('menuitem', { name: 'Base弹窗内子菜单' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('menu', { name: 'Base弹窗内子菜单' })).toBeVisible();
  await page.getByRole('menuitemradio', { name: '回到最新消息' }).click();
  await expect(page.getByRole('menu', { name: 'Base弹窗内菜单' })).not.toBeVisible();
  await expect(dialog).toBeVisible();
  await expect(page.getByRole('button', { name: 'Base弹窗内菜单' })).toBeFocused();
});

test('Base 主题菜单：Home/End 与首尾循环', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: /主题：/ }).click();
  const menu = page.getByRole('menu', { name: '主题选择' });
  await expect(menu).toBeVisible();
  await page.keyboard.press('End');
  await expect(page.getByRole('menuitemradio', { name: '深色', exact: true })).toBeFocused();
  await page.keyboard.press('Home');
  await expect(page.getByRole('menuitemradio', { name: '跟随系统', exact: true })).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect(page.getByRole('menuitemradio', { name: '深色', exact: true })).toBeFocused();
});

test('Base 主题菜单：外部点击后明确目标获得焦点', async ({ page }) => {
  await init(page);
  const button = page.getByRole('button', { name: /主题：/ });
  await button.click();
  const menu = page.getByRole('menu', { name: '主题选择' });
  await expect(menu).toBeVisible();
  const target = page.getByRole('button', { name: '外部焦点目标' });
  await target.click();
  await expect(menu).not.toBeVisible();
  await expect(target).toBeFocused();
  await expect(button).not.toBeFocused();
});

test('Base 会话切换：关闭效果独立于外部点击', async ({ page }) => {
  await init(page);
  await page.getByRole('button', { name: '指令与功能' }).click();
  const main = page.getByRole('menu', { name: '指令与功能' });
  await expect(main).toBeVisible();
  // 程序化 click 不产生 pointerdown，排除外部点击关闭路径，验证会话切换自身的关闭效果。
  await page.getByRole('button', { name: '切换会话' }).evaluate(element => (element as HTMLElement).click());
  await expect(main).not.toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __sessionCloseReason?: string }).__sessionCloseReason)).toBe('session-switch');
});
