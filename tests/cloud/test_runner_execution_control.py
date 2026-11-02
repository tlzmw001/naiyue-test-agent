"""Fixed-task root/child, stop settlement and explicit checkpoint recovery."""
import asyncio
import json
import shlex
import sys
from uuid import uuid4

from langchain_core.messages import AIMessage
import pytest

from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel, call
from test_subagent_execution import ChildModel
from test_runner_kernel import bootstrap, request


def action(kind,*,run=None,checkpoint_id=None):
    from naiyue_test_agent.contracts.cloud_runner import RunnerActionCommand
    return RunnerActionCommand(command_id=str(uuid4()),kind=kind,run_id=run.run_id if run else None,
        target_command_id=run.command_id if run else None,checkpoint_id=checkpoint_id)


def directories(tmp_path):
    root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
    return root,state


def test_configured_child_uses_task_tools_and_retains_history_across_root_runs(tmp_path):
    async def scenario():
        b=bootstrap();b=type(b).model_validate(b.model_dump()|{'subagents':[
            dict(name='tester',description='Verify',system_prompt='Test role',tool_names=['write'],model_id='B')]})
        root,state=directories(tmp_path)
        child=ChildModel(replies=[call('write',{'path':'child.txt','content':'verified'},'cw'),AIMessage(content='report')])
        model=StreamingToolModel(replies=[call('subagent',{'agent':'tester','task':'verify'},'delegate'),AIMessage(content='root done')])
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model,'B':child})) as kernel:
            result=await kernel.execute(request());assert result['status']=='completed'
            assert (root/'child.txt').read_text()=='verified'
            child_message=next(m for m in model.seen[-1] if m.type=='tool' and m.name=='subagent')
            envelope=json.loads(child_message.content)
            assert envelope['status']=='completed' and envelope['report']=='report'
            from naiyue_test_agent.storage.queries import subagent_references
            with kernel.store.database.transaction() as connection:
                references=subagent_references(connection,b.task_id)
            assert len(references)==1
            threads={row['child_thread_id'] for row in references.values()}
            records=kernel.journal.pending()
            assert any(e.payload.get('thread_id') in threads for e in records)
            assert records[-1].payload['record']=='run_finished'
            child.replies.append(AIMessage(content='second report'))
            model.replies.extend([call('subagent',{'agent':'tester','task':'follow up',
                'agent_id':envelope['agent_id']},'delegate-again'),AIMessage(content='second root done')])
            assert (await kernel.execute(request()))['status']=='completed'
            continuation=next(m for m in reversed(model.seen[-1]) if m.type=='tool' and m.name=='subagent')
            assert json.loads(continuation.content)['agent_id']==envelope['agent_id']
            assert json.loads(continuation.content)['report']=='second report'
            assert len(child.seen)==3
            assert any(m.type=='human' and m.content=='verify' for m in child.seen[-1])
    asyncio.run(scenario())


def test_stop_blocked_root_is_durable_idempotent_and_does_not_stop_next_run(tmp_path):
    async def scenario():
        root,state=directories(tmp_path);entered=asyncio.Event();release=asyncio.Event()
        model=StreamingToolModel(replies=[AIMessage(content='new answer')],entered=entered,release=release)
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            cmd=request();await kernel.accept(cmd);await asyncio.wait_for(entered.wait(),5)
            stop=action('stop',run=cmd)
            saved=await asyncio.wait_for(kernel.actions.execute(stop),5)
            assert saved['status']=='completed' and saved['phase']=='ready' and saved['quiescent']
            assert kernel.journal.lookup(cmd.command_id)['status']=='stopped'
            assert kernel.journal.pending()[-1].payload['record']=='control_finished'
            release.set()
            result=await kernel.execute(request())
            assert result['status']=='completed'
            assert await kernel.actions.execute(stop)==saved
            assert len(model.seen)==2
    asyncio.run(scenario())


def test_background_survives_root_but_explicit_stop_collects_and_allows_more_work(tmp_path):
    async def scenario():
        root,state=directories(tmp_path)
        script='import time;time.sleep(120)'
        command=shlex.quote(sys.executable)+' -c '+shlex.quote(script)
        model=StreamingToolModel(replies=[call('bash',{'command':command,'background':True}),AIMessage(content='started'),AIMessage(content='again')])
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            assert (await kernel.execute(request()))['status']=='completed'
            assert not kernel.session_quiet()
            tasks=await kernel.sessions.background_tasks.list(session_id=kernel.bootstrap.task_id)
            assert tasks['active_count']==1
            saved=await asyncio.wait_for(kernel.actions.execute(action('stop')),8)
            assert saved['status']=='completed' and saved['quiescent']
            tasks=await kernel.sessions.background_tasks.list(session_id=kernel.bootstrap.task_id)
            assert tasks['active_count']==0
            assert all(t['output_complete'] and t['cleanup_confirmed'] for t in tasks['items'])
            assert (await kernel.execute(request()))['status']=='completed'
    asyncio.run(scenario())


def test_stop_wrong_run_does_not_cancel_current_execution(tmp_path):
    async def scenario():
        root,state=directories(tmp_path);entered=asyncio.Event();release=asyncio.Event()
        model=StreamingToolModel(replies=[AIMessage(content='done')],entered=entered,release=release)
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            cmd=request();await kernel.accept(cmd);await asyncio.wait_for(entered.wait(),5)
            with pytest.raises(CloudError,match='run_identity_mismatch'):
                await kernel.actions.execute(action('stop',run=request()))
            assert not kernel.active_task.done()
            release.set();assert (await kernel.execute(cmd))['status']=='completed'
    asyncio.run(scenario())


class FailsAfterTool(StreamingToolModel):
    async def _next(self,messages):
        if not self.replies:raise RuntimeError('injected model failure after tool')
        return await super()._next(messages)


def test_failed_graph_recovery_never_replays_shell_or_mutates_source_checkpoint(tmp_path):
    async def scenario():
        root,state=directories(tmp_path);cmd=request()
        model=FailsAfterTool(replies=[call('bash',{'command':'printf once >> effects.txt'})])
        catalog=ModelCatalog('A',{'A':model})
        async with open_runner(bootstrap(),root,state,catalog=catalog) as kernel:
            with pytest.raises(RuntimeError,match='injected model failure'):await kernel.execute(cmd)
            info=await kernel.inspect_recovery()
            assert info.kind=='interrupted_execution' and info.checkpoint_id
            source=await kernel.agent.checkpointer.aget_tuple({'configurable':{'thread_id':kernel.sessions.require(kernel.bootstrap.task_id).get_thread_id(),'checkpoint_id':info.checkpoint_id}})
            recover=action('recover',run=cmd,checkpoint_id=info.checkpoint_id)
            done=await kernel.actions.execute(recover)
            assert done['status']=='completed' and done['phase']=='ready'
            assert (root/'effects.txt').read_text()=='once'
            after=await kernel.agent.checkpointer.aget_tuple(source.config)
            assert source.checkpoint==after.checkpoint and source.pending_writes==after.pending_writes
            assert await kernel.actions.execute(recover)==done
            assert kernel.journal.lookup(cmd.command_id)['status']=='recovered'
            assert kernel.journal.pending()[-1].payload['record']=='control_finished'
            model.replies.append(AIMessage(content='new input only'))
            assert (await kernel.execute(request()))['status']=='completed'
            assert (root/'effects.txt').read_text()=='once'
    asyncio.run(scenario())


def test_stale_recovery_checkpoint_is_rejected_before_mutation(tmp_path):
    async def scenario():
        root,state=directories(tmp_path);cmd=request()
        model=FailsAfterTool(replies=[call('bash',{'command':'printf once > effect.txt'})])
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            with pytest.raises(RuntimeError):await kernel.execute(cmd)
            before=await kernel.inspect_recovery()
            with pytest.raises(CloudError,match='checkpoint_mismatch'):
                await kernel.actions.execute(action('recover',run=cmd,checkpoint_id='stale'))
            assert (await kernel.inspect_recovery()).checkpoint_id==before.checkpoint_id
            assert (root/'effect.txt').read_text()=='once'
    asyncio.run(scenario())


@pytest.mark.parametrize('boundary',['checkpoint','required_save'])
def test_same_recovery_command_resumes_after_partial_recovery_without_tool_replay(tmp_path,monkeypatch,boundary):
    async def scenario():
        root,state=directories(tmp_path);cmd=request()
        model=FailsAfterTool(replies=[call('bash',{'command':'printf once >> effect.txt'})])
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            with pytest.raises(RuntimeError):await kernel.execute(cmd)
            info=await kernel.inspect_recovery();recover=action('recover',run=cmd,checkpoint_id=info.checkpoint_id)
            original_assemble=kernel.assemble;fired=False
            async def assemble():
                await original_assemble()
                original=kernel.agent.aupdate_state
                async def update(config,values,*,as_node):
                    nonlocal fired
                    result=await original(config,values,as_node=as_node)
                    if not fired:
                        fired=True;raise OSError('injected recovery checkpoint acknowledgement loss')
                    return result
                if boundary=='checkpoint':monkeypatch.setattr(kernel.agent,'aupdate_state',update)
            monkeypatch.setattr(kernel,'assemble',assemble)
            original_finish=kernel.journal._finish_control
            def finish(*args,**kwargs):
                nonlocal fired
                result=original_finish(*args,**kwargs)
                if boundary=='required_save' and not fired:
                    fired=True;raise OSError('injected recovery transaction failure')
                return result
            monkeypatch.setattr(kernel.journal,'_finish_control',finish)
            with pytest.raises(Exception):await kernel.actions.execute(recover)
            assert kernel.journal.control_lookup(recover.command_id)['status']=='unknown'
            assert kernel.phase=='blocked' and (root/'effect.txt').read_text()=='once'
            assert not any(e.payload.get('record')=='control_finished' for e in kernel.journal.pending())
            done=await kernel.actions.execute(recover)
            assert done['status']=='completed' and kernel.phase=='ready'
            assert (root/'effect.txt').read_text()=='once'
            assert sum(e.payload.get('record')=='control_finished' for e in kernel.journal.pending())==1
    asyncio.run(scenario())


def test_unknown_background_requires_physical_new_generation_before_acknowledgement(tmp_path):
    async def scenario():
        import sqlite3
        root,state=directories(tmp_path);b=bootstrap();cmd=request()
        model=StreamingToolModel(replies=[AIMessage(content='saved')])
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            await kernel.execute(cmd)
        # Fault fixture represents an unclean prior process, not an actual Docker kill.
        with sqlite3.connect(state/'session.sqlite') as c:
            c.execute('UPDATE nyt_runner_meta SET value=? WHERE key=?',(json.dumps(dict(dirty=True,generation=b.generation)),'activity'))
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            assert kernel.phase=='blocked' and not kernel.session_quiet()
            info=await kernel.inspect_recovery();assert info.kind=='background_unknown'
            with pytest.raises(CloudError,match='old_environment_not_fenced'):
                await kernel.actions.execute(action('recover',run=cmd,checkpoint_id=info.checkpoint_id))
        b=b.model_copy(update={'generation':2,'boot_id':str(uuid4())})
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            info=await kernel.inspect_recovery()
            done=await kernel.actions.execute(action('recover',run=cmd,checkpoint_id=info.checkpoint_id))
            assert done['status']=='completed' and kernel.phase=='ready' and len(model.seen)==1
            assert any(e.payload['record']=='background_unconfirmed' for e in kernel.journal.pending())
    asyncio.run(scenario())


def test_new_generation_recovery_supersedes_unknown_old_stop_without_replaying_it(tmp_path):
    async def scenario():
        root,state=directories(tmp_path);b=bootstrap();cmd=request()
        model=FailsAfterTool(replies=[call('bash',{'command':'printf once >> effect.txt'})])
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            with pytest.raises(RuntimeError):await kernel.execute(cmd)
            stop=action('stop',run=cmd)
            kernel.journal.admit_control(stop);kernel.journal.unknown_control(stop.command_id)
        b=b.model_copy(update={'generation':2,'boot_id':str(uuid4())})
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            info=await kernel.inspect_recovery()
            assert (await kernel.actions.execute(action('recover',run=cmd,checkpoint_id=info.checkpoint_id)))['status']=='completed'
            assert kernel.journal.control_lookup(stop.command_id)['status']=='failed'
            assert (root/'effect.txt').read_text()=='once'
    asyncio.run(scenario())


def test_rejected_control_is_a_saved_failure_and_does_not_poison_valid_recovery(tmp_path):
    async def scenario():
        root,state=directories(tmp_path);cmd=request()
        model=FailsAfterTool(replies=[call('bash',{'command':'printf once > effect.txt'})])
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            with pytest.raises(RuntimeError):await kernel.execute(cmd)
            bad=action('recover',run=cmd,checkpoint_id='stale')
            rejected=await kernel.actions.reject(bad,CloudError('checkpoint_mismatch'))
            assert rejected['status']=='failed' and rejected['error_code']=='checkpoint_mismatch'
            assert kernel.journal.pending()[-1].payload['record']=='control_finished'
            info=await kernel.inspect_recovery()
            good=await kernel.actions.execute(action('recover',run=cmd,checkpoint_id=info.checkpoint_id))
            assert good['status']=='completed' and (root/'effect.txt').read_text()=='once'
    asyncio.run(scenario())


def test_run_recovery_cannot_clear_an_unconfirmed_artifact(tmp_path):
    async def scenario():
        from test_artifact_capture import command
        root,state=directories(tmp_path);cmd=request();(root/'report').write_text('result')
        model=StreamingToolModel(replies=[AIMessage(content='done')])
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            await kernel.execute(cmd)
            captured=await kernel.artifacts.capture(command('report'))
            assert captured.status=='captured'
            info=await kernel.inspect_recovery()
            with pytest.raises(CloudError,match='artifact_busy'):
                await kernel.actions.execute(action('recover',run=cmd,checkpoint_id=info.checkpoint_id))
            assert kernel.artifacts.journal.get(captured.command.command_id).status=='captured'
    asyncio.run(scenario())


def test_required_activity_save_failure_keeps_original_model_error(tmp_path,monkeypatch):
    async def scenario():
        root,state=directories(tmp_path)
        model=FailsAfterTool(replies=[call('bash',{'command':'printf once > effect.txt'})])
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            original=kernel.journal.mark_activity
            def fail(dirty):raise OSError('injected required activity save failure')
            monkeypatch.setattr(kernel.journal,'mark_activity',fail)
            with pytest.raises(RuntimeError,match='injected model failure'):await kernel.execute(request())
            assert kernel.phase=='blocked' and (root/'effect.txt').read_text()=='once'
            monkeypatch.setattr(kernel.journal,'mark_activity',original)
    asyncio.run(scenario())


def test_root_stop_joins_a_blocked_child_and_preserves_its_saved_identity(tmp_path):
    async def scenario():
        from naiyue_test_agent.storage.queries import subagent_references
        root,state=directories(tmp_path);entered=asyncio.Event();release=asyncio.Event()
        child=ChildModel(replies=[AIMessage(content='unused')])
        async def hold(messages):entered.set();await release.wait()
        child.on_request=hold
        b=bootstrap();b=type(b).model_validate(b.model_dump()|{'subagents':[
            dict(name='tester',description='Verify',system_prompt='Child',tool_names=['read'],model_id='B')]})
        model=StreamingToolModel(replies=[call('subagent',{'agent':'tester','task':'wait'}),AIMessage(content='unused')])
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model,'B':child})) as kernel:
            cmd=request();await kernel.accept(cmd);await asyncio.wait_for(entered.wait(),5)
            done=await asyncio.wait_for(kernel.actions.execute(action('stop',run=cmd)),5)
            assert done['status']=='completed' and done['quiescent'] and kernel.phase=='ready'
            assert kernel.journal.lookup(cmd.command_id)['status']=='stopped'
            assert not kernel.sessions.require(b.task_id).is_busy()
            with kernel.store.database.transaction() as c:records=subagent_references(c,b.task_id)
            assert len(records)==1
            assert len(child.services)==1 and len(model.seen)==1
    asyncio.run(scenario())


def test_admitted_but_never_started_graph_can_be_explicitly_reconciled_without_fake_checkpoint(tmp_path):
    async def scenario():
        root,state=directories(tmp_path);b=bootstrap();cmd=request()
        model=StreamingToolModel(replies=[AIMessage(content='only new input')])
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            kernel.journal.admit(cmd);kernel.journal.unknown(cmd.command_id)
        b=b.model_copy(update={'generation':2,'boot_id':str(uuid4())})
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            info=await kernel.inspect_recovery()
            assert info.kind=='ready' and info.reason=='never_run' and info.checkpoint_id is None
            assert (await kernel.actions.execute(action('recover',run=cmd)))['status']=='completed'
            assert model.seen==[] and kernel.journal.lookup(cmd.command_id)['status']=='recovered'
            assert (await kernel.execute(request()))['status']=='completed' and len(model.seen)==1
    asyncio.run(scenario())


def test_committed_recovery_can_finish_actor_install_with_same_id_without_second_history_commit(tmp_path,monkeypatch):
    async def scenario():
        root,state=directories(tmp_path);cmd=request()
        model=FailsAfterTool(replies=[call('bash',{'command':'printf once >> effect.txt'})])
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as kernel:
            with pytest.raises(RuntimeError):await kernel.execute(cmd)
            info=await kernel.inspect_recovery();recover=action('recover',run=cmd,checkpoint_id=info.checkpoint_id)
            original_assemble=kernel.assemble;fired=False
            async def assemble():
                await original_assemble()
                install=kernel.persistence._install_session
                def fail_once(*args,**kwargs):
                    nonlocal fired
                    if not fired:
                        fired=True;raise OSError('injected actor installation failure after commit')
                    return install(*args,**kwargs)
                monkeypatch.setattr(kernel.persistence,'_install_session',fail_once)
            monkeypatch.setattr(kernel,'assemble',assemble)
            with pytest.raises(Exception):await kernel.actions.execute(recover)
            committed=kernel.journal.control_lookup(recover.command_id)
            assert committed['status']=='completed' and kernel.phase=='blocked'
            assert not kernel.sessions.has_session(kernel.bootstrap.task_id)
            assert sum(e.payload.get('record')=='control_finished' for e in kernel.journal.pending())==1
            done=await kernel.actions.execute(recover)
            assert done==committed and kernel.phase=='ready'
            assert kernel.sessions.has_session(kernel.bootstrap.task_id)
            assert sum(e.payload.get('record')=='control_finished' for e in kernel.journal.pending())==1
            model.replies.append(AIMessage(content='new input'))
            assert (await kernel.execute(request()))['status']=='completed'
            assert (root/'effect.txt').read_text()=='once'
    asyncio.run(scenario())
