import { useRef, useState } from 'react';
import type { ApprovalBatch, ToolDecision } from '../types';
import styles from '../App.module.css';
import approval from './ApprovalPanel.module.css';

type Choice = 'approve' | 'remember' | 'reject';
const options: { value: Choice; label: string; accessible: string }[] = [
  { value: 'approve', label: '允许', accessible: '批准' },
  { value: 'remember', label: '本会话允许', accessible: '本会话允许' },
  { value: 'reject', label: '拒绝', accessible: '拒绝' },
];

export default function ApprovalPanel({ batch, disabled, submit, stop }: {
  batch: ApprovalBatch; disabled: boolean; submit: (items: ToolDecision[], remember: string[]) => Promise<void> | void;
  stop?: () => void;
}) {
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState(false);
  const pending = useRef(false);
  const requests = batch.items;
  const complete = requests.length > 0 && requests.every(row => Object.hasOwn(choices, row.approval_id));
  const busy = disabled || sending;
  async function send(next: Record<string, Choice>) {
    if (disabled || pending.current || !requests.length || !requests.every(row => Object.hasOwn(next, row.approval_id))) return;
    pending.current = true; setSending(true); setFailed(false);
    try {
      await submit(requests.map(row => ({ approval_id: row.approval_id,
        decision: next[row.approval_id] === 'reject' ? 'reject' : 'approve' })),
        requests.filter(row => next[row.approval_id] === 'remember').map(row => row.approval_id));
    } catch {
      console.warn('approval_submission_failed'); setFailed(true);
    } finally { pending.current = false; setSending(false); }
  }
  return <section className={styles.approval} aria-label="工具审批">
    <div className={styles.approvalHeading}>{batch.agent ? `子智能体 ${batch.agent} 需要审批` : '需要你的审批'} <span>{requests.length} 个调用</span></div>
    {batch.agent && <p>实例：{batch.agent_path}</p>}
    <p>{requests.length === 1 ? '选择后提交本次决定。' : '逐项核对，收齐决定后整批提交。'}“本会话允许”仅在当前会话生效。</p>
    <div className={styles.approvalItems}>
      {requests.map((row, index) => <div className={styles.approvalItem} key={row.approval_id}>
        <div className={styles.approvalSubject}><strong>{row.tool_name}</strong>
          {row.mcp_source && <p>MCP 来源：{row.mcp_source.server_id} / {row.mcp_source.remote_tool}；外部工具，副作用未知。</p>}
          {row.remember_scope === 'tool' && <p>记住后允许本会话的此 MCP 工具，参数可变化，根与子任务共用；配置或工具定义改变后失效。</p>}
          <pre>{row.target}</pre>
          {row.workspace && <span>工作区：{row.workspace}</span>}</div>
        <div className={approval.choices} role="group" aria-label={`第${index + 1}个调用的审批决定`}>
          {options.map(option => <button type="button" key={option.value} aria-label={`${option.value === 'remember' && row.remember_scope === 'tool' ? '本会话始终允许此工具' : option.accessible} ${index + 1}`}
            aria-pressed={choices[row.approval_id] === option.value} disabled={busy} onClick={() => {
              const next = { ...choices, [row.approval_id]: option.value }; setChoices(next);
              if (requests.length === 1) void send(next);
            }}>{option.value === 'remember' && row.remember_scope === 'tool' ? '本会话始终允许此工具' : option.label}</button>)}
        </div>
      </div>)}
    </div>
    {failed && <p role="alert">提交未确认，请核对会话状态后重试。</p>}
    {(requests.length > 1 || complete) && <button className={styles.approvalSubmit} disabled={busy || !complete}
      onClick={() => void send(choices)}>{requests.length === 1 ? '重试提交审批' : '提交审批并继续'}</button>}
    {stop && <button className={styles.syncButton} disabled={busy} onClick={stop}>
      {batch.kind === 'live_child' ? '停止该子任务' : '停止本批并结束运行'}</button>}
  </section>;
}
