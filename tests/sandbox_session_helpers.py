"""Real envd dispatcher behind a controlled Docker transport, no daemon required."""
from contextlib import ExitStack
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.contracts.sandbox import SandboxRecord,SandboxError,PROTOCOL_VERSION
from naiyue_test_agent.envd.operations import EnvironmentOperations
from naiyue_test_agent.model_access import ModelCatalog


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self,tools,**kwargs):return self


class SandboxTransport:
    def __init__(self,tmp_path,monkeypatch,stack,model=None):
        self.root,self.stack,self.remotes,self.created,self.expired=tmp_path,stack,{},[],set()
        self.model=model or OfflineModel(responses=[AIMessage(content='done')])
        monkeypatch.setattr('naiyue_test_agent.application.load_model_catalog',lambda _:ModelCatalog('fake',{'fake':self.model}))
        monkeypatch.setattr(SandboxRecord,'environment',property(lambda row:
            EnvironmentRef(row.binding_id,'sandbox',row.project_id,str(self.root/row.binding_id),'linux')))
        transport=self
        class CLI:
            def __init__(self,*args):pass
            def daemon(self,*args):return 'D'
            def json(self,*args):return [dict(Os='linux',Id='image')]
        class Engine:
            def __init__(self,cli):self.cli=cli
            def validate(self,row,**kwargs):
                if row.binding_id in transport.expired:raise SandboxError('env_expired')
                return {'State':{'Running':True}}
            def create(self,row,save):
                transport.created.append(row.binding_id)
                Path(row.environment.root).mkdir()
                from naiyue_test_agent.sandbox_settings import SandboxSettings
                transport.remotes[row.binding_id]=stack.enter_context(EnvironmentOperations(row.environment,row.session_id,
                    tmp_path/('state-'+row.binding_id),SandboxSettings.model_validate(row.settings)))
                return row.changed(container_id=row.binding_id*2)
            def handshake(self,row):self.request(row,'hello',{})
            def request(self,row,op,params,**kwargs):
                self.validate(row)
                return transport.remotes[row.binding_id].dispatch(dict(protocol=PROTOCOL_VERSION,request_id='request',
                    environment=row.environment.public(),session_id=row.session_id,operation=op,params=params))
        monkeypatch.setattr('naiyue_test_agent.application.environments.service.DockerCLI',CLI)
        monkeypatch.setattr('naiyue_test_agent.application.environments.service.DockerEnvironment',Engine)
