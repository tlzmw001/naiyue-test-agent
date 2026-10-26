import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {WebApi} from '../api';
import ResultDelivery from './ResultDelivery';
import type {DeliveryCommand,DeliveryOperation} from '../deliveryTypes';

afterEach(()=>{cleanup();vi.restoreAllMocks();});
function setup(operations:DeliveryOperation[]=[]){
  const api=new WebApi('fixture');
  vi.spyOn(api,'deliveryOptions').mockResolvedValue({discarded:false,actions:{branch:{available:true},merge:{available:false,reason:'git_delivery_dirty'},discard:{available:true}},operations});
  vi.spyOn(api,'deliver').mockImplementation(async(_s,_r,command)=>({operation_id:command.operation_id,result_id:'R',command,state:'completed',result:{},error_code:null,created_at:''}));
  vi.spyOn(api,'acknowledgeDelivery').mockResolvedValue({} as never);
  render(<ResultDelivery api={api} sessionId="S" resultId="R" commit={'a'.repeat(40)} onBusy={vi.fn()} onDiscard={vi.fn()}/>);
  return api;
}
it('requires review and a branch name before explicitly creating a ref',async()=>{
  const api=setup();fireEvent.click(await screen.findByRole('button',{name:'创建本机分支'}));
  expect(api.deliver).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('textbox',{name:'分支名称'}),{target:{value:'review/task'}});
  fireEvent.click(screen.getByRole('button',{name:'确认创建分支'}));
  await waitFor(()=>expect(api.deliver).toHaveBeenCalledWith('S','R',expect.objectContaining({action:'branch',branch:'review/task'})));
});
it('offers only three local actions without remote or PR controls',async()=>{
  setup();await screen.findByRole('button',{name:'创建本机分支'});
  expect(screen.getByRole('button',{name:'合入当前分支'})).toBeTruthy();
  expect(screen.getByRole('button',{name:'丢弃任务引用'})).toBeTruthy();
  expect(screen.queryByRole('button',{name:'推送新远端分支'})).toBeNull();
  expect(screen.queryByRole('combobox',{name:'推送目标'})).toBeNull();
  expect(screen.queryByText(/PR/)).toBeNull();
});
it('explains merge refusal without starting a delivery',async()=>{
  const api=setup();await screen.findByText(/本机有未保存到提交的改动/);
  expect((screen.getByRole('button',{name:'合入当前分支'}) as HTMLButtonElement).disabled).toBe(true);
  expect(api.deliver).not.toHaveBeenCalled();
});
it('keeps the original command after a lost receipt and on reopen',async()=>{
  const command:DeliveryCommand={operation_id:'d'.repeat(32),action:'merge'};
  const api=setup([{operation_id:command.operation_id,result_id:'R',command,state:'unknown',result:null,error_code:'git_delivery_unconfirmed',created_at:''}]);
  fireEvent.click(await screen.findByRole('button',{name:'核对原交付'}));
  await waitFor(()=>expect(api.deliver).toHaveBeenCalledWith('S','R',command));
});
it('requires a separate manual acknowledgement and explains that it does not undo changes',async()=>{
  const command:DeliveryCommand={operation_id:'d'.repeat(32),action:'merge'};
  const api=setup([{operation_id:command.operation_id,result_id:'R',command,state:'unknown',result:null,error_code:null,created_at:''}]);
  fireEvent.click(await screen.findByRole('button',{name:'已在本机人工核对'}));
  expect(api.acknowledgeDelivery).not.toHaveBeenCalled();
  expect(screen.getByText(/不会撤销文件或提交/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'确认结束核对'}));
  await waitFor(()=>expect(api.acknowledgeDelivery).toHaveBeenCalledWith('S','R',command.operation_id));
});
