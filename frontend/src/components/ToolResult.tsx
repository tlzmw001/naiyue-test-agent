import FileChange from './FileChange';
import type { ChangeLoader, MessageRow } from '../types';
import styles from './ToolResult.module.css';

export default function ToolResult({ name, result, anchor, loadChange }: { name: string; result: MessageRow; anchor?: string; loadChange?: ChangeLoader }) {
  if (result.file_change && result.status !== 'error') return <FileChange result={result} loadChange={loadChange} anchor={anchor} />;
  const match = name === 'edit' && result.status !== 'error' &&
    /^edit replaced \d+ block\(s\); \d+ bytes\nfirstChangedLine=(\d+)\n([\s\S]*)$/.exec(result.content);
  if (!match) return <pre data-message-id={anchor}>{result.content}</pre>;
  const lines = match[2].split('\n');
  const notice = lines.findIndex(line => line.startsWith('[edit:'));
  const diff = notice < 0 ? match[2] : lines.slice(0, notice).join('\n');
  const truncated = result.content_truncated || lines.some(line => line === '[edit: diff incomplete or truncated by feedback budget]');
  return <div data-message-id={anchor} className={styles.result}>
    <p>首个修改行：{match[1]}</p>
    {truncated && <p className={styles.notice}>差异不完整或已截断，请结合原始工具结果核对。</p>}
    <pre className={styles.diff} tabIndex={0} role="region" aria-label="修改差异">{diff.match(/[^\n]*\n|[^\n]+$/g)?.map((line, index) =>
      <span key={index} data-change={line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : undefined}>{line}</span>)}</pre>
    <details><summary>原始工具结果</summary><pre aria-label="原始工具结果">{result.content}</pre></details>
  </div>;
}
