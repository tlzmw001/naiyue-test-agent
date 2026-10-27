import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest';
import ProjectWorkspace from './ProjectWorkspace';
import { WebApi } from './api';
import type { SessionRow, SessionView } from './types';

// jsdom models open state only; native dialog focus is checked in the browser.
beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.setAttribute('open', ''); } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value() { this.removeAttribute('open'); } });
});
afterEach(cleanup);
afterAll(() => { delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal; delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close; });
const a = '11111111-1111-1111-1111-111111111111', b = '22222222-2222-2222-2222-222222222222';
function fixture() {
  const apis = new Map<string, WebApi>();
  function api(scope: string): WebApi {
    if (apis.has(scope)) return apis.get(scope)!;
    const value = new WebApi('test-token', scope, 'f'.repeat(32)); apis.set(scope, value);
    const view: SessionView = { session_id: 'same', title: `会话 ${scope === a ? 'A' : 'B'}`, model_id: 'A', workspace_root: `/projects/${scope}`, state: 'ready',
      updated_at: '2026-10-24T10:00:00Z', last_run_status: null, messages: [], busy: false, can_run: true, has_compaction: false,
      active_run_id: null, latest_run_id: null, active_tools: [], permission_mode: 'Default', manual_review_required: false, pending_approval: false, live: null };
    vi.spyOn(value, 'forScope').mockImplementation(api);
    vi.spyOn(value, 'projects').mockResolvedValue({ items: [a, b].map((id, index) => ({ project_id: id, name: `项目 ${index ? 'B' : 'A'}`,
      source: { path: `/projects/${id}` }, trusted: true, revision: 1, lifecycle: 'active', availability: 'available' })), default_parent_dir: null });
    vi.spyOn(value, 'directory').mockImplementation(async scopeId => scopeId === 'chats' ? [] : [{ ...view, title: `会话 ${scopeId === a ? 'A' : 'B'}` }]);
    vi.spyOn(value, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
    vi.spyOn(value, 'sessions').mockResolvedValue(scope === 'chats' ? [] : [view]);
    vi.spyOn(value, 'prepare').mockResolvedValue(view);
    vi.spyOn(value, 'session').mockResolvedValue(view);
    vi.spyOn(value, 'subagents').mockResolvedValue([]);
    vi.spyOn(value, 'context').mockRejectedValue(new Error('unused'));
    vi.spyOn(value, 'backgroundTasks').mockResolvedValue({ items: [], active_count: 0, revision: 'empty', terminal_revision: 0, next_before: null });
    return value;
  }
  return { root: api('chats'), api, apis };
}

it('keeps ordinary conversations and projects as peers without sidebar project creation', async () => {
  const { root } = fixture(); render(<ProjectWorkspace api={root} />);
  await screen.findByRole('button', { name: '进入项目 A' });
  const sidebar = screen.getByRole('complementary', { name: '会话导航' });
  const groups = Array.from(sidebar.querySelector('nav')!.children).filter(child => child.getAttribute('role') === 'group');
  expect(groups.map(group => group.getAttribute('aria-label'))).toEqual(['普通对话', '项目 A', '项目 B']);
  expect(within(sidebar).queryByText('项目', { exact: true })).toBeNull();
  expect(within(sidebar).queryByRole('button', { name: '添加项目' })).toBeNull();
  const search = within(sidebar).getByRole('button', { name: '搜索会话' });
  expect(search.compareDocumentPosition(within(sidebar).getByRole('button', { name: '新建会话' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '项目与位置，当前：普通对话' }));
  expect(await screen.findByRole('dialog', { name: '项目与位置' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '选择已有文件夹' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '创建新文件夹' })).toBeTruthy();
});

it('clicking the current project name folds and reopens its sessions without changing selection', async () => {
  const { root, api } = fixture(); render(<ProjectWorkspace api={root} />);
  fireEvent.click(await screen.findByRole('button', { name: '进入项目 A' }));
  const session = await screen.findByRole('button', { name: '会话 A A' });
  fireEvent.click(session);
  await waitFor(() => expect(api(a).prepare).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button', { name: '进入项目 A' }));
  expect(screen.getByRole('button', { name: '展开项目 A' }).getAttribute('aria-expanded')).toBe('false');
  expect(screen.queryByRole('button', { name: '会话 A A' })).toBeNull();
  expect(screen.getByRole('status', { name: '当前项目' }).textContent).toContain('项目 A');
  fireEvent.click(screen.getByRole('button', { name: '进入项目 A' }));
  expect(screen.getByRole('button', { name: '收起项目 A' }).getAttribute('aria-expanded')).toBe('true');
  expect(await screen.findByRole('button', { name: '会话 A A' })).toBeTruthy();
  expect(api(a).prepare).toHaveBeenCalledTimes(1);
});

it('shows saved pin markers while browsing another scope without opening its host', async () => {
  const { root, apis } = fixture();
  vi.mocked(root.directory).mockResolvedValue([{ session_id: 'pinned', title: '已保存的任务', pinned: true, model_id: 'A', execution_binding: 'sandbox',
    workspace_root: '/projects/A', state: 'ready', updated_at: '2026-10-25T10:00:00Z', last_run_status: 'completed' }]);
  render(<ProjectWorkspace api={root} />);
  fireEvent.click(await screen.findByRole('button', { name: '展开项目 A' }));
  const row = await screen.findByRole('button', { name: '已保存的任务 A' });
  expect(within(row).getByRole('img', { name: '已置顶' })).toBeTruthy();
  expect(within(row).getByLabelText('执行位置：Docker 沙箱')).toBeTruthy();
  expect(within(row).getByLabelText('任务状态：已完成')).toBeTruthy();
  expect([...apis.keys()]).toEqual(['chats']);
});

it('connects current approval facts to the sidebar while another scope with the same session ID stays independent', async () => {
  const { root, api } = fixture(), view = await api(a).session('same');
  vi.mocked(api(a).prepare).mockResolvedValue({ ...view, pending_approval: true });
  render(<ProjectWorkspace api={root} />);
  fireEvent.click(await screen.findByRole('button', { name: '展开项目 A' }));
  fireEvent.click(await screen.findByRole('button', { name: '会话 A A' }));
  expect(await screen.findByLabelText('任务状态：等待审批')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '进入项目 B' }));
  expect(await screen.findByRole('button', { name: '会话 B A' })).toBeTruthy();
  expect(screen.queryByLabelText('任务状态：等待审批')).toBeNull();
});

it.each(['chats', a])('keeps pinning and manual order on the selected %s client', async scopeId => {
  const { root, api } = fixture(), client = api(scopeId), other = api(scopeId === 'chats' ? a : 'chats');
  let rows: SessionRow[] = ['one', 'two', 'three'].map((id, index) => ({ session_id: id, title: `任务${index + 1}`, pinned: false,
    model_id: 'A', workspace_root: null, state: 'ready', updated_at: '2026-10-25T10:00:00Z', last_run_status: null }));
  vi.mocked(client.sessions).mockImplementation(async () => [...rows]);
  const update = vi.spyOn(client, 'updateSession').mockImplementation(async (id, change) => {
    rows = rows.map(row => row.session_id === id ? { ...row, ...change } : row);
    rows.sort((left, right) => Number(!!right.pinned) - Number(!!left.pinned));
    return rows.find(row => row.session_id === id)!;
  });
  const reorder = vi.spyOn(client, 'reorderSessions').mockImplementation(async ids => {
    rows = ids.map(id => rows.find(row => row.session_id === id)!); return rows;
  });
  const foreignUpdate = vi.spyOn(other, 'updateSession'), foreignOrder = vi.spyOn(other, 'reorderSessions');
  render(<ProjectWorkspace api={root} />);
  if (scopeId !== 'chats') fireEvent.click(await screen.findByRole('button', { name: '进入项目 A' }));
  await screen.findByRole('button', { name: '任务1 A' });
  fireEvent.click(screen.getByRole('button', { name: '任务1的更多操作' }));
  fireEvent.click(screen.getByRole('menuitem', { name: '置顶' }));
  await waitFor(() => expect(within(screen.getByRole('button', { name: '任务1 A' })).getByRole('img', { name: '已置顶' })).toBeTruthy());
  expect(update).toHaveBeenCalledExactlyOnceWith('one', { pinned: true });
  fireEvent.click(screen.getByRole('button', { name: '任务1的更多操作' }));
  fireEvent.click(screen.getByRole('menuitem', { name: '取消置顶' }));
  await waitFor(() => expect(within(screen.getByRole('button', { name: '任务1 A' })).queryByRole('img')).toBeNull());
  const source = screen.getByRole('button', { name: '任务3 A' }), target = screen.getByRole('button', { name: '任务1 A' });
  const original = document.elementFromPoint;
  document.elementFromPoint = () => target.closest('[data-session-id]')!;
  try {
    fireEvent(source, new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 50, clientY: 200 }));
    fireEvent(source, new MouseEvent('pointermove', { bubbles: true, clientX: 50, clientY: 20 }));
    fireEvent(source, new MouseEvent('pointerup', { bubbles: true, clientX: 50, clientY: 20 }));
  } finally { document.elementFromPoint = original; }
  await waitFor(() => expect(reorder).toHaveBeenCalledExactlyOnceWith(['three', 'one', 'two']));
  expect(foreignUpdate).not.toHaveBeenCalled(); expect(foreignOrder).not.toHaveBeenCalled();
  expect(client.prepare).not.toHaveBeenCalled(); expect(client.session).not.toHaveBeenCalled();
});

it('uses the same decorative chevron for project, execution and permission controls and follows open state', async () => {
  const { root, api } = fixture(), project = api(a);
  vi.spyOn(project, 'sessionControlOptions').mockResolvedValue({ tools: [], permission_modes: ['Default'], default_permission_mode: 'Default', default_active_tools: [] });
  vi.spyOn(project, 'executionOptions').mockResolvedValue({ host_available: true, sandbox_available: true, error_code: null,
    requires_trust: false, project_revision: 1, network: 'none', untracked: [], blocked: [] });
  const create = vi.spyOn(project, 'createSelected');
  render(<ProjectWorkspace api={root} />);
  fireEvent.click(await screen.findByRole('button', { name: '进入项目 A' }));
  const permission = await screen.findByRole('button', { name: '权限模式：Default' });
  const projectButton = screen.getByRole('button', { name: '项目与位置，当前：项目 A' });
  const execution = screen.getByRole('button', { name: '执行位置：此计算机' });
  const arrows = [projectButton, execution, permission].map(button => button.querySelector('svg[data-chevron]'));
  expect(arrows.every(arrow => arrow !== null)).toBe(true);
  expect(new Set(arrows.map(arrow => arrow?.outerHTML)).size).toBe(1);
  expect(arrows.every(arrow => arrow?.getAttribute('aria-hidden') === 'true')).toBe(true);
  fireEvent.click(execution);
  expect(execution.querySelector('[data-chevron="up"]')).not.toBeNull();
  fireEvent.keyDown(screen.getByRole('dialog', { name: '选择执行位置' }), { key: 'Escape' });
  expect(execution.querySelector('[data-chevron="down"]')).not.toBeNull();
  fireEvent.click(permission);
  expect(permission.querySelector('[data-chevron="up"]')).not.toBeNull();
  fireEvent.keyDown(screen.getByRole('dialog', { name: '权限模式' }), { key: 'Escape' });
  expect(permission.querySelector('[data-chevron="down"]')).not.toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '收起项目 A' }));
  expect(screen.getByRole('button', { name: '展开项目 A' }).querySelector('[data-chevron="right"]')).not.toBeNull();
  expect(create).not.toHaveBeenCalled();
});

it('expands metadata without opening projects and preserves scope-local drafts with equal session IDs', async () => {
  const { root, api, apis } = fixture();
  render(<ProjectWorkspace api={root} />);
  fireEvent.click(await screen.findByRole('button', { name: '展开项目 A' }));
  expect(await screen.findByRole('button', { name: '会话 A A' })).toBeTruthy();
  expect([...apis.keys()]).toEqual(['chats']);
  fireEvent.click(screen.getByRole('button', { name: '会话 A A' }));
  await waitFor(() => expect(api(a).prepare).toHaveBeenCalledWith('same'));
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: 'A 的草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '展开项目 B' }));
  fireEvent.click(await screen.findByRole('button', { name: '会话 B A' }));
  await waitFor(() => expect(api(b).prepare).toHaveBeenCalledWith('same'));
  expect((screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement).value).toBe('');
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: 'B 的草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '进入项目 A' }));
  expect((await screen.findByRole('textbox', { name: '消息' }) as HTMLTextAreaElement).value).toBe('A 的草稿');
  expect(screen.getByRole('status', { name: '当前项目' }).textContent).toContain('项目 A');
  expect(api(a).prepare).toHaveBeenCalledTimes(1);
  expect(root.directory).toHaveBeenCalledWith(a, expect.any(AbortSignal));
});

it('shows empty, unavailable, untrusted and retryable errors without fabricating sessions', async () => {
  const { root } = fixture();
  vi.mocked(root.projects).mockResolvedValue({ items: [{ project_id: a, name: '未信任项目', source: { path: '/gone' }, trusted: false, revision: 1,
    lifecycle: 'active', availability: 'missing' }], default_parent_dir: null });
  vi.mocked(root.directory).mockRejectedValueOnce(new Error('offline')).mockResolvedValue([]);
  render(<ProjectWorkspace api={root} />);
  expect(await screen.findByText('未信任 · 目录不存在')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '展开未信任项目' }));
  const group = screen.getByRole('group', { name: '未信任项目' });
  expect(await within(group).findByRole('button', { name: '重试会话目录' })).toBeTruthy();
  fireEvent.click(within(group).getByRole('button', { name: '重试会话目录' }));
  expect(await within(group).findByText('还没有历史会话')).toBeTruthy();
});

it('detaches only observation, resumes live state with a read, and leaves execution commands untouched', async () => {
  const { root, api } = fixture();
  const left = api(a), original = await left.session('same');
  const live = { run_id: 'r', session_id: 'same', status: 'running' as const, messages: [], done: false,
    last_sequence: 0, incomplete: false, manual_review_required: false };
  vi.mocked(left.prepare).mockResolvedValue({ ...original, busy: true, live });
  vi.mocked(left.session).mockResolvedValue({ ...original, busy: true, live });
  const signals: AbortSignal[] = [];
  const events = vi.spyOn(left, 'events').mockImplementation(async (_id, _after, signal) => {
    signals.push(signal);
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true }));
  });
  const stop = vi.spyOn(left, 'control'), recover = vi.spyOn(left, 'recover'), start = vi.spyOn(left, 'start');
  render(<ProjectWorkspace api={root} />);
  fireEvent.click(await screen.findByRole('button', { name: '展开项目 A' }));
  fireEvent.click(await screen.findByRole('button', { name: '会话 A A' }));
  await waitFor(() => expect(events).toHaveBeenCalledTimes(1));
  const backgroundReads = vi.mocked(left.backgroundTasks).mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: '进入普通对话' }));
  expect(signals[0].aborted).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '进入项目 A' }));
  await waitFor(() => expect(events).toHaveBeenCalledTimes(2));
  expect(left.prepare).toHaveBeenCalledTimes(1);
  expect(left.session).toHaveBeenCalledTimes(2); // fixture read and readonly return synchronization
  expect(vi.mocked(left.backgroundTasks).mock.calls.length).toBe(backgroundReads + 1);
  expect(stop).not.toHaveBeenCalled(); expect(recover).not.toHaveBeenCalled(); expect(start).not.toHaveBeenCalled();
});

it('confines delayed selection responses to their original scope', async () => {
  const { root, api } = fixture();
  const view = await api(a).session('same');
  let finish!: (value: SessionView) => void;
  vi.mocked(api(a).prepare).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<ProjectWorkspace api={root} />);
  fireEvent.click(await screen.findByRole('button', { name: '展开项目 A' }));
  fireEvent.click(await screen.findByRole('button', { name: '会话 A A' }));
  await waitFor(() => expect(api(a).prepare).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: '进入项目 B' }));
  await act(async () => finish({ ...view, messages: [{ id: 'late', type: 'ai', content: 'A 的迟到历史', provisional: false, state: 'complete' }] }));
  expect(screen.getByRole('status', { name: '当前项目' }).textContent).toContain('项目 B');
  expect(screen.queryByRole('article', { name: /A 的迟到历史/ })).toBeNull();
  expect(screen.getByText('A 的迟到历史').closest('[hidden]')).toBeTruthy();
  expect((screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement).value).toBe('');
  fireEvent.click(screen.getByRole('button', { name: '进入项目 A' }));
  expect(screen.getByText('A 的迟到历史').closest('[hidden]')).toBeNull();
});

it('keeps page-wide theme preference consistent when returning to a visited project', async () => {
  localStorage.removeItem('naiyue.theme');
  const { root } = fixture(); render(<ProjectWorkspace api={root} />);
  fireEvent.click(await screen.findByRole('button', { name: '进入项目 A' }));
  fireEvent.click(await screen.findByRole('button', { name: '进入项目 B' }));
  fireEvent.click(screen.getByRole('button', { name: '主题：跟随系统' }));
  fireEvent.click(screen.getByRole('menuitemradio', { name: '深色' }));
  fireEvent.click(screen.getByRole('button', { name: '进入项目 A' }));
  expect(screen.getByRole('button', { name: '主题：深色' })).toBeTruthy();
  expect(document.documentElement.dataset.theme).toBe('dark');
  localStorage.removeItem('naiyue.theme');
});

it('refreshes navigation metadata while retaining the current conversation draft', async () => {
  const { root } = fixture(); render(<ProjectWorkspace api={root} />);
  fireEvent.change(await screen.findByRole('textbox', { name: '消息' }), { target: { value: '未发送内容' } });
  await screen.findByRole('button', { name: '进入项目 A' });
  const rows = (await root.projects()).items;
  vi.mocked(root.projects).mockResolvedValue({ items: [{ ...rows[0], name: '另一终端重命名的项目' }], default_parent_dir: null });
  fireEvent.click(screen.getByRole('button', { name: '项目与位置，当前：普通对话' }));
  fireEvent.click(screen.getByRole('button', { name: '刷新项目目录' }));
  fireEvent.click(screen.getByRole('button', { name: '关闭项目选择' }));
  expect(await screen.findByRole('button', { name: '进入另一终端重命名的项目' })).toBeTruthy();
  expect((screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement).value).toBe('未发送内容');
  expect(screen.queryByRole('button', { name: '进入项目 B' })).toBeNull();
});

it('does not poll conversation or background tasks for a hidden scope', async () => {
  vi.useFakeTimers();
  try {
    const { root, api } = fixture(); render(<ProjectWorkspace api={root} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.click(screen.getByRole('button', { name: '进入项目 A' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(api(a).backgroundTasks).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '进入普通对话' }));
    const count = vi.mocked(api(a).backgroundTasks).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(api(a).backgroundTasks).toHaveBeenCalledTimes(count);
    expect(api(a).session).not.toHaveBeenCalled();
  } finally { vi.useRealTimers(); }
});
