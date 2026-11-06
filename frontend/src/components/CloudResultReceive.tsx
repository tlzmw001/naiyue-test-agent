import { useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { CloudApi } from '../cloudApi';
import type { AnalysisDraft } from '../cloudReceiveTypes';
import type { ResultFile } from '../cloudResultTypes';
import { cloudError } from '../cloudPresentation';
import { useCloudReceive } from '../useCloudReceive';
import styles from './CloudTasks.module.css';
import CloudCodeDelivery from './CloudCodeDelivery';
import CloudFilesDelivery from './CloudFilesDelivery';

export default function CloudResultReceive({ api, sid, bid, rid, inputId, files, active, branchAvailable = false, onAnalysisDraft, fileActions, resumeOnly = false }: {
  api: CloudApi; sid: string; bid: string; rid: string; inputId: string | null; files: ResultFile[]; active: boolean; branchAvailable?: boolean;
  onAnalysisDraft?: (value: AnalysisDraft) => boolean | void;
  fileActions?: (item: ResultFile) => ReactNode;
  resumeOnly?: boolean;
}) {
  const selectionKey = `naiyue.cloud-report-selection.${api.projectId}.${sid}.${bid}.${rid}`;
  const [selected, setSelected] = useState<string[]>(() => {
    try { const saved: unknown = JSON.parse(sessionStorage.getItem(selectionKey) ?? '[]');
      return Array.isArray(saved) ? saved.filter((id): id is string => typeof id === 'string' && files.some(file => file.role === 'artifact' && file.artifact.artifact_id === id)) : [];
    } catch { console.warn('cloud_report_selection_read_failed'); return []; }
  }); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [selectionLocked, setSelectionLocked] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [analyzing, setAnalyzing] = useState(false); const materials = useCloudReceive(api, sid, bid, rid, inputId);
  const current = useRef({ alive: true, active }); current.current.active = active;
  useLayoutEffect(() => { current.current.alive = true; return () => { current.current.alive = false; }; }, []);
  async function cleanupCache() {
    if (!active || cleaning || materials.busy || analyzing || selectionLocked) return;
    setCleaning(true); setError(''); setNotice('');
    try {
      const result = await api.cleanupReceiveCache(sid);
      if (current.current.alive && current.current.active) setNotice(result.removed_files
        ? `已清理 ${result.removed_files} 个缓存文件，释放 ${result.freed_bytes} 字节。`
        : '没有可清理的未引用缓存；仍被引用的材料及交付日志已保留。');
    } catch (failure) { if (current.current.alive) { console.warn('cloud_cache_cleanup_failed'); setError(cloudError(failure)); } }
    finally { if (current.current.alive) setCleaning(false); }
  }
  async function analyze() {
    if (materials.busy || analyzing || cleaning || !active) return;
    setAnalyzing(true); setError(''); setNotice('');
    try {
      const record = await materials.ensure(inputId !== null, selected);
      if (!current.current.alive || !current.current.active) return;
      const value = await api.analysisDraft(sid, record.receive_id);
      if (value.local_project_id !== api.projectId || value.local_session_id !== sid || value.receive_id !== record.receive_id ||
        value.material_refs.some(item => item.receive_id !== record.receive_id || item.result_id !== rid)) throw new Error('cloud_receive_identity_mismatch');
      if (current.current.alive && current.current.active) {
        const accepted = onAnalysisDraft?.(value);
        setNotice(accepted === false ? '主会话已变化，请回到来源会话后重新加入。' : '已放入主会话输入框，请确认后发送。');
      }
    } catch (failure) { if (current.current.alive) { console.warn('cloud_analysis_prepare_failed'); setError(cloudError(failure)); } }
    finally { if (current.current.alive) setAnalyzing(false); }
  }
  const artifacts = files.filter(item => item.role === 'artifact');
  const selectionDisabled = !active || materials.busy || analyzing || selectionLocked || cleaning;
  function selectArtifacts(next: string[]) {
    setSelected(next);
    try { sessionStorage.setItem(selectionKey, JSON.stringify(next)); }
    catch { console.warn('cloud_report_selection_save_failed'); setError('报告勾选未能缓存，请保持当前窗口并及时保存。'); }
  }
  const delivery = <CloudFilesDelivery api={api} sid={sid} resultKey={`${bid}.${rid}`} active={active && !materials.busy && !analyzing && !cleaning}
    selected={selected} onSelectionLock={setSelectionLocked} prepareMaterials={(refresh, id) => materials.ensure(false, selected, refresh, id)} resumeOnly={resumeOnly} />;
  if (resumeOnly) return delivery;
  return <section aria-label="结果取回">
    <strong>报告与产物</strong>
    {!artifacts.length && <p className={styles.note}>此结果没有报告等产物。</p>}
    {!!artifacts.length && <div className={styles.resultFileHeader}>
      <label className={styles.check}><input type="checkbox" disabled={selectionDisabled} checked={selected.length === artifacts.length}
        ref={input => { if (input) input.indeterminate = selected.length > 0 && selected.length < artifacts.length; }}
        onChange={event => selectArtifacts(event.target.checked ? artifacts.map(item => item.artifact.artifact_id) : [])} />全选本轮产物</label>
      <span className={styles.note} aria-live="polite">已选 {selected.length} 个文件</span>
    </div>}
    {artifacts.map(item => <div key={item.artifact.artifact_id} className={styles.resultFile}>
      <label className={styles.check}><input type="checkbox" disabled={selectionDisabled} checked={selected.includes(item.artifact.artifact_id)} onChange={event => {
        const id = item.artifact.artifact_id;
        selectArtifacts(event.target.checked ? [...selected, id] : selected.filter(value => value !== id));
      }} />{item.artifact.relative_name} · {item.artifact.size} 字节</label>{fileActions?.(item)}
    </div>)}
    {onAnalysisDraft && <div className={styles.actions}><button disabled={!active || materials.busy || analyzing || cleaning || inputId === null && !selected.length} onClick={() => void analyze()}>交给主 Agent 分析</button></div>}
    {delivery}
    <div className={styles.actions}><button disabled={!active || materials.busy || analyzing || selectionLocked || cleaning} onClick={() => void cleanupCache()}>{cleaning ? '正在清理缓存…' : '清理未引用缓存'}</button></div>
    {materials.busy && <p role="status" className={styles.note}>正在准备并核对固定材料{materials.record && ` · ${materials.record.files.reduce((n, f) => n + f.offset, 0)} 字节`}</p>}
    {(error || materials.error) && <div className={styles.actions}><button disabled={materials.busy || analyzing || cleaning || !active} onClick={() => { void materials.ensure(inputId !== null, selected, true).then(() => { setError(''); setNotice('材料已重新核对，可再次交给主 Agent 分析。'); }).catch(failure => { console.warn('cloud_material_refresh_failed'); setError(cloudError(failure)); }); }}>重新核对分析材料</button></div>}
    {notice && <p role="status" className={styles.note}>{notice}</p>}{(error || materials.error) && <p role="alert" className={styles.error}>{error || materials.error}</p>}
    {inputId !== null && <CloudCodeDelivery api={api} sid={sid} resultKey={`${bid}.${rid}`} active={active && !materials.busy && !analyzing && !cleaning}
      branchAvailable={branchAvailable} prepareMaterials={(refresh, id) => materials.ensure(true, [], refresh, id)} />}
  </section>;
}
