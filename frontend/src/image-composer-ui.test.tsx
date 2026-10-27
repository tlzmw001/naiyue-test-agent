import { useConversationDrafts } from './useConversationDrafts';
import { act, cleanup, createEvent, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useEffect, useRef, useState } from 'react';
import Composer from './components/Composer';
import { WebApi } from './api';
import { useComposerImages } from './useComposerImages';
const send=vi.fn();
beforeEach(() => {
  vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL: vi.fn(()=>'blob:test-image'), revokeObjectURL: vi.fn() }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); send.mockReset(); });
function Fixture({ supported = true, busy = false, sending = false, uncertain = false, pending = [], onStop = () => {} }: { supported?: boolean; busy?: boolean; sending?: boolean; uncertain?: boolean; pending?: string[]; onStop?: () => void }) {
  const [text,setText]=useState('');
  const selected=useRef('new');
  const api=useRef(new WebApi('fixture')).current;
  const inputs=useComposerImages(api,selected,useConversationDrafts(selected));
  useEffect(() => {
    vi.spyOn(api,'sessionControlOptions').mockResolvedValue({ tools:[],default_active_tools:[],permission_modes:['Default'],default_permission_mode:'Default' });
    inputs.configure({ image_input_limits:{max_images_per_message:2,max_input_bytes:1024,max_total_input_bytes:2048} });
  },[]);
  useEffect(() => {
    if (uncertain) inputs.freeze({ requestId:'frozen',sessionId:'new',kind:'message',text:'older pending',draftText:'',images:[] });
  },[uncertain]);
  return <Composer text={text} setText={setText} pending={pending} setPending={()=>{}} pendingMode={false} busy={busy}
    stopping={false} sending={sending} blocked={false} send={send} stop={onStop} api={api} session={null}
    controlsDisabled={false} selectTools={()=>{}} selectMode={()=>{}} openModel={()=>{}} compact={()=>{}}
    openContext={()=>{}} openAgents={()=>{}} imageInputs={inputs} imageSupported={supported} />;
}
function png(name='截图.png') { return new File(['test-pixels'],name,{type:'image/png'}); }
it('pastes image items, ignores same-event source text and enables image-only send',async()=>{
  render(<Fixture/>);
  await waitFor(()=>expect((screen.getByLabelText('添加图片') as HTMLInputElement).disabled).toBe(false));
  const event=createEvent.paste(screen.getByRole('textbox',{name:'消息'}),{clipboardData:{items:[{kind:'file',getAsFile:()=>png()}],getData:()=> 'source text'}});
  fireEvent(screen.getByRole('textbox',{name:'消息'}),event);
  expect(event.defaultPrevented).toBe(true);
  await screen.findByRole('button',{name:'预览图片：截图.png'});
  expect((screen.getByRole('textbox',{name:'消息'}) as HTMLTextAreaElement).value).toBe('');
  fireEvent.click(screen.getByRole('button',{name:'发送消息'}));
  expect(send).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button',{name:'移除图片：截图.png'}));
  expect(screen.queryByRole('button',{name:'预览图片：截图.png'})).toBeNull();
});
it('preserves plain text paste and supports file selection/drop with unsupported model guard',async()=>{
  render(<Fixture supported={false}/>);
  await waitFor(()=>expect((screen.getByLabelText('添加图片') as HTMLInputElement).disabled).toBe(false));
  const editor=screen.getByRole('textbox',{name:'消息'});
  const text=createEvent.paste(editor,{clipboardData:{items:[],getData:()=> 'normal'}});
  fireEvent(editor,text); expect(text.defaultPrevented).toBe(false);
  fireEvent.change(screen.getByLabelText('添加图片'),{target:{files:[png('first.png')]}});
  await screen.findByRole('button',{name:'预览图片：first.png'});
  fireEvent.drop(editor,{dataTransfer:{files:[png('second.png')],types:['Files']}});
  await screen.findByRole('button',{name:'预览图片：second.png'});
  expect((screen.getByRole('button',{name:'发送消息'}) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/当前模型不支持或尚未确认支持图片/)).toBeTruthy();
  expect(send).not.toHaveBeenCalled();
});

it('keeps stop usable while a running conversation is submitting images',async()=>{
  const stop=vi.fn();
  const {rerender}=render(<Fixture busy onStop={stop}/>);
  await waitFor(()=>expect((screen.getByLabelText('添加图片') as HTMLInputElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('添加图片'),{target:{files:[png()]}});
  await screen.findByRole('button',{name:'预览图片：截图.png'});
  rerender(<Fixture busy sending onStop={stop}/>);
  fireEvent.click(screen.getByRole('button',{name:'停止运行'}));
  expect(stop).toHaveBeenCalledOnce();
  expect(screen.getByRole('button',{name:'预览图片：截图.png'})).toBeTruthy();
});

it('resets the send mode after the complete image-only draft is cleared',async()=>{
  render(<Fixture busy/>);
  await waitFor(()=>expect((screen.getByLabelText('添加图片') as HTMLInputElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('添加图片'),{target:{files:[png('one.png')]}});
  await screen.findByRole('button',{name:'预览图片：one.png'});
  fireEvent.change(screen.getByRole('combobox',{name:'发送方式'}),{target:{value:'steering'}});
  fireEvent.click(screen.getByRole('button',{name:'移除图片：one.png'}));
  fireEvent.change(screen.getByLabelText('添加图片'),{target:{files:[png('two.png')]}});
  await screen.findByRole('button',{name:'预览图片：two.png'});
  expect((screen.getByRole('combobox',{name:'发送方式'}) as HTMLSelectElement).value).toBe('follow_up');
});

it('freezes pending text carried by an image request while leaving the new draft editable',async()=>{
  const {rerender}=render(<Fixture pending={['older pending']}/>);
  await waitFor(()=>expect((screen.getByLabelText('添加图片') as HTMLInputElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('添加图片'),{target:{files:[png()]}});
  await screen.findByRole('button',{name:'预览图片：截图.png'});
  rerender(<Fixture pending={['older pending']} uncertain sending/>);
  expect((screen.getByRole('textbox',{name:'待发送消息 1'}) as HTMLTextAreaElement).disabled).toBe(true);
  expect((screen.getByRole('button',{name:'移除待发送消息 1'}) as HTMLButtonElement).disabled).toBe(true);
  rerender(<Fixture pending={['older pending']} uncertain/>);
  expect((screen.getByRole('textbox',{name:'待发送消息 1'}) as HTMLTextAreaElement).disabled).toBe(true);
  expect((screen.getByRole('button',{name:'移除待发送消息 1'}) as HTMLButtonElement).disabled).toBe(true);
  const editor=screen.getByRole('textbox',{name:'消息'}) as HTMLTextAreaElement;
  expect(editor.disabled).toBe(false);
  fireEvent.change(editor,{target:{value:'next draft'}});
  expect(editor.value).toBe('next draft');
});
