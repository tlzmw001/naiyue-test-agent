import type { BackgroundTasksState } from '../useBackgroundTasks';
import styles from './BackgroundTasks.module.css';

const statuses = { starting: '启动中', running: '运行中', stopping: '停止中', finished: '已结束', lost: '需核对', submitting:'提交中', completed:'已完成', failed:'失败', stopped:'已停止', unknown:'结果未确认' };
const reasons: Record<string, string> = { exited: '命令退出', user_stop: '用户停止', timeout: '达到执行上限',
  output_limit: '达到日志额度', archive_error: '归档失败', start_failed: '启动失败', capture_failed: '输出收集失败',
  cleanup_unconfirmed: '收尾未确认', app_close: '应用关闭' };
const results = { completed: '已完成', partial: '部分完成', failed: '失败', stopped: '已停止' };

export default function BackgroundTasks({ state }: { state: BackgroundTasksState }) {
  const row = state.output ?? state.items.find(item => item.task_id === state.selected);
  const memory = row?.kind === 'memory';
  const canStop = row && ['starting', 'running', 'stopping'].includes(row.status);
  return <section className={styles.panel} aria-label="后台任务管理">
    <div className={styles.scope}><span>当前工作区 · {state.activeCount === undefined ? '状态未同步' : `${state.activeCount} 个活动任务`}</span>
      <button onClick={state.retry}>刷新</button></div>
    <div className={styles.scroll}>
      {state.error && <p className={styles.error} role="alert">{state.error}</p>}
      {!state.items.length && !state.error && <p className={styles.empty}>{state.loading ? '正在读取任务…' : '没有后台任务'}</p>}
      <div className={styles.list} aria-label="后台任务列表">
        {state.items.map(item => <button key={item.task_id} className={styles.task}
          aria-pressed={item.task_id === state.selected} onClick={() => state.select(item.task_id)}>
          <span className={styles.taskTop}><span>{item.kind === 'memory' ? '记忆整理' : item.shell}</span>
            <span data-status={item.status}>{item.kind === 'memory' && item.result_status ? results[item.result_status] : statuses[item.status]}</span></span>
          <strong>{item.command_preview}</strong>
          <small>会话 {item.session_id}</small>
        </button>)}
      </div>
      {state.nextBefore && <button className={styles.more} disabled={state.moreLoading} onClick={() => void state.loadMore()}>
        {state.moreLoading ? '正在读取…' : '更多任务'}</button>}
      {state.selected && <section className={styles.detail} aria-label="后台任务详情">
        <div className={styles.detailHeading}><strong>任务详情</strong>
          {canStop && <button className={styles.stop} disabled={!!state.stopping || row.status === 'stopping'}
            aria-label="停止此后台任务" onClick={() => void state.stop(row.task_id)}>
            {state.stopping === row.task_id || row.status === 'stopping' ? '正在停止…' : '停止任务'}</button>}</div>
        {row && <dl className={styles.facts}>
          <dt>状态</dt><dd>{memory && row.result_status ? results[row.result_status] : statuses[row.status]}</dd>
          <dt>任务</dt><dd>{row.task_id}</dd>
          <dt>会话</dt><dd>{row.session_id}</dd>
          <dt>结束原因</dt><dd>{row.termination ? reasons[row.termination] ?? row.termination : '—'}</dd>
          {!memory && <><dt>退出码</dt><dd>{row.exit_code ?? '—'}</dd></>}
          <dt>执行上限</dt><dd>{row.max_execution_seconds === undefined ? '—' : row.max_execution_seconds == null ? '不限时' : `${row.max_execution_seconds} 秒`}</dd>
        </dl>}
        {row?.status === 'lost' && <p className={styles.error}>{memory ? '记忆整理收尾未确认，请人工核对。' : '进程或输出收尾未确认，请人工核对。'}</p>}
        {state.stopError && <p className={styles.error} role="alert">{state.stopError}</p>}
        {state.outputError && <p className={styles.error} role="alert">{state.outputError}</p>}
        {state.outputLoading && !state.output && <p className={styles.empty}>正在读取输出…</p>}
        {state.output && <>
          <div className={styles.outputHeading}>{memory ? '结果摘要' : '输出尾部'} <span>{state.output.observed_raw_bytes.toLocaleString()} B 已观察</span></div>
          <pre className={styles.output} aria-label="后台任务输出">{state.output.output}</pre>
          {state.output.truncated && <p className={styles.hint}>{memory ? '结果摘要已截断。' : '仅显示有界尾部，完整已保存内容见归档。'}</p>}
          {!state.output.output_available && <p className={styles.error}>日志文件不可用，已保留内存尾部和任务状态。</p>}
          {!memory && !state.output.archive_complete && (state.output.archive_reason || ['finished', 'lost'].includes(state.output.status)) &&
            <p className={styles.hint}>归档不完整{state.output.archive_reason ? `（${state.output.archive_reason}）` : ''}。</p>}
          {!memory && Object.entries(state.output.archive_paths).map(([kind, path]) => <p className={styles.archive} key={kind}>
            <span>{kind} 归档</span><code>{path}</code></p>)}
        </>}
      </section>}
    </div>
    <footer className={styles.footer}>任务持续运行，直到完成、达到上限或被停止。</footer>
  </section>;
}
