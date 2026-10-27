import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import MessageImages from './MessageImages';
import type { ImageMetadata } from '../imageTypes';
const image:ImageMetadata={index:1,name:'shot.png',mime_type:'image/png',width:20,height:10,bytes:32,image_digest:'b'.repeat(64),source_revision:'a'.repeat(64)};
beforeEach(()=>vi.stubGlobal('URL',Object.assign(class extends URL {},{createObjectURL:vi.fn(()=>'blob:normalized-image'),revokeObjectURL:vi.fn()})));
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('loads history pixels only on demand and releases its object URL on removal',async()=>{
  const load=vi.fn().mockResolvedValue(new Blob(['normalized-image'],{type:'image/png'}));
  const {unmount}=render(<MessageImages messageId="message" images={[image]} load={load}/>);
  expect(load).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'查看图片：shot.png'}));
  await waitFor(()=>expect(URL.createObjectURL).toHaveBeenCalledOnce());
  expect(load).toHaveBeenCalledWith('message',image,expect.any(AbortSignal));
  unmount();expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:normalized-image');
});
it('aborts a late history read when its conversation is removed without creating an orphan URL',async()=>{
  let complete!:(blob:Blob)=>void;let signal!:AbortSignal;
  const load=vi.fn((_id:string,_image:ImageMetadata,request:AbortSignal)=>{signal=request;return new Promise<Blob>(resolve=>{complete=resolve;});});
  const {unmount}=render(<MessageImages messageId="message" images={[image]} load={load}/>);
  fireEvent.click(screen.getByRole('button',{name:'查看图片：shot.png'}));
  unmount();expect(signal.aborted).toBe(true);
  await act(async()=>{complete(new Blob(['normalized-image']));});
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});
