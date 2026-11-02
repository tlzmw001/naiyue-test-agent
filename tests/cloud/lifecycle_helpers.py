"""Controlled backend and prepared input fixtures; no product fallback backend."""
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_lifecycle import BackendFailure, SandboxFacts, TemplateReceipt
from naiyue_test_agent.contracts.cloud_tasks import CreateProject
from cloud_helpers import settings, task_record


def template(**changes):
    values = dict(template_id='python-tests', runner_release_id='test-release',
        image_id='sha256:' + '1' * 64, base_image='python@sha256:' + '2' * 64,
        wheel_sha256='3' * 64, kit_version='0.6.0',
        kit_wheel_sha256='419559056a1d16b695d9b1e91a7f0d15ddc389996a369207bd3dcf8afada2331',
        uid=os.getuid(), gid=os.getgid(), backend_id='fixture-daemon',
        toolchain_sha256='4' * 64, preflight_sha256='5' * 64)
    values.update(changes)
    return TemplateReceipt(**values)


def config(tmp_path, **changes):
    return settings(tmp_path, templates={'python-tests': template()}, **changes)


def register(runtime):
    receipt = runtime.commands.create_project(CreateProject(command_id=str(uuid4()), name='lifecycle'))
    from naiyue_test_agent.cloud_files.capture import capture
    from naiyue_test_agent.contracts.cloud_inputs import Selection
    with tempfile.TemporaryDirectory(dir=runtime.settings.data_dir.parent) as temporary:
        parent=Path(temporary);source=parent/'source';source.mkdir()
        (source/'fixture.txt').write_text('prepared input fixture')
        captured=capture(source,Selection(paths=['fixture.txt']),parent/'capture',runtime.settings.input_limits)
        created=runtime.inputs.create(dict(command_id=str(uuid4()),purpose='input',input_id=captured.input_id,
            size=captured.size,archive_sha256=captured.archive_sha256,codec=captured.codec))
        transfer=runtime.inputs.transfer(created.resource_id)
        if not transfer.cache_hit:
            shutil.copyfile(captured.directory/'archive',runtime.inputs.staging(transfer.transfer_id)/'archive')
            runtime.inputs.repository.save(transfer,transfer.model_copy(update={'state':'uploaded','offset':transfer.size}))
            runtime.inputs.commit(transfer.transfer_id)
    record = task_record(receipt.resource_id,input_id=captured.input_id)
    runtime.registry.add_task(record)
    return record


def command(record, kind='start', **changes):
    fields = dict(command_id=str(uuid4()), kind=kind, expected_revision=record.revision,
        expected_generation=record.generation)
    fields.update(changes)
    return fields


class Backend:
    backend_id = 'fixture-daemon'

    def __init__(self):
        self.sandboxes, self.calls = {}, []
        self.lose_create_ack = self.lose_stop_ack = self.offline = False
        self.closed = False
        self.mounts = {}

    async def create(self, operation, mounts, resources):
        self.calls.append(('create', operation.command_id))
        facts = SandboxFacts(sandbox_id=str(uuid4()), image_id=operation.template.image_id,
            metadata=operation.metadata, state='running', expires_at=None)
        self.sandboxes[facts.sandbox_id] = facts
        self.mounts[facts.sandbox_id] = {m.destination:Path(m.source) for m in mounts}
        if self.lose_create_ack: raise BackendFailure('backend_unavailable')
        return facts

    async def get(self, sandbox_id):
        if self.offline: raise BackendFailure('backend_unavailable')
        return self.sandboxes.get(sandbox_id)

    async def find(self, metadata):
        if self.offline: raise BackendFailure('backend_unavailable')
        return [s for s in self.sandboxes.values() if s.metadata == metadata]

    async def kill(self, sandbox_id):
        self.calls.append(('kill', sandbox_id))
        if self.offline: raise BackendFailure('backend_unavailable')
        self.sandboxes.pop(sandbox_id, None)
        if self.lose_stop_ack: raise BackendFailure('backend_unavailable')

    async def preflight(self, sandbox_id, template):
        self.calls.append(('preflight', sandbox_id))

    async def close(self):
        self.closed = True

    async def install(self, sandbox_id, input_id, limits):
        from naiyue_test_agent.cloud_runner.input_install import install_input
        mounts=self.mounts[sandbox_id]
        return install_input(mounts['/input'],mounts['/workspace'],mounts['/state'],input_id,limits)
