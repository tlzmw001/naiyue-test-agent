import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, afterAll, expect, it, vi } from 'vitest';
import CloudAttachments from './CloudAttachments';
import { installDialogMock } from '../testDialogs';
import type { CloudApi } from '../cloudApi';
let restore: () => void;
beforeAll(() => { restore = installDialogMock(); }); afterAll(() => restore()); afterEach(cleanup);
it('selects hidden files in a centered picker and only stages an attachment', async () => {
  const cloud = { files: vi.fn().mockResolvedValue({ path: '', entries: [{ name: '.env', path: '.env', kind: 'file', size: 12 }], next_offset: null }),
    uploadFiles: vi.fn().mockResolvedValue({ attachment_id: 'batch', phase: 'uploading' }) } as unknown as CloudApi;
  const select = vi.fn(); const refresh = vi.fn();
  render(<CloudAttachments api={cloud} sid="S" bid="B" rows={[]} selected={[]} select={select} changed={refresh} />);
  fireEvent.click(screen.getByRole('button', { name: '添加附件' })); fireEvent.click(screen.getByRole('button', { name: '添加文件' }));
  expect(screen.getByRole('dialog', { name: '选择上传文件' })).toBeTruthy();
  fireEvent.click(await screen.findByRole('checkbox', { name: '.env' })); fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await waitFor(() => expect(cloud.uploadFiles).toHaveBeenCalledWith('S', 'B', expect.objectContaining({ paths: ['.env'] })));
  expect(select).toHaveBeenCalledWith(['batch']); expect(refresh).toHaveBeenCalledOnce();
});

it('shows children as included when their whole directory is selected', async () => {
  const cloud = { files: vi.fn().mockImplementation(async (_sid, path) => ({ path, entries: path ? [{ name: 'a.py', path: 'src/a.py', kind: 'file', size: 1 }] : [{ name: 'src', path: 'src', kind: 'directory', size: 0 }], next_offset: null })) } as unknown as CloudApi;
  render(<CloudAttachments api={cloud} sid="S" bid="B" rows={[]} selected={[]} select={vi.fn()} changed={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '添加附件' })); fireEvent.click(screen.getByRole('button', { name: '添加文件夹' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: 'src' })); fireEvent.click(screen.getByRole('button', { name: '打开 src' }));
  const child = await screen.findByRole('checkbox', { name: 'a.py' });
  expect(child).toHaveProperty('checked', true); expect(child).toHaveProperty('disabled', true);
  expect(screen.queryByText(/Git/)).toBeNull();
});

it('keeps links visible but unselectable and preserves a failed original upload intent', async () => {
  const cloud = { files: vi.fn().mockResolvedValue({ path: '', entries: [{ name: 'link', path: 'link', kind: 'symlink', size: 0 }, { name: 'report', path: 'report', kind: 'file', size: 1 }], next_offset: null }), uploadFiles: vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ attachment_id: 'batch', phase: 'uploading' }) } as unknown as CloudApi;
  render(<CloudAttachments api={cloud} sid="S" bid="B" rows={[]} selected={[]} select={vi.fn()} changed={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '添加附件' })); fireEvent.click(screen.getByRole('button', { name: '添加文件' }));
  expect(await screen.findByRole('checkbox', { name: 'link' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('checkbox', { name: 'report' })); fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: '上传所选项' }));
  await waitFor(() => expect(cloud.uploadFiles).toHaveBeenCalledTimes(2));
  expect(vi.mocked(cloud.uploadFiles).mock.calls[0]).toEqual(vi.mocked(cloud.uploadFiles).mock.calls[1]);
});
