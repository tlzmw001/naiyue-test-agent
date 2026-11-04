import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, it, expect, vi } from 'vitest';
import CloudSourcePicker from './CloudSourcePicker';
import type { CloudApi } from '../cloudApi';
afterEach(cleanup);
it('detects non Git, selects directories and invalidates a successful preview on edits', async () => {
  const api = { sourceTree: vi.fn().mockResolvedValue({ git: false, git_error: 'input_git_required', path: '', entries: [{ path: 'src', name: 'src', kind: 'directory', size: 0 }], next_offset: null, excluded_count: 0 }),
    sourcePreview: vi.fn().mockResolvedValue({ file_count: 2, total_bytes: 12, issues: [], limits: { max_input_bytes: 2147483648 } }) } as unknown as CloudApi;
  const changed=vi.fn(); render(<CloudSourcePicker api={api} sid="S" disabled={false} onChange={changed}/>);
  await screen.findByText(/当前项目不是 Git/);
  fireEvent.click(screen.getByRole('checkbox', { name: '选择 src' }));
  fireEvent.click(screen.getByRole('button', { name: '预览导入范围' }));
  await screen.findByText(/2 个文件/);
  expect(api.sourcePreview).toHaveBeenCalledWith('S', {mode:'selection',paths:['src']});
  expect(changed).toHaveBeenLastCalledWith({mode:'selection',paths:['src']});
  fireEvent.click(screen.getByRole('checkbox', { name: '选择 src' }));
  expect(changed).toHaveBeenLastCalledWith(null);
});

it('excludes a child of a selected directory without silently selecting siblings again', async () => {
  const api = { sourceTree: vi.fn().mockImplementation(async (_sid, path) => ({ git: false, git_error: null, path, entries:
    path === '' ? [{ path: 'src', name: 'src', kind: 'directory', size: 0 }] : path === 'src' ? [{ path: 'src/nested', name: 'nested', kind: 'directory', size: 0 }] : [{ path: 'src/nested/a.py', name: 'a.py', kind: 'file', size: 1 }], next_offset: null, excluded_count: 0 })),
    sourcePreview: vi.fn().mockResolvedValue({ file_count: 1, total_bytes: 12, issues: [], limits: { max_input_bytes: 2147483648 } }) } as unknown as CloudApi;
  render(<CloudSourcePicker api={api} sid="S" disabled={false} onChange={vi.fn()} />);
  fireEvent.click(await screen.findByRole('checkbox', { name: '选择 src' }));
  fireEvent.click(screen.getByRole('button', { name: '打开 src' }));
  const nested=await screen.findByRole('checkbox',{name:'选择 src/nested'});
  expect((nested as HTMLInputElement).checked).toBe(true);fireEvent.click(nested);
  fireEvent.click(screen.getByRole('button',{name:'预览导入范围'}));
  await screen.findByText(/1 个文件/);
  expect(api.sourcePreview).toHaveBeenLastCalledWith('S',{mode:'selection',paths:['src'],exclude:['src/nested']});
  fireEvent.click(screen.getByRole('button',{name:'打开 nested'}));
  const child=await screen.findByRole('checkbox',{name:'选择 src/nested/a.py'});
  expect((child as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText(/请先返回上一级重新选择/)).toBeTruthy();
});

it('clears exclusions when switching back to all Git files', async () => {
  const api={sourceTree:vi.fn().mockImplementation(async (_sid,path)=>({git:true,git_error:null,path,entries:path===''?[{path:'src',name:'src',kind:'directory',size:0}]:[{path:'src/a.py',name:'a.py',kind:'file',size:1}],next_offset:null,excluded_count:0})),sourcePreview:vi.fn().mockResolvedValue({file_count:1,total_bytes:1,issues:[],limits:{max_input_bytes:2147483648}})} as unknown as CloudApi;
  render(<CloudSourcePicker api={api} sid="S" disabled={false} onChange={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('checkbox',{name:'导入全部 Git 项目文件'}));
  fireEvent.click(screen.getByRole('checkbox',{name:'选择 src'}));
  fireEvent.click(screen.getByRole('button',{name:'打开 src'}));
  fireEvent.click(await screen.findByRole('checkbox',{name:'选择 src/a.py'}));
  fireEvent.click(screen.getByRole('checkbox',{name:'导入全部 Git 项目文件'}));
  fireEvent.click(screen.getByRole('button',{name:'预览导入范围'}));
  await screen.findByText(/1 个文件/);
  expect(api.sourcePreview).toHaveBeenLastCalledWith('S',{mode:'git',paths:[]});
});
