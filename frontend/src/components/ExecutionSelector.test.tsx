import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from '../api';
import ExecutionSelector from './ExecutionSelector';
import userEvent from '@testing-library/user-event';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function setup() {
  const api = new WebApi('synthetic','11111111-1111-1111-1111-111111111111');
  vi.spyOn(api,'executionOptions').mockResolvedValue({host_available:true,sandbox_available:true,error_code:null,
    requires_trust:false,project_revision:1,network:'none',untracked:['notes.txt','.env'],blocked:['.env']});
  const create=vi.fn().mockResolvedValue(undefined);
  render(<ExecutionSelector api={api} session={null} disabled={false} modelId="fake" create={create} refresh={vi.fn()} />);
  return {api,create};
}
it('opens saved sandbox changes directly without reading creation options or creating a session',async()=>{
  const api=new WebApi('synthetic','11111111-1111-1111-1111-111111111111');
  const options=vi.spyOn(api,'executionOptions').mockRejectedValue(new Error('environment offline'));
  vi.spyOn(api,'results').mockResolvedValue({items:[]});
  vi.spyOn(api,'resultOptions').mockRejectedValue(new Error('environment offline'));
  const create=vi.fn();
  render(<ExecutionSelector api={api} modelId="fake" session={{session_id:'S',execution:{kind:'sandbox',state:'unavailable'}} as never}
    disabled={false} running create={create} refresh={vi.fn()} />);
  expect(screen.getByRole('button',{name:'执行位置：Docker 沙箱'}).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'查看改动'}));
  expect(await screen.findByRole('dialog',{name:'沙箱结果'})).toBeTruthy();
  expect(await screen.findByText(/当前沙箱无法取回/)).toBeTruthy();
  expect(options).not.toHaveBeenCalled();expect(create).not.toHaveBeenCalled();
});
it('requires explicit untracked selection and cannot select known credentials',async()=>{
  const {create}=setup(); fireEvent.click(screen.getByRole('button',{name:'执行位置：此计算机'}));
  fireEvent.click(await screen.findByRole('button',{name:'Docker 沙箱'}));
  const note=screen.getByRole('checkbox',{name:'notes.txt'}) as HTMLInputElement;
  expect(note.checked).toBe(false);
  expect(screen.getByRole('checkbox',{name:'.env'}).hasAttribute('disabled')).toBe(true);
  fireEvent.click(note); fireEvent.click(screen.getByRole('button',{name:'创建沙箱会话'}));
  await waitFor(()=>expect(create).toHaveBeenCalledWith({execution:'sandbox',model_id:'fake',include_untracked:['notes.txt'],requestId:expect.any(String)}));
});
it('keeps keyboard focus in the execution dialog when entering input preparation',async()=>{
  setup();await userEvent.click(screen.getByRole('button',{name:'执行位置：此计算机'}));
  await userEvent.click(await screen.findByRole('button',{name:'Docker 沙箱'}));
  expect(screen.getByRole('dialog',{name:'选择执行位置'}).contains(document.activeElement)).toBe(true);
  await userEvent.keyboard('{Escape}');expect(screen.queryByRole('dialog',{name:'选择执行位置'})).toBeNull();
});
it('keeps the same creation request after an unconfirmed response',async()=>{
  const {create}=setup();create.mockRejectedValueOnce(new Error('offline'));
  fireEvent.click(screen.getByRole('button',{name:'执行位置：此计算机'}));
  fireEvent.click(await screen.findByRole('button',{name:'Docker 沙箱'}));
  fireEvent.click(screen.getByRole('button',{name:'创建沙箱会话'}));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button',{name:'核对原创建请求'}));
  await waitFor(()=>expect(create).toHaveBeenCalledTimes(2));
  expect(create.mock.calls[0]).toEqual(create.mock.calls[1]);
});

it('shows refused input paths and keeps permanent preparation failure from retrying checkout',async()=>{
  const api=new WebApi('synthetic','11111111-1111-1111-1111-111111111111');
  vi.spyOn(api,'executionOptions').mockResolvedValue({host_available:true,sandbox_available:true,error_code:null,
    requires_trust:false,project_revision:1,network:'none',untracked:[],blocked:[]});
  render(<ExecutionSelector api={api} modelId="fake" session={{execution:{kind:'sandbox',state:'failed',error:{code:'git_input_sensitive',paths:['config/.env.local']}}} as never}
    disabled={false} create={vi.fn()} refresh={vi.fn()} />);
  fireEvent.click(screen.getByRole('button',{name:'执行位置：Docker 沙箱'}));
  expect(await screen.findByText('config/.env.local')).toBeTruthy();
  expect(screen.queryByRole('button',{name:'核对并继续原准备'})).toBeNull();
});

it('freezes the creation model while another model is selected after a lost response',async()=>{
  const api=new WebApi('synthetic','11111111-1111-1111-1111-111111111111');
  vi.spyOn(api,'executionOptions').mockResolvedValue({host_available:true,sandbox_available:true,error_code:null,
    requires_trust:false,project_revision:1,network:'none',untracked:[],blocked:[]});
  const create=vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined),refresh=vi.fn();
  const view=render(<ExecutionSelector api={api} modelId="first" session={null} disabled={false} create={create} refresh={refresh} />);
  fireEvent.click(screen.getByRole('button',{name:'执行位置：此计算机'}));
  fireEvent.click(await screen.findByRole('button',{name:'Docker 沙箱'}));
  fireEvent.click(screen.getByRole('button',{name:'创建沙箱会话'}));await screen.findByRole('alert');
  view.rerender(<ExecutionSelector api={api} modelId="second" session={null} disabled={false} create={create} refresh={refresh} />);
  fireEvent.click(screen.getByRole('button',{name:'核对原创建请求'}));await waitFor(()=>expect(create).toHaveBeenCalledTimes(2));
  expect(create.mock.calls[0][0].model_id).toBe('first');expect(create.mock.calls[1]).toEqual(create.mock.calls[0]);
});
