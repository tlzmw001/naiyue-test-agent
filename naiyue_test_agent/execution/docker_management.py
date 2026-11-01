"""Fixed management batches reduce SSH handshakes without remote dependencies."""
import json
import re
import shlex

from naiyue_test_agent.contracts.sandbox import SandboxError


def command(cli,args):
    ssh=cli.settings.ssh
    if ssh is None:raise SandboxError('env_endpoint_unsupported')
    return shlex.join([ssh.docker_executable,'--host','unix://'+ssh.socket_path,*args])


def json_script(cli,requests,*,fresh_volumes=False):
    lines=['set -e',"printf '%s' '{'"]
    for index,(key,args) in enumerate(requests):
        prefix=(',' if index else '')+json.dumps(key)+':'
        lines.extend(["printf '%s' "+shlex.quote(prefix),command(cli,args)])
    if fresh_volumes:
        lines.extend(["printf '%s' ',\"fresh_volumes\":'",
            'if test -z "$nyt_existing_volumes"; then printf true; else printf false; fi'])
    lines.append("printf '%s' '}'")
    return '\n'.join(lines)


def json_batch(cli,requests,*,mutates=False):
    raw=cli.run_script(json_script(cli,requests),mutates=mutates)
    try:
        value=json.loads(raw)
        if not isinstance(value,dict) or set(value)!={key for key,_ in requests}:raise ValueError()
        return value
    except (ValueError,UnicodeError) as error:
        raise SandboxError('env_operation_unknown' if mutates else 'env_protocol_mismatch') from error


def validation_requests(row):
    return [('daemon',['info','--format','{{json .}}']),
        ('containers',['container','inspect',row.container_id]),
        ('volumes',['volume','inspect',row.volume_name,'nyt-s-'+row.binding_id])]


def validation_snapshot(cli,row):
    return json_batch(cli,validation_requests(row))


def attach_command(cli,row,args,*,start=False):
    # Full policy is acknowledged before an explicitly permitted first start.
    snapshot=json_script(cli,validation_requests(row))
    script='set -e\nnyt_snapshot=$(\n'+snapshot+'\n)\n'
    script+="printf '%s' \"$nyt_snapshot\" | tr -d '\\n'\nprintf '\\n'\n"
    script+='nyt_ack=$(dd bs=1 count=14 2>/dev/null)\n'
    script+="test \"$nyt_ack\" = NYT-ATTACH-OK\n"
    if start:script+=command(cli,['container','start',row.container_id])+' >/dev/null || exit 3\n'
    script+='exec '+command(cli,args)
    argv=cli.command([]);argv[-1]='exec '+shlex.join(['sh','-c',script])
    return argv


def startup_snapshot(cli,row):
    """One SSH; verify the frozen owner before reading this start's bounded log."""
    from .docker_environment import labels
    facts=json_script(cli,[('daemon',['info','--format','{{json .}}']),
        ('containers',['container','inspect',row.container_id])])
    identity=' '.join([row.container_id,row.image_id,'/'+row.container_name,
        json.dumps(labels(row),sort_keys=True,separators=(',',':'))])
    lines=['set -e','nyt_facts=$(\n'+facts+'\n)',
        'nyt_daemon=$('+command(cli,['info','--format','{{.ID}} {{.OSType}}'])+')',
        'test "$nyt_daemon" = '+shlex.quote(row.daemon_id+' linux'),
        'nyt_identity=$('+command(cli,['container','inspect','--format',
            '{{.Id}} {{.Image}} {{.Name}} {{json .Config.Labels}}',row.container_id])+')',
        'test "$nyt_identity" = '+shlex.quote(identity),
        'printf \'%s\' "$nyt_facts" | tr -d \'\\n\'','printf \'\\n\'',
        'nyt_started=$('+command(cli,['container','inspect','--format',
            '{{if and (not .State.Running) (eq .State.Status "exited") (ne .State.ExitCode 0)}}{{.State.StartedAt}}{{end}}',
            row.container_id])+')',
        'printf \'%s\\n\' "$nyt_started"',
        'if test -n "$nyt_started"; then '+command(cli,['logs','--tail','100','--since'])+
            ' "$nyt_started" '+shlex.quote(row.container_id)+' 2>&1; fi']
    raw=cli.run_script('\n'.join(lines))
    head,separator,body=raw.partition(b'\n')
    if not separator or len(raw)>65536:raise SandboxError('env_protocol_mismatch')
    value=json.loads(head)
    if not isinstance(value,dict) or set(value)!={'daemon','containers'}:raise SandboxError('env_protocol_mismatch')
    observed,separator,logs=body.partition(b'\n')
    state=value['containers'][0]['State']
    expected=state.get('StartedAt') if state.get('Running') is False and state.get('Status')=='exited' and state.get('ExitCode') else ''
    if not separator or observed.decode('ascii')!=expected:raise SandboxError('env_protocol_mismatch')
    return value,logs


def validate_snapshot(row,value,*,running=True):
    from .docker_environment import verify_container,verify_volume
    daemon=value.get('daemon',{})
    if daemon.get('OSType')!='linux':raise SandboxError('env_platform_unsupported')
    if daemon.get('ID')!=row.daemon_id:raise SandboxError('env_binding_mismatch')
    containers=value.get('containers');volumes=value.get('volumes')
    if not isinstance(containers,list) or len(containers)!=1 or not isinstance(volumes,list) or len(volumes)!=2:
        raise SandboxError('env_binding_mismatch')
    data=containers[0]
    verify_container(row,data);verify_volume(row,volumes[0]);verify_volume(row,volumes[1],state=True)
    if running and not data.get('State',{}).get('Running'):raise SandboxError('env_stopped')
    return data


def preflight(cli,image):
    value=json_batch(cli,[('daemon',['info','--format','{{json .}}']),('images',['image','inspect',image])])
    info=value['daemon']
    if not isinstance(info,dict) or not isinstance(info.get('ID'),str) or not info['ID']:
        raise SandboxError('env_protocol_mismatch')
    if info.get('OSType')!='linux':raise SandboxError('env_platform_unsupported')
    images=value['images']
    if not isinstance(images,list) or len(images)!=1 or not isinstance(images[0],dict):
        raise SandboxError('env_protocol_mismatch')
    return info['ID'],images[0]


def create_remote(cli,row,args,save):
    from .docker_environment import labels,state_volume
    # Verify pre-existing volume ownership before attaching either to a container.
    lines=['set -e','nyt_daemon=$('+command(cli,['info','--format','{{.ID}}'])+')',
        'test "$nyt_daemon" = '+shlex.quote(row.daemon_id)]
    pattern='^('+re.escape(row.volume_name)+'|'+re.escape(state_volume(row))+')$'
    lines.append('nyt_existing_volumes=$('+command(cli,['volume','ls','--filter','name='+pattern,'--format','{{.Name}}'])+')')
    expected=json.dumps(labels(row),sort_keys=True,separators=(',',':'))
    for name in (row.volume_name,state_volume(row)):
        create=['volume','create']
        for key,value in labels(row).items():create.extend(['--label',key+'='+value])
        lines.append(command(cli,[*create,name])+' >/dev/null')
        inspect=command(cli,['volume','inspect','--format','{{json .Labels}}',name])
        lines.extend(['nyt_labels=$('+inspect+')','test "$nyt_labels" = '+shlex.quote(expected)])
    lines.append('nyt_container=$('+command(cli,args)+')')
    # Container name is fixed by the saved binding; no response text becomes shell code.
    queries=[('daemon',['info','--format','{{json .}}']),
        ('containers',['container','inspect',row.container_name]),
        ('volumes',['volume','inspect',row.volume_name,state_volume(row)])]
    lines.append(json_script(cli,queries,fresh_volumes=True))
    raw=cli.run_script('\n'.join(lines),mutates=True)
    try:
        value=json.loads(raw);identity=value['containers'][0]['Id']
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{64}',identity):raise ValueError()
    except (ValueError,UnicodeError,KeyError,IndexError,TypeError) as error:
        raise SandboxError('env_operation_unknown') from error
    row=row.changed(container_id=identity);save(row)
    validate_snapshot(row,value,running=False)
    return row,value.get('fresh_volumes') is True
