"""Isolated fixture hosts for route/probe tests; production uses service.app directly.

These tests deliberately inject one fixed host into the chats scope so existing
fault injections can observe it. Ownership, receipts, headers and routes still
run through the real service app. Cross-project/bootstrap tests use normal scope
assembly instead of this fixture.
"""
from contextlib import asynccontextmanager
import socket
if __package__:
    from .component_fixtures import open_component_application
else:
    from component_fixtures import open_component_application
from naiyue_test_agent.application.sessions.project_instructions import BASE_SYSTEM_PROMPT
from naiyue_test_agent.settings import load_settings
from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.settings import ServiceOptions
from naiyue_test_agent.web.run_delivery import RunDelivery
from naiyue_test_agent.web.service import WebService


def create_probe_app(settings, *, application_factory=None):
    runtime = settings.runtime_settings if settings.runtime_settings is not None else load_settings()
    delivery_settings = settings if 'event_cache_bytes' in settings.model_fields_set else settings.model_copy(
        update={'event_cache_bytes': runtime.web_event_cache_bytes})
    tools = create_builtin_tools(runtime.tools, process_settings=runtime.process,
        shell_task_settings=runtime.shell_tasks, progress_settings=runtime.progress,
        network_settings=runtime.network, retry_settings=runtime.tool_retry)
    initial = runtime.default_active_tools if settings.workspace_root is not None else []
    if initial is None:
        initial = [spec.tool.name for spec in tools if spec.tool.name not in ('bash', 'powershell')]
    component_factory = application_factory or (lambda: open_component_application(
        settings.config_path, database_path=settings.database_path, tools=tools,
        system_prompt=BASE_SYSTEM_PROMPT, response_format=None, initial_active_tools=initial, settings=runtime,
        workspace_binding={'workspace_id': settings.workspace_id, 'root_path': str(settings.workspace_root)}
            if settings.workspace_id is not None and settings.expected_project is None else None,
        expected_project=settings.expected_project))
    selected = []
    @asynccontextmanager
    async def factory():
        async with component_factory() as component:
            selected.append(component)
            yield component.root
    port = settings.port
    if port == 0:
        # ASGI probes have no TCP listener; the discovery DTO still needs a real
        # loopback authority, as the production launcher assigns before startup.
        family = socket.AF_INET6 if ':' in settings.host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as listener:
            listener.bind((settings.host, 0))
            port = listener.getsockname()[1]
    options = ServiceOptions(data_dir=settings.database_path.parent / '.probe-service',
        config_path=settings.config_path, frontend_dist=settings.frontend_dist,
        host=settings.host, port=port, max_frame_bytes=settings.max_frame_bytes,
        max_receipts=max(4096, settings.max_receipts))
    app = create_service_app(options, application_factory=factory)
    lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def fixture_lifespan(app):
        async with lifespan(app):
            # Test credentials and the injected host stay private to this fixture.
            app.state.web_settings = delivery_settings
            host = selected[0].host
            delivery = RunDelivery(host, delivery_settings, progress_settings=runtime.progress)
            service = WebService(host, delivery, delivery_settings, models=app.state.application.models)
            app.state.scopes.services['chats'] = service
            app.state.delivery, app.state.service = delivery, service
            yield
    app.router.lifespan_context = fixture_lifespan
    return app


def command_headers(request):
    """Each fixture mutation uses the same IDs required of actual clients."""
    import json
    from uuid import uuid4
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        body = json.loads(request.content) if request.content else {}
        request.headers['X-Request-ID'] = body.get('request_id', str(uuid4()))


async def async_command_headers(request):
    command_headers(request)
