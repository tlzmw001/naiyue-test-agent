"""Coordinator-owned model policy: parse only, never construct an Agent or provider SDK."""
from dataclasses import dataclass,field
from hashlib import sha256

from naiyue_test_agent.contracts.cloud_models import RunnerModelProfile,validate_model_upstream
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.model_access.configuration import read_model_configuration


@dataclass(frozen=True)
class ModelRoute:
    model_id:str
    model_name:str
    kind:str
    path:str
    upstream:str
    timeout:float
    api_key:str|None=field(repr=False)


@dataclass(frozen=True)
class ModelGrant:
    task_id:str
    generation:int
    profile_id:str
    model_key:str
    route:ModelRoute


class CloudModels:
    def __init__(self,runtime):
        self.runtime=runtime;self.config=runtime.settings.model_proxy
        self.profile=None;self.routes={};self.relay=None
        if self.config is None:return
        parsed=read_model_configuration(self.config.config_path)
        expected={v['provider_id'] for v in parsed.public_configuration['models'].values()
            if parsed.public_configuration['providers'][v['provider_id']]['kind'] in ('openai','anthropic')}
        if set(self.config.upstreams)!=expected:raise ValueError('model_upstream_configuration')
        policy={}
        for model_id,d in parsed.models.items():
            kind=d['kind'];provider=d['provider']
            provider_id=parsed.public_configuration['models'][model_id]['provider_id']
            upstream=validate_model_upstream(self.config.upstreams[provider_id] if kind in ('openai','anthropic') else provider['base_url'])
            path=('v1/messages' if kind=='anthropic' else 'api/chat' if kind=='ollama'
                else 'responses' if d['api']=='responses' else 'chat/completions')
            route=ModelRoute(model_id,d['model_name'],kind,path,upstream,d['request_settings']['timeout'],provider.get('api_key'))
            self.routes[sha256(model_id.encode()).hexdigest()]=route
            policy[model_id]=dict(upstream=upstream,path=path)
        self.profile=RunnerModelProfile(profile_id=digest(dict(configuration=parsed.public_configuration,policy=policy)),
            configuration=parsed.public_configuration)
        from naiyue_test_agent.contracts.cloud_runner import validate_runner_configuration
        validate_runner_configuration(runtime.settings.subagents,runtime.settings.tool_environment,self.profile)

    @property
    def configured(self):return self.profile is not None

    def admit(self,raw,task_id,model_key):
        r=self.runtime
        with r.gates.hold(task_id):
            task=r.credentials.authorize(raw,task_id,'models:invoke')
            if not self.configured:raise CloudError('model_unavailable',503)
            if task.model_profile_id!=self.profile.profile_id:raise CloudError('model_profile_changed',409)
            if task.lifecycle not in ('ready','starting'):raise CloudError('model_task_unavailable',409)
            if model_key not in self.routes:raise CloudError('model_not_allowed',403)
            return ModelGrant(task_id,task.generation,self.profile.profile_id,model_key,self.routes[model_key])

    def check(self,grant):
        r=self.runtime
        with r.gates.hold(grant.task_id):
            task=r.registry.get_task(grant.task_id)
            if task.generation!=grant.generation:raise CloudError('model_generation_changed',409)
            if (not self.configured or task.model_profile_id!=grant.profile_id
                    or self.profile.profile_id!=grant.profile_id):raise CloudError('model_profile_changed',409)
            if task.lifecycle not in ('ready','starting'):raise CloudError('model_task_unavailable',409)

    async def aclose(self):
        if self.relay is not None:await self.relay.aclose()
