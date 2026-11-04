"""Reject retired A modules, imports, UI and build tools after the formal B switch."""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib


PACKAGE='naiyue_test_agent'
A_RULES={
    'envd':'task-side A protocol; replace with Runner and preserve permission/race assertions',
    'application.environments':'A environment/input/result/delivery orchestration; replace with cloud/client domains',
    'application.sessions.execution_binding':'mixed local admission and A binding; remove A branch, keep local admission',
    'execution.docker_':'A Docker proxy; replace with OpenSandbox and in-container Host tools',
    'execution.sandbox':'A SSH tool transport/runtime; replace with Runner control and local tool IO',
    'execution.git_snapshot':'A commit/bundle input; replace with archive and manifest',
    'execution.git_results':'A bundle import and published result refs; replace with immutable content results',
    'execution.git_delivery':'A history replay/delivery; replace with checked content delivery',
    'contracts.sandbox':'A execution wire and identities; replace with cloud task contracts',
    'contracts.git_inputs':'A Git snapshot contract; replace with cloud input contract',
    'contracts.git_results':'A bundle result contract; replace with cloud result contract',
    'contracts.git_delivery':'A branch/merge/discard contract; replace with cloud delivery contract',
    'storage.environment_registry':'A environment registry; remove active A consumer without dropping old data',
    'storage.sandbox_operations':'A operation repository; replace with cloud operation records',
    'storage.sandbox_journal':'A operation journal; replace with task/Runner records',
    'storage.git_inputs':'A input repository; replace with cloud content repository',
    'storage.git_deliveries':'A delivery repository; replace with local cloud delivery records',
    'sandbox_settings':'retired A configuration',
    'sandbox_profiles':'retired A profiles',
    'delivery_settings':'retired A delivery configuration',
    'contracts.session_environment':'retired A session DTO',
    'service.environments':'retired A environment API',
    'web.result_routes':'retired A result API',
    'application.sessions.result_api':'retired A result dispatcher',
}
SHARED={
    'execution.git_repository':'shared: native Git operations used by B result and branch delivery',
    'storage.registry_database':'shared: ordinary project registration and historical schema, no active A repository',
}

FRONTEND=('SandboxManager','SandboxResults','executionOptions','refreshEnvironments','sandboxManagement',
    'manageSandbox','createSelected','prepareEnvironment')
SCRIPT_MARKERS=('build_envd','envd_artifacts','build_sandbox_image','verify_sandbox','release_sandbox_smoke','sandbox_crash_probe','sandbox_probe','profile_sandbox','profile_git_process_cost')


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _files(root):
    names=[]
    for directory in (PACKAGE,'scripts','tests','frontend/src','.github/workflows','release'):
        base=root/directory
        if not base.exists():continue
        for parent,dirs,files in os.walk(base,followlinks=False):
            dirs[:]=sorted(d for d in dirs if not d.startswith('.') and d not in ('__pycache__','node_modules','dist','_assets'))
            for name in sorted(files):
                path=Path(parent)/name
                if path.suffix not in ('.py','.ts','.tsx','.css','.json','.toml','.yml','.yaml'):continue
                if directory=='release' and name!='envd.json':continue
                if path.is_symlink():raise ValueError('linked inventory source: '+str(path.relative_to(root)))
                names.append(path)
    names.extend(root/name for name in ('pyproject.toml','setup.py') if (root/name).exists())
    return sorted(set(names))


def _module(path):
    parts=list(path.with_suffix('').parts)
    if parts[-1]=='__init__':parts.pop()
    return '.'.join(parts)


def _rule(module):
    if module.startswith('scripts.') and any(module.split('.')[1].startswith(marker) for marker in SCRIPT_MARKERS):
        return 'retired A tooling'
    relative=module.removeprefix(PACKAGE+'.')
    if relative==module:return None
    for prefix,reason in A_RULES.items():
        if relative==prefix or relative.startswith(prefix+('.') if prefix[-1:] not in ('_',) and prefix!='execution.sandbox' else prefix):
            return reason
    return None


def _resolve(module,package):
    if not module.startswith('.'):return module
    try:return importlib.util.resolve_name(module,package)
    except (ImportError,ValueError):return module


def _python_refs(text,module,is_package):
    tree=ast.parse(text);package=module if is_package else module.rpartition('.')[0]
    refs=[];aliases={}
    def add(kind,target,line):refs.append(dict(kind=kind,target=target,line=line))
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name]=alias.name
                add('absolute_import',alias.name,node.lineno)
        elif isinstance(node,ast.ImportFrom):
            name=_resolve('.'*node.level+(node.module or ''),package) if node.level else node.module or ''
            kind='relative_import' if node.level else 'absolute_import'
            if node.module:add(kind,name,node.lineno)
            for alias in node.names:
                aliases[alias.asname or alias.name]=name+'.'+alias.name
                if alias.name!='*':add(kind,name+'.'+alias.name,node.lineno)
    for node in ast.walk(tree):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if (isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute)
                        and decorator.func.attr in ('get','post','put','delete','patch','websocket','api_route')
                        and decorator.args):
                    add('api_registration',ast.unparse(decorator.args[0])[:256],decorator.lineno)
        if isinstance(node,ast.Call):
            function=node.func
            name=(aliases.get(function.id,function.id) if isinstance(function,ast.Name)
                else aliases.get(function.value.id,function.value.id)+'.'+function.attr
                if isinstance(function,ast.Attribute) and isinstance(function.value,ast.Name) else '')
            if name not in ('__import__','importlib.import_module'):continue
            kind='__import__' if name=='__import__' else 'import_module'
            target=node.args[0] if node.args else next((k.value for k in node.keywords if k.arg=='name'),None)
            if not isinstance(target,ast.Constant) or not isinstance(target.value,str):
                add('dynamic_unresolved',ast.unparse(node)[:256],node.lineno);continue
            relative_package=package
            supplied=node.args[1] if kind=='import_module' and len(node.args)>1 else next((k.value for k in node.keywords if k.arg=='package'),None)
            if isinstance(supplied,ast.Constant) and isinstance(supplied.value,str):relative_package=supplied.value
            add(kind,_resolve(target.value,relative_package),node.lineno)
        if isinstance(node,(ast.Assign,ast.AnnAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            if not any(isinstance(target,ast.Name) and target.id.endswith('EXPORTS') for target in targets):continue
            try:exports=ast.literal_eval(node.value)
            except (ValueError,TypeError):
                add('dynamic_unresolved','nonliteral lazy export map',node.lineno);continue
            if not isinstance(exports,dict):continue
            for value in exports.values():
                target=value[0] if isinstance(value,(tuple,list)) and value else value
                if isinstance(target,str):
                    target=_resolve(target,package) if target.startswith('.') else target if target.startswith(PACKAGE+'.') else package+'.'+target
                    add('lazy_export',target,node.lineno)
    return refs


def scan(root):
    root=Path(root);files=_files(root);raws={p.relative_to(root).as_posix():p.read_bytes() for p in files}
    texts={name:raw.decode('utf-8') for name,raw in raws.items()}
    modules={_module(Path(name)):name for name in texts if name.endswith('.py')}
    refs={};retained={};owned={name:_rule(module) for module,name in modules.items() if _rule(module)}
    short={}
    for module,name in modules.items():short.setdefault(module.rsplit('.',1)[-1],[]).append(name)
    def target_file(target):
        if target in modules:return modules[target]
        found=short.get(target,[])
        return found[0] if len(found)==1 else None
    for name,text in texts.items():
        path=Path(name);items=[]
        if path.suffix=='.py':
            items=_python_refs(text,_module(path),path.name=='__init__.py')
            relative=_module(path).removeprefix(PACKAGE+'.')
            if relative in SHARED:retained[name]=SHARED[relative]
        if name.startswith('frontend/'):
            for symbol in FRONTEND:
                if symbol in text:
                    items.append(dict(kind='frontend_symbol',target=symbol,line=text[:text.index(symbol)].count('\n')+1))
                    retained[name]='formal A UI until S09.1: replace environment/sandbox actions with cloud task delivery'
        if name=='pyproject.toml':
            config=tomllib.loads(text)
            for command,value in config.get('project',{}).get('scripts',{}).items():
                items.append(dict(kind='cli_entrypoint',target=value.split(':',1)[0],line=0,command=command))
            if 'naiyue_test_agent' in text:
                items.extend(dict(kind='package_member',target=_module(Path(source)),line=0) for source in owned)
        if name=='scripts/release_artifacts.py' and 'def stage_python(' in text and "rglob('*.py')" in text:
            items.extend(dict(kind='package_member',target=_module(Path(source)),line=0) for source in owned)
        if name=='release/envd.json':
            for source in json.loads(text).get('files',[]):
                if source.endswith('.py'):items.append(dict(kind='package_member',target=_module(Path(source)),line=0))
        if path.suffix in ('.yml','.yaml'):
            for marker in SCRIPT_MARKERS:
                for match in re.finditer(r'(?<![a-zA-Z0-9_])'+re.escape(marker)+r'[a-zA-Z0-9_]*',text):
                    items.append(dict(kind='script_entrypoint',target='scripts.'+match.group(),line=text[:match.start()].count('\n')+1))
        refs[name]=items
        if name in owned:retained.setdefault(name,'formal A until S09.1: '+owned[name])
        direct=next((_rule(item['target']) for item in items if _rule(item['target'])),None)
        if direct and name not in retained:retained[name]='formal A consumer until S09 migration: '+direct
        if any(item['kind']=='dynamic_unresolved' for item in items) and name not in retained:
            retained[name]='manual review: dynamic import target is not statically resolvable; retain until S09 startup trace confirms ownership'
    affected=set(owned)|{name for name,items in refs.items() if any(_rule(item['target']) for item in items)}
    while True:
        additions=[]
        for name,items in refs.items():
            if name in affected:continue
            via=next((item['target'] for item in items if target_file(item['target']) in affected),None)
            if via is not None:
                additions.append(name)
                items.append(dict(kind='transitive_consumer',target=via,line=0))
                retained.setdefault(name,'formal A transitive consumer until S09 migration via '+via)
        if not additions:break
        affected.update(additions)
    return [dict(path=name,sha256=_sha(raws[name]),retained=retained[name],
        references=sorted(refs[name],key=lambda value:(value['line'],value['kind'],value['target']))) for name in sorted(retained)]



def check(root):
    root=Path(root);errors=[]
    for row in scan(root):
        name=row['path'];path=Path(name)
        if path.suffix=='.py' and _rule(_module(path)):
            errors.append('retired_module:'+name)
        for ref in row['references']:
            if _rule(ref['target']) or ref['kind'] in ('frontend_symbol','script_entrypoint'):
                errors.append('retired_reference:'+name+':'+str(ref['line'])+':'+ref['target'])
    for path in _files(root):
        relative=path.relative_to(root)
        if relative.parts[0]=='scripts' and ('sandbox_probe' in relative.parts or any(path.stem.startswith(marker) for marker in SCRIPT_MARKERS)):
            errors.append('retired_tool:'+relative.as_posix())
    if (root/'release/envd.json').exists():errors.append('retired_release:release/envd.json')
    return sorted(set(errors))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('check',))
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args();errors=check(args.root)
    print(json.dumps(dict(status='violation' if errors else 'retired',errors=errors)))
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
