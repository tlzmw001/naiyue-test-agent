import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from './api';
import { useConversation } from './useConversation';
import type { SessionView } from './types';
afterEach(cleanup);
it.each([
  { pending: [], newer: 'newer draft', expectedDraft: 'newer draft' },
  { pending: ['older pending'], newer: undefined, expectedDraft: '' },
  { pending: ['older pending'], newer: 'newer draft', expectedDraft: 'newer draft' },
])('retries a frozen image request with pending=$pending and newer=$newer',async({pending,newer,expectedDraft})=>{
  const api=new WebApi('fixture');
  const session:SessionView={ session_id:'s',model_id:'A',workspace_root:null,state:'ready',updated_at:'',last_run_status:null,
    messages:[],busy:false,can_run:true,has_compaction:false,active_run_id:null,latest_run_id:null,active_tools:[],
    permission_mode:'Default',manual_review_required:false,pending_approval:false,live:pending.length ? {
      session_id:'s',run_id:'old',status:'stopped',done:true,messages:[],last_sequence:0,incomplete:false,
      manual_review_required:false,retained:pending } : null };
  vi.spyOn(api,'models').mockResolvedValue({default_model_id:'A',model_ids:['A'],capabilities:{A:{image_inputs:true}},
    image_input_limits:{max_images_per_message:4,max_input_bytes:1024,max_total_input_bytes:2048}});
  vi.spyOn(api,'sessions').mockResolvedValue([]);
  vi.spyOn(api,'session').mockResolvedValue(session);vi.spyOn(api,'prepare').mockResolvedValue(session);
  vi.spyOn(api,'pendingInputs').mockResolvedValue({inputs:[]});
  const start=vi.spyOn(api,'start').mockRejectedValueOnce(new TypeError('network unavailable'))
    .mockResolvedValue({session_id:'s',run_id:'run'});
  vi.spyOn(api,'run').mockResolvedValue({session_id:'s',run_id:'run',status:'completed',done:true,messages:[],last_sequence:0,incomplete:false,manual_review_required:false});
  const {result}=renderHook(()=>useConversation(api));
  await waitFor(()=>expect(result.current.model).toBe('A'));
  await act(async()=>result.current.selectSession('s'));
  act(()=>{result.current.setDraft('original');result.current.imageInputs.write([{client_id:'image',name:'one.png',mime_type:'image/png',base64:'AA==',bytes:1}]);});
  await act(async()=>result.current.submit(false));
  const combined=[...pending,'original'].join('\n\n');
  expect(result.current.imageInputs.uncertain?.text).toBe(combined);
  if (newer !== undefined) act(()=>result.current.setDraft(newer));
  await act(async()=>result.current.submit(false));
  expect(start).toHaveBeenCalledTimes(1);
  await act(async()=>result.current.retryImageSubmission());
  expect(start).toHaveBeenCalledTimes(2);
  expect(start.mock.calls[0][2]).toBe(start.mock.calls[1][2]);
  expect(start.mock.calls[1][1]).toBe(combined);
  expect(result.current.draft).toBe(expectedDraft);
  expect(result.current.pending).toEqual([]);
  expect(result.current.imageInputs.current).toEqual([]);
  expect(result.current.imageInputs.uncertain).toBeUndefined();
});
