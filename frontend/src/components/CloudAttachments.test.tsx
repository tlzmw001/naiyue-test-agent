import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, afterAll, expect, it, vi } from 'vitest';
import CloudAttachments from './CloudAttachments';
import { installDialogMock } from '../testDialogs';
import type { CloudApi } from '../cloudApi';
let restore: () => void;
beforeAll(() => { restore = installDialogMock(); }); afterAll(() => restore()); afterEach(cleanup);
it('selects hidden files in a centered picker and only stages an attachment', async () => {
  const cloud = { browseDirectories: vi.fn().mockResolvedValue({ positions: [] }), files: vi.fn().mockResolvedValue({ path: '', entries: [{ name: '.env', path: '.env', kind: 'file', size: 12 }], next_offset: null }),
    uploadFiles: vi.fn().mockResolvedValue({ attachment_id: 'batch', phase: 'uploading' }) } as unknown as CloudApi;
  const select = vi.fn(); const refresh = vi.fn();
  render(<CloudAttachments api={cloud} sid="S" bid="B" rows={[]} selected={[]} select={select} changed={refresh} />);
  fireEvent.click(screen.getByRole('button', { name: '添加附件' })); fireEvent.click(screen.getByRole('button', { name: '添加文件或文件夹' }));
  expect(screen.getByRole('dialog', { name: '选择上传文件' })).toBeTruthy();
  fireEvent.click(await screen.findByRole('checkbox', { name: '.env' })); fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await waitFor(() => expect(cloud.uploadFiles).toHaveBeenCalledWith('S', 'B', expect.objectContaining({ paths: ['.env'] })));
  expect(select).toHaveBeenCalledWith(['batch']); expect(refresh).toHaveBeenCalledOnce();
});

it('shows children as included when their whole directory is selected', async () => {
  const cloud = { browseDirectories: vi.fn().mockResolvedValue({ positions: [] }), files: vi.fn().mockImplementation(async (_sid, path) => ({ path, entries: path ? [{ name: 'a.py', path: 'src/a.py', kind: 'file', size: 1 }] : [{ name: 'src', path: 'src', kind: 'directory', size: 0 }], next_offset: null })) } as unknown as CloudApi;
  render(<CloudAttachments api={cloud} sid="S" bid="B" rows={[]} selected={[]} select={vi.fn()} changed={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '添加附件' })); fireEvent.click(screen.getByRole('button', { name: '添加文件或文件夹' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: 'src' })); fireEvent.click(screen.getByRole('button', { name: '打开 src' }));
  const child = await screen.findByRole('checkbox', { name: 'a.py' });
  expect(child).toHaveProperty('checked', true); expect(child).toHaveProperty('disabled', true);
  expect(screen.queryByText(/Git/)).toBeNull();
});

it('keeps links visible but unselectable and preserves a failed original upload intent', async () => {
  const cloud = { browseDirectories: vi.fn().mockResolvedValue({ positions: [] }), files: vi.fn().mockResolvedValue({ path: '', entries: [{ name: 'link', path: 'link', kind: 'symlink', size: 0 }, { name: 'report', path: 'report', kind: 'file', size: 1 }], next_offset: null }), uploadFiles: vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ attachment_id: 'batch', phase: 'uploading' }) } as unknown as CloudApi;
  render(<CloudAttachments api={cloud} sid="S" bid="B" rows={[]} selected={[]} select={vi.fn()} changed={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '添加附件' })); fireEvent.click(screen.getByRole('button', { name: '添加文件或文件夹' }));
  expect(await screen.findByRole('checkbox', { name: 'link' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('checkbox', { name: 'report' })); fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await waitFor(() => expect(cloud.uploadFiles).toHaveBeenCalledTimes(2));
  expect(vi.mocked(cloud.uploadFiles).mock.calls[0]).toEqual(vi.mocked(cloud.uploadFiles).mock.calls[1]);
});

it('shows acknowledged bytes while the attachment is uploading', () => {
  render(<CloudAttachments api={{} as CloudApi} sid="S" bid="B" selected={[]} select={vi.fn()} changed={vi.fn()}
    rows={[{ attachment_id: 'A', binding_id: 'B', display_name: '报告', input_id: null, size: 100, phase: 'uploading',
      error_code: null, created_at: 0, uploaded_bytes: 25, transfer_bytes: 100 }]} />);
  expect(screen.getByRole('progressbar', { name: '报告上传进度' })).toHaveProperty('value', 25);
});

it('stages a file explicitly selected from another local directory', async () => {
  const cloud = {
    browseDirectories: vi.fn().mockResolvedValue({ path: 'D:\\Downloads', identity: 'directory-id', separator: '\\', parent: 'D:\\', items: [], positions: [], next_offset: null }),
    files: vi.fn().mockResolvedValue({ path: '', entries: [{ name: '报告.txt', path: '报告.txt', kind: 'file', size: 12 }], next_offset: null }),
    uploadFiles: vi.fn().mockResolvedValue({ attachment_id: 'batch', phase: 'uploading' }),
  } as unknown as CloudApi;
  render(<CloudAttachments api={cloud} sid="S" bid="B" rows={[]} selected={[]} select={vi.fn()} changed={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
  fireEvent.click(screen.getByRole('button', { name: '添加文件或文件夹' }));
  await screen.findByRole('checkbox', { name: '报告.txt' });
  const address = screen.getByRole('textbox', { name: '文件夹路径' });
  fireEvent.change(address, { target: { value: 'D:\\Downloads' } });
  fireEvent.click(screen.getByRole('button', { name: '前往' }));
  await screen.findByText('D:\\Downloads');
  fireEvent.click(screen.getByRole('checkbox', { name: '报告.txt' }));
  fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await waitFor(() => expect(cloud.uploadFiles).toHaveBeenCalledWith('S', 'B', expect.objectContaining({
    directory: 'D:\\Downloads', directory_identity: 'directory-id', paths: ['报告.txt'],
  })));
});

it('lets the user discard a rejected selection and browse a replaced directory', async () => {
  const select = vi.fn();
  const changed = vi.fn();
  let identity = 'original-directory';
  const cloud = {
    browseDirectories: vi.fn().mockImplementation(async () => ({
      path: 'D:\\Downloads', identity, separator: '\\', parent: 'D:\\',
      positions: [{ name: '下载', path: 'D:\\Downloads' }],
    })),
    files: vi.fn().mockResolvedValue({ path: '', entries: [{ name: '报告.zip', path: '报告.zip', kind: 'file' }] }),
    uploadFiles: vi.fn().mockRejectedValueOnce({ code: 'cloud_source_identity_changed' })
      .mockResolvedValueOnce({ attachment_id: 'new-batch' }),
  } as unknown as CloudApi;
  render(<CloudAttachments api={cloud} sid="S" bid="B" rows={[]} selected={['accepted-batch']} select={select} changed={changed} />);
  fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
  fireEvent.click(screen.getByRole('button', { name: '添加文件或文件夹' }));
  const directory = await screen.findByRole('button', { name: '下载' });
  await waitFor(() => expect(directory).toHaveProperty('disabled', false));
  fireEvent.click(directory);
  await screen.findByText('D:\\Downloads');
  fireEvent.click(screen.getByRole('checkbox', { name: '报告.zip' }));
  fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await screen.findByRole('alert');
  expect(directory).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: '放弃待确认选择' }));
  expect(changed).toHaveBeenCalledOnce();
  expect(select).not.toHaveBeenCalled();
  expect(screen.getByText('已选 0 项')).toBeTruthy();
  identity = 'replacement-directory';
  fireEvent.click(directory);
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '报告.zip' })).toHaveProperty('disabled', false));
  fireEvent.click(screen.getByRole('checkbox', { name: '报告.zip' }));
  fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await waitFor(() => expect(select).toHaveBeenCalledWith(['accepted-batch', 'new-batch']));
  const [first, replacement] = vi.mocked(cloud.uploadFiles).mock.calls;
  expect(replacement[2]).toMatchObject({ directory: 'D:\\Downloads', directory_identity: identity, paths: ['报告.zip'] });
  expect(replacement[2].attachment_id).not.toBe(first[2].attachment_id);
});

it('explains a failed background upload beside the attachment', () => {
  render(<CloudAttachments api={{} as CloudApi} sid="S" bid="B" selected={[]} select={vi.fn()} changed={vi.fn()}
    rows={[{ attachment_id: 'A', binding_id: 'B', display_name: '压缩包', input_id: null, size: 0, phase: 'failed',
      error_code: 'input_budget', created_at: 0 }]} />);
  expect(screen.getByText(/输入大小或文件数量超过上限/)).toBeTruthy();
  expect(screen.getByRole('button', { name: '重试上传' })).toBeTruthy();
});

it('stages files and folders across locations and retries only an unconfirmed batch', async () => {
  const select = vi.fn();
  const first = { path: 'D:\\Downloads', identity: 'D', separator: '\\', parent: 'D:\\' };
  const second = { path: 'E:\\Reports', identity: 'E', separator: '\\', parent: 'E:\\' };
  const positions = [{ path: first.path, name: '下载' }, { path: second.path, name: '报告盘' }];
  const cloud = {
    browseDirectories: vi.fn().mockImplementation(async ({ path }) => ({ ...(path === second.path ? second : first), positions })),
    files: vi.fn().mockImplementation(async (_sid, path, offset, _signal, location) => ({ path,
      entries: !location ? [] : location.identity === 'D' ? offset ? [{ name: 'two.zip', path: 'two.zip', kind: 'file' }] : [
        { name: 'logs', path: 'logs', kind: 'directory' }, { name: 'one.zip', path: 'one.zip', kind: 'file' },
      ] : [{ name: 'three.zip', path: 'three.zip', kind: 'file' }], next_offset: location?.identity === 'D' && !offset ? 2 : null })),
    uploadFiles: vi.fn().mockResolvedValueOnce({ attachment_id: 'batch-d' }).mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ attachment_id: 'batch-e' }),
  } as unknown as CloudApi;
  const props = { api: cloud, sid: 'S', bid: 'B', rows: [], select, changed: vi.fn() };
  const view = render(<CloudAttachments {...props} selected={[]} />);
  fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
  fireEvent.click(screen.getByRole('button', { name: '添加文件或文件夹' }));
  fireEvent.click(await screen.findByRole('button', { name: '下载' }));
  fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
  await screen.findByRole('checkbox', { name: 'two.zip' });
  fireEvent.click(screen.getByRole('button', { name: '全选已加载项' }));
  fireEvent.click(screen.getByRole('button', { name: '报告盘' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: 'three.zip' }));
  expect(screen.getByText('已选 4 项')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await screen.findByRole('alert');
  expect(select).toHaveBeenLastCalledWith(['batch-d']);
  expect(screen.getByText('已选 1 项')).toBeTruthy();
  view.rerender(<CloudAttachments {...props} selected={['batch-d']} />);
  fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await waitFor(() => expect(select).toHaveBeenLastCalledWith(['batch-d', 'batch-e']));
  const calls = vi.mocked(cloud.uploadFiles).mock.calls;
  expect(calls).toHaveLength(3);
  expect(calls[0][2]).toMatchObject({ directory: first.path, paths: ['logs', 'one.zip', 'two.zip'] });
  expect(calls[1][2]).toMatchObject({ directory: second.path, paths: ['three.zip'] });
  expect(calls[2]).toEqual(calls[1]);
});

it('only keeps a previously submitted attachment visible while the current draft references it', () => {
  const common = { binding_id: 'B', input_id: null, size: 100, error_code: null, created_at: 0 };
  const props = {
    api: {} as CloudApi, sid: 'S', bid: 'B', select: vi.fn(), changed: vi.fn(),
    rows: [
      { ...common, attachment_id: 'sent', display_name: '已提交的压缩包', phase: 'attached' },
      { ...common, attachment_id: 'ready', display_name: '待发送的压缩包', phase: 'ready' },
    ],
  };
  const view = render(<CloudAttachments {...props} selected={['sent']} />);
  expect(screen.getByRole('checkbox', { name: '已提交的压缩包' })).toBeTruthy();
  view.rerender(<CloudAttachments {...props} selected={[]} />);
  expect(screen.queryByRole('checkbox', { name: '已提交的压缩包' })).toBeNull();
  expect(screen.getByRole('checkbox', { name: '待发送的压缩包' })).toBeTruthy();
});
