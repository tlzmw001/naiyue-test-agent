"""Local immutable material identities; model reads never contact a remote owner."""
import logging
from pydantic import TypeAdapter
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.cloud_receive import ReceiveLimits,AnalysisDraft
from naiyue_test_agent.contracts.cloud_tasks import CloudError,UUIDText
from .receive_files import ReceiveFiles

logger=logging.getLogger(__name__)


class LocalMaterials:
    def __init__(self,owner,storage,project_id):
        self.owner,self.repo,self.project_id=owner,storage.cloud_receives,project_id
        self.files=ReceiveFiles(storage);self.limits=ReceiveLimits()

    def _get(self,sid,mid):
        if self.project_id is None or self.files.root is None:raise CloudError('cloud_materials_unavailable')
        TypeAdapter(UUIDText).validate_python(mid)
        ref,expected=self.repo.material(mid,sid,self.project_id)
        try:self.files.inspect(self.files.blob(ref.sha256),ref.size,ref.sha256,expected)
        except OSError as error:
            logger.warning('cloud_material_file_unavailable error_type=%s',type(error).__name__)
            raise CloudError('cloud_material_unavailable',409) from None
        return ref

    def validate_refs(self,session_id,ids):
        if self.project_id is None or self.files.root is None:raise CloudError('cloud_materials_unavailable')
        if len(ids)>self.limits.max_files or len(set(ids))!=len(ids):raise CloudError('cloud_material_budget',413)
        with self.files.use():return [self._get(session_id,mid) for mid in ids]

    def has_materials(self,session_id):
        return self.project_id is not None and self.files.root is not None and self.repo.has_materials(session_id,self.project_id)

    async def query(self,source_session_id,project_id,request):
        if self.project_id is None or self.files.root is None:raise CloudError('cloud_materials_unavailable')
        if project_id!=self.project_id:raise CloudError('cloud_material_identity_mismatch',403)
        offset,limit=request.get('offset',1),request.get('limit')
        if limit is None:limit=min(200,self.limits.max_read_lines)
        if type(offset) is not int or offset<1 or type(limit) is not int or not 1<=limit<=self.limits.max_read_lines:
            raise CloudError('cloud_material_budget',413)
        with self.owner.operation(),self.files.use():
            return await run_blocking(self._read,source_session_id,request['material_id'],offset,limit)

    def _read(self,sid,mid,offset,limit):
        ref,expected=self.repo.material(mid,sid,self.project_id);budget=self.limits.max_read_bytes
        result=dict(material=ref.model_dump(mode='json'),binary=False,text='',offset=offset,next_offset=offset,complete=False,truncated=False)
        try:
            with self.files.verified(self.files.blob(ref.sha256),ref.size,ref.sha256,expected) as stream:
                sample=stream.read(min(4096,budget));stream.seek(0)
                if b'\0' in sample:return result|dict(binary=True,complete=True,next_offset=None)
                line=1
                while line<offset:
                    chunk=stream.readline(budget+1)
                    if not chunk:return result|dict(complete=True,next_offset=None)
                    if chunk.endswith(b'\n'):line+=1
                chunks=[];used=0;count=0
                while count<limit:
                    content=stream.readline(budget-used+1)
                    if not content:break
                    if len(content)>budget-used:
                        clipped=content[:budget-used];result['truncated']=True
                        while content and not content.endswith(b'\n'):content=stream.readline(budget+1)
                        # A long line is explicitly clipped, and pagination continues at its next line.
                        chunks.append(clipped);count+=1;break
                    chunks.append(content);used+=len(content);count+=1
                    if used==budget:break
                raw=b''.join(chunks)
                try:text=raw.decode('utf-8')
                except UnicodeDecodeError as error:
                    if not result['truncated'] or error.reason!='unexpected end of data':return result|dict(binary=True,complete=True,next_offset=None)
                    text=raw[:error.start].decode('utf-8')
                if '\0' in text:return result|dict(binary=True,complete=True,next_offset=None)
                complete=not stream.read(1)
                return result|dict(text=text,next_offset=None if complete else offset+count,complete=complete)
        except OSError as error:
            logger.warning('cloud_material_read_failed error_type=%s',type(error).__name__)
            raise CloudError('cloud_material_unavailable') from None

    def analysis(self,receive_id,sid):
        record=self.repo.read(receive_id,sid)
        if record.phase!='available':raise CloudError('cloud_material_unavailable')
        refs=self.repo.materials(receive_id,sid,self.project_id)
        self.validate_refs(sid,[ref.material_id for ref in refs])
        return AnalysisDraft(local_project_id=self.project_id,local_session_id=sid,receive_id=receive_id,
            text='请分析已接收的云端任务材料，核对代码变化、测试报告和未解决问题；材料属于任务上报，不代表本机已执行验证。',material_refs=refs)
