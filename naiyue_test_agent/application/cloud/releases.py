"""Owner-authorized ready/active versions; never builds or opens task databases."""
import time
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_versions import OWNER_PROTOCOL,RUNNER_PROTOCOL,STATE_SCHEMA
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CommandReceipt,TemplateReceipt
from naiyue_test_agent.contracts.cloud_releases import CloudRelease,RegisterRelease,ActivateRelease,MaintenanceCommand
from naiyue_test_agent.storage.cloud.releases import ReleaseRepository,meta,put


class CloudReleases:
    def __init__(self,runtime):
        self.runtime=runtime
        self.repository=ReleaseRepository(runtime.database,runtime.settings.max_receipts)
        from naiyue_test_agent.storage.cloud.upgrades import UpgradeRepository
        self.upgrades=UpgradeRepository(runtime.database,runtime.settings.templates,runtime.settings.max_receipts,runtime.instance_id)

    def _command(self,request,operation,action):
        fingerprint=digest(dict(operation=operation,request=request.model_dump()))
        def run(db):
            now=time.time();result=action(db)
            return CommandReceipt(command_id=request.command_id,resource_id=self.runtime.instance_id,
                request_digest=fingerprint,operation=operation,status='succeeded',http_status=200,
                result=result,created_at=now,updated_at=now)
        return self.repository.transact(request.command_id,fingerprint,run)

    def _supported(self,release,t):
        if (release.coordinator_protocol!=OWNER_PROTOCOL or release.runner_protocol!=RUNNER_PROTOCOL or release.state_schema!=STATE_SCHEMA
                or release.min_connector_protocol>OWNER_PROTOCOL):raise CloudError('release_protocol_incompatible')
        from naiyue_test_agent.contracts.cloud_capabilities import owner_capabilities
        lifecycle=getattr(self.runtime,'lifecycle',None)
        if set(release.required_capabilities)-set(owner_capabilities(lifecycle is not None or self.runtime.settings.backend is not None)):
            raise CloudError('release_protocol_incompatible')
        from packaging.version import Version
        from naiyue_test_agent import __version__
        if release.min_coordinator_version is not None and Version(__version__)<Version(release.min_coordinator_version):
            raise CloudError('release_protocol_incompatible')
        allowed=self.runtime.settings.templates.get(t.template_id)
        if (allowed is None or t.runner_release_id!=release.release_id or t.base_image!=release.base_image
                or t.wheel_sha256 not in [a.sha256 for p,a in release.wheels.items() if p.startswith('linux-')]
                or t.kit_version!=release.kit_version or t.kit_wheel_sha256!=release.kit.sha256
                or (t.uid,t.gid,t.backend_id)!=(allowed.uid,allowed.gid,allowed.backend_id)
                or self.runtime.settings.backend is not None and t.backend_id!=self.runtime.settings.backend.backend_id
                or lifecycle is not None and t.backend_id!=lifecycle.backend.backend_id):
            raise CloudError('release_template_mismatch')

    def validate_active(self):
        for record in self.repository.active_records():
            self._supported(CloudRelease.model_validate(record['release']),TemplateReceipt.model_validate(record['template']))

    def validate_template(self,template):
        template=TemplateReceipt.model_validate(template)
        record=self.repository.read('release:'+template.runner_release_id+':'+template.template_id)
        # Bootstrap pins predate the dynamic registry and keep their original
        # trusted snapshot even when the configured default changes.
        if record is None:
            if self.repository.read('release-retired:'+template.runner_release_id+':'+template.template_id):raise CloudError('release_not_ready')
            return
        if TemplateReceipt.model_validate(record['template']).fingerprint!=template.fingerprint:raise CloudError('release_identity_conflict')
        self._supported(CloudRelease.model_validate(record['release']),template)

    def register(self,value):
        request=RegisterRelease.model_validate(value);release,t=request.release,request.template
        self._supported(release,t)
        def run(db):
            key='release:'+release.release_id+':'+t.template_id
            record=dict(release=release.model_dump(),template=t.model_dump(),phase='ready')
            original=meta(db,'release-manifest:'+release.release_id)
            if original is not None and CloudRelease.model_validate(original)!=release:raise CloudError('release_identity_conflict')
            put(db,'release-manifest:'+release.release_id,release.model_dump())
            previous=meta(db,key)
            if previous is not None:
                if (CloudRelease.model_validate(previous['release'])!=release or TemplateReceipt.model_validate(previous['template']).fingerprint!=t.fingerprint):
                    raise CloudError('release_identity_conflict')
                return previous
            put(db,'release-retired:'+release.release_id+':'+t.template_id,False)
            put(db,key,record);return record
        return self._command(request,'release:register',run)

    def get(self,release_id,template_id):
        value=self.repository.read('release:'+release_id+':'+template_id)
        if value is None:raise CloudError('release_not_ready',404)
        return value

    def active(self,template_id):return self.repository.active(template_id)

    def activate(self,value):
        request=ActivateRelease.model_validate(value)
        def run(db):
            record=meta(db,'release:'+request.release_id+':'+request.template_id)
            if record is None:raise CloudError('release_not_ready')
            self._supported(CloudRelease.model_validate(record['release']),TemplateReceipt.model_validate(record['template']))
            key='release-active:'+request.template_id;old=meta(db,key,dict(revision=0))
            if old['revision']!=request.expected_revision:raise CloudError('release_revision_conflict')
            selected=dict(release_id=request.release_id,template_id=request.template_id,revision=old['revision']+1)
            put(db,key,selected);return selected
        return self._command(request,'release:activate',run)

    def select(self,template_id):
        active=self.active(template_id)
        if active['release_id'] is None:
            t=self.runtime.settings.templates.get(template_id)
            if t is None:raise CloudError('template_unavailable')
            return t
        return TemplateReceipt.model_validate(self.get(active['release_id'],template_id)['template'])

    def maintenance_status(self):
        with self.runtime.database.transaction() as db:
            enabled=meta(db,'maintenance',False)
            from naiyue_test_agent.storage.cloud.maintenance import blockers
            waiting=blockers(db)
        return dict(maintenance=('draining' if waiting else 'ready') if enabled else 'off',blockers=waiting)

    def maintenance(self,value):
        request=MaintenanceCommand.model_validate(value)
        def run(db):
            put(db,'maintenance',request.action=='enter')
            return dict(enabled=request.action=='enter')
        self._command(request,'release:maintenance',run)
        return self.maintenance_status()

    def enter_io(self,*,continuation=None):
        return self.repository.begin_io(continuation)

    def leave_io(self,ident):
        self.repository.end_io(ident)
