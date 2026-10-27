import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest';
import { WebApi } from '../api';
import ProjectPicker from './ProjectPicker';
import type { DirectoryPage, ProjectRow } from '../projectTypes';

// jsdom has no native dialog methods. This models open/closed DOM state only;
// native focus trapping and dismissal are verified in the real browser.
beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.setAttribute('open', ''); } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value() { this.removeAttribute('open'); } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
afterAll(() => { delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal; delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close; });
const project: ProjectRow = { project_id: '11111111-1111-1111-1111-111111111111', name: '我的项目', source: { path: '/chosen' }, lifecycle: 'active', trusted: true, revision: 1, availability: 'available' };
const page = (path = '/home', extra: Partial<DirectoryPage> = {}): DirectoryPage => ({ path, parent: '/', separator: '/', identity: 'a'.repeat(64),
  items: [{ name: 'chosen', path: '/chosen', symlink: false }], next_offset: null,
  positions: [{ name: '主文件夹', path: '/home' }], default_parent_dir: null, default_error: null, ...extra });
function setup(mode: 'projects' | 'select' | 'create' = 'select') {
  const api = new WebApi('synthetic', 'chats');
  vi.spyOn(api, 'browseDirectories').mockImplementation(async value => page(value?.path));
  vi.spyOn(api, 'addLocalProject').mockResolvedValue(project);
  vi.spyOn(api, 'createLocalProject').mockResolvedValue(project);
  vi.spyOn(api, 'projectOperations').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'getProject').mockResolvedValue(project);
  vi.spyOn(api, 'reconcileProject').mockResolvedValue({ items: [] });
  const close = vi.fn(), added = vi.fn(), select = vi.fn();
  render(<ProjectPicker api={api} mode={mode} projects={[project]} currentScope="chats" close={close} added={added} select={select} />);
  return { api, close, added, select };
}
async function readyFolderButton(name = '选择此文件夹') {
  const button = await screen.findByRole('button', { name });
  await waitFor(() => expect(button.hasAttribute('disabled')).toBe(false));
  return button;
}
it('keeps initial selection disabled until directory metadata arrives', async () => {
  const api = new WebApi('synthetic', 'chats');
  let finish!: (value: DirectoryPage) => void;
  vi.spyOn(api, 'browseDirectories').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<ProjectPicker api={api} mode="select" projects={[]} currentScope="chats"
    close={vi.fn()} added={vi.fn()} select={vi.fn()} />);
  const button = await screen.findByRole('button', { name: '选择此文件夹' });
  expect(button.hasAttribute('disabled')).toBe(true);
  fireEvent.click(button);
  expect(screen.queryByRole('checkbox', { name: '我信任这个文件夹' })).toBeNull();
  await act(async () => finish(page()));
  fireEvent.click(await readyFolderButton());
  expect(screen.getByRole('checkbox', { name: '我信任这个文件夹' })).toBeTruthy();
});
it('browses without writing, requires explicit trust, then sends the observed directory identity', async () => {
  const { api, added } = setup();
  fireEvent.click(await screen.findByRole('button', { name: '打开文件夹 chosen' }));
  await waitFor(() => expect((screen.getByRole('textbox', { name: '文件夹路径' }) as HTMLInputElement).value).toBe('/chosen'));
  expect(api.addLocalProject).not.toHaveBeenCalled();
  fireEvent.click(await readyFolderButton());
  const confirm = screen.getByRole('button', { name: '信任并添加项目' });
  expect(confirm.hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: '我信任这个文件夹' }));
  fireEvent.click(confirm);
  await waitFor(() => expect(added).toHaveBeenCalledWith(project));
  expect(api.addLocalProject).toHaveBeenCalledWith('/chosen', 'a'.repeat(64), expect.any(String));
});
it('cancels a selection without registration', async () => {
  const { api, close } = setup();
  await screen.findByRole('button', { name: '选择此文件夹' });
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(close).toHaveBeenCalledOnce();
  expect(api.addLocalProject).not.toHaveBeenCalled(); expect(api.createLocalProject).not.toHaveBeenCalled();
});
it('creates under the selected parent with a single name and keeps confirmation on conflicts', async () => {
  const { api, added } = setup('create');
  await screen.findByRole('textbox', { name: '新文件夹名称' });
  fireEvent.change(screen.getByRole('textbox', { name: '新文件夹名称' }), { target: { value: 'new-folder' } });
  fireEvent.click(await readyFolderButton('继续'));
  expect(screen.getByText('/home/new-folder')).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox', { name: '我信任这个文件夹' }));
  fireEvent.click(screen.getByRole('button', { name: '创建并添加项目' }));
  await waitFor(() => expect(added).toHaveBeenCalledWith(project));
  expect(api.createLocalProject).toHaveBeenCalledWith('/home', 'new-folder', 'a'.repeat(64), expect.any(String));
});
it('prevents confirming a typed path until its metadata has loaded and ignores late directory responses', async () => {
  const { api } = setup();
  await screen.findByRole('button', { name: '选择此文件夹' });
  let finish!: (value: DirectoryPage) => void;
  vi.mocked(api.browseDirectories).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValue(page('/fresh'));
  fireEvent.change(screen.getByRole('textbox', { name: '文件夹路径' }), { target: { value: '/slow' } });
  expect(screen.getByRole('button', { name: '选择此文件夹' }).hasAttribute('disabled')).toBe(true);
  fireEvent.submit(screen.getByRole('textbox', { name: '文件夹路径' }).closest('form')!);
  await waitFor(() => expect(api.browseDirectories).toHaveBeenCalledWith(expect.objectContaining({ path: '/slow' }), expect.any(AbortSignal)));
  fireEvent.change(screen.getByRole('textbox', { name: '文件夹路径' }), { target: { value: '/fresh' } });
  fireEvent.submit(screen.getByRole('textbox', { name: '文件夹路径' }).closest('form')!);
  await waitFor(() => expect((screen.getByRole('textbox', { name: '文件夹路径' }) as HTMLInputElement).value).toBe('/fresh'));
  await act(async () => finish(page('/slow')));
  expect((screen.getByRole('textbox', { name: '文件夹路径' }) as HTMLInputElement).value).toBe('/fresh');
});
it('keeps the same request ID after an unknown result and can recover a recorded success by reading', async () => {
  const { api, added } = setup();
  vi.mocked(api.addLocalProject).mockRejectedValueOnce(new TypeError('network')).mockResolvedValue(project);
  fireEvent.click(await readyFolderButton());
  fireEvent.click(screen.getByRole('checkbox', { name: '我信任这个文件夹' }));
  fireEvent.click(screen.getByRole('button', { name: '信任并添加项目' }));
  await screen.findByRole('button', { name: '核对操作结果' });
  const id = vi.mocked(api.addLocalProject).mock.calls[0][2];
  vi.mocked(api.projectOperations).mockResolvedValue({ items: [{ operation_id: id, project_id: project.project_id, stage: 'done', error_code: null }] });
  fireEvent.click(screen.getByRole('button', { name: '核对操作结果' }));
  await waitFor(() => expect(added).toHaveBeenCalledWith(project));
  expect(api.addLocalProject).toHaveBeenCalledTimes(1);
});
it('opens an existing project without changing its session identity or issuing a registration', () => {
  const { api, select } = setup('projects');
  fireEvent.click(screen.getByRole('button', { name: '打开我的项目' }));
  expect(select).toHaveBeenCalledWith(project.project_id);
  expect(api.addLocalProject).not.toHaveBeenCalled(); expect(api.createLocalProject).not.toHaveBeenCalled();
});

it('displays Windows target paths and makes direction-control characters explicit at confirmation', async () => {
  const { api } = setup('create');
  await screen.findByRole('textbox', { name: '文件夹路径' });
  const path = 'C:\\Work\\\u202efolder';
  vi.mocked(api.browseDirectories).mockResolvedValue(page(path, { separator: '\\', items: [] }));
  fireEvent.change(screen.getByRole('textbox', { name: '文件夹路径' }), { target: { value: path } });
  fireEvent.submit(screen.getByRole('textbox', { name: '文件夹路径' }).closest('form')!);
  await waitFor(() => expect(screen.getByRole('button', { name: '继续' }).hasAttribute('disabled')).toBe(true));
  await screen.findByText('这个文件夹中没有子文件夹');
  fireEvent.change(screen.getByRole('textbox', { name: '新文件夹名称' }), { target: { value: 'new' } });
  fireEvent.click(await readyFolderButton('继续'));
  expect(screen.getByText(path.replace('\u202e', '\\u202e') + '\\new')).toBeTruthy();
});

it('retains the browsed parent and entered name when changing folder modes', async () => {
  const { api } = setup('create');
  await screen.findByRole('textbox', { name: '新文件夹名称' });
  fireEvent.click(screen.getByRole('button', { name: '打开文件夹 chosen' }));
  await waitFor(() => expect((screen.getByRole('textbox', { name: '文件夹路径' }) as HTMLInputElement).value).toBe('/chosen'));
  fireEvent.change(screen.getByRole('textbox', { name: '新文件夹名称' }), { target: { value: 'kept' } });
  fireEvent.click(screen.getByRole('button', { name: '选择已有文件夹' }));
  expect((screen.getByRole('textbox', { name: '文件夹路径' }) as HTMLInputElement).value).toBe('/chosen');
  fireEvent.click(screen.getByRole('button', { name: '创建新文件夹' }));
  expect((screen.getByRole('textbox', { name: '新文件夹名称' }) as HTMLInputElement).value).toBe('kept');
  expect(api.createLocalProject).not.toHaveBeenCalled();
});

it('retains a failed creation for correction and never silently selects another directory', async () => {
  const { ApiError } = await import('../api');
  const { api, added } = setup('create');
  vi.mocked(api.createLocalProject).mockRejectedValue(new ApiError('project_directory_exists'));
  await screen.findByRole('textbox', { name: '新文件夹名称' });
  fireEvent.change(screen.getByRole('textbox', { name: '新文件夹名称' }), { target: { value: 'exists' } });
  fireEvent.click(await readyFolderButton('继续'));
  fireEvent.click(screen.getByRole('checkbox', { name: '我信任这个文件夹' }));
  fireEvent.click(screen.getByRole('button', { name: '创建并添加项目' }));
  expect(await screen.findByText(/同名文件夹已经存在/)).toBeTruthy();
  expect(screen.getByText('/home/exists')).toBeTruthy(); expect(added).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '返回目录' }));
  expect((await screen.findByRole('textbox', { name: '新文件夹名称' }) as HTMLInputElement).value).toBe('exists');
});
it('retries the same confirmation with the same ID, then reconciles only its recorded operation', async () => {
  const { api, added } = setup();
  vi.mocked(api.addLocalProject).mockRejectedValue(new TypeError('network'));
  fireEvent.click(await readyFolderButton());
  fireEvent.click(screen.getByRole('checkbox', { name: '我信任这个文件夹' }));
  fireEvent.click(screen.getByRole('button', { name: '信任并添加项目' }));
  fireEvent.click(await screen.findByRole('button', { name: '重试添加项目' }));
  await waitFor(() => expect(api.addLocalProject).toHaveBeenCalledTimes(2));
  const id = vi.mocked(api.addLocalProject).mock.calls[0][2];
  expect(vi.mocked(api.addLocalProject).mock.calls[1][2]).toBe(id);
  vi.mocked(api.projectOperations).mockResolvedValueOnce({ items: [{ operation_id: id, project_id: project.project_id, stage: 'directory_created', error_code: 'project_operation_failed' }] })
    .mockResolvedValue({ items: [{ operation_id: id, project_id: project.project_id, stage: 'done', error_code: null }] });
  fireEvent.click(await screen.findByRole('button', { name: '核对操作结果' }));
  fireEvent.click(await screen.findByRole('button', { name: '继续完成登记' }));
  await waitFor(() => expect(added).toHaveBeenCalledWith(project));
  expect(api.reconcileProject).toHaveBeenCalledWith(id);
  expect(api.addLocalProject).toHaveBeenCalledTimes(2);
});
it('loads more folder metadata and requests hidden items explicitly', async () => {
  const { api } = setup();
  await screen.findByRole('textbox', { name: '文件夹路径' });
  vi.mocked(api.browseDirectories).mockResolvedValueOnce(page('/paged', { next_offset: 1 })).mockResolvedValue(page('/paged', { items: [{ name: 'second', path: '/paged/second', symlink: false }] }));
  fireEvent.change(screen.getByRole('textbox', { name: '文件夹路径' }), { target: { value: '/paged' } });
  fireEvent.submit(screen.getByRole('textbox', { name: '文件夹路径' }).closest('form')!);
  fireEvent.click(await screen.findByRole('button', { name: '加载更多文件夹' }));
  expect(await screen.findByRole('button', { name: '打开文件夹 second' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '打开文件夹 chosen' })).toBeTruthy();
  expect(api.browseDirectories).toHaveBeenCalledWith({ path: '/paged', show_hidden: false, offset: 1 }, expect.any(AbortSignal));
  fireEvent.click(screen.getByRole('checkbox', { name: '显示隐藏项' }));
  await waitFor(() => expect(api.browseDirectories).toHaveBeenCalledWith({ path: '/paged', show_hidden: true }, expect.any(AbortSignal)));
});
it('creates a project from the composer entry while preserving the old scope draft', async () => {
  const { default: ProjectWorkspace } = await import('../ProjectWorkspace');
  const api = new WebApi('fixture', 'chats');
  const rows: ProjectRow[] = [];
  const prepare = (value: WebApi) => {
    vi.spyOn(value, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
    vi.spyOn(value, 'sessions').mockResolvedValue([]);
    vi.spyOn(value, 'sessionControlOptions').mockResolvedValue({ tools: [], permission_modes: ['Default'], default_permission_mode: 'Default', default_active_tools: [] });
    vi.spyOn(value, 'backgroundTasks').mockResolvedValue({ items: [], active_count: 0, terminal_revision: 0, revision: 'empty', next_before: null });
    return value;
  };
  prepare(api); vi.spyOn(api, 'forScope').mockImplementation(id => prepare(new WebApi('fixture', id)));
  vi.spyOn(api, 'projects').mockImplementation(async () => ({ items: [...rows], default_parent_dir: null }));
  vi.spyOn(api, 'directory').mockResolvedValue([]);
  vi.spyOn(api, 'browseDirectories').mockResolvedValue(page('/chosen'));
  vi.spyOn(api, 'addLocalProject').mockImplementation(async () => { rows.push(project); return project; });
  render(<ProjectWorkspace api={api} />);
  const draft = await screen.findByRole('textbox', { name: '消息' });
  fireEvent.change(draft, { target: { value: '普通对话草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '项目与位置，当前：普通对话' }));
  fireEvent.click(screen.getByRole('button', { name: '选择已有文件夹' }));
  fireEvent.click(await readyFolderButton());
  fireEvent.click(screen.getByRole('checkbox', { name: '我信任这个文件夹' }));
  fireEvent.click(screen.getByRole('button', { name: '信任并添加项目' }));
  await waitFor(() => expect(screen.getByRole('status', { name: '当前项目' }).textContent).toContain('我的项目'));
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '项目与位置，当前：我的项目' }));
  fireEvent.click(screen.getByRole('button', { name: '打开普通对话' }));
  expect((screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement).value).toBe('普通对话草稿');
});

it('keeps reverse Tab at the first control inside the modal', async () => {
  setup(); await readyFolderButton();
  const close = screen.getByRole('button', { name: '关闭项目选择' }); close.focus();
  fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '选择此文件夹' }));
});
