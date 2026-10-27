import { useState } from 'react';
import type { ReactNode, RefObject } from 'react';
import type { ContextEstimate, ContextGroup, ContextView } from '../contextTypes';
import styles from './ContextPanel.module.css';
import { Chevron } from './Icons';

const labels: Record<string, string> = { system: '系统说明', tool_definitions: '工具定义', user: '用户消息',
  assistant: '模型消息与工具调用参数', tool_results: '工具结果', summary: '历史压缩摘要', other: '其他消息',
  base: '基础提示词', project: '项目说明', skills: 'Skill 索引', tool_guidance: '工具说明与指导', subagent_roles: '子智能体角色说明', background_tasks: '后台任务状态',
  memory: '长期记忆索引', session_directory: '其他会话索引' };
const colors: Record<string, string> = { system: 'var(--chart-system)', tool_definitions: 'var(--chart-tool_definitions)', user: 'var(--chart-user)',
  assistant: 'var(--chart-assistant)', tool_results: 'var(--chart-tool_results)', summary: 'var(--chart-summary)', other: 'var(--chart-other)' };
const sourceLabel: Record<string, string> = { config: '配置', default: '默认值', unknown: '未提供' };
const format = (value: number | null | undefined) => value == null ? '未提供' : value.toLocaleString('en-US');
export function contextPercent(estimate?: ContextEstimate | null) {
  return estimate?.status === 'available' && estimate.estimated_input_tokens != null && estimate.context_window_tokens
    ? estimate.estimated_input_tokens / estimate.context_window_tokens * 100 : null;
}

export function ContextTrigger({ value, open, disabled, toggle, triggerRef }: {
  value?: ContextView; open: boolean; disabled: boolean; toggle: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const percent = contextPercent(value?.current.estimate);
  const estimate = value?.current.estimate;
  const thresholdReached = estimate?.status === 'available' && estimate.estimated_input_tokens != null &&
    estimate.trigger_threshold_tokens != null && estimate.estimated_input_tokens >= estimate.trigger_threshold_tokens;
  const label = percent == null ? '查看上下文构成' : `查看上下文构成，预估占用${percent.toFixed(1)}%`;
  return <button type="button" ref={triggerRef} className={styles.trigger} disabled={disabled}
    data-threshold={thresholdReached ? 'reached' : undefined}
    aria-label={label} title={`${label}（本地估算${thresholdReached ? '，达到自动压缩阈值' : ''}）`} aria-expanded={open} onClick={toggle}>
    <svg width="15" height="15" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" className={styles.ring} />
      {percent != null && <circle cx="10" cy="10" r="7" pathLength="100" className={styles.filled}
        strokeDasharray={`${Math.min(100, Math.max(0, percent))} 100`} />}</svg>
    {percent != null && <span>{percent.toFixed(1)}%</span>}
  </button>;
}

function Group({ row, total }: { row: ContextGroup; total: number }) {
  const heading = <><i style={{ background: colors[row.id] }} /><span>{labels[row.id] ?? '其他消息'}
    {row.id !== 'system' && row.count != null ? ` · ${row.count}${row.id === 'tool_definitions' ? ' 项' : ' 条'}` : ''}</span>
    <b>≈ {format(row.tokens)}</b><small>{total > 0 ? (row.tokens / total * 100).toFixed(1) + '%' : ''}</small></>;
  return row.children?.length ? <details className={styles.group} open><summary className={styles.row}><Chevron direction="right" className={styles.groupChevron} />{heading}</summary>
    <div className={styles.children}>{row.children.map((part, index) => <div key={`${part.id}:${index}`}>
      <span>{part.source ?? labels[part.id]}{part.count != null ? ` · ${part.count} 项` : ''}</span>
      <span>≈ {format(part.tokens)}</span></div>)}</div></details> : <div className={styles.row}>{heading}</div>;
}

function Estimate({ estimate, basis }: { estimate?: ContextEstimate | null; basis: string }) {
  if (!estimate || estimate.status !== 'available') return <p className={styles.notice}>当前预估暂不可用。最近请求的已记录用量仍可查看。</p>;
  const total = estimate.estimated_input_tokens!;
  const percent = contextPercent(estimate);
  const remaining = estimate.context_window_tokens != null && estimate.max_output_tokens != null
    ? Math.max(0, estimate.context_window_tokens - estimate.max_output_tokens - total) : null;
  return <>
    <div className={styles.label}>{basis === 'last_sent' ? '本轮最近已发送的固定输入快照' : '按当前内容准备下一次请求'}</div>
    <div className={styles.big}>≈ {format(total)}<small>tokens{percent != null ? ` · ${percent.toFixed(1)}%` : ''}</small></div>
    <p className={styles.meta}>窗口 {format(estimate.context_window_tokens)} · 来源：{sourceLabel[estimate.context_window_source]}<br />
      尚未包含输入框草稿和待发送消息</p>
    {percent != null && <div className={styles.budgetBar} role="img" aria-label={`输入预估占用${percent.toFixed(1)}%`}>
      <div style={{ width: Math.min(100, percent) + '%' }} />
    </div>}
    <div className={styles.legend}><span>输入预估 {format(total)}</span><span>输出预留 {format(estimate.max_output_tokens)}</span></div>
    <div className={styles.budget}>{remaining == null ? '预算信息未提供，暂不能计算剩余输入容量。' : <>
      扣除输出预留后，还可容纳约 <strong>{format(remaining)}</strong> tokens。</>}<br />
      输出预留来源：{sourceLabel[estimate.max_output_source]}；压缩预留 {format(estimate.compaction_reserve_tokens)}（{sourceLabel[estimate.compaction_reserve_source]}）。<br />
      自动压缩检查阈值：{format(estimate.trigger_threshold_tokens)}。</div>
    {estimate.groups_status !== 'unavailable' && <>
    <div className={styles.section}>输入构成<small>各项均为本地估算</small></div>
    <div className={styles.composition} aria-hidden="true">{estimate.groups.map(row => <i key={row.id}
      style={{ background: colors[row.id], width: (total > 0 ? row.tokens / total * 100 : 0) + '%' }} />)}</div>
    {estimate.groups.map(row => <Group key={row.id} row={row} total={total} />)}
    </>}
    <div className={styles.note}><p>Skill 正文经 Read 读取后计入“工具结果”。</p>
      {!!estimate.image_count && <p>包含 {estimate.image_count} 张图片，图片 token 使用框架固定粗估，可能与实际有明显差异。</p>}
      <p>采用通用近似计数；中文和不同模型可能有误差。实际发送前仍会检查压缩条件。</p></div>
  </>;
}

export default function ContextPanel({ value, loading, error, retry, usageContent, diagnosticContent }: {
  value?: ContextView; loading: boolean; error: string; retry: () => void;
  usageContent: ReactNode;
  diagnosticContent?: ReactNode;
}) {
  const tabs = ['current', 'request', 'usage', 'diagnostics'] as const;
  const [tab, setTab] = useState<(typeof tabs)[number]>('current');
  const snapshot = value?.latest_request;
  const actual = snapshot?.usage;
  return <section className={styles.panel} aria-label="上下文">
    <div className={styles.scope}>{tab === 'usage' ? '会话累计' : tab === 'diagnostics' ? '运行诊断' : '主会话'}<span>只读查看</span></div>
    <div className={styles.tabs} role="tablist" aria-label="上下文统计口径">{tabs.map(name =>
      <button key={name} role="tab" aria-selected={tab === name} id={`context-tab-${name}`}
        aria-controls={`context-${name}`} tabIndex={tab === name ? 0 : -1} onClick={() => setTab(name)}
        onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[tabs.length - 1] :
            tabs[(tabs.indexOf(tab) + (event.key === 'ArrowLeft' ? tabs.length - 1 : 1)) % tabs.length];
          setTab(next);
          event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#context-tab-${next}`)?.focus();
        }}>{name === 'current' ? '当前预估' : name === 'request' ? '最近请求' : name === 'usage' ? '会话累计' : '运行诊断'}</button>)}</div>
    <div className={styles.scroll}>
      {tab === 'usage' || tab === 'diagnostics' ? <section role="tabpanel" id={`context-${tab}`} aria-labelledby={`context-tab-${tab}`}>
        {tab === 'usage' ? usageContent : diagnosticContent}</section> : <>
      {error && <div className={styles.notice} role="alert">{error}<button onClick={retry}>重试</button></div>}
      {loading && <p className={styles.notice} role="status">正在读取上下文…</p>}
      {!loading && !error && <section role="tabpanel" id={`context-${tab}`} aria-labelledby={`context-tab-${tab}`}>
        {tab === 'current' ? <><p className={styles.model}>{value?.current.model_id}</p>
          <Estimate estimate={value?.current.estimate} basis={value?.current.basis ?? 'unavailable'} /></> : snapshot ? <>
          <div className={styles.stamp}><span>{new Date(snapshot.recorded_at).toLocaleTimeString()}</span>
            <span>{snapshot.status === 'running' ? '请求中' : snapshot.status === 'failed' ? '请求失败' : '已返回'}</span></div>
          <p className={styles.model}>{snapshot.model_id}</p><p className={styles.meta}>对应这一次发送的输入与返回，与当前预估分别计算。</p>
          {snapshot.status === 'running' && <p className={styles.meta}>请求尚未返回，正在等待供应商报告用量。</p>}
          <div className={styles.metrics}><div><span>供应商报告的总输入<small>包含缓存读取与写入</small></span><strong>{format(actual?.input_tokens)}</strong></div>
            <div className={styles.cache}><span>↳ 其中缓存读取</span><strong>{format(actual?.cache_read_tokens)}</strong></div>
            <div className={styles.cache}><span>↳ 其中缓存写入</span><strong>{format(actual?.cache_write_tokens)}</strong></div>
            <div><span>供应商报告的输出</span><strong>{format(actual?.output_tokens)}</strong></div></div>
          <div className={styles.budget}>同一次发送前的本地估算：≈ {format(snapshot.estimate.estimated_input_tokens)} tokens。</div>
          <div className={styles.note}><p>供应商未返回的用量显示“未提供”，不记为零。</p>
            <p>分项构成均为估算，供应商报告不能精确分摊到各项。</p>
            <p>子智能体、Auto 审查和压缩摘要的调用不叠加到主会话上下文占用。</p></div>
        </> : <p className={styles.notice}>暂无已记录的模型请求。历史请求未保存的构成不会补造。</p>}
      </section>}</>}
    </div>
    {tab !== 'usage' && tab !== 'diagnostics' && <footer className={styles.footer}><span>本地查看，不调用模型</span><button onClick={retry} disabled={loading}>刷新</button></footer>}
  </section>;
}
