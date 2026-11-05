"""Network actions and identity checks; no SQL or locks across awaits."""
from naiyue_test_agent.contracts.cloud_lifecycle import BackendFailure
from naiyue_test_agent.contracts.cloud_lifecycle import MountSpec


def verify_facts(operation, facts):
    if (facts.image_id != operation.template.image_id or facts.metadata != operation.metadata
            or facts.expires_at is not None or facts.state.lower() != 'running'):
        raise BackendFailure('sandbox_facts_mismatch')


async def start(service, receipt, operation, *, reconcile=False):
    # Also applied after restart, before adopting/preflighting an old start.
    task = await service.local(lambda: service.runtime.registry.get_task(operation.task_id))
    package = await service.local(lambda: service.runtime.inputs.get_input(task.input_id)) if task.input_id is not None else None
    await service.local(lambda: service.runtime.resources.ensure(
        additional_bytes=0 if task.input_ready or package is None else sum(entry.size for entry in package.manifest.entries)))
    if reconcile and operation.phase != 'reserved':
        matches = await service.backend.find(operation.metadata)
        if len(matches) != 1: raise BackendFailure('sandbox_reconcile_ambiguous')
        facts = matches[0]
    else:
        mounts = await service.local(lambda: service.directories.prepare(
            service.runtime.registry.get_task(operation.task_id), operation.template))
        await service.local(lambda: service.runtime.control.prepare(operation.task_id))
        task = await service.local(lambda: service.runtime.registry.get_task(operation.task_id))
        mounts.append(MountSpec(source=str(service.runtime.control.bootstrap.directory(task)),destination='/run/nyt',read_only=True))
        receipt, operation = await service.dispatch(receipt, operation)
        facts = await service.backend.create(operation, mounts,
            {'cpu': str(service.settings.task_cpu), 'memory': service.settings.task_memory})
    receipt, operation = await service.discovered(receipt, operation, facts.sandbox_id)
    verify_facts(operation, facts)
    await service.backend.preflight(facts.sandbox_id, operation.template)
    task = await service.local(lambda: service.runtime.registry.get_task(operation.task_id))
    await service.backend.install(facts.sandbox_id, task.input_id, service.settings.input_limits)
    return await service.finish(receipt, operation, status='succeeded', sandbox_id=facts.sandbox_id)


async def stop(service, receipt, operation):
    receipt, operation = await service.dispatch(receipt, operation)
    if operation.sandbox_id is not None:
        facts = await service.backend.get(operation.sandbox_id)
        if facts is not None:
            verify_existing(operation, facts)
            await service.backend.kill(operation.sandbox_id)
        if await service.backend.get(operation.sandbox_id) is not None:
            raise BackendFailure('sandbox_stop_unconfirmed')
    return await service.finish(receipt, operation, status='succeeded', sandbox_id=None)


def verify_existing(operation, facts):
    required = {'nyt-instance': operation.instance_id, 'nyt-task': operation.task_id,
        'nyt-generation': str(operation.generation)}
    if facts.image_id != operation.template.image_id or any(facts.metadata.get(k) != v for k, v in required.items()):
        raise BackendFailure('sandbox_facts_mismatch')


async def refresh(service, receipt, operation):
    receipt, operation = await service.dispatch(receipt, operation)
    facts = await service.backend.get(operation.sandbox_id) if operation.sandbox_id else None
    if facts is not None:
        verify_existing(operation, facts)
        if facts.state.lower() != 'running' or facts.expires_at is not None:
            raise BackendFailure('sandbox_state_mismatch')
        task = await service.local(lambda: service.runtime.registry.get_task(operation.task_id))
        package = await service.local(lambda: service.runtime.inputs.get_input(task.input_id)) if task.input_id is not None else None
        await service.local(lambda: service.runtime.resources.ensure(
            additional_bytes=0 if task.input_ready or package is None else sum(entry.size for entry in package.manifest.entries)))
        await service.local(lambda: service.runtime.control.prepare(operation.task_id))
        await service.backend.preflight(facts.sandbox_id, operation.template)
        task = await service.local(lambda: service.runtime.registry.get_task(operation.task_id))
        await service.backend.install(facts.sandbox_id, task.input_id, service.settings.input_limits)
    return await service.finish(receipt, operation, status='succeeded', sandbox_id=facts.sandbox_id if facts else None)
