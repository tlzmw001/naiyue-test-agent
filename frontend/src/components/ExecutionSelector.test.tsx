import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from '../api';
import ExecutionSelector from './ExecutionSelector';
import userEvent from '@testing-library/user-event';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function setup() {
  const api = new WebApi('synthetic','11111111-1111-1111-1111-111111111111');
  vi.spyOn(api,'executionOptions').mockResolvedValue({host_available:true,sandbox_available:true,error_code:null,
    requires_trust:false,project_revision:1,network:'none',untracked:['notes.txt','.env'],blocked:['.env'],
    default_profile:'python',profiles:[{profile_id:'python',name:'Python tests',revision:'a'.repeat(64)}]});
  const create=vi.fn().mockResolvedValue(undefined);
  const view=render(<ExecutionSelector api={api} session={null} disabled={false} create={create} refresh={vi.fn()} />);
  return {api,create,view};
}
it('opens saved sandbox changes directly without reading creation options or creating a session',async()=>{
  const api=new WebApi('synthetic','11111111-1111-1111-1111-111111111111');
  const options=vi.spyOn(api,'executionOptions').mockRejectedValue(new Error('environment offline'));
  vi.spyOn(api,'results').mockResolvedValue({items:[]});
  vi.spyOn(api,'resultOptions').mockRejectedValue(new Error('environment offline'));
  const create=vi.fn();
  render(<ExecutionSelector api={api} session={{session_id:'S',execution:{kind:'sandbox',state:'unavailable'}} as never}
    disabled={false} running create={create} refresh={vi.fn()} />);
  expect(screen.getByRole('button',{name:'执行位置：Docker 沙箱'}).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'查看改动'}));
  expect(await screen.findByRole('dialog',{name:'沙箱结果'})).toBeTruthy();
  expect(await screen.findByText(/当前沙箱无法取回/)).toBeTruthy();
  expect(options).not.toHaveBeenCalled();expect(create).not.toHaveBeenCalled();
});
it('requires explicit untracked selection and cannot select known credentials',async()=>{
  const {create}=setup(); fireEvent.click(screen.getByRole('button',{name:'执行位置：此计算机'}));
  fireEvent.click(await screen.findByRole('button',{name:'Python tests'}));
  const note=screen.getByRole('checkbox',{name:'notes.txt'}) as HTMLInputElement;
  expect(note.checked).toBe(false);
  expect(screen.getByRole('checkbox',{name:'.env'}).hasAttribute('disabled')).toBe(true);
  fireEvent.click(note); fireEvent.click(screen.getByRole('button',{name:'使用此环境'}));
  await waitFor(()=>expect(create).toHaveBeenCalledWith({execution:'sandbox',include_untracked:['notes.txt'],
    profile_id:'python',profile_name:'Python tests',expected_profile_revision:'a'.repeat(64)}));
});
it('keeps keyboard focus in the execution dialog when entering input preparation',async()=>{
  setup();await userEvent.click(screen.getByRole('button',{name:'执行位置：此计算机'}));
  await userEvent.click(await screen.findByRole('button',{name:'Python tests'}));
  expect(screen.getByRole('dialog',{name:'选择执行位置'}).contains(document.activeElement)).toBe(true);
  await userEvent.keyboard('{Escape}');expect(screen.queryByRole('dialog',{name:'选择执行位置'})).toBeNull();
});
it('locks environment changes while the original creation is unconfirmed',async()=>{
  // The original request/model equality assertions now follow first send in environment-draft.test.tsx.
  const {api,create,view}=setup();
  view.rerender(<ExecutionSelector api={api} session={null} disabled={false} create={create} refresh={vi.fn()} locked
    draft={{execution:'sandbox',include_untracked:[],profile_id:'python',profile_name:'Python tests',expected_profile_revision:'a'.repeat(64)}} />);
  fireEvent.click(screen.getByRole('button',{name:'执行位置：Python tests'}));
  expect((await screen.findByRole('button',{name:'Python tests'})).hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('button',{name:'此计算机'}).hasAttribute('disabled')).toBe(true);
  expect(screen.getByText(/再次发送会核对原请求/)).toBeTruthy();expect(create).not.toHaveBeenCalled();
});

it('shows refused input paths and keeps permanent preparation failure from retrying checkout',async()=>{
  const api=new WebApi('synthetic','11111111-1111-1111-1111-111111111111');
  vi.spyOn(api,'executionOptions').mockResolvedValue({host_available:true,sandbox_available:true,error_code:null,
    requires_trust:false,project_revision:1,network:'none',untracked:[],blocked:[]});
  render(<ExecutionSelector api={api} session={{execution:{kind:'sandbox',state:'failed',error:{code:'git_input_sensitive',paths:['config/.env.local']}}} as never}
    disabled={false} create={vi.fn()} refresh={vi.fn()} />);
  fireEvent.click(screen.getByRole('button',{name:'执行位置：Docker 沙箱'}));
  expect(await screen.findByText('config/.env.local')).toBeTruthy();
  expect(screen.queryByRole('button',{name:'核对并继续原准备'})).toBeNull();
});

it('retains the draft label after closing and reopening the menu',async()=>{
  const {api,create,view}=setup();
  view.rerender(<ExecutionSelector api={api} session={null} disabled={false} create={create} refresh={vi.fn()}
    draft={{execution:'sandbox',include_untracked:['notes.txt'],profile_name:'Original Python',profile_id:'python',expected_profile_revision:'a'.repeat(64)}} />);
  fireEvent.click(screen.getByRole('button',{name:'执行位置：Original Python'}));
  await screen.findByRole('button',{name:'Python tests'});
  fireEvent.click(screen.getByRole('button',{name:'关闭执行位置'}));
  expect(screen.getByRole('button',{name:'执行位置：Original Python'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'执行位置：Original Python'}));
  fireEvent.click(await screen.findByRole('button',{name:'Python tests'}));
  expect((screen.getByRole('checkbox',{name:'notes.txt'}) as HTMLInputElement).checked).toBe(true);
});
it('keeps management available while preparation or a run occupies the composer',async()=>{
  const api=new WebApi('synthetic');
  vi.spyOn(api,'sandboxManagement').mockResolvedValue({policy:{max_running:2,max_retained:10,idle_stop_seconds:0,sweep_seconds:30},usage:{running:0,retained:0},items:[]});
  render(<ExecutionSelector api={api} session={null} disabled running create={vi.fn()} refresh={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'管理沙箱'}));
  expect(await screen.findByRole('dialog',{name:'管理沙箱'})).toBeTruthy();
});
