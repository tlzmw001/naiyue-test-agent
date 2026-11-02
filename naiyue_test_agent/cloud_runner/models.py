"""Native adapters use fixed task routes and read the current credential at each send."""
from hashlib import sha256
import time

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.model_access import catalog_from_configuration
from naiyue_test_agent.model_access.configuration import parse_model_configuration


class RunnerModels:
    def __init__(self,bootstrap):
        self.bootstrap=bootstrap
        public=bootstrap.models.configuration
        token=bootstrap.token.get_secret_value();providers={}
        for key,value in public['providers'].items():
            providers[key]={'base_url':bootstrap.endpoint}
            if value['kind']!='ollama':providers[key]['api_key']=token
        parsed=parse_model_configuration(public,provider_connections=providers)
        connections={}
        for model_id,d in parsed.models.items():
            model_key=sha256(model_id.encode()).hexdigest()
            base=bootstrap.endpoint.rstrip('/')+'/internal/cloud/v1/tasks/'+bootstrap.task_id+'/models/'+model_key
            connections[model_id]={'base_url':base,'client_kwargs':{'trust_env':False,'follow_redirects':False}}
        self.catalog=catalog_from_configuration(parsed,connections=connections)
        self.catalog.prepare_async_clients(http_options={'trust_env':False,'follow_redirects':False})
        for model_id in self.catalog.model_ids:
            model=self.catalog.get(model_id);kind=parsed.models[model_id]['kind']
            prefix=connections[model_id]['base_url']+'/'
            def before(request,*,kind=kind,prefix=prefix):
                b=self.bootstrap
                if b.expires_at<=time.time():raise CloudError('runner_model_credential_expired',401)
                if request.method!='POST' or not str(request.url).startswith(prefix):
                    raise CloudError('runner_model_endpoint_rejected',403)
                for name in ('authorization','x-api-key','api-key'):request.headers.pop(name,None)
                if kind=='anthropic':request.headers['x-api-key']=b.token.get_secret_value()
                else:request.headers['authorization']='Bearer '+b.token.get_secret_value()
            async def async_before(request,*,before=before):before(request)
            if kind in ('openai','openai_compatible'):
                sync,async_client=model.root_client._client,model.root_async_client._client
            else:sync,async_client=model._client._client,model._async_client._client
            sync.event_hooks['request'].insert(0,before)
            async_client.event_hooks['request'].insert(0,async_before)

    def renew(self,token,expires_at):
        self.bootstrap=self.bootstrap.model_copy(update={'token':token,'expires_at':expires_at})

    async def aclose(self):await self.catalog.aclose()
