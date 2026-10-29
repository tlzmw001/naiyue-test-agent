"""Native process preparation inside the container, never on the controller."""
from dataclasses import asdict
import os
import shutil
from langchain_core.tools import ToolException
from naiyue_test_agent.execution.host_shell import BashAdapter,PowerShellAdapter,build_environment
from naiyue_test_agent.tools.processes import ProcessRequest,run_async_sync
from naiyue_test_agent.tools.settings import BashSettings,PowerShellSettings,AITestSettings,ProcessSettings
from .identity import TaskProcessRunner


def execute_process(payload,kind,root,consume,control,on_started):
    runner=TaskProcessRunner(ProcessSettings.model_validate(payload['process_settings']) if payload.get('process_settings') else None)
    if kind=='shell':
        name=payload['name']
        settings={'bash':BashSettings,'powershell':PowerShellSettings}[name].model_validate(payload['settings'])
        if not isinstance(payload['command'],str) or not 0<len(payload['command'])<=settings.max_command_chars:
            raise ToolException('Invalid sandbox command')
        adapter={'bash':BashAdapter,'powershell':PowerShellAdapter}[name]()
        executable=adapter.resolve_executable(settings)
        if executable is None:raise ToolException('Shell interpreter is unavailable in the sandbox.')
        version=adapter.probe_version(executable,settings,runner,root)
        request=adapter.build_request(executable,version,payload['command'],payload['timeout'],root,settings)
    else:
        settings=AITestSettings.model_validate(payload['settings'])
        args=payload['args']
        if (not isinstance(args,list) or not 1<=len(args)<=settings.max_args or
                any(not isinstance(arg,str) or '\0' in arg for arg in args) or
                sum(map(len,args))>settings.max_argument_chars):raise ToolException('Invalid sandbox argv')
        parent={key:value for key,value in os.environ.items() if key in ('PATH','HOME','LANG','LC_ALL','TMPDIR')}
        environment=build_environment(settings.environment,parent=parent)
        executable=shutil.which(settings.executable,path=environment.get('PATH',''))
        if executable is None:raise ToolException('AITest executable is unavailable in the sandbox.')
        request=ProcessRequest([executable,*settings.prefix_args,*args],root,{},payload['timeout'],merge_output=True,eof_policy='shell')
    from dataclasses import replace
    parent={key:value for key,value in os.environ.items() if key in ('PATH','HOME','LANG','LC_ALL','TMPDIR')}
    request=replace(request,env=build_environment(settings.environment,parent=parent),on_started=on_started)
    outcome=run_async_sync(lambda:runner.run(request,consume,control))
    return asdict(outcome)
