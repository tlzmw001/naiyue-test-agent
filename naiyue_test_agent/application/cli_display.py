"""A local synchronous terminal consumer; never controls the Agent or its state."""

import hashlib
import json
from threading import RLock
from time import monotonic

from langchain_core.messages import AIMessage
from naiyue_test_agent.model_access.messages import content_text
from naiyue_test_agent.tools.settings import ToolProgressSettings


def _safe_text(text):
    return ''.join(char if char in '\n\t' or char.isprintable()
                   else f'\\x{ord(char):02x}' for char in str(text))


class CliEventDisplay:
    """Keep a per-call display ledger, not a second conversation history."""

    def __init__(self, session_id, *, writer=None, progress_settings=None):
        self.session_id = session_id
        self._writer = writer or (lambda text: print(text, flush=True))
        self._lock = RLock()
        self._receipts = {}
        self._progress_settings = progress_settings or ToolProgressSettings()
        self.begin_call()

    def begin_call(self):
        with self._lock:
            self._run_id = None
            self._last_sequence = 0
            self._messages = {}
            self._texts = {}
            self._tools = {}
            self._progress = {}
            self._stopping = False
            self._requested_tools = set()
            self._non_text_notices = set()
            self._terminal = False
            self._had_reply = False
            self._incomplete = False
            self._agents = {}

    @property
    def terminal_displayed(self):
        with self._lock:
            return self._terminal

    def write(self, text):
        with self._lock:
            self._writer(_safe_text(text))

    def control_receipt_count(self, kind):
        with self._lock:
            return self._receipts.get(kind, 0)

    def show_history(self, history):
        """Display stored messages without manufacturing run events or results."""
        with self._lock:
            self.write('只读历史：当前不恢复执行，也不会自动重跑旧调用。')
            recovery = history['recovery']
            self.write(f'状态：{recovery["kind"]}；原因：{recovery["reason"]}')
            for message in history['messages']:
                self.write(f'[{message.type}]\n{content_text(message.content)}')
                if message.type == 'tool':
                    from naiyue_test_agent.mcp_client.presentation import result_projection
                    self._mcp_result(result_projection(message.artifact, name=message.name))
                if isinstance(message, AIMessage):
                    for call in message.tool_calls:
                        self.write(f'已保存工具请求：{call["name"]}（{call["id"]}）\n'
                                   + json.dumps(call['args'], ensure_ascii=False))
                if isinstance(message.content, list) and any(
                    isinstance(block, dict) and block.get('type') != 'text' for block in message.content
                ):
                    self.write('该消息包含终端不能展示的非文本内容')
            if recovery['unconfirmed_tool_call_ids']:
                self.write('结果未确认：' + ', '.join(recovery['unconfirmed_tool_call_ids']))

    def _incomplete_notice(self):
        if not self._incomplete:
            self.write('消息观察不完整，将在返回时补显可确认的回答')
            self._incomplete = True

    def __call__(self, event):
        if event.session_id != self.session_id:
            return
        with self._lock:
            if event.event_type == 'run_started' and self._run_id is None:
                self._run_id = event.run_id
            if event.run_id != self._run_id or event.sequence <= self._last_sequence:
                return
            if event.source is not None:
                self._render_child(event)
            else:
                self._render(event.event_type, event.data)
            self._last_sequence = event.sequence

    def _render_child(self, event):
        source = event.source
        child = self._agents.get(source.agent_path)
        if child is None:
            child = CliEventDisplay(self.session_id, progress_settings=self._progress_settings,
                writer=lambda text: self.write(f'[{source.agent_path}] {text}'))
            self._agents[source.agent_path] = child
        if event.event_type == 'run_started':
            child._terminal = False
            child.write('子 Agent 正在执行')
        elif event.event_type in ('message_completed', 'message_revised'):
            row = json.loads(event.data.snapshot_json)
            if row.get('type') == 'ai':
                # Only tool requests are shown here; child prose is not a root reply.
                child._message({**row, 'content': ''})
            elif row.get('type') == 'tool':
                child._message(row)
        elif event.event_type == 'run_finished' and event.data.status == 'interrupted':
            child.write('子 Agent 等待审批，父运行仍在等待结果')
        else:
            child._render(event.event_type, event.data)

    def _render(self, kind, data):
        if kind in ('message_completed', 'message_revised'):
            if self._messages.get(data.message_id) == data.snapshot_sha256:
                return
            row = json.loads(data.snapshot_json)
            if not data.is_context_summary:
                self._message(row, revised=kind == 'message_revised', visibility=data.visibility)
            self._messages[data.message_id] = data.snapshot_sha256
        elif kind == 'message_snapshot_unavailable':
            self._incomplete_notice()
        elif kind == 'run_finished':
            self._finish(data)
        elif kind == 'model_call_started' and data.purpose == 'agent':
            self.write('模型正在处理请求')
        elif kind == 'tool_call_started':
            self._tools[data.tool_call_id] = data.tool_name or '工具'
            self._progress[data.tool_call_id] = {'started': monotonic(), 'batch': None,
                                                'ended': False, 'shown_at': None, 'pending': None}
            self.write(f'处理工具调用：{data.tool_name or "工具"}')
        elif kind == 'tool_progress':
            self._tool_progress(data)
        elif kind == 'tool_call_finished':
            row = self._progress.get(data.tool_call_id)
            if row:
                self._show_progress(data.tool_call_id, row)
                row['ended'] = True
            labels = {'success': '处理完成', 'error_result': '返回错误结果，模型可继续处理',
                      'not_executed': '未执行', 'exception': '处理失败',
                      'outcome_unknown': '副作用无法确认'}
            self.write(f'工具 {data.tool_name or "工具"}：{labels[data.outcome]}')
        elif kind == 'approval_requested':
            self.write(f'有 {data.count} 项工具调用待审批')
            if data.items:
                from .cli_approval import show_approval_batches
                from dataclasses import asdict
                show_approval_batches([{'approval_batch_id': data.approval_batch_id,
                    'agent_path': '当前实例', 'items': [{key: value for key, value in asdict(item).items()
                        if value is not None} for item in data.items]}], self.write)
        elif kind.startswith('control_'):
            self._control(kind, data)
        elif kind in ('compaction_started', 'context_recovery_started'):
            self.write('正在压缩上下文' if kind == 'compaction_started' else '输入超出模型窗口，正在尝试恢复')
        elif kind in ('compaction_finished', 'context_recovery_finished'):
            self._context(kind, data)

    def _tool_progress(self, data):
        row = self._progress.get(data.tool_call_id)
        if (self._terminal or not row or row['ended'] or
                row['batch'] not in (None, data.batch_id) or
                self._tools.get(data.tool_call_id) != data.tool_name):
            return
        row['batch'] = data.batch_id
        previous = row.get('latest')
        row['latest'] = data
        row['pending'] = data
        row['last_activity'] = monotonic()
        interval = self._progress_settings.min_interval_milliseconds / 1000
        if (row['shown_at'] is None or not previous or
                (previous.phase, previous.activity) != (data.phase, data.activity) or
                monotonic() - row['shown_at'] >= interval):
            self._show_progress(data.tool_call_id, row)

    def _show_progress(self, call_id, row):
        data = row['pending']
        if data is None:
            return
        labels = {'queued': '排队中', 'preparing': '准备中', 'running': '运行中',
                  'stopping': '停止中', 'settling': '收尾中'}
        label = '停止中' if self._stopping else labels[data.activity]
        now = monotonic()
        text = f'工具 {data.tool_name}：{label} · {data.phase}'
        text += f' · 已等待/运行 {int(now - row["started"])}秒'
        text += f' · 距最后活动 {int(now - row["last_activity"])}秒'
        if data.completed is not None:
            text += f' · {data.completed}' + (f'/{data.total}' if data.total is not None else '')
            text += f' {data.unit or ""}'
        tail = data.text.encode('utf-8')[-self._progress_settings.max_text_bytes:].decode('utf-8', errors='ignore')
        if tail:
            text += '\n' + tail
        self.write(text)
        row['shown_at'], row['pending'] = now, None

    def _message(self, row, *, revised=False, visibility='graph_values'):
        identity = row.get('id')
        content = content_text(row.get('content'))
        fingerprint = hashlib.sha256(content.encode('utf-8')).hexdigest()
        if row.get('type') == 'tool' and row.get('mcp_result'):
            fingerprint = hashlib.sha256(json.dumps([content, row['mcp_result']], sort_keys=True).encode()).hexdigest()
        if row.get('type') == 'ai':
            if content.strip() and (revised or self._texts.get(identity) != fingerprint):
                prefix = ('失败后状态中的助手消息：\n' if visibility == 'recovery_view' else '')
                if revised or identity in self._texts:
                    prefix += '上一条助手消息已修订：\n'
                self.write(prefix + content)
                self._texts[identity] = fingerprint
                self._had_reply = True
            if identity not in self._non_text_notices and isinstance(row.get('content'), list) and any(
                isinstance(block, dict) and block.get('type') not in ('text', 'reasoning', 'thinking')
                for block in row['content']
            ):
                self.write('助手消息包含当前终端不能展示的非文本内容')
                self._non_text_notices.add(identity)
            for call in row.get('tool_calls', []):
                call_id = call.get('id')
                if call_id not in self._requested_tools:
                    self.write(f'工具请求：{call.get("name", "工具")}')
                    self._requested_tools.add(call_id)
                    self._tools[call_id] = call.get('name', '工具')
        elif row.get('type') == 'tool':
            if self._texts.get(identity) != fingerprint:
                self._mcp_result(row.get('mcp_result'))
                if row.get('status') == 'error' and content.strip():
                    name = row.get('name') or self._tools.get(row.get('tool_call_id'), '工具')
                    self.write(f'工具 {name} 的错误说明：\n{content}')
                self._texts[identity] = fingerprint

    def _mcp_result(self, result):
        if not result:
            return
        labels = {'success': '成功', 'tool_error': '工具返回错误', 'not_executed': '未执行',
                  'outcome_unknown': '结果未知，请核对远端副作用；未自动重试', 'unsupported_result': '返回内容暂不支持'}
        source = result['source']
        self.write(f"MCP {source['server_id']}/{source['remote_tool']}：{labels[result['outcome']]}")
        if result.get('truncated'):
            status = '完整' if result.get('archive_complete') else '不完整或不可用'
            self.write(f"结果已截断；归档{status}" + (f"（{result['archive_reason']}）" if result.get('archive_reason') else ''))
            for key, path in result.get('archive_paths', {}).items():
                self.write(f'归档 {key}：{path}')

    def _finish(self, data):
        if self._terminal:
            return
        for call_id, row in self._progress.items():
            self._show_progress(call_id, row)
            if not row['ended']:
                self.write(f'工具 {self._tools[call_id]}：状态未确认')
            row['ended'] = True
        if data.status == 'failed':
            text = ('工具副作用无法确认；请先检查执行结果，勿直接重试'
                    if data.manual_review_required else '会话保存未确认，本次运行失败'
                    if data.failure.error_type == 'SessionPersistenceError' else f'运行失败（{data.failure.error_type}）')
        else:
            text = {'completed': '本次运行完成', 'stopped': '已停止',
                    'interrupted': '运行已暂停，等待审批'}[data.status]
        self.write(text)
        self._terminal = True

    def _control(self, kind, data):
        label = {'steering': 'Steering', 'follow_up': 'Follow-up', 'stop': '停止请求',
                 'approval': '审批决定'}[data.kind]
        if kind == 'control_accepted':
            if data.kind == 'stop':
                self._stopping = True
            text = {'steering': 'Steering 已排队', 'follow_up': 'Follow-up 已排队，将在自然结束边界处理',
                    'stop': '已请求停止，正在等待收尾', 'approval': '审批决定已接受'}[data.kind]
            self.write(text)
            self._receipts[data.kind] = self._receipts.get(data.kind, 0) + 1
        elif kind == 'control_delivered':
            self.write(f'{label} 已加入本次上下文')
        elif kind == 'control_applied' and data.kind == 'approval':
            self.write('审批决定已由审批流程处理；工具结果另行确认')
        elif kind == 'control_discarded':
            self.write(f'{label} 未执行，已随本次运行结束清理')
        elif kind == 'control_outcome_unknown':
            self.write(f'{label} 已被取出或提交，但未确认交付或生效')

    def _context(self, kind, data):
        labels = {'applied': '上下文已更新为摘要和保留消息',
                  'recovered': '输入溢出恢复成功，继续处理',
                  'skipped': '本次未压缩，继续使用原上下文',
                  'fallback': '摘要未完成，继续使用原上下文',
                  'failed': '上下文操作失败', 'unconfirmed': '上下文替换或恢复未确认'}
        text = labels[data.status]
        if data.status not in ('applied', 'recovered') and data.reason:
            text += f'（{data.reason}）'
        self.write(text)

    def finish_result(self, result, *, input_message_id=None, resume_start_index=None):
        with self._lock:
            messages = result.get('messages', [])
            start = resume_start_index
            if input_message_id is not None:
                start = next((i + 1 for i, message in enumerate(messages)
                              if message.id == input_message_id), None)
            if type(start) is not int or not 0 <= start <= len(messages):
                self._incomplete_notice()
            else:
                for i, message in enumerate(messages[start:], start):
                    if isinstance(message, AIMessage):
                        identity = message.id or f'result-{i}'
                        text = content_text(message.content)
                        if text.strip() and self._texts.get(identity) != hashlib.sha256(text.encode('utf-8')).hexdigest():
                            self._incomplete_notice()
                        self._message({'id': identity, 'type': 'ai',
                                       'content': message.content, 'tool_calls': message.tool_calls})
            if not self._had_reply and not result.get('approval_requests'):
                self.write('本轮没有可展示的回答')
            if not self._terminal:
                self.write('运行已返回，但未观察到终局事件')
