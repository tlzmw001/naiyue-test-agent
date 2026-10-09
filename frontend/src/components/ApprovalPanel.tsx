import { useState } from 'react';
import type { ApprovalRequest, ToolDecision } from '../types';
import styles from '../App.module.css';

export default function ApprovalPanel({ origin, requests, disabled, submit }: {
  origin: string; requests: ApprovalRequest[]; disabled: boolean; submit: (items: ToolDecision[]) => void;
}) {
  const [choices, setChoices] = useState<Record<string, 'approve' | 'reject'>>({});
  const complete = requests.every(row => Object.hasOwn(choices, row.tool_call_id));
  return <section className={styles.approval} aria-label="工具审批">
    <div className={styles.approvalHeading}>需要你的审批 <span>{requests.length} 个调用</span></div>
    <p>逐项核对后提交整批决定。批准仅对这次调用生效。</p>
    <div className={styles.approvalItems}>
      {requests.map((row, index) => <div className={styles.approvalItem} key={row.tool_call_id}>
        <div className={styles.approvalSubject}><strong>{row.tool_name}</strong><pre>{row.target}</pre>
          {row.workspace && <span>工作区：{row.workspace}</span>}</div>
        <div className={styles.approvalChoices}>
          {(['approve', 'reject'] as const).map(value => <label key={value}>
            <input type="radio" name={origin + row.tool_call_id} aria-label={`${value === 'approve' ? '批准' : '拒绝'} ${index + 1}`}
              checked={choices[row.tool_call_id] === value} disabled={disabled}
              onChange={() => setChoices({ ...choices, [row.tool_call_id]: value })} />
            {value === 'approve' ? '批准' : '拒绝'}
          </label>)}
        </div>
      </div>)}
    </div>
    <button className={styles.approvalSubmit} disabled={disabled || !complete} onClick={() =>
      submit(requests.map(row => ({ tool_call_id: row.tool_call_id, decision: choices[row.tool_call_id] })))}>
      提交审批并继续
    </button>
  </section>;
}
