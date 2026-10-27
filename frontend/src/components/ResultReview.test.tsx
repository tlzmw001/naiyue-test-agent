import { act,cleanup,fireEvent,render,screen,waitFor } from '@testing-library/react';
import { afterEach,expect,it,vi } from 'vitest';
import { WebApi } from '../api';
import ResultReview from './ResultReview';
import userEvent from '@testing-library/user-event';

afterEach(()=>{cleanup();vi.restoreAllMocks();});
function setup(){
  const api=new WebApi('fixture');
  vi.spyOn(api,'results').mockResolvedValue({items:[]});
  vi.spyOn(api,'resultOptions').mockResolvedValue({untracked:['new.txt','.env'],blocked:['.env']});
  vi.spyOn(api,'deliveryOptions').mockResolvedValue({discarded:false,actions:{branch:{available:true},merge:{available:true},discard:{available:true}},operations:[]});
  const result={result_id:'a'.repeat(32),state:'ready',operation_state:'completed',created_at:'2026-10-25T10:20:00Z',
    include_untracked:['new.txt'],files:[{path:'new.txt',old_path:'new.txt',status:'A',old_mode:'000000',new_mode:'100644',symlink:false,attributes:false}],warnings:[],
    receipt:{input_snapshot:'b'.repeat(40),output_commit:'c'.repeat(40),excluded:[]}};
  vi.spyOn(api,'captureResult').mockResolvedValue(result as never);
  vi.spyOn(api,'resultDiff').mockResolvedValue({path:'new.txt',patch:'@@ -0,0 +1 @@\n+agent output\n',binary:false} as never);
  const close=vi.fn();render(<ResultReview api={api} sessionId="S" onClose={close}/>);
  return {api,close,result};
}
it('takes only explicitly selected new files and reviews the actual input to output',async()=>{
  const {api}=setup();
  const input=await screen.findByRole('checkbox',{name:'new.txt'}) as HTMLInputElement;
  expect(input.checked).toBe(false);expect((screen.getByRole('checkbox',{name:'.env'}) as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(input);fireEvent.click(screen.getByRole('button',{name:'取回当前结果'}));
  await waitFor(()=>expect(api.captureResult).toHaveBeenCalledWith('S',expect.stringMatching(/^[0-9a-f]{32}$/),['new.txt']));
  fireEvent.click(await screen.findByRole('button',{name:/新增.*new.txt/}));
  expect((await screen.findByRole('region',{name:'结果差异'})).textContent).toContain('+agent output');
  expect(screen.getByText('bbbbbbbb → cccccccc')).toBeTruthy();
  expect(await screen.findByRole('region',{name:'交付审查后的结果'})).toBeTruthy();
  expect(api.deliveryOptions).toHaveBeenCalledWith('S','a'.repeat(32));
});
it('keeps the capture identity and selection when an acknowledgement is lost',async()=>{
  const {api}=setup();vi.mocked(api.captureResult).mockRejectedValueOnce(new Error('offline'));
  await screen.findByRole('checkbox',{name:'new.txt'});
  fireEvent.click(screen.getByRole('button',{name:'取回当前结果'}));await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button',{name:'核对原取回请求'}));
  await waitFor(()=>expect(api.captureResult).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.captureResult).mock.calls[0]).toEqual(vi.mocked(api.captureResult).mock.calls[1]);
});
it('can inspect saved results when the container is unavailable',async()=>{
  const {api,result}=setup();
  vi.mocked(api.resultOptions).mockRejectedValue(new Error('container gone'));
  vi.mocked(api.results).mockResolvedValue({items:[result as never]});
  cleanup();render(<ResultReview api={api} sessionId="S" onClose={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:/查看结果 aaaaaaaa/}));
  fireEvent.click(await screen.findByRole('button',{name:/新增.*new.txt/}));
  await screen.findByRole('region',{name:'结果差异'});
  expect(api.captureResult).not.toHaveBeenCalled();
});
it('opens the newest verified saved result without capturing or requiring a record click',async()=>{
  const {api,result}=setup();
  vi.mocked(api.results).mockResolvedValue({items:[{...result,result_id:'d'.repeat(32),state:'unknown',receipt:null} as never,result as never]});
  cleanup();render(<ResultReview api={api} sessionId="S" onClose={vi.fn()}/>);
  expect(await screen.findByRole('button',{name:/新增.*new.txt/})).toBeTruthy();
  expect((screen.getByRole('button',{name:'查看结果 aaaaaaaa'})).getAttribute('aria-pressed')).toBe('true');
  expect(api.captureResult).not.toHaveBeenCalled();
});
it('keeps a newly captured result selected when the initial history arrives late',async()=>{
  const {api,result}=setup();cleanup();
  const old={...result,result_id:'d'.repeat(32),files:[{...result.files[0],path:'older.txt'}]};
  let release!:(value:{items:never[]})=>void;
  vi.mocked(api.results).mockImplementation(()=>new Promise(resolve=>{release=resolve;}));
  render(<ResultReview api={api} sessionId="S" onClose={vi.fn()}/>);
  await screen.findByRole('checkbox',{name:'new.txt'});
  fireEvent.click(screen.getByRole('button',{name:'取回当前结果'}));
  await screen.findByRole('button',{name:/新增.*new.txt/});
  await act(async()=>release({items:[old as never]}));
  expect(screen.getByRole('button',{name:/新增.*new.txt/})).toBeTruthy();
  expect(screen.getByRole('button',{name:'查看结果 aaaaaaaa'}).getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByRole('button',{name:'查看结果 dddddddd'})).toBeTruthy();
});
it('allows reading saved changes during a run but disables capturing and local delivery',async()=>{
  const {api,result}=setup();cleanup();
  vi.mocked(api.results).mockResolvedValue({items:[result as never]});
  render(<ResultReview api={api} sessionId="S" readOnly onClose={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:/新增.*new.txt/}));
  await screen.findByRole('region',{name:'结果差异'});
  for(const name of ['取回当前结果','创建本机分支','合入当前分支','丢弃任务引用']){
    expect((await screen.findByRole('button',{name})).hasAttribute('disabled')).toBe(true);
  }
  expect(api.captureResult).not.toHaveBeenCalled();
});
it('returns keyboard focus to the caller on Escape',async()=>{
  const {close}=setup();const dialog=screen.getByRole('dialog',{name:'沙箱结果'});
  fireEvent.keyDown(dialog,{key:'Escape'});expect(close).toHaveBeenCalledOnce();
});
it.each([
  ['branch','创建本机分支','确认创建分支'],
  ['merge','合入当前分支','确认合入'],
  ['discard','丢弃任务引用','确认丢弃引用'],
])('keeps keyboard focus inside the dialog after %s removes its confirmation',async(action,label,confirmation)=>{
  const {api,result,close}=setup();cleanup();
  vi.mocked(api.results).mockResolvedValue({items:[result as never]});
  const actions={branch:{available:true},merge:{available:true},discard:{available:true}};
  vi.mocked(api.deliveryOptions).mockResolvedValueOnce({discarded:false,actions,operations:[]})
    .mockResolvedValue({discarded:action==='discard',actions,operations:[]});
  vi.spyOn(api,'deliver').mockResolvedValue({state:'completed'} as never);
  render(<ResultReview api={api} sessionId="S" onClose={close}/>);
  await userEvent.click(await screen.findByRole('button',{name:label}));
  if(action==='branch')await userEvent.type(screen.getByRole('textbox',{name:'分支名称'}),'review');
  await userEvent.click(screen.getByRole('button',{name:confirmation}));
  await screen.findByText('交付已完成。');
  await waitFor(()=>expect(screen.getByRole('button',{name:'关闭沙箱结果'}).hasAttribute('disabled')).toBe(false));
  if(action==='discard')expect(screen.getByText(/任务引用已丢弃/)).toBeTruthy();
  expect(screen.getByRole('dialog',{name:'沙箱结果'}).contains(document.activeElement)).toBe(true);
  expect(api.deliver).toHaveBeenCalledExactlyOnceWith('S',result.result_id,expect.objectContaining({action}));
  await userEvent.keyboard('{Escape}');expect(close).toHaveBeenCalledOnce();
});

it('labels delivery work separately and prevents another capture while it runs',async()=>{
  const {api,result}=setup();let finish!:(value:never)=>void;
  vi.spyOn(api,'deliver').mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  await screen.findByRole('checkbox',{name:'new.txt'});
  fireEvent.click(screen.getByRole('button',{name:'取回当前结果'}));
  fireEvent.click(await screen.findByRole('button',{name:'创建本机分支'}));
  fireEvent.change(screen.getByRole('textbox',{name:'分支名称'}),{target:{value:'review'}});
  fireEvent.click(screen.getByRole('button',{name:'确认创建分支'}));
  try{
    expect((screen.getByRole('button',{name:'交付处理中…'}) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button',{name:'关闭沙箱结果'}) as HTMLButtonElement).disabled).toBe(true);
  }finally{
    finish({operation_id:'d'.repeat(32),result_id:result.result_id,command:{action:'branch'},state:'completed'} as never);
    await waitFor(()=>expect((screen.getByRole('button',{name:'关闭沙箱结果'}) as HTMLButtonElement).disabled).toBe(false));
  }
});

it('keeps Tab inside the result dialog while capture buttons are disabled',async()=>{
  render(<button>Behind dialog</button>);
  const {api,result}=setup();let finish!:(value:never)=>void;
  vi.mocked(api.captureResult).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  await screen.findByRole('checkbox',{name:'new.txt'});
  fireEvent.click(screen.getByRole('button',{name:'取回当前结果'}));
  const summary=screen.getByText(/选择额外取回的文件/);summary.focus();
  try{await userEvent.tab();expect(document.activeElement).toBe(summary);}
  finally{finish(result as never);await waitFor(()=>expect(screen.getByRole('button',{name:'关闭沙箱结果'}).hasAttribute('disabled')).toBe(false));}
});
