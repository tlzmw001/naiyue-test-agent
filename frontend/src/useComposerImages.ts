import type { useConversationDrafts } from './useConversationDrafts';
import { useEffect, useRef, useState, type RefObject } from 'react';
import type { WebApi } from './api';
import { errorText } from './api';
import type { DraftImage, ImageConfiguration, PendingImageInput, FrozenImageRequest } from './imageTypes';

function read(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('图片读取失败，请重试。'));
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.readAsDataURL(file);
  });
}
export function useComposerImages(api: WebApi, selected: RefObject<string>, drafts: ReturnType<typeof useConversationDrafts>) {
  const requests = useRef<Record<string,FrozenImageRequest>>({});
  const [, repaint] = useState(0);
  const [config, configure] = useState<ImageConfiguration>({});
  const store = drafts.imageRef;
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [reading, setReading] = useState<string[]>([]);
  const readingRef = useRef(new Set<string>());
  const [pending, setPending] = useState<{ scope: string; inputs: PendingImageInput[] }>({scope:'new',inputs:[]});
  const generation = useRef(0);
  const id = selected.current;
  const write = drafts.writeImages;
  const report = (text: string, key = selected.current) => setErrors(old => ({ ...old, [key]: text }));
  async function addFiles(files: File[]) {
    const key = selected.current, limits = config.image_input_limits;
    if (readingRef.current.has(key)) return;
    if (!limits) { report('图片配置尚未加载，请稍后重试。', key); return; }
    const before = store.current[key] ?? [];
    if (before.length + files.length > limits.max_images_per_message) { report('图片数量超过配置上限。', key); return; }
    if (files.some(file => file.size > limits.max_input_bytes) || before.reduce((n, image) => n + image.bytes, 0) + files.reduce((n, file) => n + file.size, 0) > limits.max_total_input_bytes) { report('图片大小超过配置上限。', key); return; }
    if (files.some(file => !/^image\/(png|jpeg|webp|gif|bmp)$/.test(file.type))) { report('请选择 PNG、JPEG、WebP、GIF 或 BMP 图片。', key); return; }
    readingRef.current.add(key); setReading([...readingRef.current]); report('', key);
    try {
      const added: DraftImage[] = [];
      for (const file of files) added.push({ client_id: crypto.randomUUID(), name: file.name || '截图.png', mime_type: file.type, bytes: file.size, base64: await read(file) });
      write([...(store.current[key] ?? []), ...added], key);
    } catch (error) { report(errorText(error), key); }
    finally { readingRef.current.delete(key); setReading([...readingRef.current]); }
  }
  async function refreshPending() {
    const id = selected.current;
    if (id === 'new' || !config.image_input_limits) { setPending({scope:id,inputs:[]}); return; }
    const version = ++generation.current;
    try {
      const value = await api.pendingInputs(id);
      if (selected.current === id && generation.current === version) setPending({scope:id,inputs:value.inputs});
    } catch (error) { if (selected.current === id) report(errorText(error), id); }
  }
  useEffect(() => { void refreshPending(); return () => { ++generation.current; }; }, [api, id, config.image_input_limits]);
  async function restore(input: PendingImageInput): Promise<string | null> {
    const key = selected.current;
    if (readingRef.current.has(key)) return null;
    if (store.current[key]?.some(image => image.pendingId === input.message_id)) { report('这条输入已恢复到输入框。', key); return null; }
    readingRef.current.add(key); setReading([...readingRef.current]);
    try {
      const added: DraftImage[] = [];
      for (const image of input.images) {
        const blob = await api.pendingImage(key, input.message_id, image.index);
        added.push({ client_id: crypto.randomUUID(), name: image.name, mime_type: image.mime_type, base64: await read(blob), bytes: blob.size, pendingId: input.message_id });
      }
      const limits = config.image_input_limits;
      const before = store.current[key] ?? [];
      if (!limits || before.length + added.length > limits.max_images_per_message || [...before, ...added].reduce((n, image) => n + image.bytes, 0) > limits.max_total_input_bytes) throw new Error('恢复图片超过配置上限，请先移除输入框中的图片。');
      const text = input.text_truncated ? await api.pendingInputText(key,input.message_id) : input.text;
      drafts.appendDraft(text, added, key);
      return text;
    } catch (error) { report(errorText(error), key); return null; }
    finally { readingRef.current.delete(key); setReading([...readingRef.current]); }
  }
  async function discard(input: PendingImageInput) {
    try { await api.removePendingInput(id, input.message_id); await refreshPending(); }
    catch (error) { report(errorText(error), id); }
  }
  return { current: store.current[id] ?? [], ref: store, write, configure, limits: config.image_input_limits,
    supports: (model: string) => config.capabilities?.[model]?.image_inputs === true,
    error: errors[id] ?? '', reading: reading.includes(id), addFiles, report,
    remove: (clientId: string) => write((store.current[id] ?? []).filter(image => image.client_id !== clientId)),
    move: drafts.moveDraft,
    forget: (key: string) => { delete requests.current[key]; drafts.forget(key); },
    uncertain: requests.current[id],
    freeze: (request: FrozenImageRequest) => { requests.current[request.sessionId] = request; repaint(n=>n+1); return request; },
    settle: (key: string, requestId: string) => { if (requests.current[key]?.requestId === requestId) { delete requests.current[key]; repaint(n=>n+1); } },
    pending: pending.scope === id ? pending.inputs : [], refreshPending, restore, discard };
}
