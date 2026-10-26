import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, WebApi } from '../api';
import SandboxManager from './SandboxManager';
import userEvent from '@testing-library/user-event';

afterEach(()=>{cleanup();vi.restoreAllMocks();});
const item={binding_id:'a'.repeat(32),project_id:'P',session_id:'S',project_name:'接口测试',profile_name:'Python 测试',
  lifecycle:'running',last_used_at:100,error_code:null,busy:false,unsettled:false};
const policy={max_running:2,max_retained:10,idle_stop_seconds:1800,sweep_seconds:30};
function setup(items=[item]){
  const api=new WebApi('synthetic');
  const read=vi.spyOn(api,'sandboxManagement').mockResolvedValue({policy,usage:{running:items.length,retained:items.length},items});
  const act=vi.spyOn(api,'manageSandbox').mockResolvedValue({binding_id:item.binding_id,lifecycle:'stopped'});
  const close=vi.fn(),changed=vi.fn().mockResolvedValue(undefined);
  render(<SandboxManager api={api} onClose={close} onChanged={changed}/>);
  return {api,read,act,close,changed};
}
it('shows real usage and empty state without creating or probing containers',async()=>{
  const {act}=setup([]);
  expect(await screen.findByText('还没有保留的沙箱')).toBeTruthy();
  expect(screen.getByText('0 / 2')).toBeTruthy();expect(screen.getByText('0 / 10')).toBeTruthy();
  expect(act).not.toHaveBeenCalled();
});
it('shows registered state and requires a separate explicit destruction confirmation',async()=>{
  const {act,changed}=setup();
  await screen.findByText('接口测试');
  expect(screen.getByText('Python 测试')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'销毁…'}));expect(act).not.toHaveBeenCalled();
  expect(screen.getByText(/代码卷和状态卷/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'确认销毁沙箱和文件'}));
  await waitFor(()=>expect(act).toHaveBeenCalledWith(item.binding_id,'destroy',true,expect.any(String)));
  expect(changed).toHaveBeenCalledWith(item.binding_id);
});
it('protects active use and unsettled jobs, while allowing explicit cleanup of unknown resources',async()=>{
  setup([{...item,lifecycle:'unknown',unsettled:true}]);
  await screen.findByText('待核对');
  expect(screen.getByRole('button',{name:'停止'}).hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('button',{name:'核对原资源'}).hasAttribute('disabled')).toBe(false);
  expect(screen.getByRole('button',{name:'销毁…'}).hasAttribute('disabled')).toBe(false);
});
it('retains the same command identity after a lost response and does not retry automatically',async()=>{
  const {act}=setup();act.mockRejectedValueOnce(new TypeError('disconnected'));
  await screen.findByText('接口测试');fireEvent.click(screen.getByRole('button',{name:'停止'}));
  const retry=await screen.findByRole('button',{name:'核对原操作'});
  expect(act).toHaveBeenCalledTimes(1);const original=act.mock.calls[0];
  fireEvent.click(retry);await waitFor(()=>expect(act).toHaveBeenCalledTimes(2));
  expect(act.mock.calls[1]).toEqual(original);
});
it('retains rows on refresh errors, and keeps focus in the dialog',async()=>{
  const {read,act,close}=setup();await screen.findByText('接口测试');
  const dialog=screen.getByRole('dialog',{name:'管理沙箱'});expect(dialog.contains(document.activeElement)).toBe(true);
  read.mockRejectedValueOnce(new ApiError('env_unreachable'));
  fireEvent.click(within(dialog).getByRole('button',{name:'刷新沙箱列表'}));
  expect(await screen.findByRole('alert')).toBeTruthy();expect(screen.getByText('接口测试')).toBeTruthy();
  expect(act).not.toHaveBeenCalled();fireEvent.keyDown(dialog,{key:'Escape'});expect(close).toHaveBeenCalledOnce();
});
it('restores focus to the dialog after an action disables its originating button',async()=>{
  const {read}=setup();await screen.findByText('接口测试');
  const stop=screen.getByRole('button',{name:'停止'});
  read.mockImplementationOnce(async()=>{
    // Native browsers blur a newly disabled button; jsdom needs that focus event explicitly.
    stop.blur();return {policy,usage:{running:0,retained:1},items:[{...item,lifecycle:'stopped'}]};
  });
  await userEvent.click(stop);
  await waitFor(()=>expect(screen.getByText('已停止')).toBeTruthy());
  await waitFor(()=>expect(screen.getByRole('dialog',{name:'管理沙箱'}).contains(document.activeElement)).toBe(true));
});
