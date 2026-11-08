import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';

// Spec143BE 测试专用会话服务：真实 HTTP/SSE 数据入口，127.0.0.1 随机端口，与日常服务隔离。
// 首批 delta 自动到达，后续批次由测试显式推进；全部批次完成后才允许发送完成快照并结束。

export interface ConversationServerOptions {
  historyCount?: number;
  batches?: string[];
  live?: boolean;
  runningTool?: boolean;
  autoFirst?: boolean;
}

export interface ConversationServer {
  port: number;
  url: string;
  calls: string[];
  state: { sent: number; streamed: number; finished: boolean; startRequested: boolean; runCounter: number; lastRun: string };
  advanceStream: () => boolean;
  finishStream: () => void;
  listen: () => Promise<void>;
  close: () => Promise<void>;
}

export const SCOPE = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
export const ORDINARY_OUTPUT_TAIL = 'ORDINARY_OUTPUT_TAIL';
export const MCP_OUTPUT_TAIL = 'MCP_OUTPUT_TAIL';
export const RUNNING_OUTPUT_TAIL = 'RUNNING_OUTPUT_TAIL';
export const SESSION_ID = 'c1'.padEnd(32, '0');
export const RUN_ID = 'r1'.padEnd(32, '0');
const SESSION = SESSION_ID;
const SESSION_B = 'c2'.padEnd(32, '0');
const RUN = RUN_ID;

function historyMessage(index: number) {
  const padding = '这段是用于阅读与性能基线的长中文正文。'.repeat(6 + (index % 5));
  return {
    id: `m-${String(index).padStart(3, '0')}`,
    sequence: index + 1,
    revision: 1,
    type: index % 2 === 0 ? 'human' : 'ai',
    content: index % 2 === 0
      ? `历史问题 ${index}：请总结第 ${index} 批交付的验证结论并给出后续建议。`
      : `历史回答 ${index}：${padding}`,
    provisional: false, state: 'complete', run_id: RUN, conversation_revision: 1,
  };
}

function longOutput(kind: string, tail: string) {
  return `${Array.from({ length: 1400 }, (_, index) => `${kind} output line ${String(index + 1).padStart(4, '0')} ${'x'.repeat(12)}`).join('\n')}\n${tail}`;
}

const assistantTool = {
  id: 'assistant-tool', type: 'ai', content: '', provisional: false, state: 'complete', run_id: RUN,
  reply_id: 'root-tool', tool_calls: [
    { id: 't1', name: 'bash', args: { command: 'seq 1 2000 | gzip > /tmp/长期输出.bin && sha256sum /tmp/长期输出.bin' } },
    { id: 't2', name: 'mcp_search', args: { query: 'reading baseline', limit: 1400 } },
  ],
};
const ordinaryResult = {
  id: 'tool-result-1', type: 'tool', parent_message_id: 'assistant-tool', tool_call_id: 't1',
  content: longOutput('BASH', ORDINARY_OUTPUT_TAIL), provisional: false, state: 'complete', run_id: RUN, status: 'success',
};
const mcpResult = {
  id: 'tool-result-2', type: 'tool', parent_message_id: 'assistant-tool', tool_call_id: 't2',
  content: longOutput('MCP', MCP_OUTPUT_TAIL), provisional: false, state: 'complete', run_id: RUN, status: 'success',
  mcp_result: { version: 1, source: { server_id: 'reading', remote_tool: 'search', transport: 'streamable_http' }, outcome: 'success' },
};
export const ORDINARY_RESULT_CHARS = ordinaryResult.content.length;
export const MCP_RESULT_CHARS = mcpResult.content.length;

const runningAssistant = {
  id: 'assistant-running-tool', type: 'ai', content: '', provisional: false, state: 'complete', reply_id: 'root-running',
  tool_calls: [{ id: 'live-tool', name: 'bash', args: { command: 'npm run reading:long-output' } }],
};
const runningResult = {
  id: 'running-tool-result', type: 'tool', parent_message_id: 'assistant-running-tool', tool_call_id: 'live-tool',
  content: `运行已结束\n${RUNNING_OUTPUT_TAIL}`, provisional: false, state: 'complete', status: 'success',
};

export function defaultBatches() {
  return Array.from({ length: 8 }, (_, index) =>
    `这是流式回答的第 ${index + 1} 批内容。包含足够长度的中文正文用于观察渲染与长任务表现，同时覆盖段落、行内代码 \`npm run test\` 与列表项：\n\n- 批次要点 ${index + 1}\n- 阅读连续性检查 ${index + 1}\n`);
}

export function startConversationServer(options: ConversationServerOptions = {}): ConversationServer {
  const historyCount = options.historyCount ?? 120;
  const batches = options.batches ?? defaultBatches();
  const history = Array.from({ length: historyCount }, (_, index) => historyMessage(index));
  const calls: string[] = [];
  const state = { sent: 0, streamed: 0, finished: false, startRequested: false, runCounter: 0, lastRun: RUN };
  let activeStream: {
    response: ServerResponse;
    index: number;
    firstTimer: NodeJS.Timeout;
    heartbeat: NodeJS.Timeout;
    closed: boolean;
  } | null = null;

  const finalLiveMessages = () => {
    const answer = { id: 'assistant-live', type: 'ai', content: batches.join(''), provisional: false,
      state: 'complete', reply_id: 'root', run_id: state.lastRun };
    return options.runningTool ? [runningAssistant, runningResult, answer] : [answer];
  };

  const server: Server = createServer((request: IncomingMessage, response: ServerResponse) => {
    const url = new URL(request.url ?? '/', 'http://localhost');
    const path = url.pathname;
    calls.push(`${request.method} ${path}`);
    const cors = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
      'Access-Control-Allow-Headers': '*',
    };
    if (request.method === 'OPTIONS') {
      response.writeHead(204, cors); response.end(); return;
    }
    const json = (body: unknown) => {
      response.writeHead(200, { 'Content-Type': 'application/json', ...cors });
      response.end(JSON.stringify(body));
    };
    const runJson = () => ({
      run_id: state.lastRun, session_id: SESSION, status: state.finished ? 'completed' : 'running',
      messages: state.finished ? finalLiveMessages() : [], last_sequence: state.sent,
      incomplete: false, done: state.finished, manual_review_required: false, mode: 'message',
      tool_states: state.finished && options.runningTool ? { 'live-tool': {
        tool_call_id: 'live-tool', tool_name: 'bash', run_id: state.lastRun, batch_id: runningAssistant.id,
        state: 'finished', boundary: 'dispatch', outcome: 'success', activity: 'settling', phase: 'process_output',
        completed: 1200, total: null, unit: 'lines', text: `运行已结束\n${RUNNING_OUTPUT_TAIL}`,
        started_at: '2026-11-07T10:00:00Z', finished_at: '2026-11-07T10:00:12Z',
      } } : {},
    });
    const sessionJson = (sid: string, title: string) => {
      const isLive = sid === SESSION && options.live;
      const recent = sid === SESSION ? [history[history.length - 1], assistantTool, ordinaryResult, mcpResult] : [
        { id: 'session-b-answer', type: 'ai', content: '切换目标会话的确定回答', provisional: false,
          state: 'complete', run_id: null, reply_id: 'session-b-root' },
      ];
      return json({
        session_id: sid, title, model_id: 'A', workspace_root: null, state: 'ready',
        updated_at: '2026-11-07T10:00:00Z', last_run_status: isLive && state.finished ? 'completed' : null,
        active_tools: [], permission_mode: 'Default',
        messages: [...recent, ...(isLive && state.finished ? finalLiveMessages() : [])],
        busy: isLive && !state.finished, can_run: !isLive || state.finished, has_compaction: false,
        active_run_id: isLive && !state.finished ? state.lastRun : null,
        latest_run_id: isLive ? state.lastRun : null,
        manual_review_required: false, pending_approval: false,
        live: isLive ? runJson() : null,
        conversation_revision: state.finished ? 2 : 1, thinking_level: 'low', checkpoint_id: null,
        history: sid === SESSION ? { has_more: true, next_cursor: history[history.length - 2].id,
          coverage: { checkpoint_id: null, sync_revision: 1, history_version: 1, upper: historyCount }, has_compaction: false }
          : { has_more: false, next_cursor: null,
            coverage: { checkpoint_id: null, sync_revision: 1, history_version: 1, upper: 1 }, has_compaction: false },
      });
    };
    if (path === '/api/v1/projects') return json({ items: [{
      project_id: SCOPE, name: '阅读基线项目', source: { path: '/tmp/e2e-reading' },
      lifecycle: 'active', trusted: true, revision: 1, availability: null,
    }], default_parent_dir: null });
    if (path === `/api/v1/scopes/${SCOPE}/models`) return json({ default_model_id: 'A', model_ids: ['A'], thinking_options: {} });
    if (path === `/api/v1/scopes/${SCOPE}/session-controls`) return json({ tools: [], permission_modes: ['Default'], default_permission_mode: 'Default', default_active_tools: [] });
    if (path === `/api/v1/scopes/${SCOPE}/background-tasks`) return json({ items: [], next_before: null, active_count: 0, revision: 'r1', terminal_revision: 1 });
    const rows = [
      { session_id: SESSION, title: '阅读基线会话', model_id: 'A', workspace_root: null, state: 'ready', updated_at: '2026-11-07T10:00:00Z', last_run_status: null },
      { session_id: SESSION_B, title: '切换目标会话', model_id: 'A', workspace_root: null, state: 'ready', updated_at: '2026-11-07T10:00:00Z', last_run_status: null },
    ];
    if (path === `/api/v1/scopes/${SCOPE}/sessions` || path === `/api/v1/scopes/${SCOPE}/navigation/${SCOPE}/sessions`) return json(rows);
    if (path === `/api/v1/scopes/${SCOPE}/sessions/${SESSION}` || path === `/api/v1/scopes/${SCOPE}/sessions/${SESSION}/prepare`) {
      return sessionJson(SESSION, '阅读基线会话');
    }
    if (path === `/api/v1/scopes/${SCOPE}/sessions/${SESSION_B}` || path === `/api/v1/scopes/${SCOPE}/sessions/${SESSION_B}/prepare`) {
      return sessionJson(SESSION_B, '切换目标会话');
    }
    const messagesMatch = path.match(/^\/api\/v1\/scopes\/[^/]+\/sessions\/([^/]+)\/messages$/);
    if (messagesMatch && request.method === 'GET') {
      const before = url.searchParams.get('before');
      const endIndex = before ? history.findIndex(row => row.id === before) : history.length;
      const start = Math.max(0, endIndex - 30);
      return json({ messages: history.slice(start, endIndex), has_more: start > 0,
        next_cursor: start > 0 ? history[start].id : null,
        coverage: { checkpoint_id: null, sync_revision: 1, history_version: 1, upper: historyCount }, has_compaction: false });
    }
    if (/^\/api\/v1\/scopes\/[^/]+\/sessions\/[^/]+\/subagents$/.test(path)) return json([]);
    if (/^\/api\/v1\/scopes\/[^/]+\/sessions\/[^/]+\/context$/.test(path)) {
      return json({ session_id: SESSION, current: { basis: 'unavailable', estimate: null }, latest_request: null });
    }
    if (path === `/api/v1/scopes/${SCOPE}/sessions/${SESSION}/runs` && request.method === 'POST') {
      state.startRequested = true;
      state.runCounter += 1;
      state.lastRun = `run-${String(state.runCounter).padStart(2, '0')}`.padEnd(32, '0');
      state.finished = false;
      return json({ run_id: state.lastRun, session_id: SESSION });
    }
    const scopeRunView = `/api/v1/scopes/${SCOPE}/runs/${state.lastRun}`;
    if (path === scopeRunView) return json(runJson());
    if (path === `${scopeRunView}/events`) {
      calls.push(`SSE-OPEN after=${url.searchParams.get('after')} run=${state.lastRun} finished=${state.finished}`);
      response.writeHead(200, {
        'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache',
        Connection: 'keep-alive', ...cors,
      });
      let sequence = Number(url.searchParams.get('after') ?? 0);
      const frame = (event_type: string, data: Record<string, unknown>) => {
        sequence += 1; state.sent = sequence;
        response.write(`event: runtime\nid: ${sequence}\ndata: ${JSON.stringify({
          version: 1, session_id: SESSION, run_id: state.lastRun, sequence,
          timestamp: new Date().toISOString(), event_type, data,
        })}\n\n`);
      };
      const stream = { response, index: 0, firstTimer: setTimeout(() => {
        if (options.autoFirst !== false) advanceStream();
      }, 40), heartbeat: setInterval(() => response.write(': ping\n\n'), 15000), closed: false };
      activeStream = stream;
      frame('run_started', { mode: 'message' });
      if (options.runningTool) {
        frame('message_completed', { snapshot_json: JSON.stringify(runningAssistant) });
        frame('tool_call_started', { tool_call_id: 'live-tool', tool_name: 'bash', boundary: 'dispatch', activity: 'running' });
        frame('tool_progress', { tool_call_id: 'live-tool', tool_name: 'bash', batch_id: runningAssistant.id,
          activity: 'running', phase: 'process_output', completed: 1200, total: null, unit: 'lines',
          text: `正在产生长输出\n${RUNNING_OUTPUT_TAIL}` });
      }
      request.on('close', () => {
        stream.closed = true;
        clearInterval(stream.heartbeat); clearTimeout(stream.firstTimer);
        if (activeStream === stream) activeStream = null;
      });
      return;
    }
    calls.push(`UNMOCKED ${request.method} ${request.url}`);
    response.writeHead(404, { 'Content-Type': 'application/json', ...cors });
    response.end(JSON.stringify({ error: { code: 'unmocked_e2e_route' } }));
  });

  function advanceStream() {
    const stream = activeStream;
    if (!stream || stream.closed || state.finished || stream.index >= batches.length) return false;
    const index = stream.index;
    const frame = (event_type: string, data: Record<string, unknown>) => {
      state.sent += 1;
      stream.response.write(`event: runtime\nid: ${state.sent}\ndata: ${JSON.stringify({
        version: 1, session_id: SESSION, run_id: state.lastRun, sequence: state.sent,
        timestamp: new Date().toISOString(), event_type, data,
      })}\n\n`);
    };
    frame('message_delta', { message_id: 'assistant-live', reply_id: 'root', index: index + 1, text: batches[index] });
    stream.index += 1;
    state.streamed = stream.index;
    return true;
  }

  const wrapper: ConversationServer = {
    port: 0,
    url: '',
    calls,
    state,
    advanceStream,
    finishStream: () => {
      const stream = activeStream;
      if (state.finished) return;
      if (!stream || stream.closed || stream.index !== batches.length) {
        throw new Error(`stream_incomplete:${stream?.index ?? -1}/${batches.length}`);
      }
      clearInterval(stream.heartbeat); clearTimeout(stream.firstTimer);
      const frame = (event_type: string, data: Record<string, unknown>) => {
        state.sent += 1;
        stream.response.write(`event: runtime\nid: ${state.sent}\ndata: ${JSON.stringify({
          version: 1, session_id: SESSION, run_id: state.lastRun, sequence: state.sent,
          timestamp: new Date().toISOString(), event_type, data,
        })}\n\n`);
      };
      if (options.runningTool) {
        frame('message_completed', { snapshot_json: JSON.stringify(runningResult) });
        frame('tool_call_finished', { tool_call_id: 'live-tool', tool_name: 'bash', boundary: 'dispatch', outcome: 'success' });
      }
      frame('message_completed', { snapshot_json: JSON.stringify(finalLiveMessages().at(-1)) });
      frame('run_finished', { status: 'completed' });
      state.finished = true;
      stream.response.end();
    },
    listen: () => new Promise<void>(resolve => {
      server.listen(0, '127.0.0.1', () => {
        const address = server.address();
        const port = typeof address === 'object' && address ? address.port : 0;
        wrapper.port = port;
        wrapper.url = `http://127.0.0.1:${port}`;
        resolve();
      });
    }),
    close: async () => {
      if (activeStream) {
        clearInterval(activeStream.heartbeat); clearTimeout(activeStream.firstTimer);
        activeStream = null;
      }
      const closing = new Promise<void>(resolve => server.close(() => resolve()));
      server.closeAllConnections();
      await closing;
    },
  };
  return wrapper;
}
