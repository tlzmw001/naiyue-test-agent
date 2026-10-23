"""Temporary-profile factory for the composed application."""

async def build_async_local_application(config_path, *, tools, system_prompt, response_format, initial_active_tools=None, settings=None):
    from .assembly import build_application
    return await build_application(config_path, database_path=None, tools=tools, system_prompt=system_prompt,
        response_format=response_format, initial_active_tools=initial_active_tools, settings=settings)
