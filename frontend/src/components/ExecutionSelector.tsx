import { useEffect, useRef, useState } from 'react';
import { ApiError, errorText, WebApi } from '../api';
import type { ExecutionDraft, ExecutionOptions, SessionView } from '../types';
import { keepDialogFocus } from '../dialogFocus';
import styles from './ExecutionSelector.module.css';
import ResultReview from './ResultReview';
import SandboxManager from './SandboxManager';
import { Chevron, Icon } from './Icons';

export default function ExecutionSelector({ api, session, disabled, create, refresh, running = false, draft, locked = false }: {
  api: WebApi; session: SessionView | null; disabled: boolean;
  create: (choice: ExecutionDraft) => Promise<void>; refresh: () => Promise<void>;
  draft?: ExecutionDraft | null; locked?: boolean;
  running?: boolean;
}) {
  const [open,setOpen]=useState(false),[sandbox,setSandbox]=useState(false),[busy,setBusy]=useState(false);
  const [options,setOptions]=useState<ExecutionOptions|null>(null),[error,setError]=useState('');
  const [selected,setSelected]=useState<string[]>([]),[trust,setTrust]=useState(false),[destroy,setDestroy]=useState(false);
  const [candidate,setCandidate]=useState<NonNullable<ExecutionOptions['profiles']>[number]|null>(null);
  const [loading,setLoading]=useState(false),[refreshing,setRefreshing]=useState(false);
  const generation=useRef(0);
  const [review,setReview]=useState(false);
  const [managing,setManaging]=useState(false);
  const managerTrigger=useRef<HTMLButtonElement>(null);
  const trigger=useRef<HTMLButtonElement>(null),panel=useRef<HTMLDivElement>(null);
  const focusBack=useRef(false);
  const busyRef=useRef(busy);busyRef.current=busy;
  const actual=session?.execution, label=actual?.kind==='sandbox' ? actual.profile_name??'Docker 沙箱' :
    !session&&draft?.execution==='sandbox' ? draft.profile_name??'Docker 沙箱' : '此计算机';
  useEffect(()=>{
    if(!open)return;
    void loadOptions();
    return ()=>{generation.current++;};
  },[open,api]);
  useEffect(()=>{
    if(!open)return;
    panel.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const dismiss=(event:PointerEvent)=>{if(!panel.current?.contains(event.target as Node)&&!trigger.current?.contains(event.target as Node))close();};
    document.addEventListener('pointerdown',dismiss);
    return()=>document.removeEventListener('pointerdown',dismiss);
  },[open,sandbox]);
  useEffect(()=>{if(!open&&!busy&&focusBack.current){focusBack.current=false;trigger.current?.focus();}},[open,busy]);
  async function loadOptions(reload=false){
    const current=++generation.current;setLoading(true);setError('');if(reload)setRefreshing(true);
    try{
      if(reload)await api.refreshEnvironments();
      const value=await api.executionOptions();
      if(current===generation.current)setOptions(value);
    }catch(reason){if(current===generation.current)setError(errorText(reason));}
    finally{if(current===generation.current){setLoading(false);setRefreshing(false);}}
  }
  function close(){if(busyRef.current)return;focusBack.current=true;setOpen(false);setSandbox(false);setDestroy(false);setSelected([]);setTrust(false);setRefreshing(false);trigger.current?.focus();}
  async function submit(){
    if(!options||!candidate||locked)return;
    setBusy(true);setError('');
    try {
      await create({execution:'sandbox',include_untracked:selected,profile_id:candidate.profile_id,
        profile_name:candidate.name,expected_profile_revision:candidate.revision,
        ...(options.requires_trust?{trust_revision:options.project_revision}:{})});
      focusBack.current=true;setOpen(false);setSandbox(false);setSelected([]);
    }catch(reason){setError(errorText(reason));}finally{setBusy(false);}
  }
  async function action(value:'prepare'|'start'|'stop'|'destroy'|'recover'){
    if(!session)return;
    setBusy(true);setError('');
    try{if(value==='prepare')await api.prepareEnvironment(session.session_id);else await api.environmentAction(session.session_id,value);
      await refresh();focusBack.current=true;setOpen(false);setDestroy(false);
    }catch(reason){setError(errorText(reason));}finally{setBusy(false);}
  }
  return <div className={styles.root}>
    <button ref={trigger} className={styles.trigger} disabled={disabled||busy||running} aria-label={`执行位置：${label}`}
      aria-haspopup="dialog" aria-expanded={open} onClick={()=>open?close():setOpen(true)}>
      <Icon name={actual?.kind==='sandbox'?'sandbox':'computer'}/><span>{label}</span><Chevron direction={open?'up':'down'}/>
    </button>
    {actual?.kind==='sandbox'&&<>
      <span className={styles.state} data-ready={actual.state==='ready'}>{({ready:'可用',preparing:'准备中',failed:'准备失败',unavailable:'不可用'})[actual.state]}</span>
      <button type="button" className={styles.results} disabled={disabled||busy} onClick={()=>setReview(true)}><Icon name="changes"/>查看改动</button>
    </>}
    <button ref={managerTrigger} type="button" className={styles.results} disabled={disabled||busy}
      onClick={()=>{setOpen(false);setManaging(true);}}><Icon name="sandbox"/>管理沙箱</button>
    {open&&<div ref={panel} role="dialog" aria-label="选择执行位置" className={styles.panel} aria-busy={busy||refreshing}
      onKeyDown={event=>{keepDialogFocus(event,panel.current);if(event.key==='Escape')close();}}>
      <div className={styles.heading}><strong>{sandbox?candidate?.name:'执行环境'}</strong><div className={styles.headingActions}>
        {!sandbox&&<button onClick={()=>void loadOptions(true)} disabled={busy||refreshing} aria-label="刷新环境配置" title="从配置文件刷新环境"><Icon name="refresh"/></button>}
        <button onClick={close} disabled={busy} aria-label="关闭执行位置"><Icon name="close"/></button></div></div>
      {session&&<p>当前会话使用{label}。更换位置会新建会话。</p>}
      {!session&&locked&&<p role="status">原创建结果未确认。再次发送会核对原请求，环境选择已锁定。</p>}
      {!session&&!locked&&draft?.profile_id&&options&&!options.profiles?.some(p=>p.profile_id===draft.profile_id&&p.revision===draft.expected_profile_revision)&&
        <p role="status">当前选择的配置已变化或移除，请重新选择环境；消息仍保留。</p>}
      {!sandbox&&<>
        <button className={styles.choice} aria-label="此计算机" disabled={busy||locked||!options?.host_available} onClick={async()=>{
          setBusy(true);try{await create({execution:'host',include_untracked:[]});focusBack.current=true;setOpen(false);}
          catch(reason){setError(errorText(reason));}finally{setBusy(false);}}}><Icon name="computer"/><span>此计算机<small>在项目文件夹中工作</small></span></button>
        {options?.profiles?.map(profile=><button key={profile.profile_id} className={styles.choice} aria-label={profile.name}
          disabled={busy||locked||refreshing||!options.sandbox_available} onClick={()=>{setCandidate(profile);setSelected(draft?.include_untracked??[]);setSandbox(true);setError('');}}>
          <Icon name="sandbox"/><span>{profile.name}<small>Docker 沙箱{profile.profile_id===options.default_profile&&<em className={styles.defaultMark}>默认</em>}</small></span></button>)}
        {loading&&<p role="status">{refreshing?'正在刷新环境配置…':'正在检查可用环境…'}</p>}
        {options&&!options.profiles?.length&&!loading&&<p>没有已配置的沙箱环境。</p>}
        {options?.error_code&&<p>{errorText(new ApiError(options.error_code))}</p>}
        {actual?.kind==='sandbox'&&<div className={styles.lifecycle}>
          <p>当前沙箱：{actual.state==='ready'?'可用':'不可用'}{actual.error?`（${errorText(new ApiError(actual.error.code))}）`:''}</p>
          {actual.state==='failed'&&<p>输入准备失败。修正源文件后请新建会话，原输入不会替换。</p>}
          {actual.error?.paths?.length ? <ul aria-label="未传入的文件" className={styles.files}>{actual.error.paths.map(path=><li key={path}><code>{path}</code></li>)}</ul> : null}
          {actual.state!=='ready'&&actual.state!=='failed'&&<button disabled={busy} onClick={()=>void action('prepare')}>核对并继续原准备</button>}
          <button disabled={busy} onClick={()=>void action('start')}>启动原沙箱</button>
          <button disabled={busy} onClick={()=>void action('stop')}>停止沙箱</button>
          <button disabled={busy} onClick={()=>setDestroy(true)}>销毁沙箱…</button>
          {destroy&&<div role="alert"><p>这会删除当前沙箱及其中的文件。请先保留所需产物；会话历史仍会保留。</p>
            <button disabled={busy} onClick={()=>void action('destroy')}>确认销毁沙箱和文件</button><button disabled={busy} onClick={()=>setDestroy(false)}>取消销毁</button></div>}
        </div>}
      </>}
      {sandbox&&options&&<>
        <p>仅选择用于新会话的环境，发送第一条消息后才准备沙箱。</p>
        <p>已跟踪文件使用当前内容。忽略文件不会传入；未跟踪文件由你逐项选择。聊天和记忆仍保存在此计算机。</p>
        <p>网络：{options.network==='none'?'完全禁网':'由 Docker 所在环境的外部策略管理'}。文件保留在沙箱中，不会自动写回项目。</p>
        <fieldset disabled={busy||locked} className={styles.files}><legend>额外传入的未跟踪文件</legend>
          {!options.untracked.length&&<p>没有可选择的文件</p>}
          {options.untracked.map(path=><label key={path}><input type="checkbox" aria-label={path} checked={selected.includes(path)} disabled={options.blocked.includes(path)}
            onChange={event=>setSelected(previous=>event.target.checked?[...previous,path]:previous.filter(item=>item!==path))}/><span>{path}</span>{options.blocked.includes(path)&&<small>凭据文件，不可传入</small>}</label>)}
        </fieldset>
        {options.requires_trust&&<label className={styles.trust}><input type="checkbox" checked={trust} disabled={busy} onChange={event=>setTrust(event.target.checked)}/>我信任此仓库，允许 Agent 执行其中的内容</label>}
        <div className={styles.footer}><button disabled={busy} onClick={()=>setSandbox(false)}>返回</button>
          <button disabled={busy||locked||(options.requires_trust&&!trust)} onClick={()=>void submit()}>{busy?'正在选择…':'使用此环境'}</button></div>
      </>}
      {error&&<p role="alert" className={styles.error}>{error}</p>}
    </div>}
    {review&&session&&<ResultReview key={session.session_id} api={api} sessionId={session.session_id} readOnly={running} onClose={()=>{setReview(false);trigger.current?.focus();}}/>}
    {managing&&<SandboxManager api={api} onClose={()=>{setManaging(false);managerTrigger.current?.focus();}}
      onChanged={async binding=>{if(actual?.binding_id===binding)await refresh();}}/>}
  </div>;
}
