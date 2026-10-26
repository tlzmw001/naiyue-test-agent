import { useEffect,useRef,useState } from 'react';
import { createPortal } from 'react-dom';
import { ApiError,errorText,WebApi } from '../api';
import type { ResultOptions,SandboxResult,ResultDiff } from '../resultTypes';
import { keepDialogFocus } from '../dialogFocus';
import styles from './ResultReview.module.css';
import ResultDelivery from './ResultDelivery';
import { Icon } from './Icons';

const labels:Record<string,string>={A:'新增',D:'删除',M:'修改',R:'重命名',T:'类型变化',C:'复制'};
export default function ResultReview({api,sessionId,onClose,readOnly=false}:{api:WebApi;sessionId:string;onClose:()=>void;readOnly?:boolean}) {
  const [items,setItems]=useState<SandboxResult[]>([]),[current,setCurrent]=useState<SandboxResult|null>(null);
  const [options,setOptions]=useState<ResultOptions|null>(null),[selected,setSelected]=useState<string[]>([]);
  const [attempt,setAttempt]=useState<{id:string;include:string[]}|null>(null),[capturing,setBusy]=useState(false),[delivering,setDelivering]=useState(false);
  const busy=capturing||delivering;
  const [loading,setLoading]=useState(true),[error,setError]=useState(''),[optionError,setOptionError]=useState('');
  const [diff,setDiff]=useState<ResultDiff|null>(null),[file,setFile]=useState(''),[reading,setReading]=useState(false);
  const panel=useRef<HTMLDivElement>(null),generation=useRef(0);
  useEffect(()=>{
    let cancelled=false;const previous=document.activeElement as HTMLElement|null;
    panel.current?.querySelector<HTMLButtonElement>('button')?.focus();
    api.results(sessionId).then(value=>{if(!cancelled){
      setItems(previous=>[...previous,...value.items.filter(item=>!previous.some(row=>row.result_id===item.result_id))]);
      setCurrent(previous=>previous??value.items.find(item=>item.state==='ready'&&item.receipt)??null);
    }})
      .catch(reason=>{if(!cancelled)setError(errorText(reason));}).finally(()=>{if(!cancelled)setLoading(false);});
    api.resultOptions(sessionId).then(value=>{if(!cancelled)setOptions(value);})
      .catch(reason=>{if(!cancelled)setOptionError(errorText(reason));});
    return()=>{cancelled=true;generation.current++;previous?.focus();};
  },[api,sessionId]);
  async function capture(){
    if(readOnly)return;
    panel.current?.focus();
    const request=attempt??{id:crypto.randomUUID().replaceAll('-',''),include:[...selected]};
    setAttempt(request);setBusy(true);setError('');
    try{
      const result=await api.captureResult(sessionId,request.id,request.include);
      setCurrent(result);setDiff(null);setFile('');generation.current++;
      setItems(previous=>[result,...previous.filter(item=>item.result_id!==result.result_id)]);
      setAttempt(null);setSelected([]);
    }catch(reason){setError(errorText(reason));}
    finally{setBusy(false);}
  }
  function select(result:SandboxResult){
    generation.current++;setCurrent(result);setDiff(null);setFile('');setReading(false);setError('');
    setAttempt(result.state==='ready'?null:{id:result.result_id,include:result.include_untracked});
  }
  async function viewFile(path:string){
    if(!current)return;
    const version=++generation.current;setFile(path);setReading(true);setDiff(null);setError('');
    try{const value=await api.resultDiff(sessionId,current.result_id,path);if(generation.current===version)setDiff(value);}
    catch(reason){if(generation.current===version)setError(errorText(reason));}
    finally{if(generation.current===version)setReading(false);}
  }
  return createPortal(<div className={styles.backdrop}>
    <div ref={panel} role="dialog" tabIndex={-1} aria-modal="true" aria-label="沙箱结果" className={styles.panel} aria-busy={busy}
      onKeyDown={event=>{keepDialogFocus(event,panel.current);if(event.key==='Escape'&&!busy)onClose();}}>
      <header className={styles.heading}><div><h2>查看改动</h2><p>取回结果 → 审查差异 → 在本机处理</p></div>
        <button disabled={busy} onClick={onClose} aria-label="关闭沙箱结果"><Icon name="close"/></button></header>
      <div className={styles.scroll}>
        {readOnly&&<p className={styles.notice} role="status">任务运行中，可以查看已保存差异；结束后再取回或处理结果。</p>}
        <section className={styles.capture} aria-label="取回结果">
          <div><h3>取回结果</h3><p>保存当前改动供你审查，不会自动改写本机项目。</p></div>
          {options&&<details><summary>选择额外取回的文件（{selected.length}）</summary>
            <p>已跟踪文件使用当前内容。忽略文件不传，未跟踪文件由你选择。</p>
            <fieldset disabled={busy||readOnly||attempt!==null} className={styles.selection}><legend>未跟踪文件</legend>
              {!options.untracked.length&&<p>没有可选择的文件</p>}
              {options.untracked.map(path=><label key={path}><input type="checkbox" aria-label={path} checked={selected.includes(path)} disabled={options.blocked.includes(path)}
                onChange={event=>setSelected(previous=>event.target.checked?[...previous,path]:previous.filter(p=>p!==path))}/><span>{path}</span>{options.blocked.includes(path)&&<small>凭据文件，不可取回</small>}</label>)}
            </fieldset></details>}
          {optionError&&<p className={styles.notice}>当前沙箱无法取回：{optionError}。已保存的结果仍可查看。</p>}
          <div className={styles.actions}><button className={styles.primary} disabled={busy||readOnly||(!options&&!attempt)} onClick={()=>void capture()}>
            {capturing?'正在取回和验证…':delivering?'交付处理中…':attempt?'核对原取回请求':'取回当前结果'}</button>
            {attempt&&<button disabled={busy||readOnly} onClick={()=>{setAttempt(null);setSelected([]);setError('');}}>重新选择取回</button>}</div>
          {attempt&&<p className={styles.notice}>请求 {attempt.id.slice(0,8)}：核对会继续取回同一份结果，不重新捕获沙箱文件。</p>}
        </section>
        <section className={styles.history} aria-label="已取回的结果">
          <h3>已保存的结果</h3>
          {loading&&<p role="status">正在读取取回记录…</p>}
          {!loading&&!items.length&&<p>还没有取回结果</p>}
          <div className={styles.records}>{items.map(item=><button key={item.result_id} disabled={busy} aria-pressed={current?.result_id===item.result_id}
            onClick={()=>select(item)} aria-label={`查看结果 ${item.result_id.slice(0,8)}`}>
            <code>{item.result_id.slice(0,8)}</code><span>{item.state==='ready'?`${item.files.length} 个文件`:'待核对'}</span></button>)}</div>
        </section>
        {current?.receipt&&<section className={styles.review} aria-label="输入到输出的差异">
          <div className={styles.basis}><h3>审查改动 · {current.files.length} 个文件</h3><code>{current.receipt.input_snapshot.slice(0,8)} → {current.receipt.output_commit.slice(0,8)}</code>
            <span>输入快照 → 沙箱结果</span></div>
          {current.state!=='ready'?<p>结果尚未完成本机验证，请核对原取回请求。</p>:<>
            {!current.files.length&&<p>结果与输入快照相同，没有文件改动。</p>}
            {!!current.warnings.length&&<p role="note" className={styles.notice}>存在大小写冲突：{current.warnings.flatMap(w=>w.paths).join('、')}。当前只审查，不检出文件。</p>}
            {!!current.files.length&&<div className={styles.workspace}>
              <nav aria-label="结果文件" className={styles.files}>{current.files.map(item=><button key={item.path} disabled={busy} aria-pressed={file===item.path} onClick={()=>void viewFile(item.path)}>
                <span className={styles.status}>{labels[item.status]??item.status}</span><span>{item.path}{item.status==='R'&&<small>原路径：{item.old_path}</small>}</span>
                {(item.symlink||item.old_mode!==item.new_mode||item.attributes)&&<small>{item.symlink?'符号链接':item.attributes?'Git 属性':'模式变化'}</small>}</button>)}</nav>
              <div className={styles.detail}>
                {!file&&<p>选择一个文件查看差异</p>}{reading&&<p role="status">正在读取差异…</p>}
                {diff&&<><div className={styles.fileHeading}><strong>{diff.path}</strong><small>{diff.old_mode} → {diff.new_mode}</small></div>
                  {diff.binary?<p>二进制文件，内容已保存在结果中，不提供文本差异。</p>:<pre tabIndex={0} role="region" aria-label="结果差异" className={styles.diff}>
                    {diff.patch?.match(/[^\n]*\n|[^\n]+$/g)?.map((line,index)=><span key={index} data-change={line.startsWith('+')?'added':line.startsWith('-')?'removed':undefined}>{line}</span>)}</pre>}</>}
              </div></div>}
            {!!current.receipt.excluded.length&&<details><summary>未纳入结果的文件（{current.receipt.excluded.length}）</summary><ul>{current.receipt.excluded.map(item=><li key={item.path}><code>{item.path}</code>（{item.reason==='ignored'?'忽略项':'未选择'}）</li>)}</ul></details>}
          </>}
        </section>}
        {error&&<p role="alert" className={styles.error}>{error}</p>}
      </div>
      {current?.receipt&&current.state==='ready'&&<ResultDelivery key={current.result_id} api={api} sessionId={sessionId} resultId={current.result_id}
          commit={current.receipt.output_commit} disabled={readOnly} onBusy={value=>{setDelivering(value);panel.current?.focus();}} onDiscard={()=>{
            setCurrent(previous=>previous?{...previous,discarded:true}:previous);
            setItems(previous=>previous.map(item=>item.result_id===current.result_id?{...item,discarded:true}:item));
          }}/>}
      <footer className={styles.footer}>取回只保存结果。建分支、合入和丢弃都需要单独确认。</footer>
    </div>
  </div>,document.body);
}
