import { useEffect, useRef, useState } from 'react';
import type { ImageMetadata } from '../imageTypes';
import { errorText } from '../api';
import ImagePreview from './ImagePreview';
import styles from './Images.module.css';
export type ImageLoader = (messageId: string, image: ImageMetadata, signal: AbortSignal) => Promise<Blob>;
function SavedImage({ messageId, image, load }: { messageId: string; image: ImageMetadata; load: ImageLoader }) {
  const [url, setUrl] = useState('');
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const request = useRef<AbortController | null>(null);
  const objectUrl = useRef('');
  useEffect(() => () => { request.current?.abort(); if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); }, []);
  async function preview() {
    if (url) { setOpen(true); return; }
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setLoading(true); setError('');
    try {
      const blob = await load(messageId, image, controller.signal);
      if (controller.signal.aborted) return;
      const value = URL.createObjectURL(blob); objectUrl.current = value; setUrl(value); setOpen(true);
    } catch (failure) { if (!controller.signal.aborted) setError(errorText(failure)); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  }
  return <div>
    <button type="button" className={styles.saved} disabled={loading} onClick={() => void preview()} aria-label={`查看图片：${image.name}`}>
      <span>{image.name}</span><small>{loading ? '读取中…' : `${image.width} × ${image.height} · 点击预览${image.first_frame_only ? ' · GIF仅首帧' : ''}`}</small>
    </button>
    {error && <p className={styles.warning} role="status">{error} 点击图片可重试。</p>}
    {open && <ImagePreview url={url} name={image.name} close={() => setOpen(false)} />}
  </div>;
}
export default function MessageImages({ messageId, images, load }: { messageId: string; images: ImageMetadata[]; load: ImageLoader }) {
  return <div className={styles.attachments} aria-label="消息图片">{images.map(image =>
    <SavedImage key={`${image.index}:${image.source_revision}`} messageId={messageId} image={image} load={load} />)}</div>;
}
