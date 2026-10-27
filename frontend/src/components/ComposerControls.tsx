import { useEffect, useId, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { McpRefreshResult, PermissionMode, SessionControlOptions, SkillCatalogView } from '../types';
import type { WebApi } from '../api';
import SkillsMenu from './SkillsMenu';
import { commands } from '../commands';
import type { CommandName, ComposerMenu } from '../commands';
import { Chevron, Icon } from './Icons';
import { sendScrollOptions, type SendScroll } from '../useSendScroll';
import type { ImageInputs } from './ImageAttachments';
import styles from './ComposerControls.module.css';

const modes: { name: PermissionMode; description: string }[] = [
  { name: 'Default', description: '需要询问的操作交给你审批' },
  { name: 'NoAsk', description: '需要询问的操作直接拒绝' },
  { name: 'Auto', description: '先由模型审查，剩余请求再交给你' },
  { name: 'BypassApprovals', description: '跳过询问，仍遵守 Deny 与执行检查' },
];

export default function ComposerControls({ options, mode, tools, disabled, loading, error, reload, selectMode, selectTools, sessionId,
  menu, setMenu, runCommand, compactDisabled, api, selectSkill, focusEditor, sendScroll, imageInputs, sending = false }: {
  sendScroll?: { preference: SendScroll; select: (value: SendScroll) => void };
  imageInputs?: ImageInputs; sending?: boolean;
  options: SessionControlOptions | null; mode?: PermissionMode; tools: string[]; disabled: boolean;
  loading: boolean; error: boolean; reload: () => void;
  selectMode: (mode: PermissionMode) => void; selectTools: (names: string[]) => void;
  sessionId: string | null;
  menu: ComposerMenu; setMenu: Dispatch<SetStateAction<ComposerMenu>>;
  runCommand: (command: CommandName) => void; compactDisabled: boolean;
  api: WebApi; selectSkill: (command: string, catalog: SkillCatalogView) => void; focusEditor: () => void;
}) {
  const open = menu;
  const setOpen = setMenu;
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const imagePicker = useRef<HTMLInputElement>(null);
  const scrollTrigger = useRef<HTMLButtonElement>(null);
  const scrollMenu = useRef<HTMLElement>(null);
  const [scrollMenuMode, setScrollMenuMode] = useState<'pointer' | 'keyboard' | null>(null);
  const previousSession = useRef(sessionId);
  const [refreshingMcp, setRefreshingMcp] = useState(false);
  const [mcpReport, setMcpReport] = useState<McpRefreshResult | null>(null);
  const [mcpError, setMcpError] = useState(false);
  const mcpPending = useRef(false);
  const enabledCount = options ? tools.filter(name => options.tools.some(tool => tool.name === name && tool.available !== false)).length : tools.length;
  async function refreshMcp() {
    if (mcpPending.current) return;
    mcpPending.current = true; setRefreshingMcp(true); setMcpError(false);
    try { setMcpReport(await api.refreshMcp()); }
    catch { console.warn('mcp_configuration_refresh_failed'); setMcpError(true); }
    finally { mcpPending.current = false; setRefreshingMcp(false); reload(); }
  }
  const id = useId();
  useEffect(() => {
    if (previousSession.current !== null && previousSession.current !== sessionId) setOpen(null);
    previousSession.current = sessionId;
  }, [sessionId]);
  useEffect(() => {
    if (scrollMenuMode === 'keyboard') scrollMenu.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  }, [scrollMenuMode]);
  useEffect(() => {
    setScrollMenuMode(null);
    if (!open) return;
    function outside(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(null);
    }
    document.addEventListener('pointerdown', outside);
    const focus = open === 'commands' ? root.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)') :
      root.current?.querySelector<HTMLInputElement>('input:checked:not(:disabled)') ??
      root.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled), input:not([type="file"]):not(:disabled)');
    focus?.focus();
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);

  function toggle(kind: 'modes' | 'tools' | 'commands', button: HTMLButtonElement) {
    trigger.current = button;
    setOpen(previous => previous === kind ? null : kind);
  }

  return <div className={styles.controls} ref={root} onKeyDown={event => {
    if (open === 'commands' && scrollMenuMode && ['ArrowLeft', 'Escape'].includes(event.key)) {
      event.preventDefault(); setScrollMenuMode(null); scrollTrigger.current?.focus(); return;
    }
    if (event.key === 'Escape' && open) { event.preventDefault(); setOpen(null); trigger.current ? trigger.current.focus() : focusEditor(); }
    if (open === 'commands' && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      const currentMenu = (event.target as HTMLElement).closest('[role="menu"]') ?? root.current?.querySelector('[role="menu"]');
      if (currentMenu !== scrollMenu.current) setScrollMenuMode(null);
      const items = Array.from(currentMenu?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled), [role="menuitemradio"]:not(:disabled)') ?? [])
        .filter(item => item.closest('[role="menu"]') === currentMenu);
      const current = items.indexOf(document.activeElement as HTMLButtonElement);
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 :
        (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      event.preventDefault(); items[index]?.focus();
    }
  }}>
    <button type="button" className={`${styles.trigger} ${styles.plus}`} aria-label="指令与功能"
      aria-haspopup="menu" aria-expanded={open === 'commands'} disabled={disabled}
      onClick={event => toggle('commands', event.currentTarget)}><Icon name="plus" /></button>
    {imageInputs && <input ref={imagePicker} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp"
      multiple hidden aria-label="添加图片" disabled={sending || imageInputs.reading || !imageInputs.limits}
      onChange={event => {
        if (event.target.files) void imageInputs.addFiles(Array.from(event.target.files));
        event.target.value = '';
      }} />}
    <button type="button" className={styles.trigger} aria-label={`权限模式：${mode ?? '加载中'}`}
      aria-expanded={open === 'modes'} aria-controls={open === 'modes' ? id : undefined}
      onClick={event => toggle('modes', event.currentTarget)} disabled={disabled} title="权限模式 · Shift + Tab 切换">
      {mode ?? '权限模式'} <Chevron direction={open === 'modes' ? 'up' : 'down'} />
    </button>
    <button type="button" className={styles.trigger} aria-label={`工具 · ${enabledCount} 项启用`}
      aria-expanded={open === 'tools'} aria-controls={open === 'tools' ? id : undefined}
      onClick={event => toggle('tools', event.currentTarget)} disabled={disabled}>
      工具 <span className={styles.count}>{options ? enabledCount : '…'}</span>
    </button>
    {open === 'commands' && <section role="menu" aria-label="指令与功能" className={styles.popover}>
      <div className={styles.heading}>指令与功能</div>
      {imageInputs && <button type="button" role="menuitem" className={styles.command}
        disabled={sending || imageInputs.reading || !imageInputs.limits}
        onClick={() => { imagePicker.current?.click(); setOpen(null); }}><span>添加图片</span></button>}
      {commands.filter(command => command.name === 'compact' || command.name === 'skills').map(command => <button key={command.name} type="button" role="menuitem" className={styles.command}
        disabled={command.name === 'compact' && compactDisabled}
        onClick={() => { setOpen(null); runCommand(command.name); }}>
        <span>{command.label}</span><kbd>/{command.name}</kbd>
      </button>)}
      {sendScroll && <div className={styles.submenuRoot} onMouseEnter={() => setScrollMenuMode('pointer')}
        onMouseLeave={() => setScrollMenuMode(null)} onBlur={event => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setScrollMenuMode(null);
        }}>
        <button type="button" ref={scrollTrigger} role="menuitem" className={styles.command}
          aria-haspopup="menu" aria-expanded={!!scrollMenuMode} aria-controls={scrollMenuMode ? `${id}-scroll` : undefined}
          onClick={() => setScrollMenuMode('keyboard')} onKeyDown={event => {
            if (event.key === 'ArrowRight') { event.preventDefault(); event.stopPropagation(); setScrollMenuMode('keyboard'); }
          }}><span>发送后滚动</span><Chevron direction="right" /></button>
        {scrollMenuMode && <section id={`${id}-scroll`} ref={scrollMenu} role="menu" aria-label="发送后滚动"
          className={`${styles.popover} ${styles.submenu}`}>
          {(Object.keys(sendScrollOptions) as SendScroll[]).map(value => <button key={value} type="button"
            role="menuitemradio" aria-checked={sendScroll.preference === value} className={styles.command}
            onClick={() => { sendScroll.select(value); setOpen(null); trigger.current?.focus(); }}>
            <span>{sendScrollOptions[value]}</span><span aria-hidden="true">{sendScroll.preference === value ? '✓' : ''}</span>
          </button>)}
        </section>}
      </div>}
      <p className={styles.note}>指令也可在输入框输入。压缩需先结束运行并处理审批。</p>
    </section>}
    {open === 'skills' && <SkillsMenu api={api} sessionId={sessionId} readEnabled={tools.includes('read')} className={styles.popover}
      select={selectSkill} close={() => { setOpen(null); focusEditor(); }} />}
    {(open === 'modes' || open === 'tools') && <section id={id} role="dialog" aria-label={open === 'modes' ? '权限模式' : '主 Agent 工具'} className={styles.popover}>
      <div className={styles.heading}>{open === 'modes' ? '权限模式' : '主 Agent 可用工具'}
        <button type="button" onClick={() => { setOpen(null); trigger.current?.focus(); }} aria-label="关闭工具与模式菜单">×</button>
      </div>
      {loading ? <p className={styles.note}>正在加载工具与模式…</p> : error ?
        <p className={styles.note} role="status">工具与模式加载失败。<button type="button" onClick={reload}>重试加载</button></p> : <>
        <div className={styles.options}>
          {open === 'modes' ? <fieldset className={styles.fieldset} disabled={disabled}>
            <legend className={styles.srOnly}>选择权限模式</legend>
            {modes.map(item => <label key={item.name} className={styles.option}>
              <input type="radio" name={id} checked={mode === item.name} disabled={!options?.permission_modes.includes(item.name)}
                onChange={() => { selectMode(item.name); setOpen(null); trigger.current?.focus(); }} />
              <span><strong>{item.name}</strong><small>{item.name === 'Auto' && !options?.permission_modes.includes('Auto') ?
                '未配置 Auto 审查模型' : item.description}</small></span>
            </label>)}
          </fieldset> : <fieldset className={styles.fieldset} disabled={disabled}>
            <legend className={styles.srOnly}>选择主 Agent 工具</legend>
            {!options?.tools.length && <p className={styles.note}>没有已注册工具</p>}
            {options?.tools.map(tool => <label key={tool.name} className={styles.option}>
              <input type="checkbox" disabled={tool.available === false} checked={tools.includes(tool.name)} onChange={event =>
                selectTools(event.target.checked ? [...tools, tool.name] : tools.filter(name => name !== tool.name))} />
              <span><strong>{tool.name}</strong>
                {tool.mcp_source && <small>MCP 来源：{tool.mcp_source.server_id} / {tool.mcp_source.remote_tool}
                  {tool.mcp_source.transport && ` · ${tool.mcp_source.transport}`}</small>}
                {!!tool.subagent_roles?.length && <small>子 Agent 角色：{tool.subagent_roles.join('、')}</small>}
                {tool.available === false && <small>{tool.unavailable_reason === 'server_disabled' ? '服务已停用' :
                  tool.unavailable_reason === 'tool_disabled' ? '工具已从服务允许名单移除' : '工具定义已变化，请刷新 MCP 配置'}；保留已保存的会话选择。</small>}
                <small className={styles.toolDescription} title={tool.description}>{tool.description}</small></span>
            </label>)}
          </fieldset>}
        </div>
        <p className={styles.note}>{open === 'modes' ? '下一批工具调用生效，已有审批保持原样。' :
          '下一次模型调用生效。'}</p>
        <button type="button" className={styles.refresh} disabled={refreshingMcp}
          onClick={open === 'tools' ? () => void refreshMcp() : reload}>
          {refreshingMcp ? '正在刷新…' : '刷新工具与模式'}</button>
      </>}
      {open === 'tools' && <div aria-live="polite">
        {mcpError && <p role="status" className={styles.note}>MCP 配置刷新失败，请检查配置后重试；已生效配置保持。</p>}
        {(mcpReport?.servers ?? options?.mcp_servers ?? []).map(server => <p key={server.server_id} className={styles.note}>
          {server.server_id}：{server.status === 'error' ? server.enabled ? '刷新失败，原服务仍启用' : '刷新失败，服务未启用' :
            server.enabled ? '已启用' : server.draining ? '已停用，在途调用收尾中' : '已停用'}
          {server.error === 'connection_configuration_changed' && '；连接配置已变化，本次未应用，请停用并收尾后再启用。'}
        </p>)}
      </div>}
    </section>}
  </div>;
}
