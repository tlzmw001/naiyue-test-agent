import type { MessageRow, ToolCallRow, ToolState } from '../types';
import styles from '../App.module.css';

export default function ToolCall({ call, result, phase }: {
  call: ToolCallRow; result?: MessageRow; phase?: ToolState;
}) {
  const outcomes: Record<string, string> = { success: '成功', error_result: '错误结果',
    not_executed: '未执行', exception: '执行异常', outcome_unknown: '结果待核对' };
  const label = phase?.state === 'unconfirmed' ? '状态未确认' : phase?.outcome
    ? outcomes[phase.outcome] ?? '状态未确认' : phase?.state === 'running' ? '处理中'
      : result ? result.status === 'error' ? '错误结果' : '已有结果' : '待执行';
  return <details className={styles.toolCall}>
    <summary><span className={styles.toolName}>{call.name}</span><span className={styles.toolStatus}>{label}</span></summary>
    <div className={styles.toolDetail}>
      <div className={styles.detailLabel}>参数</div><pre>{JSON.stringify(call.args, null, 2)}</pre>
      {result && <><div className={styles.detailLabel}>工具结果</div><pre>{result.content}</pre></>}
      {phase?.reason && <p className={styles.toolReason}>原因：{phase.reason}</p>}
    </div>
  </details>;
}
