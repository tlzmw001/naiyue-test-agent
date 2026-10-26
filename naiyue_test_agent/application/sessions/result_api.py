"""Resolve result ownership from saved sessions, never caller-supplied bindings."""
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.session_environment import saved_setup
from naiyue_test_agent.contracts.sandbox import SandboxError


async def result_call(api,sid,action,*args,**kwargs):
    with api._owner.operation():
        row=await run_blocking(api._metadata.get,sid)
        if row.get('execution_binding')!='sandbox':raise SandboxError('env_binding_mismatch')
        setup=saved_setup(row);environments=api._sessions.environments
        env=await environments.get(setup.binding_id)
        if env.project_id!=row['project_id'] or env.session_id!=sid or env.input_id!=setup.input_id:
            raise SandboxError('env_binding_mismatch')
        if action!='capture_result':return await getattr(environments,action)(setup.binding_id,*args,**kwargs)
        async with api._persistence.history_lock(sid):
            with api._sessions.manage_environment(sid):
                return await environments.capture_result(setup.binding_id,**kwargs)
