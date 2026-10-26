import { useEffect, useRef, useState } from 'react';
import { ApiError, errorText, WebApi } from '../api';
import type { ExecutionOptions, SessionCreation, SessionView } from '../types';
import { keepDialogFocus } from '../dialogFocus';
import styles from './ExecutionSelector.module.css';
import ResultReview from './ResultReview';
import { Chevron, Icon } from './Icons';

export default function ExecutionSelector({ api, session, disabled, modelId, create, refresh, running = false }: {
  api: WebApi; modelId: string; session: SessionView | null; disabled: boolean;
  create: (choice: SessionCreation) => Promise<void>; refresh: () => Promise<void>;
  running?: boolean;
}) {
  const [open,setOpen]=useState(false),[sandbox,setSandbox]=useState(false),[busy,setBusy]=useState(false);
  const [options,setOptions]=useState<ExecutionOptions|null>(null),[error,setError]=useState('');
  const [selected,setSelected]=useState<string[]>([]),[trust,setTrust]=useState(false),[destroy,setDestroy]=useState(false);
  const [attempt,setAttempt]=useState<SessionCreation|null>(null);
  const [review,setReview]=useState(false);
  const trigger=useRef<HTMLButtonElement>(null),panel=useRef<HTMLDivElement>(null);
  const busyRef=useRef(busy);busyRef.current=busy;
  const trustRequest=useRef(crypto.randomUUID());
  const actual=session?.execution, label=actual?.kind==='sandbox' ? 'Docker 沙箱' : '此计算机';
  useEffect(()=>{
    if(!open)return;
    let cancelled=false;
    setOptions(null);setError('');
    api.executionOptions().then(value=>{if(!cancelled)setOptions(value);})
      .catch(reason=>{if(!cancelled)setError(errorText(reason));});
    return ()=>{cancelled=true;};
  },[open,api]);
  useEffect(()=>{
    if(!open)return;
    panel.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const dismiss=(event:PointerEvent)=>{if(!panel.current?.contains(event.target as Node)&&!trigger.current?.contains(event.target as Node))close();};
    document.addEventListener('pointerdown',dismiss);
    return()=>document.removeEventListener('pointerdown',dismiss);
  },[open,sandbox]);
  function close(){if(busyRef.current)return;setOpen(false);setSandbox(false);setDestroy(false);setAttempt(null);setSelected([]);setTrust(false);trigger.current?.focus();}
  async function submit(){
    if(!options)return;
    setBusy(true);setError('');
    try {
      if(options.requires_trust){await api.trustGit(options.project_revision,trustRequest.current);setOptions({...options,requires_trust:false});}
      const choice=attempt??{execution:'sandbox',model_id:modelId,include_untracked:selected,requestId:crypto.randomUUID()} as SessionCreation;
      setAttempt(choice);await create(choice);setOpen(false);setSandbox(false);setAttempt(null);setSelected([]);
    }catch(reason){setError(errorText(reason));}finally{setBusy(false);}
  }
  async function action(value:'prepare'|'start'|'stop'|'destroy'|'recover'){
    if(!session)return;
    setBusy(true);setError('');
    try{if(value==='prepare')await api.prepareEnvironment(session.session_id);else await api.environmentAction(session.session_id,value);
      await refresh();setOpen(false);setDestroy(false);
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
    {open&&<div ref={panel} role="dialog" aria-label="选择执行位置" className={styles.panel} aria-busy={busy}
      onKeyDown={event=>{keepDialogFocus(event,panel.current);if(event.key==='Escape')close();}}>
      <div className={styles.heading}><strong>{sandbox?'准备沙箱输入':'执行位置'}</strong><button onClick={close} disabled={busy} aria-label="关闭执行位置"><Icon name="close"/></button></div>
      {session&&<p>当前会话使用{label}。更换位置会新建会话。</p>}
      {!sandbox&&<>
        <button className={styles.choice} disabled={busy||!options?.host_available} onClick={async()=>{
          setBusy(true);try{await create({execution:'host',model_id:modelId,include_untracked:[],requestId:crypto.randomUUID()});setOpen(false);}
          catch(reason){setError(errorText(reason));}finally{setBusy(false);}}}><Icon name="computer"/><span>此计算机<small>在项目文件夹中工作</small></span></button>
        <button className={styles.choice} aria-label="Docker 沙箱" disabled={busy||!options?.sandbox_available} onClick={()=>{setSandbox(true);setError('');}}><Icon name="sandbox"/><span>Docker 沙箱<small>在独立环境中工作</small></span></button>
        {!options&&!error&&<p role="status">正在检查可用位置…</p>}
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
        <p>已跟踪文件使用当前内容。忽略文件不会传入；未跟踪文件由你逐项选择。聊天和记忆仍保存在此计算机。</p>
        <p>网络：{options.network==='none'?'完全禁网':'由 Docker 所在环境的外部策略管理'}。文件保留在沙箱中，不会自动写回项目。</p>
        <fieldset disabled={busy||attempt!==null} className={styles.files}><legend>额外传入的未跟踪文件</legend>
          {!options.untracked.length&&<p>没有可选择的文件</p>}
          {options.untracked.map(path=><label key={path}><input type="checkbox" aria-label={path} checked={selected.includes(path)} disabled={options.blocked.includes(path)}
            onChange={event=>setSelected(previous=>event.target.checked?[...previous,path]:previous.filter(item=>item!==path))}/><span>{path}</span>{options.blocked.includes(path)&&<small>凭据文件，不可传入</small>}</label>)}
        </fieldset>
        {options.requires_trust&&<label className={styles.trust}><input type="checkbox" checked={trust} disabled={busy} onChange={event=>setTrust(event.target.checked)}/>我信任此仓库，允许 Agent 执行其中的内容</label>}
        <div className={styles.footer}><button disabled={busy||attempt!==null} onClick={()=>setSandbox(false)}>返回</button>
          <button disabled={busy||(options.requires_trust&&!trust)} onClick={()=>void submit()}>{busy?'正在准备…':attempt?'核对原创建请求':'创建沙箱会话'}</button></div>
      </>}
      {error&&<p role="alert" className={styles.error}>{error}</p>}
    </div>}
    {review&&session&&<ResultReview key={session.session_id} api={api} sessionId={session.session_id} readOnly={running} onClose={()=>{setReview(false);trigger.current?.focus();}}/>}
  </div>;
}
