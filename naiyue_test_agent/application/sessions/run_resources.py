"""Prepare current Host instructions and Skills through the existing parsers."""
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from .skills import prepare_run_resources,discover_project_skills


async def prepare_resources(files,*,enable_skills=True):
    return await run_blocking(prepare_run_resources,files,enable_skills=enable_skills)


async def discover_skills(files):
    return await run_blocking(discover_project_skills,files)
