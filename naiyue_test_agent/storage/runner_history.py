"""Export actual archive versions and registered child relationships in the finish transaction."""
from naiyue_test_agent.contracts.cloud_history import message_frames
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def append_messages(journal,c,session_id,run_id,command_id,updates):
    for update in updates:
        scope=(session_id,update.thread_id,update.checkpoint_ns)
        if update.agent_path=='root':
            thread=dict(thread_id=update.thread_id,agent_path='root',role='root',agent_id=None,
                parent_run_id=None,parent_tool_call_id=None,parent_reply_id=None)
        else:
            child=c.execute('''SELECT agent_id,agent_path,last_parent_run_id,last_parent_tool_call_id,last_parent_reply_id
                FROM naiyue_subagent_instances WHERE session_id=? AND child_thread_id=?''',(session_id,update.thread_id)).fetchone()
            if not child or child['agent_path']!=update.agent_path:raise CloudError('runner_child_relationship_missing')
            thread=dict(thread_id=update.thread_id,agent_path=child['agent_path'],role='child',agent_id=child['agent_id'],
                parent_run_id=child['last_parent_run_id'],parent_tool_call_id=child['last_parent_tool_call_id'],
                parent_reply_id=child['last_parent_reply_id'])
        position=c.execute('''SELECT COALESCE(MAX(sequence),0) FROM naiyue_chat_messages
            WHERE session_id=? AND thread_id=? AND checkpoint_ns=?''',scope).fetchone()[0]
        boundaries={b['input_message_id']:b for b in update.boundaries}
        for message in update.messages:
            # Internal system instructions are not public conversation messages.
            if message['message_type']=='system':continue
            saved=c.execute('''SELECT body_hash,parent_message_id,sequence,revision FROM naiyue_chat_messages
                WHERE session_id=? AND thread_id=? AND checkpoint_ns=? AND message_id=?''',(*scope,message['message_id'])).fetchone()
            if saved and saved['body_hash']==message['body_hash'] and saved['parent_message_id']==message.get('parent_message_id'):continue
            if not saved:position+=1
            payload=dict(record='message',version=2,command_id=command_id,thread_id=update.thread_id,
                thread=thread,checkpoint_id=update.checkpoint_id,boundary=boundaries.get(message['message_id']),
                message=message|dict(position=saved['sequence'] if saved else position,revision=saved['revision']+1 if saved else 1))
            for part in message_frames(payload,frame_bytes=journal.bootstrap.max_frame_bytes,limits=journal.bootstrap.history_limits):
                journal._append(c,run_id,part)
