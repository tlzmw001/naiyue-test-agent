import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ApiError, errorText, WebApi } from '../api';
import type { ManagedSandbox, SandboxAction, SandboxManagement } from '../sandboxManagementTypes';
import { keepDialogFocus } from '../dialogFocus';
import { Icon } from './Icons';
import styles from './SandboxManager.module.css';

type Attempt = { binding: string; action: SandboxAction; requestId: string };
const states: Record<string,string> = { running:'运行中',stopped:'已停止',creating:'准备中',starting:'启动中',unknown:'待核对' };

export default function SandboxManager({api,onClose,onChanged}: {
  api: WebApi; onClose: () => void; onChanged: (binding: string) => Promise<void>;
}) {
  const [data,setData]=useState<SandboxManagement|null>(null),[error,setError]=useState('');
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false);
  const [destroy,setDestroy]=useState<ManagedSandbox|null>(null),[attempt,setAttempt]=useState<Attempt|null>(null);
  const panel=useRef<HTMLDivElement>(null),confirm=useRef<HTMLDivElement>(null),inFlight=useRef(false),generation=useRef(0);
  const destroyButton=useRef<HTMLButtonElement|null>(null);
  useEffect(()=>{
    panel.current?.focus();void load();
    return()=>{generation.current++;};
  },[api]);
  useEffect(()=>{if(destroy)confirm.current?.querySelector<HTMLButtonElement>('button')?.focus();},[destroy]);
  async function load(){
    const version=++generation.current;setLoading(true);setError('');
    try{const view=await api.sandboxManagement();if(version===generation.current)setData(view);}
    catch(reason){if(version===generation.current)setError(errorText(reason));}
    finally{if(version===generation.current)setLoading(false);}
  }
  async function act(binding:string,action:SandboxAction,original?:Attempt){
    if(inFlight.current)return;
    const command=original??{binding,action,requestId:crypto.randomUUID()};
    inFlight.current=true;setBusy(true);setError('');
    try{
      await api.manageSandbox(command.binding,command.action,command.action==='destroy',command.requestId);
      setAttempt(null);setDestroy(null);await load();
      try{await onChanged(binding);}catch(reason){setError(`操作已完成，会话状态刷新失败：${errorText(reason)}`);}
    }catch(reason){
      setDestroy(null);setError(errorText(reason));setAttempt(reason instanceof ApiError?null:command);
    }finally{inFlight.current=false;setBusy(false);panel.current?.focus();}
  }
  function cancelDestroy(){setDestroy(null);destroyButton.current?.focus();}
  return createPortal(<div className={styles.backdrop}>
    <div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label="管理沙箱" className={styles.panel} aria-busy={busy}
      onKeyDown={event=>{
        keepDialogFocus(event,destroy?confirm.current:panel.current);
        if(event.key==='Escape'&&!busy){if(destroy)cancelDestroy();else onClose();}
      }}>
      <header className={styles.heading}><div><h2>管理沙箱</h2><p>此服务中所有项目的沙箱 · 登记状态</p></div>
        <button disabled={busy} aria-label="关闭沙箱管理" onClick={onClose}><Icon name="close"/></button></header>
      <div className={styles.scroll}>
        {data&&<><section aria-label="沙箱额度" className={styles.usage}>
          <div><span>运行占用</span><strong>{data.usage.running} / {data.policy.max_running}</strong></div>
          <div><span>保留数量</span><strong>{data.usage.retained} / {data.policy.max_retained}</strong></div>
        </section><p className={styles.note}>{data.policy.idle_stop_seconds>0?`空闲 ${data.policy.idle_stop_seconds/60} 分钟后自动停止，保留文件。`:'已关闭空闲自动停止。'}任务、待审批和未确认操作会阻止自动停止。</p></>}
        <div className={styles.toolbar}><p>停止释放运行额度；销毁释放保留额度。</p>
          <button disabled={busy||loading||!!attempt} aria-label="刷新沙箱列表" onClick={()=>void load()}><Icon name="refresh"/>刷新</button></div>
        {loading&&<p role="status">正在读取沙箱列表…</p>}
        {data&&!data.items.length&&!loading&&<p className={styles.empty}>还没有保留的沙箱</p>}
        <div className={styles.rows}>{data?.items.map(row=><article key={row.binding_id} aria-label={`沙箱 ${row.binding_id.slice(0,8)}`}>
          <div className={styles.rowHeading}><div><strong>{row.project_name??row.project_id}</strong><span>{row.profile_name??'Docker 沙箱'}</span></div>
            <span className={styles.state} data-state={row.lifecycle}>{states[row.lifecycle]??'待核对'}</span></div>
          <p className={styles.identity}>绑定 {row.binding_id.slice(0,8)} · 会话 {row.session_id.slice(0,8)}</p>
          {row.last_used_at!==null&&<p>上次使用 {new Date(row.last_used_at*1000).toLocaleString()}</p>}
          {row.busy&&<p role="status">正在使用，暂时不能管理此沙箱。</p>}
          {row.unsettled&&<p>存在未结束或未确认的后台操作；请先核对或停止任务。</p>}
          {row.error_code&&<p className={styles.error}>{errorText(new ApiError(row.error_code))}</p>}
          <div className={styles.actions}>
            <button disabled={busy||!!attempt||row.busy||row.lifecycle!=='stopped'} onClick={()=>void act(row.binding_id,'start')}>启动</button>
            <button disabled={busy||!!attempt||row.busy||row.unsettled||row.lifecycle!=='running'} onClick={()=>void act(row.binding_id,'stop')}>停止</button>
            <button disabled={busy||!!attempt||row.busy} onClick={()=>void act(row.binding_id,'recover')}>核对原资源</button>
            <button className={styles.danger} disabled={busy||!!attempt||row.busy} onClick={event=>{destroyButton.current=event.currentTarget;setDestroy(row);}}>销毁…</button>
          </div>
        </article>)}</div>
        {attempt&&<div className={styles.retry} role="status"><p>响应未确认，保留原操作请求。核对不会创建替代沙箱。</p>
          <button disabled={busy} onClick={()=>void act(attempt.binding,attempt.action,attempt)}>核对原操作</button></div>}
        {error&&<p role="alert" className={styles.error}>{error}</p>}
      </div>
      <footer>停止和服务退出会保留卷。数量上限不限制磁盘字节；不再需要的文件请明确销毁。</footer>
      {destroy&&<div className={styles.confirmBackdrop}><div ref={confirm} role="alertdialog" aria-modal="true" aria-label="确认销毁沙箱" className={styles.confirm}>
        <h3>销毁 {destroy.profile_name??'Docker 沙箱'}？</h3><p>{destroy.project_name??destroy.project_id} · {destroy.binding_id.slice(0,8)}</p>
        <p>将删除原容器、代码卷和状态卷，其中的文件无法恢复；未确认的进程也会被移除。会话历史和已取回的结果保留。</p>
        <div className={styles.actions}><button disabled={busy} onClick={cancelDestroy}>取消</button>
          <button className={styles.danger} disabled={busy||!!attempt} onClick={()=>void act(destroy.binding_id,'destroy')}>确认销毁沙箱和文件</button></div>
      </div></div>}
    </div>
  </div>,document.body);
}
