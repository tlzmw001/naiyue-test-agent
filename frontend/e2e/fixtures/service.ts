import { expect } from '@playwright/test';
import type { Page, Route } from '@playwright/test';

/**
 * 浏览器回归的接口边界：真实前端页面 + 拦截层返回指定场景数据。
 * 只注册一个 /api/** 处理器，按声明顺序对 URL pathname 匹配（pathname 不含 query，
 * 天然区分 `/cloud-tasks` 列表与 `/cloud-tasks/B` 详情）；未声明的请求记录进
 * unexpected 并返回 404，测试末显式断言为空，避免兜底空响应掩盖接线变化。
 * 测试数据只存在于本目录，不修改产品鉴权逻辑。
 */

// 云端任务面板要求 projectId 为 UUID 且存在当前会话（CloudTasks.tsx 的 valid 校验），
// 因此连接使用项目 scope 并预置一条可选中会话。
export const project = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
export const sessionId = 's' + 'e'.repeat(31);
export const taskId = 'a'.repeat(32);
export const bindingId = 'B';

// 满足 browserConnection() 的格式校验（api.ts）：token 32–256 位、scope 为 UUID、instance 32 位 hex。
export function connectionHash() {
  return `#access_token=${'e2e0'.padEnd(48, '0')}&scope_id=${project}&instance_id=${'0'.repeat(32)}`;
}

// 基础任务与 CloudTasks.test.tsx 的 fixture 同形状；后台命令阻塞场景额外带 reclaim_blockers。
const baseTask = {
  task_id: taskId, project_id: project, input_id: 'b'.repeat(64), generation: 1, revision: 1,
  lifecycle: 'ready', runner_phase: 'ready', environment_phase: 'backend_ready', sandbox_id: 'container',
  active_run_id: null, last_run_id: null, last_error_code: null, last_quiescent_at: 1,
  environment_operation_id: null, control_operation_id: null, artifact_operation_id: null,
  runner_model_ids: ['A'], reclaim_blockers: [],
};

export const backgroundCommandsTask = { ...baseTask, last_run_id: 'c'.repeat(32), reclaim_blockers: ['background_commands'] };
export const backgroundCommandsBinding = {
  binding_id: bindingId, local_session_id: sessionId, profile_id: 'work', instance_id: 'I',
  task_id: taskId, input_id: 'b'.repeat(64), revision: 1, preparation: 'prepared', retained: true,
  create_command_id: 'C', cloud: backgroundCommandsTask,
};

const models = {
  default_model_id: 'A', model_ids: ['A', 'B'],
  thinking_options: { A: { default_level: 'low', supported_levels: ['low', 'high'] } },
};

const taskModels = {
  default_model_id: 'A', model_ids: ['A'],
  thinking_options: { A: { default_level: 'low', supported_levels: ['low', 'high'] } },
};

const snapshot = {
  task: backgroundCommandsTask, cursor: 's1',
  archive: { epoch: 'e2e-history', coverage_seq: 1,
    messages: [{ id: 'm1', type: 'ai', content: '云端任务已就绪。', provisional: false, state: 'complete' }],
    next_cursor: null, thread: { thread_id: 'root' } },
  live: { epoch: 'e2e-live', seq: 1, replace: true, available: true, capture_incomplete: false, messages: [], tools: [], threads: [] },
};

const streamBody = 'event: snapshot\nid: s1\ndata: ' + JSON.stringify(snapshot) + '\n\n'
  + 'event: heartbeat\ndata: {}\n\n';

export interface Mock {
  path: string;
  /** 缺省时匹配任意方法；声明后仅该方法的请求命中（同一路径可按方法区分）。 */
  method?: string;
  reply: (route: Route) => Promise<unknown> | unknown;
}

/** JSON 响应声明；path 为 URL pathname（不含 query）。 */
export const json = (path: string, data: unknown): Mock =>
  ({ path, reply: route => route.fulfill({ contentType: 'application/json', json: data }) });

/** 首屏（ProjectWorkspace → App → 会话列表含一条可选中会话）的最小声明。 */
export function baseMocks(): Mock[] {
  const scope = `/api/v1/scopes/${project}`;
  return [
    json('/api/v1/projects', { items: [{
      project_id: project, name: '验收项目', source: { path: '/tmp/e2e-acceptance' },
      lifecycle: 'active', trusted: true, revision: 1, availability: null,
    }], default_parent_dir: null }),
    json(`${scope}/sessions`, [...historyRows, sessionRow]),
    json(`${scope}/navigation/${project}/sessions`, [...historyRows, sessionRow]),
    json(`${scope}/models`, models),
    json(`${scope}/sessions/${sessionId}/prepare`, sessionView),
    json(`${scope}/sessions/${sessionId}`, sessionView),
    json(`${scope}/sessions/${sessionId}/subagents`, []),
    json(`${scope}/sessions/${sessionId}/context`,
      { session_id: sessionId, current: { basis: 'unavailable', estimate: null }, latest_request: null }),
    json(`${scope}/session-controls`,
      { tools: [], permission_modes: ['Default'], default_permission_mode: 'Default', default_active_tools: [] }),
    json(`${scope}/background-tasks`, { items: [], next_before: null, active_count: 0, revision: 'r1', terminal_revision: 1 }),
    json('/api/v1/directories', { path: '/tmp/e2e-acceptance', parent: null, separator: '/', identity: 'e2e-root',
      items: [], next_offset: null, positions: [], default_parent_dir: null, default_error: null }),
  ];
}

/** 云端任务列表 + 任务详情页（CloudTaskDetail 及其子面板）的最小声明。
 *  CloudApi 走 `/api/client/v1` 前缀（api.ts 的 'client' 通道）。 */
export function cloudTaskMocks(extra: Mock[] = []): Mock[] {
  const client = '/api/client/v1';
  const bid = `/api/client/v1/cloud-tasks/${bindingId}`;
  return [
    json(`${client}/cloud-profiles`, [{ profile_id: 'work', template_ids: ['python'], default_template: 'python' }]),
    json(`${client}/cloud-tasks`, [backgroundCommandsBinding]),
    json(bid, backgroundCommandsBinding),
    json(`${bid}/models`, taskModels),
    json(`${bid}/operations`, []),
    json(`${bid}/attachments`, []),
    json(`${bid}/queued-message`, null),
    json(`${bid}/snapshot`, snapshot),
    json(`${bid}/usage`, { recorded_calls: 0, capture: { capture_incomplete: false } }),
    json(`${bid}/results`, []),
    json(`${bid}/mcp`, { status: 'available', refresh_id: null, services: [] }),
    json(`${bid}/draft`, { draft_revision: 1, text: '', model_id: 'A', thinking_level: 'model_default' }),
    json(`${bid}/history/threads`, { threads: [], next_cursor: null }),
    json(`${bid}/delete-preview`, { instance_id: 'I', task_id: taskId, revision: 1, generation: 1,
      lifecycle: 'ready', scope: [], requires_stop: false, preview_digest: 'd'.repeat(64) }),
    json(`/api/client/v1/cloud-files`, { path: '', entries: [], next_offset: null }),
    { path: `${bid}/stream`,
      reply: route => route.fulfill({ contentType: 'text/event-stream', body: streamBody }) },
    ...extra,
  ];
}

export async function installService(page: Page, mocks: Mock[] = []) {
  const unexpected: string[] = [];
  const postRequests: string[] = [];
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (method !== 'GET') postRequests.push(`${method} ${url.pathname}`);
    const match = mocks.find(mock => url.pathname === mock.path && (!mock.method || mock.method === method));
    if (match) return match.reply(route);
    unexpected.push(`${method} ${url.pathname}${url.search}`);
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'unmocked_e2e_route' }) });
  });
  return {
    unexpected,
    postRequests,
    /** 会话准备（prepare）之外的写操作——确认框场景用于断言「取消/Escape 未发送命令」。 */
    commandPosts() {
      return postRequests.filter(path => !path.endsWith('/prepare'));
    },
    expectNoUnexpectedRequests() {
      expect(unexpected, '出现未声明的接口请求（接线变化或缺少场景声明）').toEqual([]);
    },
  };
}

export async function openHome(page: Page, mocks: Mock[]) {
  const service = await installService(page, mocks);
  await page.goto('http://127.0.0.1:4573/' + connectionHash());
  return service;
}

/** 首页 → 选中会话 → 云端任务面板 → 打开列表中唯一任务（详情含「云端任务控制」操作区）。
 *  extras 中的声明优先于默认云任务数据，用于替换单任务 binding。 */
export async function openCloudTask(page: Page, extras: Mock[] = []) {
  const service = await openHome(page, [...baseMocks(), ...extras, ...cloudTaskMocks()]);
  const prepare = page.waitForResponse(`**/sessions/${sessionId}/prepare`);
  await page.getByRole('button', { name: '验收会话 A' }).click();
  await prepare;
  await page.getByRole('button', { name: '指令与功能' }).click();
  await page.getByRole('menuitem', { name: '云端任务' }).click();
  await page.getByRole('button', { name: /任务 aaaaaaaa/ }).click();
  await expect(page.getByRole('button', { name: '删除任务' })).toBeVisible();
  return service;
}

const sessionRow = {
  session_id: sessionId, title: '验收会话', model_id: 'A', workspace_root: null, state: 'ready',
  updated_at: '2026-11-04T10:00:00Z', last_run_status: null,
};

// 多条历史会话让会话列表可滚动（背景滚动控制用例）。
const historyRows = Array.from({ length: 24 }, (_, index) => ({
  session_id: `h${String(index).padStart(2, '0')}${'e'.repeat(28)}`,
  title: `历史会话 ${index + 1}`, model_id: index % 2 ? 'B' : 'A', workspace_root: null, state: 'ready',
  updated_at: `2026-11-0${(index % 3) + 1}T0${index % 9}:00:00Z`, last_run_status: 'completed',
}));

// 与 history.test.tsx 的 SessionView fixture 同形状；空消息、无活跃运行。
const sessionView = {
  ...sessionRow,
  active_tools: [], permission_mode: 'Default', messages: [], busy: false, can_run: true,
  has_compaction: false, active_run_id: null, latest_run_id: null,
  manual_review_required: false, pending_approval: false, live: null,
  conversation_revision: 1, thinking_level: 'low', checkpoint_id: null,
};
