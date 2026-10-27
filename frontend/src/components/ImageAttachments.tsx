import { useEffect, useState } from 'react';
import type { DraftImage } from '../imageTypes';
import type { useComposerImages } from '../useComposerImages';
import ImagePreview from './ImagePreview';
import { Icon } from './Icons';
import styles from './Images.module.css';
export type ImageInputs = ReturnType<typeof useComposerImages>;
function Attachment({ image, remove }: { image: DraftImage; remove: () => void }) {
  const [url, setUrl] = useState('');
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const data = Uint8Array.from(atob(image.base64), c => c.charCodeAt(0));
    const value = URL.createObjectURL(new Blob([data], { type: image.mime_type }));
    setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [image.base64, image.mime_type]);
  return <div className={styles.attachment}>
    <button type="button" className={styles.thumbnail} aria-label={`预览图片：${image.name}`} onClick={() => setOpen(true)}>
      {url && <img src={url} alt="" />}<span>{image.name}{image.mime_type === 'image/gif' && <small> · 仅发送首帧</small>}</span>
    </button>
    <button type="button" className={styles.remove} aria-label={`移除图片：${image.name}`} onClick={remove}><Icon name="close" /></button>
    {open && <ImagePreview url={url} name={image.name} close={() => setOpen(false)} />}
  </div>;
}
export default function ImageAttachments({ inputs, supported }: { inputs: ImageInputs; supported: boolean }) {
  return <>
    {!!inputs.current.length && <div className={styles.attachments} aria-label="图片附件">
      {inputs.current.map(image => <Attachment key={image.client_id} image={image} remove={() => inputs.remove(image.client_id)} />)}
    </div>}
    {!!inputs.current.length && <p className={styles.hint}>图片草稿只保留在当前页面，刷新或关闭后不会恢复。发送后保存处理后的版本。
      {inputs.limits?.max_dimension ? ` 最长边按配置处理到 ${inputs.limits.max_dimension} 像素以内。` : ''}</p>}
    {!!inputs.current.length && !supported && <p className={styles.warning} role="status">当前模型不支持或尚未确认支持图片。请切换模型或移除图片，输入会保留。</p>}
    {inputs.reading && <p className={styles.hint} role="status">正在读取图片…</p>}
    {inputs.error && <p className={styles.warning} role="status">{inputs.error}</p>}
  </>;
}
