import {useEffect,useState} from 'react';
import {errorText,WebApi} from '../api';
import type {DeliveryAction,DeliveryCommand,DeliveryOperation,DeliveryOptions} from '../deliveryTypes';
import styles from './ResultReview.module.css';

const labels:Record<DeliveryAction,string>={branch:'创建本机分支',merge:'合入当前分支',discard:'丢弃任务引用'};
const confirmations:Record<DeliveryAction,string>={branch:'确认创建分支',merge:'确认合入',discard:'确认丢弃引用'};
const reasons:Record<string,string>={
  git_delivery_dirty:'本机有未保存到提交的改动，请先在本机处理。',git_delivery_input_wip:'输入包含原有改动，可创建结果分支审查。',
  git_delivery_head_changed:'本机分支已有新提交，需要在本机审查和合入。',git_delivery_no_worktree:'这个项目没有本机工作目录。',
  git_delivery_nonlinear:'任务包含非线性历史，需要在本机处理。',
  git_delivery_unfinished_git:'本机存在尚未结束的 Git 操作，请先处理。',git_delivery_untracked_collision:'未跟踪或忽略文件与结果冲突，请先在本机处理。',
};
const pending=(row:DeliveryOperation)=>['preparing','executing','unknown'].includes(row.state);
export default function ResultDelivery({api,sessionId,resultId,commit,onBusy,onDiscard,disabled=false}:{api:WebApi;sessionId:string;resultId:string;commit:string;onBusy:(value:boolean)=>void;onDiscard:()=>void;disabled?:boolean}) {
  const [options,setOptions]=useState<DeliveryOptions|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [action,setAction]=useState<DeliveryAction|null>(null),[branch,setBranch]=useState('');
  const [attempt,setAttempt]=useState<DeliveryCommand|null>(null),[ack,setAck]=useState<string|null>(null),[message,setMessage]=useState('');
  const blocked=busy||disabled;
  useEffect(()=>{
    let cancelled=false;
    api.deliveryOptions(sessionId,resultId).then(value=>{if(!cancelled)setOptions(value);})
      .catch(reason=>{if(!cancelled)setError(errorText(reason));});
    return()=>{cancelled=true;};
  },[api,sessionId,resultId]);
  async function refresh(){
    const value=await api.deliveryOptions(sessionId,resultId);setOptions(value);
    setAttempt(previous=>previous&&value.operations.some(row=>row.operation_id===previous.operation_id&&!pending(row))?null:previous);
    if(value.discarded)onDiscard();
  }
  async function execute(command:DeliveryCommand){
    if(disabled)return;
    setBusy(true);onBusy(true);setAttempt(command);setError('');setMessage('');
    try{
      const row=await api.deliver(sessionId,resultId,command);
      if(['completed','failed','acknowledged'].includes(row.state))setAttempt(null);
      setMessage(row.state==='completed'?'交付已完成。':row.state==='failed'?'本次交付未执行，请核对原因。':'交付结果尚未确认，请核对原交付。');
      setAction(null);await refresh();
    }catch(reason){setError(errorText(reason));try{await refresh();}catch(refreshError){setError(errorText(reason)+'；记录读取也未确认：'+errorText(refreshError));}}
    finally{setBusy(false);onBusy(false);}
  }
  async function acknowledge(operation:string){
    if(disabled)return;
    setBusy(true);onBusy(true);setError('');
    try{await api.acknowledgeDelivery(sessionId,resultId,operation);setAck(null);setAttempt(null);await refresh();setMessage('已记录人工核对，原执行结果仍保留。');}
    catch(reason){setError(errorText(reason));}finally{setBusy(false);onBusy(false);}
  }
  const unsettled=options?.operations.find(pending),request=attempt??unsettled?.command;
  function confirm(){
    if(!action)return;
    const command:DeliveryCommand={operation_id:crypto.randomUUID().replaceAll('-',''),action};
    if(action==='branch')command.branch=branch.trim();
    void execute(command);
  }
  return <section className={styles.delivery} aria-label="交付审查后的结果" aria-busy={busy}>
    <h3>在本机处理</h3><p>确认差异后，选择如何保留这份结果。</p>
    <details><summary>结果提交</summary><code className={styles.commit}>{commit}</code></details>
    {!options&&!error&&<p role="status">正在核对本机交付条件…</p>}
    {options?.discarded?<p>任务引用已丢弃，差异仍可查看。用户分支和工作文件保留。</p>:options&&<>
      <div className={styles.actions}>{(Object.keys(labels) as DeliveryAction[]).map(name=><button key={name} disabled={blocked||!!request||!options.actions[name].available}
        aria-pressed={action===name} onClick={()=>{setAction(name);setError('');setMessage('');}}>{labels[name]}</button>)}</div>
      {(Object.keys(labels) as DeliveryAction[]).filter(name=>!options.actions[name].available).map(name=><p key={name} className={styles.notice}>{labels[name]}：{reasons[options.actions[name].reason??'']??'当前条件不支持，请在本机核对。'}</p>)}
    </>}
    {action&&!request&&<div className={styles.confirmation}>
      {action==='branch'&&<p>仅新建分支，不切换当前分支。结果可能包含输入时已有的改动，请继续审查。</p>}
      {action==='merge'&&<p>将已审查的任务提交依次合入当前分支。中断或冲突会保留现场，需要人工处理。</p>}
      {action==='discard'&&<p>仅删除应用的任务引用，不删除用户分支、工作文件或已保存差异。</p>}
      {action==='branch'&&<label className={styles.field}>分支名称<input value={branch} onChange={event=>setBranch(event.target.value)} disabled={blocked} maxLength={200}/></label>}
      <div className={styles.actions}><button onClick={confirm} disabled={blocked||(action==='branch'&&!branch.trim())}>{confirmations[action]}</button>
        <button disabled={blocked} onClick={()=>setAction(null)}>取消</button></div>
    </div>}
    {request&&<div className={styles.confirmation}><p>请求 {request.operation_id.slice(0,8)} · {labels[request.action]}。可能已产生部分结果，核对不会再次执行。</p>
      <div className={styles.actions}><button disabled={blocked} onClick={()=>void execute(request)}>{busy?'正在核对…':'核对原交付'}</button>
        {unsettled&&<button disabled={blocked} onClick={()=>setAck(unsettled.operation_id)}>已在本机人工核对</button>}</div></div>}
    {ack&&<div className={styles.confirmation}><p>请先在本机核对 Git 状态和目标分支。此操作只结束待核对状态，不会撤销文件或提交，也不代表原交付成功。</p>
      <div className={styles.actions}><button disabled={blocked} onClick={()=>void acknowledge(ack)}>确认结束核对</button><button disabled={blocked} onClick={()=>setAck(null)}>继续保留待核对</button></div></div>}
    {message&&<p role="status">{message}</p>}{error&&<p role="alert" className={styles.error}>{error}</p>}
    {!!options?.operations.length&&<details><summary>交付记录（{options.operations.length}）</summary><ul>{options.operations.map(row=><li key={row.operation_id}><code>{row.operation_id.slice(0,8)}</code> · {labels[row.command.action]} · {({completed:'已完成',failed:'未执行',unknown:'待核对',preparing:'准备中',executing:'待核对',acknowledged:'已人工核对'} as Record<string,string>)[row.state]??row.state}</li>)}</ul></details>}
  </section>;
}
