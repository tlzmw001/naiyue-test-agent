import type { RunView, SessionRow, SessionView } from '../types';
import { Icon } from './Icons';
import styles from './SessionIndicators.module.css';

const statuses: Record<string, { text: string; tone: string }> = {
  running: { text: '运行中', tone: 'active' }, completed: { text: '已完成', tone: 'success' },
  stopped: { text: '已停止', tone: 'muted' }, failed: { text: '失败', tone: 'danger' },
  interrupted: { text: '待继续', tone: 'warning' }, unconfirmed: { text: '待核对', tone: 'warning' },
  review_required: { text: '待核对', tone: 'warning' },
};

export default function SessionIndicators({ id, row, currentSession, currentRun }: {
  id: string; row: SessionRow; currentSession?: SessionView; currentRun?: RunView | null;
}) {
  const session = currentSession?.session_id === row.session_id ? currentSession : undefined;
  const candidate = currentRun?.session_id === row.session_id ? currentRun : session?.live;
  const live = candidate?.session_id === row.session_id ? candidate : undefined;
  const kind = session?.execution?.kind ?? (row.execution_binding === 'sandbox' ? 'sandbox' :
    row.execution_binding === 'local' || row.execution_binding === 'project_local' ? 'host' : null);
  let status = statuses[row.state === 'ready' ? row.last_run_status ?? '' : row.state];
  if (live) status = statuses[live.status];
  else if (session?.busy) status = statuses.running;
  else if (!status && row.state === 'ready' && session?.execution) {
    const state = session.execution.state;
    if (state !== 'ready') status = { text: state === 'preparing' ? '准备中' : state === 'failed' ? '准备失败' : '执行不可用', tone: 'warning' };
  }
  if (session?.pending_approval) status = { text: '等待审批', tone: 'warning' };
  if (session?.manual_review_required || live?.manual_review_required || row.state === 'review_required') status = statuses.review_required;
  const location = kind === 'sandbox' ? 'Docker 沙箱' : '此计算机';
  return <span id={id} className={styles.metadata}>
    {kind && <span className={styles.location} aria-label={`执行位置：${location}`} title={location}>
      <Icon name={kind === 'sandbox' ? 'sandbox' : 'computer'} /><span>{kind === 'sandbox' ? '沙箱' : '本机'}</span>
    </span>}
    {status && <span className={styles.status} data-tone={status.tone} aria-label={`任务状态：${status.text}`}>
      <i aria-hidden="true" />{status.text}</span>}
  </span>;
}
