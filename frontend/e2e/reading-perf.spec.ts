import { expect, test, type Browser, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  MCP_RESULT_CHARS, ORDINARY_RESULT_CHARS, RUN_ID, SCOPE, SESSION_ID,
  startConversationServer, type ConversationServer, type ConversationServerOptions,
} from './fixtures/conversation-server';
import { openConversation } from './fixtures/reading-page';

// Spec143BE 性能基线：仅生产构建 + vite preview + 独立 HTTP/SSE 服务。
// 每个场景三次同条件采样；Node 操作耗时与浏览器时钟渲染耗时分别记录。
test.skip(process.env.READING_PROD !== '1', '性能基线仅在 READING_PROD=1 的生产 preview 中运行');

type LongTask = { name: string; startTime: number; duration: number };
type Timed = { nodeMs: number; browserRenderMs: number };
type ScenarioSample = Timed & { run: number; longTasks: LongTask[] };
type StreamBatch = Timed & { batch: number; marker: string; longTasks: LongTask[] };
type StreamSample = { run: number; batches: StreamBatch[]; finalize: Timed & { longTasks: LongTask[] } };
type MeasurementKind = 'history' | 'tool' | 'session' | 'stream' | 'final';

const perfBatches = Array.from({ length: 12 }, (_, index) =>
  `第 ${index + 1} 批流式内容。${'用于观察分批渲染。'.padEnd(180, '流')}\n\n- 阅读连续性检查 ${index + 1}`);

function round(value: number) {
  return Math.round(value * 1000) / 1000;
}

function median(values: number[]) {
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.floor(ordered.length / 2)];
}

function summary(values: number[]) {
  expect(values).toHaveLength(3);
  return { unit: 'ms', samples: values, median: round(median(values)), range: { min: Math.min(...values), max: Math.max(...values) } };
}

async function installPerformanceObserver(page: Page) {
  await page.addInitScript(() => {
    const target = window as unknown as { __readingPerf: { longTasks: LongTask[] } };
    target.__readingPerf = { longTasks: [] };
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) {
        target.__readingPerf.longTasks.push({ name: entry.name,
          startTime: Math.round(entry.startTime * 1000) / 1000,
          duration: Math.round(entry.duration * 1000) / 1000 });
      }
    }).observe({ entryTypes: ['longtask'] });
  });
}

async function takeLongTasks(page: Page) {
  return page.evaluate(() => {
    const target = window as unknown as { __readingPerf: { longTasks: LongTask[] } };
    const entries = target.__readingPerf.longTasks;
    target.__readingPerf.longTasks = [];
    return entries;
  });
}

async function armMeasurement(page: Page, kind: MeasurementKind, text: string, trigger?: 'history' | 'tool' | 'session') {
  await page.evaluate(({ kind, text, trigger }) => {
    const targetWindow = window as unknown as { __readingMeasurement: { done: Promise<number> } };
    let start: number | null = null;
    let settled = false;
    let resolveDone: (value: number) => void = () => {};
    const done = new Promise<number>(resolve => { resolveDone = resolve; });
    targetWindow.__readingMeasurement = { done };
    const visible = (element: Element | null) => !!element && element.getClientRects().length > 0;
    const summary = () => Array.from(document.querySelectorAll('summary')).find(element => element.textContent?.includes(text));
    const matches = () => {
      if (kind === 'history') return visible(document.querySelector('[data-message-id="m-089"]'));
      if (kind === 'session') return visible(document.querySelector('[data-message-id="session-b-answer"]'));
      if (kind === 'tool') {
        const card = summary()?.closest('details');
        return card?.open === true && visible(card.querySelector('[class*="toolDetail"] pre'));
      }
      const answer = document.querySelector('[data-message-id="assistant-live"]');
      if (!visible(answer) || !answer?.textContent?.includes(text)) return false;
      return kind === 'stream' || !answer.textContent.includes('正在生成') && !answer.textContent.includes('未完成内容');
    };
    const observer = new MutationObserver(() => {
      if (start === null || settled || !matches()) return;
      settled = true;
      observer.disconnect();
      requestAnimationFrame(() => requestAnimationFrame(() => resolveDone(performance.now() - start!)));
    });
    observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true });
    const begin = () => {
      if (start !== null) return;
      start = performance.now();
      if (matches()) {
        settled = true;
        observer.disconnect();
        requestAnimationFrame(() => requestAnimationFrame(() => resolveDone(performance.now() - start!)));
      }
    };
    if (!trigger) return begin();
    const button = trigger === 'history'
      ? Array.from(document.querySelectorAll('button')).find(element => element.textContent?.trim() === '加载更早消息')
      : trigger === 'session'
        ? document.querySelector('button[aria-label="切换目标会话 A"]')
        : summary();
    if (!button) throw new Error(`measurement_trigger_missing:${trigger}`);
    button.addEventListener('click', begin, { once: true, capture: true });
  }, { kind, text, trigger });
}

async function browserDuration(page: Page) {
  return round(await page.evaluate(() => {
    const target = window as unknown as { __readingMeasurement: { done: Promise<number> } };
    return target.__readingMeasurement.done;
  }));
}

async function openPerfConversation(page: Page, server: ConversationServer) {
  await installPerformanceObserver(page);
  await openConversation(page, { port: server.port });
  await expect(page.getByText('历史回答 119', { exact: false }).first()).toBeVisible();
  await takeLongTasks(page);
}

async function withServer<T>(options: ConversationServerOptions, work: (server: ConversationServer) => Promise<T>) {
  const server = startConversationServer(options);
  await server.listen();
  try {
    return await work(server);
  } finally {
    await server.close();
  }
}

async function historySample(page: Page, run: number): Promise<ScenarioSample> {
  return withServer({}, async server => {
    await openPerfConversation(page, server);
    await armMeasurement(page, 'history', 'm-089', 'history');
    const start = Date.now();
    await page.getByRole('button', { name: '加载更早消息' }).click();
    await expect(page.locator('[data-message-id="m-089"]')).toBeVisible();
    const nodeMs = Date.now() - start;
    const browserRenderMs = await browserDuration(page);
    return { run, nodeMs, browserRenderMs, longTasks: await takeLongTasks(page) };
  });
}

async function toolSample(page: Page, run: number): Promise<ScenarioSample> {
  return withServer({}, async server => {
    await openPerfConversation(page, server);
    const summaryLocator = page.locator('summary', { hasText: 'seq 1 2000' }).first();
    await armMeasurement(page, 'tool', 'seq 1 2000', 'tool');
    const start = Date.now();
    await summaryLocator.click();
    const card = page.locator('details', { has: summaryLocator });
    await expect(card).toHaveAttribute('open', '');
    await expect(card.locator('[class*="toolDetail"] pre').first()).toBeVisible();
    const nodeMs = Date.now() - start;
    const browserRenderMs = await browserDuration(page);
    return { run, nodeMs, browserRenderMs, longTasks: await takeLongTasks(page) };
  });
}

async function streamSample(page: Page, run: number): Promise<StreamSample> {
  return withServer({ live: true, batches: perfBatches, autoFirst: false }, async server => {
    await openPerfConversation(page, server);
    await expect.poll(() => server.calls.some(call => call.startsWith('SSE-OPEN'))).toBe(true);
    const batches: StreamBatch[] = [];
    for (let index = 0; index < perfBatches.length; index++) {
      const marker = `阅读连续性检查 ${index + 1}`;
      await armMeasurement(page, 'stream', marker);
      const start = Date.now();
      expect(server.advanceStream(), `第 ${index + 1} 批应可推进`).toBe(true);
      await expect(page.locator('[data-message-id="assistant-live"]')).toContainText(marker);
      const nodeMs = Date.now() - start;
      const browserRenderMs = await browserDuration(page);
      batches.push({ batch: index + 1, marker, nodeMs, browserRenderMs, longTasks: await takeLongTasks(page) });
    }

    const finalRun = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/scopes/${SCOPE}/runs/${RUN_ID}` && response.ok());
    const finalSession = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/scopes/${SCOPE}/sessions/${SESSION_ID}` && response.ok());
    await armMeasurement(page, 'final', '阅读连续性检查 12');
    const start = Date.now();
    server.finishStream();
    const answer = page.locator('[data-message-id="assistant-live"]');
    await expect(answer).toContainText('阅读连续性检查 12');
    await expect(answer).not.toContainText('正在生成');
    await expect(answer).not.toContainText('未完成内容');
    const [runResponse, sessionResponse] = await Promise.all([finalRun, finalSession]);
    const nodeMs = Date.now() - start;
    const browserRenderMs = await browserDuration(page);
    const finalRunBody = await runResponse.json();
    const finalSessionBody = await sessionResponse.json();
    expect(finalRunBody.done).toBe(true);
    expect(finalRunBody.messages.find((row: { id: string }) => row.id === 'assistant-live').content).toBe(perfBatches.join(''));
    expect(finalSessionBody.busy).toBe(false);
    expect(finalSessionBody.messages.find((row: { id: string }) => row.id === 'assistant-live').content).toBe(perfBatches.join(''));
    return { run, batches, finalize: { nodeMs, browserRenderMs, longTasks: await takeLongTasks(page) } };
  });
}

async function sessionSample(page: Page, run: number): Promise<ScenarioSample> {
  return withServer({}, async server => {
    await openPerfConversation(page, server);
    await armMeasurement(page, 'session', 'session-b-answer', 'session');
    const start = Date.now();
    await page.getByRole('button', { name: '切换目标会话 A', exact: true }).click();
    await expect(page.locator('[data-message-id="session-b-answer"]')).toContainText('切换目标会话的确定回答');
    const nodeMs = Date.now() - start;
    const browserRenderMs = await browserDuration(page);
    return { run, nodeMs, browserRenderMs, longTasks: await takeLongTasks(page) };
  });
}

async function withFreshPage<T>(browser: Browser, work: (page: Page) => Promise<T>) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  try {
    return await work(page);
  } finally {
    await context.close();
  }
}

function writeArtifact(browser: Browser, raw: {
  historyLoad: ScenarioSample[]; toolExpand: ScenarioSample[]; stream: StreamSample[]; sessionSwitch: ScenarioSample[];
}) {
  const streamFirst = raw.stream.map(sample => sample.batches[0]);
  const streamLaterNode = raw.stream.map(sample => round(median(sample.batches.slice(1).map(batch => batch.nodeMs))));
  const streamLaterRender = raw.stream.map(sample => round(median(sample.batches.slice(1).map(batch => batch.browserRenderMs))));
  const artifact = {
    schemaVersion: 1,
    status: 'complete',
    generatedAt: new Date().toISOString(),
    conditions: {
      build: 'production vite build; independent vite preview on 127.0.0.1:4573',
      fixture: 'independent node:http HTTP/SSE service on an ephemeral 127.0.0.1 port',
      browser: { name: browser.browserType().name(), version: browser.version(), headless: true },
      viewport: { width: 1440, height: 900 },
      samplesPerScenario: 3,
      isolation: 'new BrowserContext and Page per sample; cold browser storage and one init-script set per document',
      loadedData: {
        historyTotal: 120, initialHistoryRows: 1, historyPageSize: 30, historyRowsAfterOnePage: 31,
        staticToolCalls: 2, ordinaryResultChars: ORDINARY_RESULT_CHARS, mcpResultChars: MCP_RESULT_CHARS,
        streamBatches: perfBatches.length, streamBatchChars: perfBatches.map(batch => batch.length),
        streamTotalChars: perfBatches.join('').length, sessionSwitchTargetMessages: 1,
      },
    },
    timingDefinitions: {
      nodeMs: 'Date.now() immediately before the Playwright click/server advance until the deterministic DOM assertion (and final HTTP snapshots for stream finalization) completes.',
      browserRenderMs: 'performance.now() from the capturing click handler (or immediately before a server-driven batch) until the target DOM condition is observed and two requestAnimationFrame callbacks complete.',
      streamBatchSummary: 'Median of batches 2–12 within each run; batch 1 is reported separately. Every raw batch remains in rawSamples.stream.',
      longTasks: 'PerformanceObserver longtask entries collected after the initial conversation open; arrays are drained and recorded for every measured action.',
    },
    rawSamples: raw,
    summary: {
      historyLoadNodeMs: summary(raw.historyLoad.map(sample => sample.nodeMs)),
      historyLoadBrowserRenderMs: summary(raw.historyLoad.map(sample => sample.browserRenderMs)),
      toolExpandNodeMs: summary(raw.toolExpand.map(sample => sample.nodeMs)),
      toolExpandBrowserRenderMs: summary(raw.toolExpand.map(sample => sample.browserRenderMs)),
      streamFirstBatchNodeMs: summary(streamFirst.map(sample => sample.nodeMs)),
      streamFirstBatchBrowserRenderMs: summary(streamFirst.map(sample => sample.browserRenderMs)),
      streamLaterBatchMedianNodeMs: summary(streamLaterNode),
      streamLaterBatchMedianBrowserRenderMs: summary(streamLaterRender),
      streamFinalizeNodeMs: summary(raw.stream.map(sample => sample.finalize.nodeMs)),
      streamFinalizeBrowserRenderMs: summary(raw.stream.map(sample => sample.finalize.browserRenderMs)),
      sessionSwitchNodeMs: summary(raw.sessionSwitch.map(sample => sample.nodeMs)),
      sessionSwitchBrowserRenderMs: summary(raw.sessionSwitch.map(sample => sample.browserRenderMs)),
    },
    optimizationClaimed: false,
  };
  const output = resolve('../build/143be/perf.json');
  mkdirSync(resolve('../build/143be'), { recursive: true });
  writeFileSync(output, JSON.stringify(artifact, null, 2));
  console.log(`PERF_ARTIFACT=${output}`);
  console.log(`PERF_SUMMARY=${JSON.stringify(artifact.summary)}`);
}

test('生产基线：历史、展开、12 批流式/完成与会话切换，各 3 次', async ({ browser }) => {
  test.setTimeout(180_000);
  const raw: { historyLoad: ScenarioSample[]; toolExpand: ScenarioSample[]; stream: StreamSample[]; sessionSwitch: ScenarioSample[] } = {
    historyLoad: [], toolExpand: [], stream: [], sessionSwitch: [],
  };
  try {
    // 先验证稳定交互，再进入流式和切换；每轮使用全新页面，避免旧导航与 init script 串轮。
    for (let run = 1; run <= 3; run++) raw.historyLoad.push(await withFreshPage(browser, page => historySample(page, run)));
    for (let run = 1; run <= 3; run++) raw.toolExpand.push(await withFreshPage(browser, page => toolSample(page, run)));
    for (let run = 1; run <= 3; run++) raw.stream.push(await withFreshPage(browser, page => streamSample(page, run)));
    for (let run = 1; run <= 3; run++) raw.sessionSwitch.push(await withFreshPage(browser, page => sessionSample(page, run)));
    writeArtifact(browser, raw);
  } catch (error) {
    const output = resolve('../build/143be/perf-incomplete.json');
    mkdirSync(resolve('../build/143be'), { recursive: true });
    writeFileSync(output, JSON.stringify({ status: 'incomplete', generatedAt: new Date().toISOString(),
      error: error instanceof Error ? error.message : String(error), rawSamples: raw }, null, 2));
    throw error;
  }
});
