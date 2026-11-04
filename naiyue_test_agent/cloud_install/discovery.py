"""One bounded channel poll; the existing detached worker owns all update work."""
from uuid import NAMESPACE_URL,uuid5

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .source import load_index,load_release
from .store import UpdateStore
from .worker import _plan,launch


def discover(settings,client,*,settings_file):
    index=load_index(settings);release_id=index.channels.get(settings.channel)
    if release_id is None:raise CloudError('release_channel_unavailable')
    manifest,sha=load_release(settings,release_id,expected_sha256=index.releases[release_id].sha256)
    plan=_plan(settings,manifest,sha,client)
    if not plan['compatible']:return plan|dict(phase='requires_maintenance')
    if plan['active']['release_id']==release_id:return plan|dict(phase='up_to_date')
    ident=str(uuid5(NAMESPACE_URL,':'.join(map(str,('naiyue-release',plan['instance_id'],settings.build.template_id,
        sha,plan['active']['revision'],plan['active'].get('inventory_revision',0),settings.auto_activate)))))
    store=UpdateStore(settings)
    store.create(ident,release_id,activate=settings.auto_activate,manifest_sha256=sha,
        instance_id=plan['instance_id'],expected_revision=plan['active']['revision'])
    return launch(settings_file,ident)
