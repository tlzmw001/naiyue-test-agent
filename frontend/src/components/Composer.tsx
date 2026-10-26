import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { WebApi } from '../api';
import type { PermissionMode, SessionControlOptions, SessionView, SkillCatalogView } from '../types';
import { errorText } from '../api';
import { Icon } from './Icons';
import ComposerControls from './ComposerControls';
import ComposerEditor from './ComposerEditor';
import { commands } from '../commands';
import type { CommandName, ComposerMenu } from '../commands';
import type { SendScroll } from '../useSendScroll';
import styles from '../App.module.css';
import composer from './Composer.module.css';

export default function Composer({ text, pending, pendingMode, busy, stopping, sending, blocked,
  setText, setPending, send, stop, api, session, controlsDisabled, selectTools, selectMode, contextEntry,
  openModel, compact, openContext, openAgents, sendScroll, workspaceEntry }: {
  sendScroll?: { preference: SendScroll; select: (value: SendScroll) => void };
  text: string; pending: string[]; pendingMode: boolean; busy: boolean; stopping: boolean;
  sending: boolean; blocked: boolean; setText: (text: string) => void;
  setPending: (values: string[]) => void; send: (followUp: boolean) => void; stop: () => void;
  contextEntry?: ReactNode;
  workspaceEntry?: ReactNode;
  api: WebApi; session: SessionView | null; controlsDisabled: boolean;
  selectTools: (names: string[]) => void; selectMode: (mode: PermissionMode) => void;
  openModel: () => void;
  compact: () => void; openContext: () => void; openAgents: () => void;
}) {
  const [followUp, setFollowUp] = useState(true);
  const [options, setOptions] = useState<SessionControlOptions | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(true);
  const [optionsError, setOptionsError] = useState(false);
  const [reload, setReload] = useState(0);
  const [menu, setMenu] = useState<ComposerMenu>(null);
  const [commandNotice, setCommandNotice] = useState('');
  const editor = useRef<HTMLTextAreaElement>(null);
  const [checkingSkill, setCheckingSkill] = useState(false);
  const confirming = useRef(false);
  const mounted = useRef(true);
  const mode = session?.permission_mode ?? options?.default_permission_mode;
  const tools = session?.active_tools ?? options?.default_active_tools ?? [];
  const readEnabled = tools.includes('read');
  const pendingVersion = JSON.stringify(pending);
  // A confirmation belongs to this draft, session and send state, never a later render.
  const submissionVersion = useMemo(() => ({}), [text, session?.session_id, followUp, blocked, sending,
    controlsDisabled, readEnabled, busy, pendingMode, pendingVersion, api]);
  const submission = useRef(submissionVersion);
  submission.current = submissionVersion;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const previousSession = useRef(session?.session_id);
  useEffect(() => {
    if (previousSession.current && previousSession.current !== session?.session_id) setMenu(null);
    if (previousSession.current !== session?.session_id) setFollowUp(true);
    previousSession.current = session?.session_id;
    setCommandNotice('');
  }, [session?.session_id]);
  useEffect(() => { if (!text.trim()) setFollowUp(true); }, [text]);
  useLayoutEffect(() => {
    const element = editor.current;
    if (!element) return;
    const resize = () => {
      element.style.height = 'auto';
      const css = window.getComputedStyle(element);
      const minimum = Number.parseFloat(css.minHeight) || 0;
      const maximum = Number.parseFloat(css.maxHeight) || Infinity;
      element.style.height = `${Math.min(maximum, Math.max(minimum, element.scrollHeight))}px`;
      element.style.overflowY = element.scrollHeight > maximum ? 'auto' : 'hidden';
    };
    resize();
    window.addEventListener('resize', resize);
    let width: number | undefined;
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(entries => {
      const nextWidth = entries[0]?.contentRect.width;
      if (nextWidth !== undefined && nextWidth !== width) { width = nextWidth; resize(); }
    });
    if (element.parentElement) observer?.observe(element.parentElement);
    return () => { window.removeEventListener('resize', resize); observer?.disconnect(); };
  }, [text]);
  const canSupplement = busy && !pendingMode && !blocked && !stopping;
  const showStop = busy && (!text.trim() || !canSupplement);
  const compactDisabled = !session || !session.can_run || busy || controlsDisabled;
  function runCommand(command: CommandName, typed = false) {
    setCommandNotice('');
    setMenu(null);
    if (command === 'compact') {
      if (compactDisabled) { setCommandNotice('压缩需要已有会话处于空闲状态，并先处理待审批或恢复事项。'); return; }
      compact(); return;
    }
    if ((command === 'context' || command === 'agents') && !session) {
      setCommandNotice('请先创建或选择一个会话。'); return;
    }
    if (typed) setText('');
    if (command === 'context') openContext();
    else if (command === 'agents') openAgents();
    else if (command === 'model') openModel();
    else setMenu(command === 'permissions' ? 'modes' : command);
  }
  function selectSkill(command: string, catalog: SkillCatalogView) {
    const previous = /^(\S+)(?:\s+([\s\S]*))?$/.exec(text.trim());
    const argumentsText = previous && catalog.entries.some(item => item.command === previous[1]) ? previous[2] ?? '' : text;
    setText(`${command} ${argumentsText}`);
    setCommandNotice(''); setMenu(null); editor.current?.focus();
  }
  async function submit() {
    if (confirming.current || sending || blocked) return;
    const match = /^\/([a-z0-9][a-z0-9_:-]*)(?:\s+([\s\S]*))?$/i.exec(text.trim());
    if (!match) { setCommandNotice(''); send(busy && followUp); return; }
    const command = commands.find(item => item.name === match[1]);
    if (command) {
      if (match[2]) { setCommandNotice(`/${command.name} 暂不接受参数，请直接输入该指令。`); return; }
      runCommand(command.name, true); return;
    }
    const captured = submission.current;
    confirming.current = true; setCheckingSkill(true); setCommandNotice('');
    try {
      const catalog = await (session?.session_id ? api.skills(session.session_id) : api.skills());
      if (!mounted.current || submission.current !== captured) return;
      if (!catalog.entries.some(item => item.command === `/${match[1]}`))
        setCommandNotice('不支持的指令，请从加号菜单查看可用指令。');
      else if (!catalog.read_registered || !tools.includes('read')) setCommandNotice('请先在工具中启用 read，再调用技能。');
      else send(busy && followUp);
    } catch (error) { if (mounted.current && submission.current === captured) setCommandNotice(errorText(error)); }
    finally { confirming.current = false; if (mounted.current) setCheckingSkill(false); }
  }
  useEffect(() => {
    let disposed = false;
    setLoadingOptions(true);
    setOptionsError(false);
    api.sessionControlOptions().then(value => { if (!disposed) setOptions(value); })
      .catch(() => { if (!disposed) setOptionsError(true); })
      .finally(() => { if (!disposed) setLoadingOptions(false); });
    return () => { disposed = true; };
  }, [api, reload]);
  return <div className={styles.composerArea}>
    {!!pending.length && <section className={styles.pending} aria-label="待发送内容">
      <div className={styles.pendingTitle}>待发送内容 · {pending.length} 条</div>
      {pending.map((value, index) => <div key={index} className={styles.pendingItem}>
        <textarea aria-label={`待发送消息 ${index + 1}`} value={value} rows={2} onChange={event =>
          setPending(pending.map((item, i) => i === index ? event.target.value : item))} />
        <button className={styles.iconButton} aria-label={`移除待发送消息 ${index + 1}`} onClick={() =>
          setPending(pending.filter((_, i) => i !== index))}><Icon name="close" /></button>
      </div>)}
    </section>}
    {workspaceEntry && <div className={composer.workspaceContext} role="region" aria-label="项目与执行位置">{workspaceEntry}</div>}
    <form className={`${styles.composer} ${composer.form}`} onSubmit={event => { event.preventDefault(); submit(); }}>
      {canSupplement && text.trim() && <div className={composer.modeRow}>
        <select className={styles.sendMode} aria-label="发送方式" value={followUp ? 'follow_up' : 'steering'}
          disabled={sending || checkingSkill} onChange={event => setFollowUp(event.target.value === 'follow_up')}>
          <option value="follow_up">结束后跟进</option><option value="steering">引导本轮</option>
        </select>
      </div>}
      <ComposerEditor editor={editor} text={text} setText={value => { setMenu(null); setText(value); }} api={api}
        sessionId={session?.session_id ?? null} readEnabled={readEnabled} compactDisabled={compactDisabled}
        controlsDisabled={controlsDisabled} menuOpen={!!menu} onKeyDown={event => {
          if (event.key === 'Tab' && event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey && !event.nativeEvent.isComposing &&
              !controlsDisabled && !loadingOptions && !optionsError && options && mode) {
            const available = options.permission_modes;
            event.preventDefault();
            selectMode(available[(available.indexOf(mode) + 1) % available.length]);
          }
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault(); submit();
          }
        }} />
      <div className={styles.composerActions}>
        <ComposerControls sessionId={session?.session_id ?? null} options={options} mode={mode} tools={tools}
          sendScroll={sendScroll}
          menu={menu} setMenu={setMenu} runCommand={runCommand} compactDisabled={compactDisabled}
          api={api} selectSkill={selectSkill} focusEditor={() => editor.current?.focus()}
          disabled={controlsDisabled} loading={loadingOptions} error={optionsError} reload={() => setReload(value => value + 1)}
          selectMode={selectMode} selectTools={selectTools} />
        <div className={styles.actionGroup}>
          {pendingMode && !busy && <span className={styles.resendLabel}>重新发送</span>}
          {contextEntry}
          {showStop ? <button type="button" className={styles.stopButton} aria-label="停止运行" onClick={stop} disabled={stopping}>
            <Icon name="stop" /></button> :
            <button type="submit" className={styles.sendButton} aria-label={pendingMode && !busy ? '重新发送消息' : '发送消息'}
              disabled={sending || checkingSkill || blocked || (!text.trim() && !pending.some(value => value.trim()))}><Icon name="send" /></button>}
        </div>
      </div>
      {pendingMode && <div className={styles.pendingHint}>{busy ? '停止期间仅保留输入' : '内容由你明确重新发送'}</div>}
      {commandNotice && <div className={styles.pendingHint} role="status">{commandNotice}</div>}
    </form>
    <div className={styles.composerFooter}>Enter 发送 · Shift + Enter 换行 · Shift + Tab 切换模式</div>
  </div>;
}
