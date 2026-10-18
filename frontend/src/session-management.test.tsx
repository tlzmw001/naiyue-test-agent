import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import ModelMenu from './components/ModelMenu';
import { ApiError, WebApi } from './api';
import type { SessionRow, SessionView } from './types';

afterEach(cleanup);

function setup() {
  const api = new WebApi('fixture');
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  let rows: SessionRow[] = ['one', 'two', 'three'].map((id, index) => ({ session_id: id, title: `会话${index + 1}`,
    pinned: false, model_id: 'A', workspace_root: null, state: 'ready', updated_at: '', last_run_status: null }));
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockImplementation(async () => [...rows]);
  vi.spyOn(api, 'session').mockImplementation(async id => ({ ...rows.find(row => row.session_id === id),
    messages: [], busy: false, can_run: true, has_compaction: false, active_run_id: null, latest_run_id: null,
    active_tools: [], permission_mode: 'Default', manual_review_required: false, pending_approval: false, live: null }) as SessionView);
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], default_active_tools: [],
    permission_modes: ['Default'], default_permission_mode: 'Default' });
  vi.spyOn(api, 'updateSession').mockImplementation(async (id, change) => {
    rows = rows.map(row => row.session_id === id ? { ...row, ...change } : row);
    rows.sort((a, b) => Number(b.pinned) - Number(a.pinned));
    return rows.find(row => row.session_id === id)!;
  });
  vi.spyOn(api, 'reorderSessions').mockImplementation(async ids => {
    rows = ids.map(id => rows.find(row => row.session_id === id)!); return rows;
  });
  vi.spyOn(api, 'deleteSession').mockImplementation(async id => {
    rows = rows.filter(row => row.session_id !== id); return { session_id: id };
  });
  vi.spyOn(api, 'create'); vi.spyOn(api, 'start');
  render(<App api={api} />);
  return { api };
}

async function menu(title: string) {
  fireEvent.contextMenu(await screen.findByRole('button', { name: new RegExp(`^${title} A$`) }), { clientX: 100, clientY: 180 });
  return screen.findByRole('menu', { name: '会话操作' });
}

it('renames from the context menu without selecting or executing a conversation', async () => {
  const { api } = setup();
  await menu('会话1');
  expect(screen.getAllByRole('menuitem').map(item => item.textContent)).toEqual(['重命名', '置顶', '删除']);
  fireEvent.click(screen.getByRole('menuitem', { name: '重命名' }));
  const dialog = screen.getByRole('dialog', { name: '重命名会话' });
  const input = within(dialog).getByRole('textbox', { name: '会话名称' });
  expect((input as HTMLInputElement).value).toBe('会话1');
  fireEvent.change(input, { target: { value: '接口回归' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '保存名称' }));
  await screen.findByRole('button', { name: '接口回归 A' });
  expect(api.updateSession).toHaveBeenCalledExactlyOnceWith('one', { title: '接口回归' });
  expect(api.session).not.toHaveBeenCalled();
  expect(api.create).not.toHaveBeenCalled(); expect(api.start).not.toHaveBeenCalled();
});

it('pins and unpins using the ellipsis menu and keeps failures visible', async () => {
  const { api } = setup();
  fireEvent.click(await screen.findByRole('button', { name: '会话2的更多操作' }));
  fireEvent.click(screen.getByRole('menuitem', { name: '置顶' }));
  const pinned = await screen.findByRole('group', { name: '已置顶会话' });
  expect(within(pinned).getByRole('button', { name: /会话2 A/ })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '会话2的更多操作' }));
  vi.mocked(api.updateSession).mockRejectedValueOnce(new ApiError('session_save_unconfirmed'));
  fireEvent.click(screen.getByRole('menuitem', { name: '取消置顶' }));
  await screen.findByText(/操作未确认/);
  expect(screen.getByRole('group', { name: '已置顶会话' })).toBeTruthy();
  expect(api.updateSession).toHaveBeenCalledTimes(2);
});

it('persists mouse drag order without switching sessions or changing pin membership', async () => {
  const { api } = setup();
  const source = await screen.findByRole('button', { name: '会话3 A' });
  const target = screen.getByRole('button', { name: '会话1 A' });
  // JSDOM has no layout hit-testing; point the real Pointer handler at the target row.
  const hitTest = document.elementFromPoint;
  document.elementFromPoint = () => target.closest('[data-session-id]')!;
  try {
    fireEvent(source, new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 50, clientY: 200 }));
    fireEvent(source, new MouseEvent('pointermove', { bubbles: true, clientX: 50, clientY: 20 }));
    fireEvent(source, new MouseEvent('pointerup', { bubbles: true, clientX: 50, clientY: 20 }));
    fireEvent.click(source);
  } finally { document.elementFromPoint = hitTest; }
  await waitFor(() => expect(api.reorderSessions).toHaveBeenCalledExactlyOnceWith(['three', 'one', 'two']));
  expect(api.session).not.toHaveBeenCalled();
  expect(api.updateSession).not.toHaveBeenCalled();
});

it('requires deletion confirmation, then clears the selected session and its draft', async () => {
  const { api } = setup();
  fireEvent.click(await screen.findByRole('button', { name: '会话1 A' }));
  await waitFor(() => expect(api.session).toHaveBeenCalledWith('one'));
  const editor = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(editor, { target: { value: 'private draft' } });
  await menu('会话1');
  fireEvent.click(screen.getByRole('menuitem', { name: '删除' }));
  expect(api.deleteSession).not.toHaveBeenCalled();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '取消' }));
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(api.deleteSession).not.toHaveBeenCalled();
  await menu('会话1'); fireEvent.click(screen.getByRole('menuitem', { name: '删除' }));
  fireEvent.click(within(screen.getByRole('dialog', { name: '删除会话' })).getByRole('button', { name: '删除会话' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: '会话1 A' })).toBeNull());
  expect(api.deleteSession).toHaveBeenCalledExactlyOnceWith('one');
  expect((editor as HTMLTextAreaElement).value).toBe('');
  expect(screen.getByText('今天想完成什么？')).toBeTruthy();
  expect(api.create).not.toHaveBeenCalled();
});

it('keeps other drafts and a failed deletion intact', async () => {
  const { api } = setup();
  fireEvent.click(await screen.findByRole('button', { name: '会话1 A' }));
  await waitFor(() => expect(api.session).toHaveBeenCalledWith('one'));
  const editor = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(editor, { target: { value: 'keep this task' } });
  vi.mocked(api.deleteSession).mockRejectedValueOnce(new ApiError('session_delete_unconfirmed'));
  await menu('会话2'); fireEvent.click(screen.getByRole('menuitem', { name: '删除' }));
  fireEvent.click(within(screen.getByRole('dialog', { name: '删除会话' })).getByRole('button', { name: '删除会话' }));
  await screen.findByText(/删除未确认/);
  expect(screen.getByRole('button', { name: '会话2 A' })).toBeTruthy();
  expect((editor as HTMLTextAreaElement).value).toBe('keep this task');
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  await menu('会话2'); fireEvent.click(screen.getByRole('menuitem', { name: '删除' }));
  fireEvent.click(within(screen.getByRole('dialog', { name: '删除会话' })).getByRole('button', { name: '删除会话' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: '会话2 A' })).toBeNull());
  expect((editor as HTMLTextAreaElement).value).toBe('keep this task');
});

it('prevents an older list read from overwriting a confirmed rename', async () => {
  const { api } = setup();
  await screen.findByRole('button', { name: '会话1 A' });
  let resolve!: (rows: SessionRow[]) => void;
  const old = await api.sessions();
  vi.mocked(api.sessions).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  // New-session refresh is an existing independent list consumer.
  vi.mocked(api.create).mockResolvedValueOnce({ session_id: 'one' });
  fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
  await waitFor(() => expect(api.sessions).toHaveBeenCalledTimes(3));
  await menu('会话1'); fireEvent.click(screen.getByRole('menuitem', { name: '重命名' }));
  fireEvent.change(screen.getByRole('textbox', { name: '会话名称' }), { target: { value: 'new name' } });
  fireEvent.click(screen.getByRole('button', { name: '保存名称' }));
  await screen.findByRole('button', { name: 'new name A' });
  await act(async () => resolve(old));
  expect(screen.getByRole('button', { name: 'new name A' })).toBeTruthy();
});

it('shows only model and resolved thinking level in the model trigger', () => {
  render(<ModelMenu models={['A']} model="A" level="model_default" options={{ default_level: 'low', supported_levels: ['low', 'high'] }}
    open={false} disabled={false} toggle={vi.fn()} close={vi.fn()} selectModel={vi.fn()} selectThinking={vi.fn()} />);
  const trigger = screen.getByRole('button', { name: '模型与推理强度：A · low' });
  expect(trigger.querySelector('svg')).toBeNull();
  expect(trigger.textContent).toBe('Alow');
});

it('shows the end drop target only in the dragged group so other rows do not shift', async () => {
  setup();
  await menu('会话1'); fireEvent.click(screen.getByRole('menuitem', { name: '置顶' }));
  await screen.findByRole('group', { name: '已置顶会话' });
  const source = screen.getByRole('button', { name: '会话3 A' });
  const target = screen.getByRole('button', { name: '会话2 A' });
  const hitTest = document.elementFromPoint;
  document.elementFromPoint = () => target.closest('[data-session-id]')!;
  try {
    fireEvent(source, new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 50, clientY: 200 }));
    fireEvent(source, new MouseEvent('pointermove', { bubbles: true, clientX: 50, clientY: 20 }));
    expect(within(screen.getByRole('group', { name: '已置顶会话' })).queryByText('拖到这里移至组末尾')).toBeNull();
    expect(within(screen.getByRole('group', { name: '普通会话' })).getByText('拖到这里移至组末尾')).toBeTruthy();
  } finally { document.elementFromPoint = hitTest; fireEvent.pointerCancel(source); }
});
