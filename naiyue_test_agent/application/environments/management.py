"""Explicit interrupt closes one binding's admission before remote management."""
from naiyue_test_agent.agent_runtime.async_utils import run_blocking, await_settled
from naiyue_test_agent.contracts.sandbox import SandboxError


async def manage_binding(api,binding,action,*,interrupt):
    async def manage():
        with api._owner.operation(),api._operations.accepting(),api._service.activity.manage(binding,interrupt=interrupt):
            try:row=await run_blocking(api._service.get,binding)
            except SandboxError as error:
                pending=next((item for item in api._preparations.active() if item['binding_id']==binding),None)
                if error.code!='env_not_registered' or not interrupt or pending is None:raise
                row=None
            if interrupt:
                if row is not None:
                    host=api._hosts._hosts.get(row.project_id)
                    if host is not None and host.sessions.is_loaded(row.session_id):
                        actor=host.sessions._sessions.require(row.session_id)
                        if actor.is_busy():actor.controls.request_scope_stop()
                await api._preparations.stop(binding)
                try:row=await run_blocking(api._service.get,binding)
                except SandboxError as error:
                    if error.code!='env_not_registered':raise
                    return dict(binding_id=binding,lifecycle='not_created')
            row=await run_blocking(api._service._perform_action,binding,action,shutdown=interrupt)
            return dict(binding_id=row.binding_id,lifecycle=row.lifecycle)
    return await await_settled(manage())


def management_view(api):
    rows=api._lifecycle.rows()
    pending=api._preparations.active()
    names={p.project_id:p.name for p in api._service.projects.registry.list()} if rows or pending else {}
    items=[dict(binding_id=r.binding_id,project_id=r.project_id,session_id=r.session_id,
        project_name=names.get(r.project_id),profile_name=r.profile_name,lifecycle=r.lifecycle,
        last_used_at=r.last_used_at,error_code=r.error_code,
        busy=api._service.activity.busy(r.binding_id),unsettled=api._operations.unsettled(r.binding_id),
        preparation=api._preparations.view(r.binding_id)) for r in rows if r.lifecycle!='destroyed']
    registered={r.binding_id for r in rows}
    for job in pending:
        if job['binding_id'] not in registered:
            items.append(dict(**job,project_name=names.get(job['project_id']),profile_name=None,
                lifecycle='preparing',last_used_at=None,error_code=None,busy=True,unsettled=False))
    return dict(policy=api._service.configuration.lifecycle.model_dump(),
        usage=api._service.records().usage() if rows else dict(running=0,retained=0),items=items)
