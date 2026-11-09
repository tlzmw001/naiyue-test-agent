import { useEffect, useId, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { McpRefreshResult, PermissionMode, SessionControlOptions, SkillCatalogView } from '../types';
import type { WebApi } from '../api';
import SkillsMenu from './SkillsMenu';
import CommandMenu from './CommandMenu';
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
  menu, setMenu, runCommand, compactDisabled, api, selectSkill, focusEditor, sendScroll, imageInputs, sending = false, openCloud }: {
  openCloud?: (trigger: HTMLButtonElement) => void;
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
  const previousSession = useRef(sessionId);
  const [refreshingMcp, setRefreshingMcp] = useState(false);
  const [mcpReport, setMcpReport] = useState<McpRefreshResult | null>(null);
  const [mcpError, setMcpError] = useState(false);
  const mcpPending = useRef(false);
  const builtinTools = options?.tools.filter(tool => !tool.mcp_source) ?? [];
  const mcpStatus = options?.mcp_servers ?? [];
  const serviceIds = new Set(mcpStatus.map(server => server.server_id));
  for (const tool of options?.tools ?? []) {
    if (tool.mcp_source) serviceIds.add(tool.mcp_source.server_id);
  }
  function serviceFor(name: string) {
    if (name.startsWith('mcp:')) return name.slice(4);
    const registered = options?.tools.find(tool => tool.name === name);
    if (registered) return registered.mcp_source?.server_id;
    const parts = name.split('__');
    return parts.length === 3 && parts[0] === 'mcp' && !parts[1].endsWith('_')
      && !parts[2].startsWith('_') && serviceIds.has(parts[1]) ? parts[1] : undefined;
  }
  const groups = [...serviceIds].map(id => {
    const status = mcpStatus.find(server => server.server_id === id);
    const count = (options?.tools ?? []).filter(tool => tool.mcp_source?.server_id === id && tool.available !== false).length;
    const enabled = status?.enabled ?? count > 0;
    return { id, enabled, count: enabled ? count : 0,
      selected: tools.some(name => serviceFor(name) === id) };
  });
  const enabledCount = options ? builtinTools.filter(tool => tools.includes(tool.name) && tool.available !== false).length
    + groups.filter(group => group.enabled && group.selected).length : tools.length;
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
    if (!open || open === 'commands') return;
    function outside(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(null);
    }
    // 外点监听对 skills 同样生效（此前 rAF 分支提前返回导致监听缺失，点击消息框无法关闭面板）。
    document.addEventListener('pointerdown', outside);
    let frame: number | undefined;
    if (open === 'skills') {
      frame = requestAnimationFrame(() => root.current
        ?.querySelector<HTMLElement>('[aria-label="关闭技能列表"]')?.focus());
    } else {
      const focus = root.current?.querySelector<HTMLInputElement>('input:checked:not(:disabled)') ??
        root.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled), input:not([type="file"]):not(:disabled)');
      focus?.focus();
    }
    return () => {
      document.removeEventListener('pointerdown', outside);
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, [open]);

  function toggle(kind: 'modes' | 'tools' | 'commands', button: HTMLButtonElement) {
    trigger.current = button;
    setOpen(previous => previous === kind ? null : kind);
  }

  return <div className={styles.controls} ref={root} onKeyDown={event => {
    // 指令菜单的键盘与关闭由 Radix 接管；modes/tools/skills 面板的 Escape 沿用原实现。
    // Skills 面板的 Escape 明确回编辑器（此前会落到旧的权限/工具触发器）。
    if (event.key === 'Escape' && open && open !== 'commands') {
      event.preventDefault();
      if (open === 'skills') { setOpen(null); focusEditor(); return; }
      setOpen(null);
      trigger.current ? trigger.current.focus() : focusEditor();
    }
  }}>
    <CommandMenu open={open === 'commands'} onOpenChange={value => setOpen(value ? 'commands' : null)}
      disabled={disabled} openCloud={openCloud} imageInputs={imageInputs} imagePicker={imagePicker}
      sending={sending} compactDisabled={compactDisabled} runCommand={runCommand} sendScroll={sendScroll} />
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
            {!builtinTools.length && !groups.length && <p className={styles.note}>没有已注册工具</p>}
            {builtinTools.map(tool => <label key={tool.name} className={styles.option}>
              <input type="checkbox" disabled={tool.available === false} checked={tools.includes(tool.name)} onChange={event =>
                selectTools(event.target.checked ? [...tools, tool.name] : tools.filter(name => name !== tool.name))} />
              <span><strong>{tool.name}</strong>
                {!!tool.subagent_roles?.length && <small>子 Agent 角色：{tool.subagent_roles.join('、')}</small>}
                {tool.available === false && <small>{tool.unavailable_reason === 'server_disabled' ? '服务已停用' :
                  tool.unavailable_reason === 'tool_disabled' ? '工具已从服务允许名单移除' : '工具定义已变化，请刷新 MCP 配置'}；保留已保存的会话选择。</small>}
                <small className={styles.toolDescription} title={tool.description}>{tool.description}</small></span>
            </label>)}
            {groups.map(group => <label key={`mcp:${group.id}`} className={styles.option}
              title={group.enabled ? undefined : '服务已停用'}>
              <input type="checkbox" disabled={!group.enabled} checked={group.selected} onChange={event => {
                const remaining = tools.filter(name => serviceFor(name) !== group.id);
                selectTools(event.target.checked ? [...remaining, `mcp:${group.id}`] : remaining);
              }} />
              <span><strong>{group.id}</strong><small>{group.count} 个工具</small></span>
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
        {(mcpReport?.servers ?? mcpStatus).filter(server => server.status === 'error' || server.draining).map(server =>
          <p key={server.server_id} className={styles.note}>
          {server.server_id}：{server.status === 'error' ? server.enabled ? '刷新失败，原服务仍启用' : '刷新失败，服务未启用' :
            '已停用，在途调用收尾中'}
          {server.error === 'connection_configuration_changed' && '；连接配置已变化，本次未应用，请停用并收尾后再启用。'}
        </p>)}
      </div>}
    </section>}
  </div>;
}
