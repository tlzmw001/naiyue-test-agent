"""Resolve saved project bindings for status, history and approval recovery."""
from naiyue_test_agent.execution_roots import ExecutionRootUnavailable, execution_error
import json
import logging
from contextlib import asynccontextmanager
from uuid import UUID,uuid5,NAMESPACE_URL

from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.contracts.git_inputs import GitInputError,input_path
from naiyue_test_agent.contracts.session_environment import SandboxSessionSetup,SandboxProfileSelection,SandboxProfileReference,saved_setup
from naiyue_test_agent.storage.errors import SessionPersistenceError

logger=logging.getLogger(__name__)


def resolve_saved_execution(sessions, row):
    root = sessions.saved_execution_root(row)
    return sessions.resolve_execution_root(root, row['project_id'], row.get('execution_binding'))


def saved_execution_status(sessions, row):
    if row.get('execution_binding')=='sandbox':return None,None
    root = sessions.saved_execution_root(row)
    try:
        root = sessions.resolve_execution_root(root, row['project_id'], row.get('execution_binding'))
        return root, execution_error(root, row['project_id'])
    except ExecutionRootUnavailable as error:
        # The saved path remains an audit/display fact, never an execution fallback.
        return root, error.public()


def check_sandbox(sessions,row):
    setup=saved_setup(row)
    if sessions.environments is None:raise SandboxError('env_not_registered')
    record=sessions.environments._service.validate(setup.binding_id)
    if (record.session_id!=row['session_id'] or record.project_id!=row['project_id']
            or record.input_id!=setup.input_id or record.input_snapshot is None):
        raise SandboxError('env_binding_mismatch')
    if setup.selection is not None:
        try:matches=SandboxProfileSelection.from_record(record)==setup.selection
        except ValueError as error:raise SandboxError('env_binding_mismatch') from error
        if not matches:raise SandboxError('env_binding_mismatch')
    return record


def environment_status(sessions,row):
    if row.get('execution_binding')!='sandbox':
        root,error=saved_execution_status(sessions,row)
        return dict(kind='host',state='unavailable' if error else 'ready')
    setup=saved_setup(row)
    value=dict(kind='sandbox',state='preparing',binding_id=setup.binding_id,input_id=setup.input_id,error=None)
    selection=setup.selection
    if selection is None:
        try:selection=SandboxProfileSelection.from_record(sessions.environments._service.get(setup.binding_id))
        except (SandboxError,ValueError):
            logger.info('session_environment_profile_unavailable')
    if selection is not None:
        value.update(profile_id=selection.profile_id,profile_name=selection.name,profile_revision=selection.revision)
    try:
        record=sessions.environments._inputs.get(row['project_id'],setup.input_id)
        if record.state!='ready':
            return dict(value,state='failed' if record.state=='failed' else 'unavailable',
                error=dict(code=record.error_code or 'git_input_unconfirmed',paths=list(record.error_paths)))
        checked=check_sandbox(sessions,row)
        return dict(value,state='ready',root=checked.environment.root)
    except (GitInputError,SandboxError) as error:
        if error.code=='env_not_registered':
            capacity=sessions.environments._service.capacity_error()
            if capacity:error=SandboxError(capacity)
        return dict(value,state='unavailable',error=dict(code=error.code,paths=list(getattr(error,'paths',()))))


@asynccontextmanager
async def execution_use(sessions,scope,*,stop=False):
    if not stop and scope['execution_binding']=='sandbox':
        setup=SandboxSessionSetup.model_validate(scope['execution_setup'])
        async with sessions.environments.using(setup.binding_id):yield
    else:yield


async def resolve_capabilities(sessions,row):
    from naiyue_test_agent.contracts.environment import PreparedExecution
    from naiyue_test_agent.execution.host_files import HostFileSystem
    from naiyue_test_agent.execution.host_processes import HostProcesses
    if row.get('execution_binding')!='sandbox':
        root=await run_blocking(resolve_saved_execution,sessions,row)
        files=HostFileSystem(root,row['project_id'],session_id=row['session_id']) if root is not None else None
        return PreparedExecution(files.environment if files else None,files,HostProcesses(files) if files else None)
    record=await run_blocking(check_sandbox,sessions,row)
    api=sessions.environments
    # These constructors are pure; the original record has already been checked.
    from naiyue_test_agent.execution.docker_files import DockerFileSystem
    from naiyue_test_agent.execution.docker_processes import DockerProcesses
    facts=await run_blocking(api._service.projects.registry.execution_facts,row['project_id'])
    tasks=await api.tasks(record.binding_id,project_id=row['project_id'],session_id=row['session_id'])
    if sessions.memory_jobs is not None:
        from .task_queries import TaskQueries
        tasks=TaskQueries(tasks,sessions.memory_jobs)
    return PreparedExecution(record.environment,DockerFileSystem(api._service,record),
        DockerProcesses(api._operations,record,api._owner),tasks,facts.revision)


async def create_sandbox_session(sessions,model_id,request_id,include,*,profile_id=None,expected_profile_revision=None):
    if sessions.environments is None or sessions._project is None:
        raise SandboxError('env_project_unavailable')
    requested=None
    if profile_id is not None or expected_profile_revision is not None:
        try:requested=SandboxProfileReference(profile_id=profile_id,revision=expected_profile_revision)
        except ValueError as error:raise SandboxError('env_profile_selection_invalid') from error
    with sessions.environments._operations.accepting():
        project_id=sessions._project.project_id
        await run_blocking(sessions.environments._service._project,project_id)
        key=str(UUID(request_id))
        selected=sorted(input_path(path) for path in include)
        if len(set(selected))!=len(selected):raise GitInputError('git_input_invalid_selection')
        sid=uuid5(NAMESPACE_URL,'nyt:session:'+project_id+':'+key).hex
        setup=SandboxSessionSetup(model_id=model_id,include_untracked=selected,
            input_id=uuid5(NAMESPACE_URL,'nyt:input:'+project_id+':'+key).hex,
            binding_id=uuid5(NAMESPACE_URL,'nyt:environment:'+project_id+':'+key).hex,
            requested_profile=requested)
        async with sessions._persistence.history_lock(sid):
            try:row=await run_blocking(sessions._persistence.storage.sessions.get,sid)
            except SessionPersistenceError as error:
                if str(error)!='session_not_found':raise
                # Existing provider facts cannot be adopted after deletion of their owner.
                try:await sessions.environments.get(setup.binding_id)
                except SandboxError as error:
                    if error.code!='env_not_registered':raise
                else:raise SandboxError('env_binding_mismatch')
                selection=sessions.environments._service.configuration.select(profile_id)
                if requested is not None and requested.revision!=selection.revision:
                    raise SandboxError('env_profile_changed')
                setup=SandboxSessionSetup(**setup.model_dump(exclude={'selection'}),selection=selection)
                await run_blocking(sessions._create_session,model_id,None,session_id=sid,
                    execution_setup=setup.model_dump(mode='json'))
            else:
                if (row['execution_binding']!='sandbox' or saved_setup(row).model_dump(exclude={'selection'})
                        !=setup.model_dump(exclude={'selection'})):
                    raise SandboxError('env_operation_conflict')
            try:await prepare_sandbox_session(sessions,sid)
            except (GitInputError,SandboxError):
                logger.warning('session_environment_not_ready')
        return sid


async def prepare_sandbox_session(sessions,sid):
    row=await run_blocking(sessions._persistence.storage.sessions.get,sid)
    setup=saved_setup(row)
    async with sessions.environments.using(setup.binding_id):return await _prepare_sandbox_session(sessions,sid)


async def _prepare_sandbox_session(sessions,sid):
    row=await run_blocking(sessions._persistence.storage.sessions.get,sid)
    if row['execution_binding']!='sandbox':raise SandboxError('env_binding_mismatch')
    setup=saved_setup(row);api=sessions.environments
    with api._operations.accepting():
        selection=setup.selection
        if selection is None:
            try:original=await api.get(setup.binding_id)
            except SandboxError as error:
                if error.code=='env_not_registered':raise SandboxError('env_legacy_setup_unavailable') from error
                raise
            try:selection=SandboxProfileSelection.from_record(original)
            except ValueError as error:raise SandboxError('env_binding_mismatch') from error
        await api.prepare_input(row['project_id'],input_id=setup.input_id,
            include_untracked=setup.include_untracked,git_settings=selection.settings.git)
        record=await run_blocking(api._service.create,row['project_id'],sid,
            binding_id=setup.binding_id,selection=selection)
        if record.lifecycle!='running':raise SandboxError('env_operation_unknown')
        await api.install_input(setup.binding_id,setup.input_id)
        return await run_blocking(environment_status,sessions,row)


async def check_environment_deletion(sessions,sid):
    row=await run_blocking(sessions._persistence.storage.sessions.get,sid)
    if row['execution_binding']!='sandbox':return
    setup=saved_setup(row)
    try:record=await sessions.environments.get(setup.binding_id)
    except SandboxError as error:
        if error.code!='env_not_registered':raise
        return
    if record.lifecycle!='destroyed':raise SessionPersistenceError('session_environment_in_use')


async def environment_action(sessions,sid,action):
    if action not in ('start','stop','destroy','recover'):raise SandboxError('env_operation_invalid')
    row=await run_blocking(sessions._persistence.storage.sessions.get,sid)
    if row['execution_binding']!='sandbox':raise SandboxError('env_binding_mismatch')
    setup=saved_setup(row);api=sessions.environments
    record=await api.get(setup.binding_id)
    if record.project_id!=row['project_id'] or record.session_id!=sid:raise SandboxError('env_binding_mismatch')
    await getattr(api,action)(setup.binding_id)
    return await run_blocking(environment_status,sessions,row)
