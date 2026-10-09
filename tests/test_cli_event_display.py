"""Display facts once, keeping result/exception fallback independent of events."""

import json
from types import SimpleNamespace
from threading import Barrier, Thread

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent.application.cli_display import CliEventDisplay


def event(event_type, sequence, **data):
    return SimpleNamespace(event_type=event_type, session_id='session', run_id='run',
                           sequence=sequence, data=SimpleNamespace(**data))


def message(sequence, identity='a', text='answer', kind='message_completed', **extra):
    row = {'id': identity, 'type': 'ai', 'content': text, 'tool_calls': []}
    row.update(extra.pop('row', {}))
    return event(kind, sequence, message_id=identity, snapshot_sha256=str(sequence),
                 snapshot_json=json.dumps(row), visibility=extra.pop('visibility', 'graph_values'),
                 is_context_summary=extra.pop('summary', False), **extra)


def display():
    lines = []
    view = CliEventDisplay('session', writer=lines.append)
    view.begin_call()
    view(event('run_started', 1, mode='message'))
    return view, lines


def test_messages_are_immediate_and_result_is_not_reprinted():
    view, lines = display()
    first = message(2)
    view(first)
    assert 'answer' in '\n'.join(lines)
    view(first)
    view.finish_result({'messages': [HumanMessage(content='q', id='h'),
                                     AIMessage(content='answer', id='a'),
                                     AIMessage(content='next answer', id='b')]},
                       input_message_id='h')
    assert lines.count('answer') == lines.count('next answer') == 1


def test_equal_text_different_ids_and_revisions_are_visible():
    view, lines = display()
    view(message(2, 'a', 'same'))
    view(message(3, 'b', 'same'))
    view(message(4, 'a', 'new version', kind='message_revised'))
    assert lines.count('same') == 2
    assert '已修订' in '\n'.join(lines) and 'new version' in '\n'.join(lines)


def test_hidden_messages_recovery_view_and_terminal_control_escaping():
    view, lines = display()
    view(message(2, text='PRIVATE_SUMMARY', summary=True))
    view(message(3, row={'type': 'human', 'content': 'PRIVATE_INPUT'}))
    view(message(4, 'recovered', 'safe\x1b[31m\nnext', visibility='recovery_view'))
    out = '\n'.join(lines)
    assert 'PRIVATE_' not in out
    assert '失败后' in out and '\x1b' not in out and '\\x1b' in out


@pytest.mark.parametrize('status,text', [('completed', '本次运行完成'),
    ('stopped', '已停止'), ('interrupted', '等待审批'), ('failed', '运行失败')])
def test_terminal_is_once_and_turn_end_is_not_terminal(status, text):
    view, lines = display()
    view(event('turn_finished', 2, status='completed'))
    assert not view.terminal_displayed
    last = event('run_finished', 3, status=status, manual_review_required=False,
                 failure=SimpleNamespace(error_type='RuntimeError') if status == 'failed' else None)
    view(last)
    view(last)
    assert view.terminal_displayed
    assert '\n'.join(lines).count(text) == 1


def test_failed_terminal_does_not_erase_previous_reply():
    view, lines = display()
    view(message(2, text='first reply'))
    view(event('run_finished', 3, status='failed', manual_review_required=True,
               failure=SimpleNamespace(error_type='WriteOutcomeUnknown')))
    assert 'first reply' in lines
    assert '工具副作用无法确认' in '\n'.join(lines)


def test_missing_message_events_fallback_and_no_terminal_are_explicit():
    view, lines = display()
    view(event('message_snapshot_unavailable', 2))
    view.finish_result({'messages': [HumanMessage(content='q', id='h'), AIMessage(content='fallback')]},
                       input_message_id='h')
    out = '\n'.join(lines)
    assert 'fallback' in out and '观察不完整' in out and '未观察到终局事件' in out
    assert '本次运行完成' not in out


def test_result_with_missing_boundary_does_not_print_old_history():
    view, lines = display()
    view.finish_result({'messages': [AIMessage(content='PRIVATE_OLD', id='old')]}, input_message_id='absent')
    assert 'PRIVATE_OLD' not in '\n'.join(lines)
    assert '观察不完整' in '\n'.join(lines)


def test_context_outcomes_hide_summary_and_control_phases_remain_distinct():
    view, lines = display()
    for seq, status in enumerate(['applied', 'skipped', 'fallback', 'failed', 'unconfirmed'], 2):
        view(event('compaction_finished', seq, status=status, reason='no_safe_cut'))
    view(event('control_accepted', 8, kind='steering'))
    view(event('control_delivered', 9, kind='steering'))
    view(event('control_discarded', 10, kind='follow_up'))
    view(event('control_outcome_unknown', 11, kind='steering'))
    out = '\n'.join(lines)
    assert view.control_receipt_count('steering') == 1
    assert 'Steering 已排队' in out and 'Steering 已加入' in out
    assert '未执行' in out and '未确认' in out and '原上下文' in out


def test_other_session_and_run_are_ignored_and_output_blocks_are_serialized():
    view, lines = display()
    other = message(2, text='PRIVATE_OTHER')
    other.session_id = 'other'
    view(other)
    other.session_id = 'session'
    other.run_id = 'other'
    view(other)
    gate = Barrier(3)
    def write(text):
        gate.wait(timeout=3)
        view.write(text)
    workers = [Thread(target=write, args=('one\ntwo',)), Thread(target=write, args=('three\nfour',))]
    for worker in workers:
        worker.start()
    gate.wait(timeout=3)
    for worker in workers:
        worker.join(timeout=3)
        assert not worker.is_alive()
    assert 'PRIVATE_' not in '\n'.join(lines)
    assert 'one\ntwo' in lines and 'three\nfour' in lines


def test_display_ledger_does_not_retain_message_bodies():
    view, lines = display()
    view(message(2, text='PRIVATE_REPLY_BODY'))
    assert 'PRIVATE_REPLY_BODY' not in repr(view._texts)
    assert 'PRIVATE_REPLY_BODY' not in repr(view._messages)


def test_non_text_notice_is_not_repeated_by_result_fallback():
    view, lines = display()
    content = [{'type': 'text', 'text': 'caption'}, {'type': 'image', 'url': 'synthetic'}]
    view(message(2, row={'content': content}))
    view.finish_result({'messages': [HumanMessage(content='q', id='h'),
                                     AIMessage(content=content, id='a')]}, input_message_id='h')
    assert '\n'.join(lines).count('非文本内容') == 1


def test_registered_control_unknown_event_has_an_explicit_display():
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData
    from naiyue_test_agent.agent_runtime.control_events import ControlData
    lines = []
    view = CliEventDisplay('session', writer=lines.append)
    publisher = RunEventPublisher('session', 'run', (view,))
    publisher.publish('run_started', RunStartedData('message'))
    publisher.publish('control_accepted', ControlData('c', 'steering'))
    publisher.publish('control_delivery_started', ControlData('c', 'steering', boundary='queue'))
    publisher.publish('control_outcome_unknown', ControlData('c', 'steering',
                      boundary='message_state', reason='message_state_unconfirmed'))
    assert '未确认交付或生效' in '\n'.join(lines)
