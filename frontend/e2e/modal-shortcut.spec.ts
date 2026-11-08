import { expect, test } from '@playwright/test';
import { baseMocks, openHome } from './fixtures/service';

// 交互规则（Spec143BA）：有模态打开时 Cmd/Ctrl+K 不打开、不挂载全局会话搜索；
// 无模态时保留现有搜索能力，关闭后焦点恢复到触发元素。
// 复现形态备注：修复前 Sidebar 的 window keydown 无模态检查，搜索界面仍被挂载，
// 但 Chromium 的 top-layer 焦点守卫把焦点留在模态内，搜索界面被 top layer 遮挡，
// 用户感知为按键无反应。断言按 DOM 挂载判断，不用可见性代替存在性。
test.skip(({ viewport }) => (viewport?.width ?? 0) <= 760, '仅桌面布局');

test('原生模态打开时，全局搜索快捷键不挂载搜索且焦点留在模态内', async ({ page }) => {
  const service = await openHome(page, baseMocks());
  // 打开原生 ProjectPicker（showModal → top layer）。
  await page.getByRole('button', { name: /项目与位置/ }).click();
  const picker = page.getByRole('dialog');
  await expect(picker).toBeVisible();

  await page.keyboard.press('ControlOrMeta+KeyK');

  await expect(page.getByRole('combobox', { name: '搜索会话标题' })).toHaveCount(0);
  const focusInsidePicker = await page.evaluate(() => !!document.activeElement?.closest('dialog'));
  expect(focusInsidePicker, '模态打开时按搜索快捷键后焦点应留在模态内').toBe(true);
  service.expectNoUnexpectedRequests();
});

test('模态关闭后再按快捷键，搜索正常打开', async ({ page }) => {
  await openHome(page, baseMocks());
  await page.getByRole('button', { name: /项目与位置/ }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();

  await page.keyboard.press('ControlOrMeta+KeyK');
  const search = page.getByRole('combobox', { name: '搜索会话标题' });
  await expect(search).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(search).toHaveCount(0);
});

test('无模态时按快捷键正常打开搜索并可关闭', async ({ page }) => {
  const service = await openHome(page, baseMocks());
  await page.keyboard.press('ControlOrMeta+KeyK');
  const search = page.getByRole('combobox', { name: '搜索会话标题' });
  await expect(search).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(search).toHaveCount(0);
  service.expectNoUnexpectedRequests();
});

test('点击搜索按钮打开搜索，关闭后焦点回到按钮', async ({ page }) => {
  await openHome(page, baseMocks());
  const trigger = page.getByRole('button', { name: '搜索会话' });
  await trigger.click();
  const search = page.getByRole('combobox', { name: '搜索会话标题' });
  await expect(search).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(search).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

// 焦点轨迹实测（build/spec143ba/focus-probe，headless shell 与完整 Chrome 一致）：
// 模态首控件上首次 Shift+Tab → activeElement=BODY 且 document.hasFocus()=false（焦点离开
// 网页，进入浏览器 UI 导航），背景控件并未获得焦点；再次 Shift+Tab 焦点回到模态内部。
// 因此圈定验收不依赖「activeElement 始终在 dialog 内」这一过强断言，而验证：
// 背景控件不获得焦点、模态保持打开、连续导航焦点回到模态内部。
test('模态打开时连续 Shift+Tab 不激活背景控件，导航后焦点回到模态内部', async ({ page }) => {
  await openHome(page, baseMocks());
  await page.getByRole('button', { name: '搜索会话' }).click();
  const search = page.getByRole('combobox', { name: '搜索会话标题' });
  await expect(search).toBeFocused();

  const steps: { active: string; hasFocus: boolean; dialogOpen: boolean; backgroundFocused: boolean }[] = [];
  const snapshot = () => page.evaluate(() => ({
    active: document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.tagName ?? 'null',
    hasFocus: document.hasFocus(),
    dialogOpen: !!document.querySelector('dialog[open]'),
    backgroundFocused: document.activeElement?.getAttribute('aria-label') === '新建会话',
  }));
  for (let index = 0; index < 3; index++) {
    await page.keyboard.press('Shift+Tab');
    await page.waitForTimeout(120);
    steps.push(await snapshot());
  }
  expect(steps.every(step => !step.backgroundFocused), '背景控件不得获得焦点').toBe(true);
  expect(steps.every(step => step.dialogOpen), '模态应持续打开').toBe(true);
  expect(steps.some(step => step.active === '关闭搜索' || step.active === '搜索会话标题' || step.active === '匹配会话'),
    '连续导航后焦点应回到模态内部').toBe(true);
  // 焦点离开网页的瞬间（hasFocus=false）允许存在，属浏览器 UI 导航而非背景激活。
});
