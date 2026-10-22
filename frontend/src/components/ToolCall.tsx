import { useEffect, useState, type ReactNode } from 'react';
import type { AgentView, ChangeLoader, MessageRow, ToolCallRow, ToolState } from '../types';
import ToolResult, { mcpOutcomes } from './ToolResult';
import styles from '../App.module.css';
import card from './ToolCall.module.css';

const activities: Record<string, string> = { queued: '排队中', preparing: '准备中', running: '运行中',
  stopping: '停止中', settling: '收尾中' };
const phases: Record<string, string> = { batch_wait: '等待前序工具', authorize: '准备执行',
  process_starting: '准备执行 / 等待输出', process_output: '输出更新', capture_cleanup: '整理输出',
  image_detect: '检查图片类型', image_read: '读取图片', image_decode: '解码图片', image_resize: '调整图片尺寸',
  image_encode: '编码图片', read: '读取文件', skip_lines: '定位起始行', read_page: '读取分页',
  write_prepare: '准备写入', create_parents: '创建父目录', temp_write: '写入临时文件',
  flush: '刷新缓冲', fsync: '确认落盘', commit: '提交修改', cleanup: '清理临时资源',
  scan_exact: '扫描精确匹配', match_plan: '准备匹配', scan_normalized: '扫描模糊匹配', temp_copy: '复制未修改内容',
  diff: '生成修改摘要', enumerate: '枚举目录', inspect_entries: '检查目录条目', list_results: '整理目录结果',
  find_results: '查找路径', verify_results: '核对查找结果', discover_candidates: '发现候选文件',
  search_group: '搜索文件组', search_matches: '统计匹配结果' };

function seconds(start: string | undefined, now: number) {
  if (!start || !Number.isFinite(Date.parse(start))) return null;
  return `${Math.max(0, Math.floor((now - Date.parse(start)) / 1000))}秒`;
}

export default function ToolCall({ call, result, phase, loadChange, contentControl, agents = [], stopAgent, openAgent, stopping = false, unconfirmed = false, initiallyOpen = false }: {
  loadChange?: ChangeLoader;
  contentControl?: ReactNode;
  call: ToolCallRow; result?: MessageRow; phase?: ToolState; stopping?: boolean; unconfirmed?: boolean;
  agents?: AgentView[]; initiallyOpen?: boolean;
  stopAgent?: (agentPath: string, originRunId: string, agentRunId?: string) => void;
  openAgent?: (agentPath: string) => void;
}) {
  const [expanded, setExpanded] = useState(initiallyOpen);
  const active = phase?.state === 'running';
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active || !phase?.started_at) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, phase?.started_at]);
  const end = active ? now : Date.parse(phase?.finished_at ?? '') || now;
  const elapsed = seconds(phase?.started_at, end);
  const quiet = seconds(phase?.last_activity_at, end);
  const outcomes: Record<string, string> = { success: '成功', error_result: '错误结果',
    not_executed: '未执行', exception: '执行异常', outcome_unknown: '结果待核对' };
  const delegation = agents.length ? agents.some(agent => agent.status === 'awaiting_approval')
    ? '等待子任务审批' : '等待子任务结果' : null;
  const label = unconfirmed ? '结果未确认' : result?.mcp_result ? mcpOutcomes[result.mcp_result.outcome] : phase?.state === 'unconfirmed' ? '状态未确认' : phase?.outcome
    ? outcomes[phase.outcome] ?? '状态未确认' : active ? stopping ? '停止中' : activities[phase?.activity ?? ''] ?? '处理中'
      : result ? result.status === 'error' ? '错误结果' : '已有结果' : '待执行';
  const primary = call.name === 'bash' ? call.args.command : call.name === 'subagent' ? call.args.agent :
    ['grep', 'find'].includes(call.name) ? call.args.pattern :
      ['read', 'write', 'edit', 'ls'].includes(call.name) ? call.args.path : undefined;
  const location = ['grep', 'find'].includes(call.name) ? call.args.path : undefined;
  const values = [primary, location].filter((value): value is string => typeof value === 'string' && !!value.trim());
  const target = values.join(' · ').replace(/\s+/g, ' ').trim();
  const preview = target.length > 160 ? `${target.slice(0, 159)}…` : target;
  const icon = label === '成功' ? '✓' : ['错误结果', '执行异常'].includes(label) ? '!' : active ? '⋯' : '◇';
  return <><details open={expanded} onToggle={event => setExpanded(event.currentTarget.open)} className={`${styles.toolCall} ${card.card} ${!active ? card.terminal : ''}`}>
    <summary><span className={card.icon} aria-hidden="true">{icon}</span>
      <span className={`${styles.toolName} ${card.name}`} title={call.name}>{call.name}</span>
      {preview && <code className={card.target}>{preview}</code>}
      <span className={`${styles.toolStatus} ${card.status}`}>{active && !stopping && delegation ? delegation : label}</span>
      {active && phase?.phase && !delegation && <span className={styles.toolPhase}>{phases[phase.phase] ?? phase.phase}</span>}
      {active && (elapsed || quiet) && <span className={styles.toolTiming}>
        {elapsed && <span>已历时 {elapsed}</span>}{active && quiet && <span>距最后活动 {quiet}</span>}
      </span>}
    </summary>
    <div className={styles.toolDetail}>
      {!active && phase?.phase && !delegation && <div className={`${styles.toolPhase} ${card.finishedPhase}`}>{phases[phase.phase] ?? phase.phase}</div>}
      {!active && elapsed && <div className={`${styles.toolTiming} ${card.finishedTiming}`}>已历时 {elapsed}</div>}
      {phase?.completed != null && <p className={styles.toolCounter}>
        {phase.completed}{phase.total != null ? ` / ${phase.total}` : ''}{phase.unit ? ` ${phase.unit}` : ''}
      </p>}
      {phase?.text && <><div className={styles.detailLabel}>最新进度 / 输出尾部</div><pre>{phase.text}</pre></>}
      <div className={styles.detailLabel}>参数</div><pre>{JSON.stringify(call.args, null, 2)}</pre>
      {result && <><div className={styles.detailLabel}>工具结果</div><ToolResult loadChange={loadChange} name={call.name} result={result} anchor={expanded ? result.id : undefined} />
        {contentControl ?? (result.content_truncated && <p className={styles.historyPreview}>{result.detail ?
          '当前为工具结果预览，完整内容仍保存在会话中。' : '当前为工具结果预览。'}</p>)}</>}
      {phase?.reason && <p className={styles.toolReason}>原因：{phase.reason}</p>}
    </div>
  </details>
    {agents.map(agent => <section key={agent.source.agent_path} className={styles.delegatedAgent} aria-label="子智能体进度">
        <div className={styles.delegatedHeading}>
          {openAgent ? <button className={styles.agentLink}
            aria-label={`查看子智能体 ${agent.source.agent_path.split('/')[1]} · ${agent.source.agent_id.slice(0, 6)}`}
            onClick={() => openAgent(agent.source.agent_path)}>
            {agent.source.agent_path.split('/')[1]} · {agent.source.agent_id.slice(0, 6)} <span>›</span>
          </button> : <span>{agent.source.agent_path.split('/')[1]} · {agent.source.agent_id.slice(0, 6)}</span>}
          <span className={styles.agentState}>{
          ({ running: '运行中', awaiting_approval: '等待审批', completed: '已完成', failed: '失败', stopped: '已停止',
            interrupted: '等待审批', stopping: '正在收尾', unconfirmed: '状态未确认' })[agent.status]}</span>
        </div>
        {typeof call.args.task === 'string' && <p className={styles.delegatedTask}>{call.args.task}</p>}
        {stopAgent && ['running', 'awaiting_approval', 'stopping'].includes(agent.status) && <button className={styles.syncButton}
          disabled={agent.status === 'stopping' || stopping} onClick={() => stopAgent(agent.source.agent_path, agent.source.parent_run_id, agent.source.agent_run_id)}>
          {agent.status === 'stopping' ? '正在停止子任务…' : '停止该子任务'}</button>}
      </section>)}
  </>;
}
