import FileChange from './FileChange';
import type { ChangeLoader, MessageRow } from '../types';
import styles from './ToolResult.module.css';

export const mcpOutcomes = { success: '成功', tool_error: '工具返回错误', not_executed: '未执行',
  outcome_unknown: '结果未知', unsupported_result: '返回内容暂不支持' };
const archiveReasons: Record<string, string> = { command_limit: '单次归档达到上限', workspace_limit: '工作区归档达到上限',
  directory_unavailable: '归档目录不可用', write_error: '归档写入失败', workspace_unavailable: '未绑定可用工作区',
  archive_root_unavailable: '未绑定输出归档目录' };

export default function ToolResult({ name, result, anchor, loadChange }: { name: string; result: MessageRow; anchor?: string; loadChange?: ChangeLoader }) {
  if (name === 'subagent' && result.status !== 'error') {
    let envelope;
    try { envelope = JSON.parse(result.content); } catch { envelope = null; }
    if (envelope && typeof envelope.agent_id === 'string' && /^[0-9a-f]{64}$/.test(envelope.agent_id)
      && typeof envelope.agent === 'string' && envelope.status === 'completed'
      && typeof envelope.report === 'string' && typeof envelope.truncated === 'boolean') {
      return <div data-message-id={anchor} className={styles.result}>
        <p>子智能体：{envelope.agent} · <code>{envelope.agent_id}</code></p>
        {envelope.truncated && <p className={styles.notice}>报告已截断，完整报告可在子智能体历史中查看。</p>}
        <pre aria-label="子智能体报告">{envelope.report}</pre>
        <details><summary>原始工具结果</summary><pre aria-label="原始工具结果">{result.content}</pre></details>
      </div>;
    }
  }
  const mcp = result.mcp_result;
  if (mcp) return <div data-message-id={anchor} className={styles.result}>
    <p>MCP 来源：{mcp.source.server_id} / {mcp.source.remote_tool}{mcp.source.transport && ` · ${mcp.source.transport}`}</p>
    <p>{mcpOutcomes[mcp.outcome]}</p>
    {mcp.outcome === 'outcome_unknown' && <p className={styles.notice}>远端可能已产生副作用，请先核对；本次请求未自动重试。</p>}
    {mcp.outcome === 'unsupported_result' && <p className={styles.notice}>已收到响应，当前客户端无法展示此类内容；未读取媒体或资源地址，也未发起后续交互。</p>}
    {mcp.truncated && <div className={styles.notice}>
      <p>结果已截断{mcp.result_bytes !== undefined ? `（原始结果 ${mcp.result_bytes} 字节）` : ''}。</p>
      <p>{mcp.archive_complete ? '归档完整' : '归档不完整或不可用'}
        {mcp.archive_reason && archiveReasons[mcp.archive_reason] && <span>：{archiveReasons[mcp.archive_reason]}</span>}</p>
      {Object.entries(mcp.archive_paths ?? {}).map(([key, path]) => <p key={key}>输出归档 {key}：<code>{path}</code></p>)}
    </div>}
    <pre>{result.content}</pre>
  </div>;
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
