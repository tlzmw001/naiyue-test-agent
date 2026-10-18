import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent, KeyboardEventHandler, RefObject } from 'react';
import { errorText } from '../api';
import type { WebApi } from '../api';
import { commands } from '../commands';
import type { SkillCatalogView } from '../types';
import styles from './Composer.module.css';

export default function ComposerEditor({ text, setText, editor, onKeyDown, api, sessionId, readEnabled,
  compactDisabled, controlsDisabled, menuOpen }: {
  text: string; setText: (value: string) => void; editor: RefObject<HTMLTextAreaElement | null>;
  onKeyDown: KeyboardEventHandler<HTMLTextAreaElement>; api: WebApi; sessionId: string | null;
  readEnabled: boolean; compactDisabled: boolean; controlsDisabled: boolean; menuOpen: boolean;
}) {
  const [activeSession, setActiveSession] = useState<string | null | undefined>();
  const [composing, setComposing] = useState(false);
  const [selected, setSelected] = useState('');
  const [catalog, setCatalog] = useState<SkillCatalogView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const panel = useRef<HTMLElement>(null);
  const listId = useId();
  const open = activeSession === sessionId && /^\/(?:[a-z0-9][a-z0-9_:-]*)?$/i.test(text) && !composing && !menuOpen;
  // Exact built-ins need no skill lookup; Enter retains their original execution path.
  const builtin = commands.some(item => `/${item.name}` === text);
  const loadSkills = open && !builtin;
  useEffect(() => {
    let disposed = false;
    setCatalog(null); setError(''); setLoading(loadSkills);
    if (loadSkills) api.skills().then(value => { if (!disposed) setCatalog(value); })
      .catch(error => { if (!disposed) setError(errorText(error)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [loadSkills, api, sessionId, revision]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node) && event.target !== editor.current) setActiveSession(undefined);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open, editor]);

  const items = [
    ...commands.map(item => ({ command: `/${item.name}`, description: item.label, source: '内置',
      reason: item.name === 'compact' && compactDisabled ? '需要空闲会话，并先处理审批或恢复事项' :
        (item.name === 'context' || item.name === 'agents') && !sessionId ? '请先选择会话' :
          controlsDisabled ? '当前暂不可用' : '' })),
    ...(catalog?.entries ?? []).map(item => ({ command: item.command, description: item.description,
      source: item.source_id === 'agents' ? '.agents' : '.claude',
      reason: controlsDisabled ? '当前暂不可用' : !readEnabled || !catalog?.read_registered ? '请先启用 read 工具' : '' })),
  ].filter(item => `${item.command} ${item.description}`.toLowerCase().includes(text.slice(1).toLowerCase()));
  const available = items.filter(item => !item.reason);
  const current = available.find(item => item.command === selected) ?? available[0];
  const currentIndex = items.indexOf(current);
  const activeId = current ? `${listId}-${currentIndex}` : undefined;
  useEffect(() => {
    if (open && activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: 'nearest' });
  }, [open, activeId]);
  function complete(command: string) {
    setText(`${command} `); setActiveSession(undefined); setSelected('');
    editor.current?.focus();
  }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (composing || event.nativeEvent.isComposing) return;
    if (open && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
      if (event.key === 'Escape') {
        event.preventDefault(); setActiveSession(undefined); return;
      }
      if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && available.length) {
        event.preventDefault();
        const index = available.indexOf(current);
        setSelected(available[(index + (event.key === 'ArrowDown' ? 1 : -1) + available.length) % available.length].command);
        return;
      }
      const exact = items.some(item => item.command === text);
      if ((event.key === 'Tab' && current) || (event.key === 'Enter' && !exact && items.length > 0)) {
        event.preventDefault(); if (current) complete(current.command); return;
      }
      if (event.key === 'Enter') setActiveSession(undefined);
    }
    onKeyDown(event);
  }
  return <>
    {open && <section ref={panel} className={styles.suggestions} aria-label="指令补全">
      <div className={styles.heading}>指令与技能 <span>↑ ↓ 选择 · Tab 补全 · Esc 关闭</span></div>
      <div id={listId} role="listbox" aria-label="指令建议" className={styles.list}>
        {items.map((item, index) => <button type="button" role="option" tabIndex={-1} key={item.command}
          id={`${listId}-${index}`} aria-label={`${item.command} ${item.description} ${item.reason} ${item.source}`}
          aria-selected={item === current} disabled={!!item.reason}
          className={styles.suggestion} onMouseDown={event => event.preventDefault()}
          onClick={() => complete(item.command)}>
          <span className={styles.command}><code>{item.command}</code><small>{item.source}</small></span>
          <span className={styles.description}>{item.description}</span>
          {item.reason && <span className={styles.reason}>{item.reason}</span>}
        </button>)}
      </div>
      {!items.length && !loading && <p className={styles.message}>没有匹配的指令或技能</p>}
      {loading && <p className={styles.message} role="status">正在查找可用技能…</p>}
      {error && <p className={styles.message} role="status">{error} <button type="button"
        onClick={() => setRevision(value => value + 1)}>重试加载技能建议</button></p>}
      {!!catalog?.diagnostics.length && <p className={styles.message} role="status">{catalog.diagnostics.length} 项技能资源未能加载，已跳过。</p>}
      <div className={styles.hint}>选择后填入输入框，再按 Enter 执行</div>
    </section>}
    <textarea ref={editor} className={styles.editor} aria-label="消息" placeholder="描述你想完成的任务…" value={text}
      rows={2} maxLength={32768} aria-autocomplete="list" aria-controls={open ? listId : undefined}
      aria-activedescendant={open ? activeId : undefined} onKeyDown={keyDown}
      onChange={event => { setSelected(''); setActiveSession(sessionId); setText(event.target.value); }}
      onFocus={() => setActiveSession(sessionId)} onBlur={event => {
        if (!panel.current?.contains(event.relatedTarget as Node)) setActiveSession(undefined);
      }}
      onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} />
  </>;
}
