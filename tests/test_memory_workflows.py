"""Fixed w01-w04 orchestration proves mechanisms, not autonomous/model business quality.

Only disposable SQLite/files, offline scripted models and loopback services are used.
The generated unittest is an output artifact; no AIAutoTest or provider is contacted.
"""

import asyncio
import json
import os
from pathlib import Path
import shlex
import socket
import sys

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.tools import (create_session_read_tool, create_session_write_tool,
    create_session_bash_tool, create_session_powershell_tool)
from naiyue_test_agent.tools.history import create_history_tool
from naiyue_test_agent.tools.memory import create_memory_tool
from test_async_application import StreamingToolModel, call
from test_memory_application import config
from test_memory_jobs import response
from test_memory_lifecycle import proposal
from test_memory_tool import opened
from test_memory_wiki_pagination import decode_content
from test_memory_wiki_worker import finish, read


WORKFLOWS = {item['id']: item for item in json.loads((Path(__file__).resolve().parents[1] /
    'docs/research/memory00/fixtures.json').read_text())['workflows']}
SHELL = 'powershell' if os.name == 'nt' else 'bash'


def settings(root):
    base = config(root, model_id='M')
    return base.model_copy(update={'permissions': base.permissions.model_copy(update={'mode': 'BypassApprovals'}),
        'storage': base.storage.model_copy(update={'data_dir': root.parent / (root.name + '-data')})})


def tools(*names):
    factories = dict(memory=create_memory_tool, history=create_history_tool, read=create_session_read_tool,
        write=create_session_write_tool, bash=create_session_bash_tool, powershell=create_session_powershell_tool)
    return [factories[name]() for name in names]


def command(script, *args):
    values = [sys.executable, str(script), *map(str, args)]
    return '& ' + ' '.join("'" + value.replace("'", "''") + "'" for value in values) if os.name == 'nt' else shlex.join(values)


def messages(result):
    return {message.tool_call_id: message for message in result['messages'] if isinstance(message, ToolMessage)}


async def turn(app, model, sid, text, *replies):
    model.replies.extend(replies)
    return await app.host.runs.start(sid, [('user', text)])


def record_request(value, sources):
    content = {key: field for key, field in value.items() if key != 'kind'}
    page = dict(topic='working_agreements', title=content['title'], description=content['subject'])
    return dict(action='record', scope='workspace', proposal=content,
                destination={'new_page': page}, sources=sources)


async def record(app, model, sid, text, proposals):
    calls = [call('memory', {'request': record_request(value, [dict(current_user=True, quote=text)])},
                  f'seed-{index}').tool_calls[0] for index, value in enumerate(proposals)]
    result = await turn(app, model, sid, text, AIMessage(content='', tool_calls=calls), AIMessage(content='已保存约定'))
    rows = [messages(result)[f'seed-{index}'] for index in range(len(proposals))]
    assert all(message.status == 'success' for message in rows)
    return [json.loads(message.content)['item'] for message in rows]


def source_ref(app, sid, message, quote):
    parent = app.host.sessions._persistence.storage.sessions.get(sid)
    saved, = app.host.sessions._persistence.storage.database.read_rows('''SELECT revision FROM naiyue_chat_messages
        WHERE session_id=? AND thread_id=? AND message_id=?''', (sid, parent['thread_id'], message.id))
    return dict(session_id=sid, thread_id=parent['thread_id'], message_id=message.id,
        revision=saved['revision'], conversation_revision=parent['conversation_revision'],
        quote=quote, offset=message.content.index(quote))


def test_w01_first_directory_formal_history_and_no_other_session_execution(monkeypatch, tmp_path):
    async def scenario():
        root, worker = StreamingToolModel(replies=[]), StreamingToolModel(replies=[response()])
        async with opened(monkeypatch, tmp_path, root, settings=settings(tmp_path),
                tools=tools('memory', 'history'), models={'A': root, 'M': worker}) as app:
            old = await app.host.sessions.create_workspace('A', tmp_path)
            raw = '旧任务原文 ' + 'x' * 600 + 'DETAIL_ONLY_MARKER'
            await turn(app, root, old, raw, call('memory', {'request': dict(action='refresh_overview')}, 'overview'),
                AIMessage(content='已完成旧任务检查，下一步继续验证'))
            await app.host.sessions._sessions.memory_jobs.wait_idle()
            cold = await app.host.sessions.create_workspace('A', tmp_path)
            current = await app.host.sessions.create_workspace('A', tmp_path)
            before = {sid: app.host.sessions._persistence.storage.sessions.get(sid) for sid in (old, cold)}
            count = len(root.seen)
            result = await turn(app, root, current, WORKFLOWS['w01']['name'],
                call('history', dict(action='list_sessions'), 'directory'),
                call('history', dict(action='read', session_id=old), 'old-history'),
                call('history', dict(action='read', session_id=cold), 'cold-history'),
                AIMessage(content='根据概览及原文，在当前会话继续'))
            system = next(m.content for m in root.seen[count] if isinstance(m, SystemMessage))
            assert 'Other session directory' in system and old in system and cold in system
            assert 'DETAIL_ONLY_MARKER' not in system
            views = messages(result)
            old_view = json.loads(views['old-history'].content)
            cold_view = json.loads(views['cold-history'].content)
            assert old_view['overview']['next_step'] == '继续验证' and raw in str(old_view)
            assert cold_view['overview'] is None and cold_view['overview_status'] == 'not_generated'
            assert cold_view['items'] == [] and len(worker.seen) == 1
            for sid in (old, cold):
                after = app.host.sessions._persistence.storage.sessions.get(sid)
                assert (after['run_id'], after['checkpoint_id'], after['state']) == (
                    before[sid]['run_id'], before[sid]['checkpoint_id'], before[sid]['state'])
            assert len(root.seen) == count + 4
    asyncio.run(scenario())


def test_w02_memory_plan_current_assets_and_actual_current_report(monkeypatch, tmp_path):
    async def scenario():
        skill = tmp_path / '.agents/skills/testing/SKILL.md'; skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: testing\ndescription: 合成测试工作流\n---\n读取源资产与配置，再生成、收集、运行，最后读取本次run_id报告。', encoding='utf-8')
        assets = tmp_path / 'case.json'; assets.write_text(json.dumps(dict(value=3, expected=6)))
        current_config = tmp_path / 'test-config.json'
        current_config.write_text(json.dumps(dict(interpreter=sys.executable, source='case.json')))
        (tmp_path / 'service.py').write_text('def double(value): return value * 2\n')
        script = tmp_path / 'run_tests.py'
        script.write_text('''import importlib.util,json,pathlib,sys,unittest
root=pathlib.Path(__file__).parent
case=json.loads((root/'case.json').read_text())
code="import unittest,service\\nclass TestDouble(unittest.TestCase):\\n def setUp(self): self.resources=[]\\n def tearDown(self): self.resources.clear()\\n def test_case(self): self.assertEqual(service.double(%r),%r)\\n"%(case['value'],case['expected'])
generated=root/'generated_test.py';generated.write_text(code)
spec=importlib.util.spec_from_file_location('generated_test',generated)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
suite=unittest.defaultTestLoader.loadTestsFromModule(module)
collected=suite.countTestCases();result=unittest.TextTestRunner().run(suite)
report=dict(run_id=sys.argv[1],collected=collected,tests_run=result.testsRun,success=result.wasSuccessful(),interpreter=sys.executable,generated_from='case.json')
(root/sys.argv[2]).write_text(json.dumps(report))
print(json.dumps(report));sys.exit(0 if result.wasSuccessful() else 1)
''')
        original_assets = assets.read_bytes()
        root, worker = StreamingToolModel(replies=[]), StreamingToolModel(replies=[])
        async with opened(monkeypatch, tmp_path, root, settings=settings(tmp_path),
                tools=tools('memory', 'read', SHELL), models={'A': root, 'M': worker}) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            saved = await record(app, root, sid, '以后测试工作流按当前配置解释器，断言和清理齐备，先核对资产再生成执行读报告。', [
                proposal(kind='context', title='测试工作流环境', subject='local environment', body='测试工作流使用当前配置解释器，执行前确认。'),
                proposal(kind='convention', title='测试工作流规范', subject='local assertions', body='测试工作流必须有前置、断言和资源清理。'),
                proposal(kind='context', title='测试工作流顺序', subject='local workflow', body='测试工作流先读资产和Skill，再生成收集运行并读本次报告。')])
            await turn(app, root, sid, '先运行一次，形成真实旧报告',
                call(SHELL, dict(command=command(script, 'w02-old', 'old-report.json')), 'old-run'), AIMessage(content='旧次已运行'))
            assert json.loads((tmp_path / 'old-report.json').read_text())['success']
            reads = [call('memory', {'request': dict(action='read', scope='workspace', memory_id=item['memory_id'], version=1)},
                'memory-' + item['memory_id']) for item in saved]
            planned = await turn(app, root, sid, WORKFLOWS['w02']['name'] + '，先制定本次计划',
                call('memory', {'request': dict(action='search', scope='workspace', query='测试工作流')}, 'search'), *reads,
                call('read', dict(path=str(skill)), 'skill'), call('read', dict(path=str(current_config)), 'config'),
                call('read', dict(path=str(assets)), 'assets'), AIMessage(content='这是本次计划；旧成功不能替代本次验证'))
            planned_messages = messages(planned)
            found = decode_content(json.loads(planned_messages['search'].content))['entries']
            assert {row['title'] for row in found} == {'测试工作流环境', '测试工作流规范', '测试工作流顺序'}
            for item in saved:
                message = planned_messages['memory-' + item['memory_id']]
                assert message.status == 'success'
                entry, = decode_content(json.loads(message.content))['entries']
                assert entry['memory_id'] == item['memory_id'] and entry['version'] == 1
            assert {row['memory_id'] for row in found} == {item['memory_id'] for item in saved}
            assert not (tmp_path / 'current-report.json').exists()
            result = await turn(app, root, sid, '现在执行并核对本次报告',
                call(SHELL, dict(command=command(script, 'w02-current', 'current-report.json')), 'current-run'),
                call('read', dict(path=str(tmp_path / 'current-report.json')), 'current-report'), AIMessage(content='本次执行结果已核对'))
            report = json.loads((tmp_path / 'current-report.json').read_text())
            assert report['run_id'] == 'w02-current' and report['success'] and report['tests_run'] == report['collected'] == 1
            assert 'w02-current' in messages(result)['current-report'].content
            assert messages(result)['current-run'].status == 'success' and assets.read_bytes() == original_assets
            assert (tmp_path / 'generated_test.py').exists() and report['generated_from'] == 'case.json'
            assert worker.seen == []
    asyncio.run(scenario())


def test_w03_current_failure_recheck_and_finite_experience_preserve_user_choice(monkeypatch, tmp_path):
    async def scenario():
        knowledge = tmp_path / '.agents/skills/nacos/SKILL.md'; knowledge.parent.mkdir(parents=True)
        knowledge.write_text('---\nname: nacos\ndescription: 合成本机配置检查\n---\n合成本机检查：先读知识入口、源码，再核对当前配置与本次返回；不访问真实Nacos。', encoding='utf-8')
        script = tmp_path / 'probe.py'
        script.write_text('''import json,pathlib,socket,sys
root=pathlib.Path(__file__).parent;cfg=json.loads((root/'route.json').read_text())
try:
 with socket.create_connection((cfg['host'],cfg['port']),timeout=2) as connection:
  assert connection.recv(4)==b'pong'
 report=dict(run_id=sys.argv[1],success=True);marker='PROBE_OK';code=0
except Exception as error:
 report=dict(run_id=sys.argv[1],success=False,error_type=type(error).__name__);marker='PROBE_FAILED';code=1
(root/(sys.argv[1]+'.json')).write_text(json.dumps(report));print(marker,json.dumps(report));sys.exit(code)
''')
        blocked = socket.socket(); blocked.bind(('127.0.0.1', 0))
        current = tmp_path / 'route.json'
        current.write_text(json.dumps(dict(host='127.0.0.1', port=blocked.getsockname()[1])))
        async def serve(reader, writer):
            writer.write(b'pong'); await writer.drain(); writer.close(); await writer.wait_closed()
        server = await asyncio.start_server(serve, '127.0.0.1', 0)
        try:
            root, worker = StreamingToolModel(replies=[]), StreamingToolModel(replies=[])
            async with opened(monkeypatch, tmp_path, root, settings=settings(tmp_path),
                    tools=tools('memory', 'read', 'write', SHELL), models={'A': root, 'M': worker}) as app:
                sid = await app.host.sessions.create_workspace('A', tmp_path)
                selected, = await record(app, root, sid, '以后网络保持配置代理模式，每次使用前重新检查实际配置。',
                    [proposal(title='代理环境选择', subject='network choice', body='代理环境仍采用配置代理模式；历史成功不是当前可用保证。')])
                before = await turn(app, root, sid, WORKFLOWS['w03']['name'],
                    call('memory', {'request': dict(action='search', query='代理环境')}, 'clue'),
                    call('read', dict(path=str(knowledge)), 'knowledge'), call('read', dict(path=str(script)), 'source'),
                    call('read', dict(path=str(current)), 'route-before'),
                    call(SHELL, dict(command=command(script, 'w03-failed')), 'failed-probe'), AIMessage(content='当前连接失败，原长期选择保持，核对端点'))
                failure = messages(before)['failed-probe']
                assert failure.status == 'error' and 'PROBE_FAILED' in failure.content
                assert not json.loads((tmp_path / 'w03-failed.json').read_text())['success']
                corrected = json.dumps(dict(host='127.0.0.1', port=server.sockets[0].getsockname()[1]))
                after = await turn(app, root, sid, '只修正当前合成配置的端点，再验证相同探针',
                    call('write', dict(path=str(current), content=corrected), 'route-fix'),
                    call('read', dict(path=str(current)), 'route-after'),
                    call(SHELL, dict(command=command(script, 'w03-current')), 'success-probe'),
                    call('read', dict(path=str(tmp_path / 'w03-current.json')), 'current-report'), AIMessage(content='本次修正后成功，结论仅限本次条件'))
                success = messages(after)['success-probe']
                assert success.status == 'success' and 'PROBE_OK' in success.content
                clue, = decode_content(json.loads(messages(before)['clue'].content))['entries']
                assert clue['memory_id'] == selected['memory_id'] and clue['version'] == 1
                args = record_request(proposal(title='有限端点观察',
                    subject='local probe observation', target='loopback probe', basis='tool_observation',
                    body='本次合成端点配置修正后，同一探针成功；仅限本次条件，不推翻长期代理选择。'),
                    [source_ref(app, sid, failure, 'PROBE_FAILED'), source_ref(app, sid, success, 'PROBE_OK')])
                saved = await turn(app, root, sid, '记录有本次前后证据的有限经验', call('memory', {'request': args}, 'experience'),
                    call('memory', {'request': dict(action='read', scope='workspace', memory_id=selected['memory_id'], version=1)}, 'original-choice'),
                    AIMessage(content='有限经验保存，长期选择未改'))
                views = messages(saved)
                item = json.loads(views['experience'].content)['item']
                assert views['experience'].status == 'success' and item['status'] == 'active'
                assert item['ttl_days'] == 30 and item['expires_at'] == item['evidence_at'] + 30 * 86400
                original, = decode_content(json.loads(views['original-choice'].content))['entries']
                assert original['version'] == 1
                assert original['body'] == '代理环境仍采用配置代理模式；历史成功不是当前可用保证。'
                assert json.loads((tmp_path / 'w03-current.json').read_text())['success'] and not worker.seen
        finally:
            blocked.close(); server.close(); await server.wait_closed()
    asyncio.run(scenario())


class GatedWikiProposal(StreamingToolModel):
    branch: str
    page_id: str = ''
    evidence_id: str = ''

    async def _next(self, model_messages):
        self.seen.append(model_messages)
        step = len(self.seen)
        if step == 1:
            payload = json.loads(model_messages[-1].content)
            old = next(unit for unit in payload['batch']['units']
                       if unit['role'] == 'human' and '以后并发规则使用旧选择' in unit['text'])
            self.evidence_id = old['evidence_id']
            item = dict(proposal(title='并发规则', subject='concurrent rule', body='并发规则旧选择'),
                        matter='并发规则', topic='working_agreements', scope='workspace',
                        sources=[dict(evidence_id=self.evidence_id)])
            item.pop('kind')
            return response(proposals=[item])
        # After forget, read the fresh catalog so the evidence suppression gate is exercised.
        # For correction, retain the completed pre-update page read to exercise snapshot CAS.
        if (self.branch == 'forget' and step == 2) or (self.branch == 'correct' and step == 4):
            self.entered.set()
            await self.release.wait()
        if step == 2:
            return read(dict(action='list_pages', topic='working_agreements'))
        if self.branch == 'forget':
            return finish(decisions=[dict(relation='add', proposal_ids=['P1'], destination='N1')],
                          page_changes=[dict(ref='N1', topic='working_agreements', title='并发规则', description='')])
        if step == 3:
            return read(dict(action='read_page', page_id=self.page_id))
        return finish(decisions=[dict(relation='supplement', proposal_ids=['P1'], old_refs=['O1'],
                                      target_old_ref='O1', evidence_ids=[self.evidence_id])])


@pytest.mark.parametrize('branch', ['correct', 'forget'])
def test_w04_other_session_rejects_inflight_old_update_or_suppressed_record(monkeypatch, tmp_path, branch):
    async def scenario():
        root = StreamingToolModel(replies=[])
        worker = GatedWikiProposal(replies=[], branch=branch, entered=asyncio.Event(), release=asyncio.Event())
        async with opened(monkeypatch, tmp_path, root, settings=settings(tmp_path),
                tools=tools('memory'), models={'A': root, 'M': worker}) as app:
            first = await app.host.sessions.create_workspace('A', tmp_path)
            original, = await record(app, root, first, '以后并发规则使用旧选择',
                [proposal(kind='convention', title='并发规则', subject='concurrent rule', body='并发规则旧选择')])
            worker.page_id = original['page_id']
            accepted = await turn(app, root, first, '并发规则',
                call('memory', {'request': dict(action='refresh_overview')}, 'refresh'), AIMessage(content='整理已登记'))
            receipt = json.loads(messages(accepted)['refresh'].content)
            await asyncio.wait_for(worker.entered.wait(), 5)
            second = await app.host.sessions.create_workspace('A', tmp_path)
            change = dict(action='update' if branch == 'correct' else 'forget', scope='workspace',
                memory_id=original['memory_id'], expected_version=1,
                sources=[dict(current_user=True, quote='更正并发规则为新选择' if branch == 'correct' else '遗忘并发规则旧选择')])
            if branch == 'correct':
                change['change'] = dict(mode='replace', proposal=dict(title='并发规则', subject='concurrent rule',
                    body='并发规则新选择', basis='user_declaration', intent='activate', reason='用户更正'))
            else:change['reason'] = '用户显式遗忘'
            changed = await turn(app, root, second, change['sources'][0]['quote'],
                call('memory', {'request': change}, 'change'), AIMessage(content='已处理'))
            assert messages(changed)['change'].status == 'success'
            from naiyue_test_agent.application.memory import memory_organization
            rejected, query = [], memory_organization.commit_group
            def observed(*args, **kwargs):
                try:return query(*args, **kwargs)
                except MemoryReadError as error:
                    rejected.append(str(error)); raise
            monkeypatch.setattr(memory_organization, 'commit_group', observed)
            worker.release.set(); await app.host.sessions._sessions.memory_jobs.wait_idle()
            status = await app.host.tasks.output(receipt['task_id'], session_id=first, workspace_root=tmp_path)
            counts = json.loads(status['output'])
            assert status['result_status'] == 'partial' and status['cleanup_confirmed']
            assert counts['overview_saved'] == 1 and counts['memory_saved'] == 0 and counts['failed'] == 1
            assert ('memory_conflict' if branch == 'correct' else 'memory_source_suppressed') in rejected
            checked = await turn(app, root, second, '核对权威记忆状态',
                call('memory', {'request': dict(action='search', scope='workspace', query='并发规则')}, 'verify'), AIMessage(content='权威状态已核对'))
            rows = decode_content(json.loads(messages(checked)['verify'].content))['entries']
            if branch == 'correct':
                assert len(rows) == 1 and rows[0]['version'] == 2 and rows[0]['body'] == '并发规则新选择'
            else:
                assert rows == []
                for table in ('naiyue_memory_sources', 'naiyue_memory_versions', 'naiyue_memory_search'):
                    assert app.host.sessions._persistence.storage.database.connection.execute(f'SELECT count(*) FROM {table} WHERE memory_id=?',
                        (original['memory_id'],)).fetchone()[0] == 0
                tombstone = app.host.sessions._persistence.storage.database.connection.execute('SELECT body,status FROM naiyue_memories WHERE memory_id=?',
                    (original['memory_id'],)).fetchone()
                assert tuple(tombstone) == ('', 'forgotten')
            assert len(worker.seen) == (4 if branch == 'correct' else 3)
    asyncio.run(scenario())
