import { useState } from 'react';
import type { ApprovalBatch, ToolDecision } from '../types';
import styles from '../App.module.css';

export default function ApprovalPanel({ batch, disabled, submit, stop }: {
  batch: ApprovalBatch; disabled: boolean; submit: (items: ToolDecision[], remember: string[]) => void;
  stop?: () => void;
}) {
  const [choices, setChoices] = useState<Record<string, 'approve' | 'reject'>>({});
  const [remembered, setRemembered] = useState<Record<string, boolean>>({});
  const requests = batch.items;
  const complete = requests.every(row => Object.hasOwn(choices, row.approval_id));
  return <section className={styles.approval} aria-label="工具审批">
    <div className={styles.approvalHeading}>{batch.agent ? `子智能体 ${batch.agent} 需要审批` : '需要你的审批'} <span>{requests.length} 个调用</span></div>
    {batch.agent && <p>实例：{batch.agent_path}</p>}
    <p>逐项核对后提交整批决定。默认仅批准这次调用。</p>
    <div className={styles.approvalItems}>
      {requests.map((row, index) => <div className={styles.approvalItem} key={row.approval_id}>
        <div className={styles.approvalSubject}><strong>{row.tool_name}</strong><pre>{row.target}</pre>
          {row.workspace && <span>工作区：{row.workspace}</span>}</div>
        <div className={styles.approvalChoices}>
          {(['approve', 'reject'] as const).map(value => <label key={value}>
            <input type="radio" name={batch.approval_batch_id + row.approval_id} aria-label={`${value === 'approve' ? '批准' : '拒绝'} ${index + 1}`}
              checked={choices[row.approval_id] === value} disabled={disabled}
              onChange={() => { setChoices({ ...choices, [row.approval_id]: value });
                if (value === 'reject') setRemembered({ ...remembered, [row.approval_id]: false }); }} />
            {value === 'approve' ? '批准' : '拒绝'}
          </label>)}
          <label><input type="checkbox" aria-label={`本会话不再询问 ${index + 1}`}
            disabled={disabled || choices[row.approval_id] !== 'approve'} checked={remembered[row.approval_id] === true}
            onChange={event => setRemembered({ ...remembered, [row.approval_id]: event.target.checked })} />本会话不再询问</label>
        </div>
      </div>)}
    </div>
    <button className={styles.approvalSubmit} disabled={disabled || !complete} onClick={() =>
      submit(requests.map(row => ({ approval_id: row.approval_id, decision: choices[row.approval_id] })),
        requests.filter(row => choices[row.approval_id] === 'approve' && remembered[row.approval_id]).map(row => row.approval_id))}>
      提交审批并继续
    </button>
    {stop && <button className={styles.syncButton} disabled={disabled} onClick={stop}>
      {batch.kind === 'live_child' ? '停止该子任务' : '停止本批并结束运行'}</button>}
  </section>;
}
