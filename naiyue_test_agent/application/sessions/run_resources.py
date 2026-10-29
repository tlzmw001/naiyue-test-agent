"""Prepare remote resources in two bounded phases and reuse the local parsers."""
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from .project_instructions import instruction_target,instruction_part
from .skills import (skill_candidates,skill_catalog,assemble_resources,SkillCatalog,
                     prepare_run_resources,discover_project_skills)


async def _remote_resources(files,*,instructions,skills):
    view=await files.resource_view(instructions=instructions,skills=skills)
    selected=instruction_target(view) if instructions else None
    candidates,diagnostics=skill_candidates(view) if skills else ([],[])
    paths=([selected[1]] if selected else [])+[str(path) for _,path,_ in candidates]
    await files.load_resources(view,list(dict.fromkeys(paths)))
    part=instruction_part(view,selected)
    catalog=skill_catalog(view,candidates,diagnostics) if skills else SkillCatalog((),())
    return part,catalog


async def prepare_resources(files,*,enable_skills=True):
    if files is None or files.environment.kind=='host':
        return await run_blocking(prepare_run_resources,files,enable_skills=enable_skills)
    part,catalog=await _remote_resources(files,instructions=True,skills=enable_skills)
    return assemble_resources(part,catalog)


async def discover_skills(files):
    if files is None or files.environment.kind=='host':
        return await run_blocking(discover_project_skills,files)
    _,catalog=await _remote_resources(files,instructions=False,skills=True)
    return catalog
