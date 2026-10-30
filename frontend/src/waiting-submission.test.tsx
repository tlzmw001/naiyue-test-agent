import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from './api';
import { useConversation } from './useConversation';
import type { SessionView } from './types';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
function setup() {
  const api=new WebApi('fixture');
  const views:Record<string,SessionView>={};
  for(const sid of ['A','B'])views[sid]={session_id:sid,model_id:'model-'+sid,workspace_root:null,state:'ready',
    updated_at:'',last_run_status:null,conversation_revision:0,messages:[],busy:false,can_run:sid==='B',has_compaction:false,
    active_tools:[],permission_mode:'Default',manual_review_required:false,active_run_id:null,latest_run_id:null,
    pending_approval:false,live:null,execution:{kind:'sandbox',state:sid==='A'?'preparing':'ready',binding_id:'binding-'+sid}};
  vi.spyOn(api,'models').mockResolvedValue({default_model_id:'model-A',model_ids:['model-A','model-B']});
  vi.spyOn(api,'sessions').mockImplementation(async()=>Object.values(views));
  vi.spyOn(api,'prepare').mockImplementation(async sid=>views[sid]);
  vi.spyOn(api,'session').mockImplementation(async sid=>views[sid]);
  vi.spyOn(api,'createSelected').mockResolvedValue({session_id:'A'});
  vi.spyOn(api,'start').mockImplementation(async(sid,_text,request_id)=>{
    const receipt={session_id:sid,request_id:request_id!,status:'waiting_environment' as const};
    views[sid]={...views[sid],pending_submission:{request_id:request_id!,status:'waiting_environment'}};
    return receipt;
  });
  vi.spyOn(api,'run');
  return {api,views,...renderHook(()=>useConversation(api))};
}

it('accepts A once and releases navigation without waiting for environment readiness',async()=>{
  const {api,views,result}=setup();
  await waitFor(()=>expect(result.current.model).toBe('model-A'));
  await act(async()=>result.current.newSession({execution:'sandbox',include_untracked:[]}));
  act(()=>result.current.setDraft('original A'));
  await act(async()=>result.current.submit(false));
  expect(result.current.submitting).toBe(false);
  expect(result.current.session?.pending_submission?.status).toBe('waiting_environment');
  expect(api.start).toHaveBeenCalledExactlyOnceWith('A','original A',expect.any(String),0,[],[],true);
  expect(api.run).not.toHaveBeenCalled();
  expect(result.current.draft).toBe('original A');
  await act(async()=>result.current.selectSession('B'));
  act(()=>result.current.setDraft('B unsent'));
  views.A={...views.A,pending_submission:{request_id:vi.mocked(api.start).mock.calls[0][2]!,status:'completed',run_id:'real-A'},
    execution:{...views.A.execution!,state:'ready'}};
  expect(result.current.session?.session_id).toBe('B');expect(result.current.draft).toBe('B unsent');
  await act(async()=>result.current.selectSession('A'));
  expect(result.current.draft).toBe('');expect(api.start).toHaveBeenCalledTimes(1);
  await act(async()=>result.current.selectSession('B'));
  expect(result.current.draft).toBe('B unsent');
});

it('discovers execution-only failure through normal synchronization',async()=>{
  const {views,result}=setup();
  await waitFor(()=>expect(result.current.model).toBe('model-A'));
  await act(async()=>result.current.selectSession('A'));
  vi.useFakeTimers();
  views.A={...views.A,execution:{...views.A.execution!,state:'unavailable',error:{code:'env_unreachable'}}};
  // Re-open with fake timers to install the regular discovery timer on this clock.
  await act(async()=>result.current.selectSession('B'));
  await act(async()=>result.current.selectSession('A'));
  views.A={...views.A,execution:{...views.A.execution!,state:'failed',error:{code:'git_input_changed'}}};
  await act(async()=>vi.advanceTimersByTimeAsync(3000));
  expect(result.current.session?.execution?.state).toBe('failed');
});
