import { StrictMode, useEffect, useState, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';

// 测试专用组合场景：只验证 Radix 公开 API 的受控子菜单机制，不进入产品页面。
// 两个场景：A 普通受控子菜单；B 原生 dialog 内嵌菜单（Portal container 挂 dialog 内）。
function SubMenu({ label = '发送后滚动', container }: { label?: string; container?: HTMLElement | null }) {
  const [open, setOpen] = useState(false);
  const subTrigger = useRef<HTMLDivElement>(null);
  return <DropdownMenu.Sub open={open} onOpenChange={setOpen}>
    <DropdownMenu.SubTrigger ref={subTrigger} aria-label={label}>{label}</DropdownMenu.SubTrigger>
    <DropdownMenu.Portal container={container}>
      <DropdownMenu.SubContent aria-label={label} style={{ zIndex: 1000 }} loop
        onEscapeKeyDown={event => {
          // 公开 prop：preventDefault 取消默认「关闭整棵树」，手动关闭子层并恢复触发器焦点。
          event.preventDefault();
          setOpen(false);
          subTrigger.current?.focus();
        }}>
        <DropdownMenu.Item onSelect={() => {
          (window as unknown as { __pilot: { selects: number } }).__pilot.selects++;
        }}>选项一</DropdownMenu.Item>
        <DropdownMenu.Item>选项二</DropdownMenu.Item>
      </DropdownMenu.SubContent>
    </DropdownMenu.Portal>
  </DropdownMenu.Sub>;
}

function PlainPilot() {
  return <DropdownMenu.Root modal={false}>
    <DropdownMenu.Trigger asChild>
      <button aria-label="打开主菜单">打开</button>
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal>
      <DropdownMenu.Content aria-label="主菜单" side="top" loop>
        <DropdownMenu.Item>首项</DropdownMenu.Item>
        <SubMenu />
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>;
}

function DialogPilot() {
  const [dialogEl, setDialogEl] = useState<HTMLDialogElement | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  return <>
    <button onClick={() => { dialogEl?.showModal(); setDialogOpen(true); }}>打开弹窗</button>
    <dialog ref={setDialogEl} aria-label="组合弹窗" style={{ width: 420, padding: 24, minHeight: 220 }}>
      {dialogOpen && <DropdownMenu.Root modal={false}>
        <DropdownMenu.Trigger asChild>
          <button aria-label="弹窗内菜单">打开菜单</button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal container={dialogEl}>
          <DropdownMenu.Content aria-label="弹窗内菜单" style={{ zIndex: 1000 }}>
            <SubMenu label="弹窗内子菜单" container={dialogEl} />
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>}
    </dialog>
  </>;
}

function AppRadix() {
  const pilotState = window as unknown as { __pilot?: Record<string, unknown> };
  pilotState.__pilot = { selects: 0, themeSelects: 0, scrollSelects: 0, reasons: [], ...(pilotState.__pilot ?? {}) };
  return <main>
    <section aria-label="场景A">
      <PlainPilot />
    </section>
    <section aria-label="场景B">
      <DialogPilot />
    </section>
  </main>;
}


function BaseCommandPilot({ menu, onMenuChange, editorRef, onSkillSelect, compactDisabled = true }: {
  menu: 'commands' | 'skills' | null;
  onMenuChange: (menu: 'commands' | 'skills' | null) => void;
  editorRef: React.RefObject<HTMLButtonElement | null>;
  onSkillSelect: (command: string) => void;
  compactDisabled?: boolean;
}) {
  const [rootOpen, setRootOpen] = useState(false);
  const popupRef = useRef<HTMLDivElement>(null);
  const subTrigger = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!rootOpen) return;
    const timer = window.setTimeout(() => popupRef.current
      ?.querySelector<HTMLElement>('[role=menuitem]:not([data-disabled])')?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [rootOpen]);
  const [scrollOpen, setScrollOpen] = useState(false);
  const scrollTrigger = useRef<HTMLDivElement>(null);
  const [preference, setPreference] = useState('keep');
  return <>
    <BaseMenu.Root modal={false} loopFocus open={menu === 'commands'}
      onOpenChange={open => onMenuChange(open ? 'commands' : null)}>
      <BaseMenu.Trigger render={<button className={composerStyles.trigger} aria-label="指令与功能"><Icon name="plus" /></button>} />
      <BaseMenu.Portal>
        <BaseMenu.Positioner align="start" side="top" sideOffset={8}>
          <BaseMenu.Popup ref={popupRef} className={composerStyles.radixPopover} aria-label="指令与功能">
            <div className={composerStyles.heading}>指令与功能</div>
            <BaseMenu.Item className={composerStyles.command}>云端任务</BaseMenu.Item>
            <BaseMenu.Item className={composerStyles.command}>添加图片</BaseMenu.Item>
            <BaseMenu.Item className={composerStyles.command} disabled={compactDisabled}
              onClick={() => { (window as unknown as { __pilot: { compactCalls: number } }).__pilot.compactCalls++; }}>
              压缩上下文 <kbd>/compact</kbd></BaseMenu.Item>
            <BaseMenu.Item className={composerStyles.command} closeOnClick={false}
              onClick={() => { setScrollOpen(false); onSkillSelect('/skills'); }}>
              技能 <kbd>/skills</kbd></BaseMenu.Item>
            <BaseMenu.SubmenuRoot open={scrollOpen} closeParentOnEsc={false}
              onOpenChange={setScrollOpen}>
              <BaseMenu.SubmenuTrigger ref={subTrigger} className={composerStyles.command} aria-label="发送后滚动">
                <span>发送后滚动</span><Chevron direction="right" />
              </BaseMenu.SubmenuTrigger>
              <BaseMenu.Portal>
                <BaseMenu.Positioner side="right" align="start" sideOffset={8}>
                  <BaseMenu.Popup className={composerStyles.radixSubmenu} aria-label="发送后滚动"
                    ref={element => { element?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus(); }}>
                    <BaseMenu.RadioGroup value={preference} onValueChange={value => { setPreference(value); (window as unknown as { __pilot: { scrollSelects: number } }).__pilot.scrollSelects++; }}>
                      <BaseMenu.RadioItem value="keep" closeOnClick className={composerStyles.command}>保持当前位置</BaseMenu.RadioItem>
                      <BaseMenu.RadioItem value="latest" closeOnClick className={composerStyles.command}>回到最新消息</BaseMenu.RadioItem>
                    </BaseMenu.RadioGroup>
                  </BaseMenu.Popup>
                </BaseMenu.Positioner>
              </BaseMenu.Portal>
            </BaseMenu.SubmenuRoot>
            <p className={composerStyles.note}>指令也可在输入框输入。压缩需先结束运行并处理审批。</p>
          </BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
    {menu === 'skills' && <BaseSkillsPanel
      close={() => { onMenuChange(null); editorRef.current?.focus(); }}
      editorRef={editorRef}
      select={command => { onSkillSelect(command); onMenuChange(null); editorRef.current?.focus(); }} />}
  </>;
}

function BaseDialogPilot() {
  const [dialogEl, setDialogEl] = useState<HTMLDialogElement | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [baseSubOpen, setBaseSubOpen] = useState(false);
  const [dialogPreference, setDialogPreference] = useState('keep');
  return <>
    <button onClick={() => { dialogEl?.showModal(); setDialogOpen(true); }}>打开Base弹窗</button>
    <dialog ref={setDialogEl} aria-label="Base组合弹窗" style={{ width: 420, padding: 24, minHeight: 220 }}>
      {dialogOpen && <BaseMenu.Root modal={false}>
        <BaseMenu.Trigger render={<button aria-label="Base弹窗内菜单">打开菜单</button>} />
        <BaseMenu.Portal container={dialogEl}>
          <BaseMenu.Positioner align="start" side="bottom" sideOffset={8}>
            <BaseMenu.Popup className={composerStyles.radixPopover} aria-label="Base弹窗内菜单">
              <BaseMenu.SubmenuRoot open={baseSubOpen} onOpenChange={setBaseSubOpen} closeParentOnEsc={false}>
                <BaseMenu.SubmenuTrigger className={composerStyles.command} aria-label="Base弹窗内子菜单">
                  <span>发送后滚动</span><Chevron direction="right" />
                </BaseMenu.SubmenuTrigger>
                <BaseMenu.Portal container={dialogEl}>
                  <BaseMenu.Positioner side="bottom" align="start" sideOffset={8}>
                    <BaseMenu.Popup className={composerStyles.radixSubmenu} aria-label="Base弹窗内子菜单"
                      finalFocus={closeType => (closeType === 'keyboard' ? true : undefined)}>
                      <BaseMenu.RadioGroup value={dialogPreference} onValueChange={setDialogPreference}>
                        <BaseMenu.RadioItem value="keep" closeOnClick className={composerStyles.command}>保持当前位置</BaseMenu.RadioItem>
                        <BaseMenu.RadioItem value="latest" closeOnClick className={composerStyles.command}>回到最新消息</BaseMenu.RadioItem>
                      </BaseMenu.RadioGroup>
                    </BaseMenu.Popup>
                  </BaseMenu.Positioner>
                </BaseMenu.Portal>
              </BaseMenu.SubmenuRoot>
            </BaseMenu.Popup>
          </BaseMenu.Positioner>
        </BaseMenu.Portal>
      </BaseMenu.Root>}
    </dialog>
  </>;
}

function BaseCommandMenuController({ editorRef }: { editorRef: React.RefObject<HTMLButtonElement | null> }) {
  const [menu, setMenuState] = useState<'commands' | 'skills' | null>(null);
  const setMenu = setMenuState;
  const [skillCommands, setSkillCommands] = useState<string[]>([]);
  const [sessionId, setSessionId] = useState('current');
  const previousSession = useRef<string | null>(null);
  useEffect(() => {
    if (previousSession.current !== null && previousSession.current !== sessionId) {
      setMenu(null);
      (window as unknown as { __sessionCloseReason?: string }).__sessionCloseReason = 'session-switch';
    }
    previousSession.current = sessionId;
  }, [sessionId]);
  return <>
    <BaseCommandPilot menu={menu} onMenuChange={setMenu} editorRef={editorRef}
      compactDisabled
      onSkillSelect={command => { setSkillCommands(previous => [...previous, command]); setMenu('skills'); }} />
    <button ref={editorRef} aria-label="编辑器占位">编辑器</button>
    <button aria-label="外部焦点目标" style={{ position: 'fixed', top: 8, right: 8 }}>外部目标</button>
    <button aria-label="切换会话" onClick={() => setSessionId('other')}>切换会话</button>
    <span aria-label="已选技能">{skillCommands.join(',')}</span>
  </>;
}

function App() {
  const editorRef = useRef<HTMLButtonElement>(null);
  const pilotState = window as unknown as { __pilot?: { selects: number; themeSelects: number; scrollSelects: number; reasons: string[] } };
  pilotState.__pilot = { selects: 0, themeSelects: 0, scrollSelects: 0, reasons: [], ...(pilotState.__pilot ?? {}) };
  return <main>
    <section aria-label="场景A">
      <PlainPilot />
    </section>
    <section aria-label="场景B">
      <DialogPilot />
    </section>
    <section aria-label="场景C">
      <BaseThemePilot />
    </section>
    <section aria-label="场景D">
      <BaseCommandMenuController editorRef={editorRef} />
    </section>
    <section aria-label="场景E">
      <BaseDialogPilot />
    </section>
  </main>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);

// ---- Spec143BD：Base UI 对照场景（测试专用） ----
import { Menu as BaseMenu } from '@base-ui/react/menu';
import { Chevron, Icon } from '../src/components/Icons';
import SkillsMenu from '../src/components/SkillsMenu';
import type { WebApi } from '../src/api';
import type { SkillCatalogView } from '../src/types';
import themeStyles from '../src/components/ThemeMenu.module.css';
import composerStyles from '../src/components/ComposerControls.module.css';

function skillApi(): Pick<WebApi, 'skills'> {
  return {
    skills: () => {
      const mode = (window as unknown as { __skillMode?: 'normal' | 'empty' | 'error' | 'pending' }).__skillMode ?? 'normal';
      if (mode === 'pending') return new Promise<SkillCatalogView>(() => undefined);
      if (mode === 'error') return Promise.reject(new Error('skill catalog failed'));
      const catalog: SkillCatalogView = mode === 'empty'
        ? { entries: [], default_source: 'agents', diagnostics: [], read_registered: true }
        : { entries: [{ source_id: 'agents', name: '代码审查', description: '审查当前改动', command: '/review' }],
            default_source: 'agents', diagnostics: [], read_registered: true };
      return Promise.resolve(catalog);
    },
  } as Pick<WebApi, 'skills'>;
}

function BaseThemePilot() {
  const themes = { system: '跟随系统', light: '浅色', dark: '深色' } as const;
  const [theme, setTheme] = useState<keyof typeof themes>('system');
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [rootOpen, setRootOpen] = useState(false);
  const popupRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!rootOpen) return;
    const timer = window.setTimeout(() => popupRef.current
      ?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [rootOpen]);
  return <BaseMenu.Root modal={false} loopFocus
      onOpenChange={(open, details) => {
        setRootOpen(open);
        (window as unknown as { __pilot: { reasons: string[] } }).__pilot.reasons.push(`${open}:${details.reason}`);
        // Escape 关闭时恢复主题触发器（鼠标外部点击不走此分支，尊重点击目标）。
        if (!open && details.reason === 'escape-key') triggerRef.current?.focus();
      }}>
      <BaseMenu.Trigger render={<button ref={triggerRef} className={themeStyles.trigger} aria-label={`主题：${themes[theme]}`}><span aria-hidden>◐</span></button>} />
      <BaseMenu.Portal>
        <BaseMenu.Positioner align="end" side="top" sideOffset={10}>
          <BaseMenu.Popup ref={popupRef} className={themeStyles.menu} aria-label="主题选择"
            finalFocus={closeType => (closeType === 'keyboard' || closeType === '' ? triggerRef.current : undefined)}>
            <BaseMenu.RadioGroup value={theme} onValueChange={value => { setTheme(value as keyof typeof themes); (window as unknown as { __pilot: { selects: number; themeSelects: number; scrollSelects: number; reasons: string[] } }).__pilot.themeSelects++; }}>
              {(Object.keys(themes) as (keyof typeof themes)[]).map(value =>
                <BaseMenu.RadioItem key={value} value={value} closeOnClick className={themeStyles.item}>{themes[value]}</BaseMenu.RadioItem>)}
            </BaseMenu.RadioGroup>
          </BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>;
}

function BaseSkillsPanel({ close, editorRef, select }: { close: () => void; editorRef: React.RefObject<HTMLButtonElement | null>; select: (command: string, catalog: SkillCatalogView) => void }) {
  const api = skillApi() as unknown as WebApi;
  return <div onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); editorRef.current?.focus(); }
  }}><SkillsMenu api={api} readEnabled className={composerStyles.radixPopover} close={close}
    select={select} /></div>;
}
