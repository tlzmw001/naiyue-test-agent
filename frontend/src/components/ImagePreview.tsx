import { useEffect, useRef } from 'react';
import styles from './Images.module.css';
export default function ImagePreview({ url, name, close }: { url: string; name: string; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal?.();
    return () => previous?.focus();
  }, []);
  return <dialog ref={dialog} className={styles.dialog} aria-label={`图片预览：${name}`} onCancel={close}
    onClick={event => { if (event.target === dialog.current) close(); }}>
    <div className={styles.toolbar}><span>{name}</span><a href={url} download={name}>下载</a><button type="button" onClick={close} aria-label="关闭图片预览">关闭</button></div>
    <img src={url} alt={name} />
  </dialog>;
}
