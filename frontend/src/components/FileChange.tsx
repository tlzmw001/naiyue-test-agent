import { useEffect, useRef, useState } from 'react';
import type { FileChangeView, ChangeLoader, MessageRow } from '../types';
import styles from './ToolResult.module.css';

export default function FileChange({ result, loadChange, anchor }: {
  result: MessageRow; loadChange?: ChangeLoader; anchor?: string;
}) {
  const [detail, setDetail] = useState<FileChangeView | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  useEffect(() => { generation.current++; setDetail(null); return () => { generation.current++; }; }, [result.id, result.revision]);
  const change = detail ?? result.file_change!;
  const reasons: Record<string, string> = { read_not_allowed: '未取得读取旧内容的授权', input_budget: '文件超出差异处理预算',
    not_utf8: '旧文件不是 UTF-8 文本', binary: '旧文件包含二进制内容', read_failed: '旧内容读取失败' };
  async function load(patch = false) {
    if (!loadChange || pending) return;
    const version = generation.current;
    setPending(true); setError('');
    try {
      const value = await loadChange(result, patch);
      if (version !== generation.current) return;
      if (patch && typeof value === 'string') {
        const url = URL.createObjectURL(new Blob([value], { type: 'text/x-diff' }));
        const link = document.createElement('a'); link.href = url; link.download = 'change.patch';
        link.click(); URL.revokeObjectURL(url);
      } else if (typeof value !== 'string') setDetail(value);
    } catch { if (version === generation.current) setError('读取保存的差异失败，可以重试。'); }
    finally { if (version === generation.current) setPending(false); }
  }
  return <div data-message-id={anchor} className={styles.result}>
    {change.firstChangedLine != null && <p>首个修改行：{change.firstChangedLine}</p>}
    {change.unavailable_reason && <p className={styles.notice}>修改已执行；差异不可用：{reasons[change.unavailable_reason] ?? '未生成差异详情'}。</p>}
    {change.diff_truncated && <p className={styles.notice}>差异不完整或已截断，请结合原始工具结果核对。</p>}
    {change.diff && <pre className={styles.diff} tabIndex={0} role="region" aria-label="修改差异">
      {change.diff.match(/[^\n]*\n|[^\n]+$/g)?.map((line, index) => <span key={index}
        data-change={line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : undefined}>{line}</span>)}
    </pre>}
    {loadChange && result.detail && <button disabled={pending} onClick={() => void load()}>查看保存的详情</button>}
    {loadChange && result.detail && !change.patch_omitted && <button disabled={pending} onClick={() => void load(true)}>下载 patch</button>}
    {detail && <details open><summary>保存的修改后预览</summary><pre>{detail.preview}</pre>
      {detail.preview_truncated && <p className={styles.notice}>预览已截断。</p>}</details>}
    {error && <p role="alert">{error}</p>}
    <details><summary>原始工具结果</summary><pre>{result.content}</pre></details>
  </div>;
}
