import { test } from '@playwright/test';
import { baseMocks, openHome } from './fixtures/service';

test('debug: 二级移除时刻的事件序列', async ({ page }) => {
  await openHome(page, baseMocks());
  await page.evaluate(() => {
    (window as unknown as { __log: string[] }).__log = [];
    const log = (window as unknown as { __log: string[] }).__log;
    const stamp = () => performance.now().toFixed(0);
    document.addEventListener('mousemove', event => {
      log.push(`${stamp()} mousemove @${(event.target as HTMLElement).textContent?.slice(0, 8)}`);
    }, true);
    document.addEventListener('mouseout', event => {
      log.push(`${stamp()} mouseout ${(event.target as HTMLElement).textContent?.slice(0, 8)} -> ${(event.relatedTarget as HTMLElement)?.textContent?.slice(0, 8) ?? 'null'}`);
    }, true);
    document.addEventListener('focusout', event => {
      log.push(`${stamp()} focusout ${(event.target as HTMLElement).textContent?.slice(0, 10)} -> ${(event.relatedTarget as HTMLElement)?.textContent?.slice(0, 10) ?? 'null'}`);
    }, true);
    const observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        for (const node of mutation.removedNodes) {
          if ((node as HTMLElement).id === 'command-scroll-menu') log.push(`${stamp()} REMOVED scroll-menu`);
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  });
  await page.getByRole('button', { name: '指令与功能' }).click();
  await page.getByRole('menuitem', { name: '云端任务' }).waitFor();
  await page.getByRole('menuitem', { name: '发送后滚动' }).hover();
  await page.waitForTimeout(300);
  const radio = page.getByRole('menuitemradio', { name: '回到最新消息' });
  const box = await radio.boundingBox();
  await page.mouse.move(box!.x + 20, box!.y + 10);
  await page.waitForTimeout(400);
  const result = await page.evaluate(() => ({
    log: (window as unknown as { __log: string[] }).__log,
    submenuExists: document.querySelectorAll('[aria-label="发送后滚动"]').length,
  }));
  console.log('RESULT:', JSON.stringify(result.log, null, 0).replace(/","/g, '"\n"'));
  console.log('exists:', result.submenuExists);
});
