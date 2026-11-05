"""Upload independently of runs; only message admission can deliver staged files."""
import asyncio,logging
from pathlib import Path
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.cloud_files.source_scope import select_files
from naiyue_test_agent.contracts.cloud_tasks import CloudError,TaskAttachment
from naiyue_test_agent.storage.cloud_attachments import AttachmentRepository,public

logger=logging.getLogger(__name__)


class LocalAttachments:
    def __init__(self,manager):self.manager=manager;self.flights={}

    async def submit(self,domain,bid,sid,root,request):
        with self.manager.operation(),domain.owner.operation():
            repo=AttachmentRepository(domain.repo.database)
            async with self.manager.admission:
                key=(domain.project_id,request['attachment_id'])
                if key not in self.flights and len(self.flights)>=self.manager.settings.max_preparations:
                    raise CloudError('cloud_preparation_capacity',429)
                value=await run_blocking(repo.reserve,bid,sid,root,request)
                self.launch(domain,value)
                return public(value)

    def launch(self,domain,value):
        aid=value['attachment_id'];sid=value['local_session_id'];bid=value['binding_id'];key=(domain.project_id,aid)
        if key in self.flights or value['phase'] in ('ready','attached','cancelled'):return
        if len(self.flights)>=self.manager.settings.max_preparations:raise CloudError('cloud_preparation_capacity',429)
        repo=AttachmentRepository(domain.repo.database)
        async def work():
            with domain.owner.operation():
                try:
                    current=await run_blocking(repo.update,bid,sid,aid,phase='uploading',error_code=None)
                    if current['source_handle'] is None:
                        selection=await run_blocking(select_files,Path(current['root']),self.manager.settings.input_limits,current['paths'])
                        captured=await domain.capture(sid,Path(current['root']),selection.model_dump(by_alias=True),self.manager.settings.input_limits)
                        current=await run_blocking(repo.update,bid,sid,aid,**captured)
                    binding=await run_blocking(domain.repo.binding,bid,sid)
                    source=await run_blocking(domain.repo.source,current['source_handle'],sid)
                    def check_cancel():
                        if repo.read(bid,sid,aid)['phase']=='cancelled':raise CloudError('attachment_cancelled')
                    async with self.manager.connection(binding) as client:
                        await run_blocking(client.upload,Path(source['captured']['directory']),upload_id=aid,check_cancel=check_cancel)
                        await run_blocking(check_cancel)
                        grant=dict(attachment_id=aid,input_id=current['input_id'],display_name=current['display_name'])
                        result=TaskAttachment.model_validate(await run_blocking(client.request,'POST','/tasks/'+binding['task_id']+'/attachments',json=grant))
                        if any(getattr(result,k)!=v for k,v in grant.items()):raise CloudError('attachment_identity_conflict')
                        await run_blocking(repo.update,bid,sid,aid,phase='ready',size=result.size,error_code=None)
                except Exception as error:
                    code=error.code if isinstance(error,CloudError) else 'attachment_upload_failed'
                    logger.warning('cloud_attachment_upload_failed code=%s error_type=%s',code,type(error).__name__)
                    if code!='attachment_cancelled':
                        try:await run_blocking(repo.update,bid,sid,aid,phase='failed',error_code=code)
                        except CloudError as secondary:
                            if secondary.code!='attachment_cancelled':raise
        job=asyncio.create_task(work());self.flights[key]=job;domain.own(job)
        job.add_done_callback(lambda done:self.flights.pop(key,None))

    async def list(self,domain,bid,sid):
        with self.manager.operation(),domain.owner.operation():
            repo=AttachmentRepository(domain.repo.database)
            async with self.manager.admission:
                values=await run_blocking(repo.read,bid,sid)
                for index,value in enumerate(values):
                    if value['phase']=='uploading' and (domain.project_id,value['attachment_id']) not in self.flights:
                        values[index]=await run_blocking(repo.update,bid,sid,value['attachment_id'],phase='failed',error_code='attachment_upload_interrupted')
            return [public(value) for value in values]

    async def action(self,domain,bid,sid,aid,action):
        with self.manager.operation(),domain.owner.operation():
            repo=AttachmentRepository(domain.repo.database)
            if action=='cancel':value=await run_blocking(repo.update,bid,sid,aid,phase='cancelled')
            else:
                value=await run_blocking(repo.read,bid,sid,aid);self.launch(domain,value)
            return public(value)
