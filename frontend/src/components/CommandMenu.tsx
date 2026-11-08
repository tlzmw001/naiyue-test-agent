import { useCallback, useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { commands } from '../commands';
import type { CommandName } from '../commands';
import { Chevron, Icon } from './Icons';
import { sendScrollOptions, type SendScroll } from '../useSendScroll';
import type { ImageInputs } from './ImageAttachments';
import styles from './ComposerControls.module.css';

/**
 * 「指令与功能」菜单与「发送后滚动」二级菜单（Radix 行为层 + 现有 CSS Modules）。
 * 非模态（modal=false）、循环导航（loop，与自研一致）。
 * 二级子菜单用公开的受控 DropdownMenu.Sub：Escape 时 preventDefault 取消默认
 * 「关闭整棵树」，手动关闭子层并恢复 SubTrigger 焦点（机制见 e2e-harness 组合验证）。
 */
export default function CommandMenu({ open, onOpenChange, disabled, openCloud, imageInputs, imagePicker, sending, compactDisabled, runCommand, sendScroll }: {
  open: boolean; onOpenChange: (open: boolean) => void; disabled: boolean;
  openCloud?: (trigger: HTMLButtonElement) => void;
  imageInputs?: ImageInputs; imagePicker: RefObject<HTMLInputElement | null>; sending?: boolean;
  compactDisabled: boolean; runCommand: (command: CommandName) => void;
  sendScroll?: { preference: SendScroll; select: (value: SendScroll) => void };
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const subTrigger = useRef<HTMLDivElement>(null);
  // 「技能」项会把控制权交给 SkillsMenu：抑制 Radix 卸载时的触发器焦点恢复，
  // 保留 SkillsMenu autoFocus 的关闭按钮焦点（body 焦点会导致 Escape 无法回编辑器）。
  const skillsHandoff = useRef(false);
  const [scrollOpen, setScrollOpen] = useState(false);
  const [narrow, setNarrow] = useState(() => typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 640px)').matches);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia('(max-width: 640px)');
    const update = () => setNarrow(query.matches);
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  const contentRef = useCallback((element: HTMLDivElement | null) => {
    // 非模态 Radix 不自动聚焦；挂载时聚焦首个可用菜单项（稳定 ref，避免重渲染抢焦点）。
    element?.querySelector<HTMLElement>('[role="menuitem"]:not([data-disabled])')?.focus();
  }, []);
  const subContentRef = useCallback((element: HTMLElement | null) => {
    // 二级展开时聚焦当前选中项，与自研的键盘展开语义一致。
    element?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  }, []);
  return <DropdownMenu.Root open={open} onOpenChange={onOpenChange} modal={false}>
    <DropdownMenu.Trigger asChild>
      <button ref={trigger} type="button" className={`${styles.trigger} ${styles.plus}`} aria-label="指令与功能"
        disabled={disabled && !openCloud}>
        <Icon name="plus" />
      </button>
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal container={trigger.current?.parentElement}>
      <DropdownMenu.Content className={styles.radixPopover} align="start" side="top" sideOffset={8} loop
        aria-label="指令与功能" aria-labelledby={undefined} ref={contentRef}
        onCloseAutoFocus={event => {
          if (!skillsHandoff.current) return;
          skillsHandoff.current = false;
          event.preventDefault();
        }}>
        <div className={styles.heading}>指令与功能</div>
        {openCloud && <DropdownMenu.Item className={styles.command} onSelect={() => { if (trigger.current) openCloud(trigger.current); }}>
          <span>云端任务</span></DropdownMenu.Item>}
        {imageInputs && <DropdownMenu.Item className={styles.command}
          disabled={sending || imageInputs.reading || !imageInputs.limits}
          onSelect={() => { imagePicker.current?.click(); }}><span>添加图片</span></DropdownMenu.Item>}
        {commands.filter(command => command.name === 'compact' || command.name === 'skills').map(command =>
          <DropdownMenu.Item key={command.name} className={styles.command}
            disabled={command.name === 'compact' && compactDisabled}
            onSelect={() => {
              if (command.name === 'skills') skillsHandoff.current = true;
              runCommand(command.name);
            }}>
            <span>{command.label}</span><kbd>/{command.name}</kbd>
          </DropdownMenu.Item>)}
        {sendScroll && <DropdownMenu.Sub open={scrollOpen} onOpenChange={setScrollOpen}>
          <DropdownMenu.SubTrigger ref={subTrigger} className={styles.command} aria-label="发送后滚动" aria-expanded={scrollOpen}>
            <span>发送后滚动</span><Chevron direction="right" />
          </DropdownMenu.SubTrigger>
          <DropdownMenu.Portal container={trigger.current?.parentElement}>
            <DropdownMenu.SubContent className={styles.radixSubmenu} sideOffset={narrow ? -160 : 0} avoidCollisions={!narrow} collisionPadding={8} loop
              aria-label="发送后滚动" aria-labelledby={undefined}
              ref={subContentRef}
              onEscapeKeyDown={event => {
                // 公开 prop：preventDefault 取消默认「关闭整棵树」，手动关闭子层并恢复触发器焦点。
                event.preventDefault();
                setScrollOpen(false);
                subTrigger.current?.focus();
              }}>
              <DropdownMenu.RadioGroup value={sendScroll.preference} onValueChange={value => sendScroll.select(value as SendScroll)}>
                {(Object.keys(sendScrollOptions) as SendScroll[]).map(value => <DropdownMenu.RadioItem key={value} value={value} className={styles.command}>
                  <span>{sendScrollOptions[value]}</span><span aria-hidden="true">{sendScroll.preference === value ? '✓' : ''}</span>
                </DropdownMenu.RadioItem>)}
              </DropdownMenu.RadioGroup>
            </DropdownMenu.SubContent>
          </DropdownMenu.Portal>
        </DropdownMenu.Sub>}
        <p className={styles.note}>指令也可在输入框输入。压缩需先结束运行并处理审批。</p>
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>;
}
